"""
Tanishi Tool Registry — Hands for the brain.

This is the central hub that:
1. Defines tools in Claude's native tool-use format
2. Routes tool calls to the right handler
3. Manages tool permissions and safety

When Claude decides to use a tool, it returns a tool_use block.
We execute it here and feed the result back. Claude then responds
with the final answer incorporating the tool's output.
"""

import asyncio
import json
import inspect
import logging
import uuid
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from tanishi.config import tool_params as tool_cfg
from tanishi.core_state import predictions
from tanishi.core_state.events import current_task, emit, redact

logger = logging.getLogger(__name__)

# What the caller gets when a tool is refused because its tool_call event could not be written.
EVENT_LOG_UNAVAILABLE = "Event log unavailable: the tool was not run."


def _record(kind: str, payload: dict, session_id: str | None) -> None:
    """Emit a tool_result event. The tool has already run, so a broken log only warns (decision 0008)."""
    try:
        emit(kind, payload, session_id=session_id)
    except Exception as e:  # noqa: BLE001 - never log the payload: it may hold what we failed to redact
        logger.warning("could not record %s event: %s", kind, type(e).__name__)


def _jsonable(value: Any) -> Any:
    """Tool input as plain JSON, whatever the model sent; emit() then redacts and clips it."""
    value = redact(value)  # caps the nesting first, so neither json nor repr can recurse too deep
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        try:
            text = repr(value)
        except Exception:  # noqa: BLE001 - a broken __repr__ must not stop the call being logged
            text = f"<{type(value).__name__}>"
        return {"unserialisable": text}


def _predict(tool_name: str) -> str | None:
    """Write the prediction for one tool call (node CS5). The ledger only learns, so a broken one only warns."""
    try:
        p_success, latency_ms = predictions.tool_forecast(tool_name)
        return predictions.predict(f"tool:{tool_name}", {"success": True, "latency_ms": latency_ms}, p_success)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not write the prediction for tool %s: %s", tool_name, type(e).__name__)
        return None


def _resolve(prediction_id: str | None, success: bool, latency_ms: float | None) -> None:
    if prediction_id is None:
        return
    try:
        predictions.resolve(prediction_id, {"success": success, "latency_ms": latency_ms})
    except Exception as e:  # noqa: BLE001
        logger.warning("could not resolve the prediction for a tool call: %s", type(e).__name__)


@dataclass
class ToolResult:
    """Result from executing a tool."""
    success: bool
    output: str
    tool_name: str
    execution_time_ms: float = 0
    error: str = ""


@dataclass
class ToolDefinition:
    """A registered tool."""
    name: str
    description: str
    input_schema: dict
    handler: Callable
    requires_approval: bool = False  # If True, ask user before executing
    category: str = "general"        # "search", "filesystem", "system", "code", "communication"
    risk_level: str = "low"          # "low", "medium", "high"
    timeout_override: Optional[float] = None  # None=DEFAULT_TOOL_TIMEOUT; 0=handler owns timeout


def _registry_timeout_seconds(tool: ToolDefinition) -> Optional[float]:
    """Return registry wait_for cap in seconds, or None if the handler owns timing."""
    if tool.timeout_override is not None:
        if tool.timeout_override == 0:
            return None
        return tool.timeout_override
    return tool_cfg.DEFAULT_TOOL_TIMEOUT


async def _run_handler_with_timeout(
    handler: Callable,
    tool_input: dict,
    timeout_s: Optional[float],
) -> Any:
    if inspect.iscoroutinefunction(handler):
        coro = handler(**tool_input)
        if timeout_s is None:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout_s)
    if timeout_s is None:
        return await asyncio.to_thread(handler, **tool_input)
    return await asyncio.wait_for(
        asyncio.to_thread(handler, **tool_input),
        timeout=timeout_s,
    )


