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

import json
import traceback
from datetime import datetime
from typing import Any, Callable, Optional
from dataclasses import dataclass, field


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

        Returns ToolResult with the output or error.
        """
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

        # Check if approval needed
        if tool.requires_approval and self._approval_callback:
            approved = self._approval_callback(tool_name, tool_input)
            if not approved:
                return ToolResult(
                    success=False,
                    output="",
                    tool_name=tool_name,
                    error="User denied permission for this action.",
                )

        # Execute
        try:
            result = await tool.handler(**tool_input)
            elapsed = (time.time() - start) * 1000

            return ToolResult(
                success=True,
                output=str(result) if not isinstance(result, str) else result,
                tool_name=tool_name,
                execution_time_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return ToolResult(
                success=False,
                output="",
                tool_name=tool_name,
                execution_time_ms=elapsed,
                error=f"{type(e).__name__}: {str(e)}",
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
