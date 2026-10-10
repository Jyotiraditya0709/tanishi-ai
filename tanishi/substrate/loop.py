"""The reasoning loop: attend, recall, deliberate, predict, act, reflect, under a step budget (node SUB2).

`CognitiveLoop(router, memory, tools, budget, generate=...).run(task)` runs passes over the program's phases until the
model gives a final answer, the step budget runs out, or no phase can make progress. `TanishiBrain.think()` delegates
here only when `TANISHI_SUBSTRATE=1` (`enabled()`); otherwise the legacy path runs untouched. Decision 0024.

- **Program.** Which phases run, in what order, and how many times each may run in one task is data:
  `loop_program.json` next to this file, `{"version", "steps": [{"name", "budget"}, ...]}`. It is read when a loop is
  built, never at import, so evolution can change it. A phase missing from the program never runs.
- **Step.** One pass through the program. `StepBudget.max_steps` bounds the passes, and a pass acts at most once,
  so it bounds tool calls too. When the last allowed pass ends without an answer the loop stops with `STOP_BUDGET`.
- **Model.** The router picks the model (`route(step, context)`, or `aroute` when it has one); `generate(decision,
  prompt)` calls it. A reply that is one JSON object `{"action": {"tool": <name>, "input": {...}}}` asks to act; any
  other reply is the final answer.
- **Act.** A prediction (CS5, `about = "act:<tool>"`) and a `loop_step` event naming the tool (CS2) are written
  before `tools.execute()`; the prediction is resolved with the tool's real `success` after. If either write fails,
  the tool does not run and the loop stops with `STOP_UNRECORDED` (fail closed: an act that is not recorded does not
  happen). Every other phase event fails open, like `think()`.
- **Memory** is optional and fails open: a memory that raises is skipped.

Events carry no task text, prompt, memory or tool input: the task can be personal, and the registry already logs
tool calls (redacted). `loop_start`, one `loop_step {"step": <phase>, ...}` per phase run, and `loop_stop`.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tanishi.core_state import events, predictions
from tanishi.substrate.router import (
    RouteContext,
    RouteDecision,
    RouteRefused,
    estimate_tokens,
)
from tanishi.substrate.state import Step

logger = logging.getLogger(__name__)

FLAG = "TANISHI_SUBSTRATE"
PROGRAM_PATH = Path(__file__).with_name("loop_program.json")
PHASES = ("attend", "recall", "deliberate", "predict", "act", "reflect")
ACTOR = "substrate.loop"

STOP_ANSWER = "answered"
STOP_BUDGET = "budget"
STOP_PHASE_BUDGET = "phase_budget"  # no progress because a phase used up its program budget
STOP_STALLED = "stalled"  # no progress for another reason (the model asked to act, the program has no act)
STOP_REFUSED = "route_refused"
STOP_ROUTE_ERROR = "route_error"
STOP_MODEL_ERROR = "model_error"
STOP_UNRECORDED = "unrecorded"  # the prediction or the act event could not be written, so the act did not run

RECALL_LIMIT = 5
ATTEND_WINDOW = 6  # observations shown in full; older ones are counted, not shown
OBSERVATION_CHARS = 2000
MEMORY_CHARS = 500

Generate = Callable[[RouteDecision, str], Awaitable[str]]


def enabled() -> bool:
    """True when `TANISHI_SUBSTRATE=1`. Read on every call."""
    return os.environ.get(FLAG, "").strip() == "1"


def load_program(path: Path | None = None) -> dict:
    """The loop program as stored. Raises ValueError (or TypeError) for one the loop cannot run."""
    path = Path(PROGRAM_PATH if path is None else path)
    program = json.loads(path.read_text(encoding="utf-8"))
    _check_program(program)
    return program


def _check_program(program: Any) -> None:
    if not isinstance(program, dict):
        raise TypeError("the loop program must be a JSON object")
    version = program.get("version")
    if not ((isinstance(version, int) and not isinstance(version, bool))
            or (isinstance(version, str) and version.strip())):
        raise ValueError("the loop program needs a version (an int or a non-blank str)")
    steps = program.get("steps")
    if not isinstance(steps, list):
        raise TypeError("the loop program needs a list of steps")
    seen: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise TypeError(f"program step #{i} must be an object")
        name, budget = step.get("name"), step.get("budget")
        if name not in PHASES:
            raise ValueError(f"program step #{i} names no known phase (known: {', '.join(PHASES)})")
        if name in seen:
            raise ValueError(f"program step #{i} repeats phase {name!r}")
        seen.add(name)
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ValueError(f"program step #{i} needs a budget that is an int >= 0")


@dataclass(frozen=True)
class StepBudget:
    """How many passes through the program one task may take."""

    max_steps: int

    def __post_init__(self):
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int):
            raise TypeError("max_steps must be an int")
        if self.max_steps < 0:
            raise ValueError("max_steps must be >= 0")


@dataclass
class Result:
    content: str
    success: bool
    stop_reason: str
    steps: int
    model_used: str = ""
    tools_used: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class _Action:
    tool: str
    input: dict
    prediction_id: str | None = None


@dataclass
class _Observation:
    tool: str
    success: bool
    text: str


class _Stop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def parse_reply(text: str) -> _Action | str | None:
    """An `_Action` for a well-formed act request, None for a malformed one, else the text as the final answer."""
    body = text.strip()
    if body.startswith("```") and body.endswith("```"):
        body = body[3:-3].removeprefix("json")
    try:
        obj = json.loads(body)
    except ValueError:
        return text
    if not isinstance(obj, dict) or "action" not in obj:
        return text
    action = obj["action"]
    if not isinstance(action, dict):
        return None
    tool, tool_input = action.get("tool"), action.get("input", {})
    if not isinstance(tool, str) or not tool.strip() or not isinstance(tool_input, dict):
        return None
    return _Action(tool, tool_input)


class CognitiveLoop:
    def __init__(self, router: Any, memory: Any, tools: Any, budget: StepBudget, *, generate: Generate,
                 context: str = "", personal: bool = False, local_up: bool = True):
        if not isinstance(budget, StepBudget):
            raise TypeError("budget must be a StepBudget")
        self.router, self.memory, self.tools, self.budget = router, memory, tools, budget
        self.generate = generate
        self.context = context  # extra text for every prompt (history, persona); never sent to memory or logs
        self.personal, self.local_up = personal, local_up
        self.program = load_program()
        self._order = [s["name"] for s in self.program["steps"]]
        self._limits = {s["name"]: s["budget"] for s in self.program["steps"]}

    async def run(self, task: str) -> Result:
        if not isinstance(task, str):
            raise TypeError("task must be a str")
        task_id, session_id = events.current_task()
        scope = contextlib.nullcontext()
        if task_id is None:
            task_id = uuid.uuid4().hex
            scope = events.task_scope(task_id, session_id)
        with scope:
            return await _Run(self, task, task_id, session_id).go()


class _Run:
    """The state of one `run()`."""

    def __init__(self, loop: CognitiveLoop, task: str, task_id: str, session_id: str | None):
        self.loop, self.task, self.task_id, self.session_id = loop, task, task_id, session_id
        self.uses = dict.fromkeys(loop._order, 0)
        self.steps = 0
        self.memories: list[str] = []
        self.observations: list[_Observation] = []
        self.notes: list[str] = []
        self.pending: _Action | None = None
        self.answer: str | None = None
        self.last: _Observation | None = None  # what the act phase of this pass produced, for reflect
        self.model_used = ""
        self.tools_used: list[str] = []
        self.blocked: set[str] = set()

    # -------------------------------------------------------------------------------------------- driver

    async def go(self) -> Result:
        lp = self.loop
        self._event("loop_start", {"program_version": lp.program["version"], "phases": list(lp._order),
                                   "max_steps": lp.budget.max_steps})
        try:
            reason, detail = await self._passes()
        except _Stop as stop:
            reason, detail = stop.reason, stop.detail
        success = reason == STOP_ANSWER
        content = self.answer if success else _apology(reason, detail)
        self._event("loop_stop", {"stop_reason": reason, "success": success, "steps": self.steps,
                                  "tools_used": list(self.tools_used), "detail": detail})
        return Result(content=content or "", success=success, stop_reason=reason, steps=self.steps,
                      model_used=self.model_used, tools_used=list(self.tools_used), detail=detail)

    async def _passes(self) -> tuple[str, str]:
        while True:
            if self.steps >= self.loop.budget.max_steps:
                return STOP_BUDGET, f"used all {self.steps} steps"
            self.steps += 1
            self.last, self.blocked = None, set()
            progressed = False
            for phase in self.loop._order:
                if self.uses[phase] >= self.loop._limits[phase]:
                    self.blocked.add(phase)
                    continue
                if await getattr(self, f"_{phase}")():
                    self.uses[phase] += 1
                    progressed |= phase in ("deliberate", "act")
            if self.answer is not None:
                return STOP_ANSWER, ""
            if not progressed:
                if self.blocked & {"deliberate", "act"}:
                    return STOP_PHASE_BUDGET, "phase budget used up: " + ", ".join(sorted(self.blocked))
                why = "the model asked to act but the program has no act phase" if self.pending else "nothing to do"
                return STOP_STALLED, why

    # -------------------------------------------------------------------------------------------- phases
    # Each returns True when it ran (and so wrote its event), False when it had nothing to do.

    async def _attend(self) -> bool:
        shown = min(len(self.observations), ATTEND_WINDOW)
        self._phase("attend", observations=len(self.observations), shown=shown, memories=len(self.memories))
        return True

    async def _recall(self) -> bool:
        memory = self.loop.memory
        if memory is None:
            return False
        ok, hits = True, []
        try:
            if callable(getattr(memory, "recall", None)):
                hits = await asyncio.to_thread(memory.recall, self.task, RECALL_LIMIT)
            else:
                hits = await asyncio.to_thread(memory.search, self.task, RECALL_LIMIT)
        except Exception as e:  # noqa: BLE001 - memory fails open; never log what it held
            ok = False
            logger.warning("loop recall failed: %s", type(e).__name__)
        added = 0
        for hit in list(hits or [])[:RECALL_LIMIT]:
            text = getattr(hit, "content", None)
            if isinstance(text, str) and text.strip() and text not in self.memories:
                self.memories.append(text)
                added += 1
        self._phase("recall", ok=ok, hits=added)
        return True

    async def _deliberate(self) -> bool:
        if self.pending is not None or self.answer is not None:
            return False  # an act is waiting: nothing new to think about until it runs
        prompt = self._prompt()
        step = Step(id=f"{self.task_id}:{self.steps}:deliberate", description=prompt)
        context = RouteContext(needs_tools="act" in self.loop._order, personal=self.loop.personal,
                               local_up=self.loop.local_up, input_tokens=estimate_tokens(prompt))
        try:
            decision = await self._route(step, context)
        except RouteRefused as e:
            self._phase("deliberate", outcome="refused", wait=e.wait)
            raise _Stop(STOP_REFUSED, e.reason) from None
        except Exception as e:  # noqa: BLE001 - a broken router ends the task, it does not crash the caller
            self._phase("deliberate", outcome="route_error", error=type(e).__name__)
            raise _Stop(STOP_ROUTE_ERROR, type(e).__name__) from None
        self.model_used = decision.model_id
        try:
            reply = await self.loop.generate(decision, prompt)
        except Exception as e:  # noqa: BLE001
            self._phase("deliberate", outcome="model_error", model_id=decision.model_id, error=type(e).__name__)
            raise _Stop(STOP_MODEL_ERROR, type(e).__name__) from None
        parsed = parse_reply(reply if isinstance(reply, str) else "")
        if isinstance(parsed, _Action):
            self.pending = parsed
            self._phase("deliberate", outcome="act", model_id=decision.model_id, tool=parsed.tool)
        elif parsed is None:
            self.notes.append("Your last reply asked to act but was not a valid action object; fix the format.")
            self._phase("deliberate", outcome="malformed_action", model_id=decision.model_id)
        else:
            self.answer = parsed
            self._phase("deliberate", outcome="answer", model_id=decision.model_id)
        return True

    async def _predict(self) -> bool:
        if self.pending is None or self.pending.prediction_id is not None:
            return False
        if "act" not in self.uses or self.uses["act"] >= self.loop._limits["act"]:
            return False  # the act can never run, so there is nothing to predict
        self._write_prediction(self.pending)
        self._phase("predict", tool=self.pending.tool, prediction_id=self.pending.prediction_id)
        return True

    async def _act(self) -> bool:
        action = self.pending
        if action is None:
            return False
        if action.prediction_id is None:  # the program may put predict after act, or leave it out
            self._write_prediction(action)
        try:
            events.emit("loop_step", self._payload("act", tool=action.tool, prediction_id=action.prediction_id),
                        actor=ACTOR, session_id=self.session_id)
        except Exception as e:  # noqa: BLE001 - fail closed: an act that is not in the log does not happen
            logger.warning("loop could not record its act: %s", type(e).__name__)
            raise _Stop(STOP_UNRECORDED, f"act event not written ({type(e).__name__})") from None
        self.pending = None
        try:
            result = await self.loop.tools.execute(action.tool, action.input)
            success = bool(result.success)
            text = (result.output if success else f"Error: {result.error}") or ""
        except Exception as e:  # noqa: BLE001 - a raising tool is a failed act, not a crashed loop
            success, text = False, f"Error: {type(e).__name__}"
        self.tools_used.append(action.tool)
        try:
            predictions.resolve(action.prediction_id, {"success": success})
        except Exception as e:  # noqa: BLE001 - the act happened; a lost score must not undo it
            logger.warning("loop could not resolve its prediction: %s", type(e).__name__)
        self.last = _Observation(action.tool, success, str(text)[:OBSERVATION_CHARS])
        self.observations.append(self.last)
        return True

    async def _reflect(self) -> bool:
        obs = self.last
        if obs is None and self.answer is None:
            return False
        if obs is not None and not obs.success:
            self.notes.append(f"Calling {obs.tool} failed. Try something else, or answer with what you have.")
        self._phase("reflect", tool=obs.tool if obs else None, success=obs.success if obs else None,
                    answered=self.answer is not None)
        return True

    # -------------------------------------------------------------------------------------------- helpers

    async def _route(self, step: Step, context: RouteContext) -> RouteDecision:
        aroute = getattr(self.loop.router, "aroute", None)
        if inspect.iscoroutinefunction(aroute):
            return await aroute(step, context)
        return await asyncio.to_thread(self.loop.router.route, step, context)

    def _write_prediction(self, action: _Action) -> None:
        """Fail closed: without a prediction there is no act."""
        try:
            confidence, _ = predictions.tool_forecast(action.tool)
            action.prediction_id = predictions.predict(f"act:{action.tool}", {"success": True}, confidence)
        except Exception as e:  # noqa: BLE001
            logger.warning("loop could not write its prediction: %s", type(e).__name__)
            raise _Stop(STOP_UNRECORDED, f"prediction not written ({type(e).__name__})") from None

    def _prompt(self) -> str:
        lp = self.loop
        parts = [f"TASK:\n{self.task}"]
        if lp.context.strip():
            parts.append(f"CONTEXT:\n{lp.context.strip()}")
        if self.memories:
            parts.append("WHAT YOU REMEMBER:\n" + "\n".join(f"- {m[:MEMORY_CHARS]}" for m in self.memories))
        if self.observations:
            older = len(self.observations) - ATTEND_WINDOW
            lines = [f"({older} earlier tool calls not shown)"] if older > 0 else []
            for obs in self.observations[-ATTEND_WINDOW:]:
                lines.append(f"- {obs.tool} {'ok' if obs.success else 'failed'}: {obs.text}")
            parts.append("WHAT YOU HAVE DONE SO FAR:\n" + "\n".join(lines))
        if self.notes:
            parts.append("NOTES:\n" + "\n".join(f"- {n}" for n in self.notes[-3:]))
        if "act" in lp._order:
            parts.append("TOOLS:\n" + _describe_tools(lp.tools))
            parts.append('To use a tool, reply with only one JSON object: {"action": {"tool": "<name>", '
                         '"input": {...}}}. Otherwise reply with your final answer as plain text.')
        else:
            parts.append("You cannot use tools. Reply with your final answer as plain text.")
        return "\n\n".join(parts)

    def _payload(self, phase: str, **fields: Any) -> dict:
        return {"step": phase, "pass": self.steps, "task_id": self.task_id, **fields}

    def _phase(self, phase: str, **fields: Any) -> None:
        self._event("loop_step", self._payload(phase, **fields))

    def _event(self, kind: str, payload: dict) -> None:
        if kind != "loop_step":
            payload = {"task_id": self.task_id, **payload}
        try:
            events.emit(kind, payload, actor=ACTOR, session_id=self.session_id)
        except Exception as e:  # noqa: BLE001 - phase events fail open (decision 0008 item 6)
            logger.warning("could not record %s event: %s", kind, type(e).__name__)


def _describe_tools(tools: Any) -> str:
    try:
        specs = tools.get_claude_tools() if callable(getattr(tools, "get_claude_tools", None)) else []
    except Exception:  # noqa: BLE001
        specs = []
    lines = []
    for spec in specs or []:
        if isinstance(spec, dict) and isinstance(spec.get("name"), str):
            schema = json.dumps(spec.get("input_schema", {}), ensure_ascii=False)
            lines.append(f"- {spec['name']}: {spec.get('description', '')} input schema: {schema}")
    return "\n".join(lines) or "(none listed)"


def _apology(reason: str, detail: str) -> str:
    if reason == STOP_BUDGET:
        return "I ran out of steps before I could finish this."
    if reason == STOP_REFUSED:
        return f"I can't work on this right now: {detail}"
    return f"I stopped before finishing ({reason})."