class ToolRegistry:
    """
    Central registry for all Tanishi tools.

    Tools are registered here and exposed to Claude's tool-use API.
    When Claude wants to use a tool, we look it up here and execute it.
    """

    def __init__(self):
        self.tools: dict[str, ToolDefinition] = {}
        self._approval_callback: Optional[Callable] = None

    def register(self, tool: ToolDefinition):
        """Register a tool."""
        self.tools[tool.name] = tool

    def set_approval_callback(self, callback: Callable):
        """Set the function to call when a tool needs user approval."""
        self._approval_callback = callback

    def get_claude_tools(self) -> list[dict]:
        """
        Get all tools in Claude's native format.
        This is passed to the `tools` parameter of the API call.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]

    async def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        """
        Execute a tool by name with given input.

        Returns ToolResult with the output or error. Every call is recorded in the
        Core State event log as a tool_call event followed by a tool_result event; both
        carry the same call_id, and the task_id and session_id of the current task.
        Fails closed: if the tool_call event cannot be written, the tool does not run.
        Before the tool_call event, a prediction (expected success and latency) goes into the
        prediction ledger; it is resolved with what happened, whatever happened (node CS5).
        """
        task_id, session_id = current_task()
        ids = {"tool": tool_name, "call_id": uuid.uuid4().hex, "task_id": task_id}
        prediction_id = _predict(tool_name)
        try:
            emit("tool_call", {**ids, "input": _jsonable(tool_input)}, session_id=session_id)
        except Exception as e:  # noqa: BLE001 - never log the payload: it may hold what we failed to redact
            logger.warning("tool %s not run, its tool_call event could not be recorded: %s",
                           tool_name, type(e).__name__)
            _resolve(prediction_id, False, None)
            return ToolResult(success=False, output="", tool_name=tool_name, error=EVENT_LOG_UNAVAILABLE)
        result: ToolResult | None = None
        try:
            result = await self._execute(tool_name, tool_input)
            return result
        finally:
            if result is None:  # cancelled, or the approval callback raised
                _resolve(prediction_id, False, None)
                _record("tool_result", {**ids, "success": False, "error": "did not finish"}, session_id)
            else:
                _resolve(prediction_id, result.success, round(result.execution_time_ms, 1))
                _record("tool_result", {
                    **ids,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                    "ms": round(result.execution_time_ms, 1),
                }, session_id)

    async def _execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        import time
        start = time.time()

        if tool_name not in self.tools:
            return ToolResult(
                success=False,
                output="",
                tool_name=tool_name,
                error=f"Unknown tool: {tool_name}. I must be dreaming about capabilities I don't have yet.",
            )

        tool = self.tools[tool_name]

        # Check if approval needed (fail-closed: no callback => deny)
        if tool.requires_approval:
            if self._approval_callback is None:
                elapsed = (time.time() - start) * 1000
                return ToolResult(
                    success=False,
                    output="",
                    tool_name=tool_name,
                    execution_time_ms=elapsed,
                    error=(
                        f"'{tool_name}' requires approval, but no approval mechanism is "
                        "available in this context. Denied."
                    ),
                )

            approved = self._approval_callback(tool_name, tool_input)
            if inspect.isawaitable(approved):
                approved = await approved
            if not approved:
                elapsed = (time.time() - start) * 1000
                return ToolResult(
                    success=False,
                    output="",
                    tool_name=tool_name,
                    execution_time_ms=elapsed,
                    error=f"'{tool_name}' was denied.",
                )

        max_attempts = 1 + tool_cfg.TOOL_RETRIES
        last_error = ""
        timeout_s = _registry_timeout_seconds(tool)
        for _attempt in range(max_attempts):
            try:
                result = await _run_handler_with_timeout(
                    tool.handler,
                    tool_input,
                    timeout_s,
                )
                elapsed = (time.time() - start) * 1000
                if isinstance(result, (dict, list)):
                    normalized = json.dumps(result, ensure_ascii=False)
                else:
                    normalized = str(result) if not isinstance(result, str) else result

                return ToolResult(
                    success=True,
                    output=normalized,
                    tool_name=tool_name,
                    execution_time_ms=elapsed,
                )
            except asyncio.TimeoutError:
                cap = timeout_s if timeout_s is not None else tool_cfg.DEFAULT_TOOL_TIMEOUT
                last_error = f"Timeout after {cap}s"
            except Exception as e:
                last_error = f"{type(e).__name__}: {str(e)}"

        elapsed = (time.time() - start) * 1000
        return ToolResult(
            success=False,
            output="",
            tool_name=tool_name,
            execution_time_ms=elapsed,
            error=last_error,
        )

    def list_tools(self) -> list[dict]:
        """List all registered tools with metadata."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "risk_level": t.risk_level,
                "requires_approval": t.requires_approval,
            }
            for t in self.tools.values()
        ]

    def get_tools_summary(self) -> str:
        """Get a human-readable summary of available tools."""
        if not self.tools:
            return "No tools registered. I'm all brain, no hands."

        by_category: dict[str, list[str]] = {}
        for t in self.tools.values():
            by_category.setdefault(t.category, []).append(t.name)

        lines = [f"**{len(self.tools)} tools available:**"]
        for cat, names in by_category.items():
            lines.append(f"  [{cat}] {', '.join(names)}")
        return "\n".join(lines)
