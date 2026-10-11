"""The reasoning loop: attend, recall, deliberate, predict, act, reflect, under a step budget (node SUB2).

`CognitiveLoop(router, memory, tools, budget, generate=...).run(task)` runs passes over the program's phases until the
model gives a final answer, the step budget runs out, or no phase can make progress. `TanishiBrain.think()` delegates
here only when `TANISHI_SUBSTRATE=1` (`enabled()`); otherwise the legacy path runs untouched. Decision 0024.

- **Program.** Which phases run, in what order, how many times each may run in one task, and the task's wall-time
  budget are data: `loop_program.json` next to this file, `{"version", "wall_time_s"?, "steps": [{"name", "budget"}]}`.
  It is read when a loop is built, never at import, so evolution can change it. A phase missing from the program
  never runs.
- **Step.** One pass through the program. `StepBudget.max_steps` bounds the passes, and a pass acts at most once,
  so it bounds tool calls too. When the last allowed pass acted, the model gets one more deliberate with no tools to
  answer from what it has (it does not count as a step); otherwise the loop stops with `STOP_BUDGET`.
- **Model.** The router picks the model (`route(step, context)`, or `aroute` when it has one); `generate(decision,
  prompt)` calls it. A reply that is exactly one JSON object `{"action": {"tool": <a listed tool>, "input": {...}}}`
  asks to act; any other non-blank reply, JSON or not, is the final answer. A blank reply stops the loop.
- **Act.** A prediction (CS5, `about = "act:<tool>"`, tagged with the `planning` capability) and a `loop_step` event
  naming the tool (CS2) are written before `tools.execute()`; the prediction is resolved with the tool's real
  `success` after. If either write fails, the tool does not run and the loop stops with `STOP_UNRECORDED` (fail
  closed: an act that is not recorded does not happen). Every other phase event fails open, like `think()`.
- **Personal is sticky.** Once recalled memory or a tool result marked personal enters the context, every later
  deliberate of the task is routed as personal.
- **Memory** is optional and fails open: a memory that raises is skipped.
- **Never raises** for anything the model, router, tools or memory do. Every run writes `loop_stop` with a reason;
  a cancellation writes it too (reason `cancelled`), scores the act it interrupted as failed, and is re-raised.

Events carry no task text, prompt, memory or tool input: the task can be personal, and the registry already logs
tool calls (redacted). `loop_start`, one `loop_step {"step": <phase>, ...}` per phase run, and `loop_stop`.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import math
import os
import re
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
PLANNING = "planning"  # the capability an act: prediction is tagged with; tool: predictions keep their own

STOP_ANSWER = "answered"
STOP_BUDGET = "budget"
STOP_PHASE_BUDGET = "phase_budget"  # no progress because a phase used up its program budget
STOP_STALLED = "stalled"  # no progress for another reason (the model asked to act, the program has no act)
STOP_REFUSED = "route_refused"
STOP_ROUTE_ERROR = "route_error"
STOP_MODEL_ERROR = "model_error"
STOP_EMPTY = "empty_reply"
STOP_UNRECORDED = "unrecorded"  # the prediction or the act event could not be written, so the act did not run
STOP_TIMEOUT = "timeout"  # the program's wall-time budget ran out
STOP_CANCELLED = "cancelled"  # the caller cancelled the task; run() re-raises
STOP_INTERNAL = "internal_error"  # a bug in the loop itself; reported, never raised

DEFAULT_WALL_TIME_S = 300.0
RECALL_LIMIT = 5
RECALL_TERMS = 6  # key terms of the task searched one by one, besides the whole task
ATTEND_WINDOW = 6  # observations shown in full; older ones are counted, not shown
OBSERVATION_CHARS = 2000
MEMORY_CHARS = 500
MAX_ACTION_CHARS = 100_000  # a longer reply is never parsed as an action (it can still be the answer)
MAX_ACTION_DEPTH = 32  # nor is one nested deeper than this

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
    if "wall_time_s" in program:
        wall = program["wall_time_s"]
        if isinstance(wall, bool) or not isinstance(wall, int | float) or not math.isfinite(wall) or wall <= 0:
            raise ValueError("the loop program's wall_time_s must be a finite number of seconds > 0")
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
    resolved: bool = False


@dataclass
class _Observation:
    tool: str
    success: bool
    text: str


class _Stop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


_FENCE = re.compile(r"\A```(?:json)?[ \t]*\n?(.*?)\n?```\Z", re.IGNORECASE | re.DOTALL)


def _depth(text: str) -> int:
    """How deeply `[` and `{` nest in `text`, outside JSON strings. Linear, so it is safe on any input."""
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


def parse_reply(text: str, tools: set[str] | None = None) -> _Action | str:
    """An `_Action` when `text` is exactly an action request, else `text` itself, the final answer.

    An action is one JSON object (bare, or in a ``` or ```json fence, any case) with only the key `action`, whose value
    has only `tool` (a non-blank str naming one of `tools`; any name when `tools` is None) and `input` (an object).
    Any other reply is an answer, JSON with an `action` key included. A reply too long or nested too deeply to be an
    action, or one that does not parse, is not one: parsing never raises.
    """
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    if not body.startswith("{") or len(body) > MAX_ACTION_CHARS or _depth(body) > MAX_ACTION_DEPTH:
        return text
    try:
        obj = json.loads(body)
    except (ValueError, RecursionError):
        return text
    if not isinstance(obj, dict) or set(obj) != {"action"}:
        return text
    action = obj["action"]
    if not isinstance(action, dict) or set(action) != {"tool", "input"}:
        return text
    tool, tool_input = action["tool"], action["input"]
    if not isinstance(tool, str) or not tool.strip() or not isinstance(tool_input, dict):
        return text
    if tools is not None and tool not in tools:
        return text
    return _Action(tool, tool_input)


def key_terms(task: str) -> list[str]:
    """The words of `task` worth searching memory for: 3+ letters, not a stop word, longest first, at most
    RECALL_TERMS. Lower case, deduplicated."""
    seen: list[str] = []
    for word in _WORD.findall(task.lower()):
        if word not in _STOP_WORDS and word not in seen:
            seen.append(word)
    return sorted(seen, key=len, reverse=True)[:RECALL_TERMS]


_WORD = re.compile(r"[^\W_]{3,}")
_STOP_WORDS = frozenset({
    "about", "after", "again", "all", "also", "and", "any", "are", "been", "before", "being", "but", "can", "could",
    "did", "does", "doing", "done", "each", "for", "from", "get", "got", "had", "has", "have", "her", "here", "hers",
    "him", "his", "how", "into", "its", "just", "know", "let", "like", "make", "many", "may", "more", "most", "much",
    "must", "need", "not", "now", "off", "one", "only", "onto", "other", "our", "ours", "out", "over", "own", "please",
    "put", "really", "said", "same", "say", "see", "she", "should", "some", "such", "tell", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "too", "use", "very", "want", "was", "way",
    "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "yes", "yet",
    "you", "your", "yours",
})


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
        self.wall_time_s = float(self.program.get("wall_time_s", DEFAULT_WALL_TIME_S))
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
        self.personal = loop.personal  # sticky: once true for this task, it stays true
        self.known_tools = _tool_names(loop.tools)
        self.memories: list[str] = []
        self.observations: list[_Observation] = []
        self.notes: list[str] = []
        self.pending: _Action | None = None
        self.inflight: _Action | None = None  # the act whose tool is running now
        self.answer: str | None = None
        self.last: _Observation | None = None  # what the act phase of this pass produced, for reflect
        self.model_used = ""
        self.tools_used: list[str] = []
        self.blocked: set[str] = set()

    # -------------------------------------------------------------------------------------------- driver

    async def go(self) -> Result:
        lp = self.loop
        self._event("loop_start", {"program_version": lp.program["version"], "phases": list(lp._order),
                                   "max_steps": lp.budget.max_steps, "wall_time_s": lp.wall_time_s})
        reason, detail = STOP_INTERNAL, "the loop did not finish"
        try:
            reason, detail = await self._timed()
        except _Stop as stop:
            reason, detail = stop.reason, stop.detail
        except Exception as e:  # noqa: BLE001 - a bug in the loop ends the task; it never crashes the caller
            logger.warning("loop failed: %s", type(e).__name__)
            reason, detail = STOP_INTERNAL, type(e).__name__
        except BaseException as e:  # cancelled (or interrupted): record it, then let it through
            reason, detail = STOP_CANCELLED, type(e).__name__
            raise
        finally:
            if reason in (STOP_CANCELLED, STOP_TIMEOUT, STOP_INTERNAL):
                self._fail_open_acts()
            success = reason == STOP_ANSWER
            self._event("loop_stop", {"stop_reason": reason, "success": success, "steps": self.steps,
                                      "tools_used": list(self.tools_used), "detail": detail})
        content = self.answer if success else _apology(reason, detail)
        return Result(content=content or "", success=success, stop_reason=reason, steps=self.steps,
                      model_used=self.model_used, tools_used=list(self.tools_used), detail=detail)

    async def _timed(self) -> tuple[str, str]:
        try:
            async with asyncio.timeout(self.loop.wall_time_s) as deadline:
                return await self._passes()
        except TimeoutError:
            if not deadline.expired():
                raise
            return STOP_TIMEOUT, f"over the {self.loop.wall_time_s:g} s wall-time budget"

    async def _passes(self) -> tuple[str, str]:
        while True:
            if self.steps >= self.loop.budget.max_steps:
                if self.last is not None and self._may("deliberate"):  # the last pass acted: answer from it
                    self.uses["deliberate"] += 1
                    await self._deliberate(final=True)
                    if self.answer is not None:
                        return STOP_ANSWER, ""
                return STOP_BUDGET, f"used all {self.steps} steps"
            self.steps += 1
            self.last, self.blocked = None, set()
            progressed = False
            for phase in self.loop._order:
                if not self._may(phase):
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

    def _may(self, phase: str) -> bool:
        return phase in self.uses and self.uses[phase] < self.loop._limits[phase]

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
        queries = [self.task] + [t for t in key_terms(self.task) if t != self.task.strip().lower()]
        found: list[Any] = []
        ok = True
        try:
            found = await asyncio.to_thread(_search_all, memory, queries)
        except Exception as e:  # noqa: BLE001 - memory fails open; never log what it held
            ok = False
            logger.warning("loop recall failed: %s", type(e).__name__)
        added = 0
        for text in _rank(found):
            if added >= RECALL_LIMIT:
                break
            if text not in self.memories:
                self.memories.append(text)
                added += 1
        if added:
            self.personal = True  # what memory holds is the user's own
        self._phase("recall", ok=ok, hits=added, queries=len(queries))
        return True

    async def _deliberate(self, final: bool = False) -> bool:
        if self.pending is not None or self.answer is not None:
            return False  # an act is waiting: nothing new to think about until it runs
        prompt = self._prompt(final)
        step = Step(id=f"{self.task_id}:{self.steps}:deliberate", description=prompt)
        needs_tools = "act" in self.loop._order and not final
        context = RouteContext(needs_tools=needs_tools, personal=self.personal, local_up=self.loop.local_up,
                               input_tokens=estimate_tokens(prompt))
        try:
            decision = await self._route(step, context)
        except RouteRefused as e:
            self._phase("deliberate", outcome="refused", wait=e.wait, final=final)
            raise _Stop(STOP_REFUSED, e.reason) from None
        except Exception as e:  # noqa: BLE001 - a broken router ends the task, it does not crash the caller
            self._phase("deliberate", outcome="route_error", error=type(e).__name__, final=final)
            raise _Stop(STOP_ROUTE_ERROR, type(e).__name__) from None
        self.model_used = decision.model_id
        try:
            reply = await self.loop.generate(decision, prompt)
        except Exception as e:  # noqa: BLE001
            self._phase("deliberate", outcome="model_error", model_id=decision.model_id, error=type(e).__name__,
                        final=final)
            raise _Stop(STOP_MODEL_ERROR, type(e).__name__) from None
        if not isinstance(reply, str) or not reply.strip():
            self._phase("deliberate", outcome="empty", model_id=decision.model_id, final=final)
            raise _Stop(STOP_EMPTY, "the model's reply was empty" if isinstance(reply, str) else "reply is not text")
        parsed = parse_reply(reply, self.known_tools)
        if isinstance(parsed, _Action) and final:
            self._phase("deliberate", outcome="act_refused", model_id=decision.model_id, tool=parsed.tool, final=True)
        elif isinstance(parsed, _Action):
            self.pending = parsed
            self._phase("deliberate", outcome="act", model_id=decision.model_id, tool=parsed.tool)
        else:
            self.answer = parsed
            self._phase("deliberate", outcome="answer", model_id=decision.model_id, final=final)
        return True

    async def _predict(self) -> bool:
        if self.pending is None or self.pending.prediction_id is not None:
            return False
        if not self._may("act"):
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
        self.pending, self.inflight = None, action
        try:
            result = await self.loop.tools.execute(action.tool, action.input)
            success = bool(result.success)
            text = (result.output if success else f"Error: {result.error}") or ""
            if getattr(result, "personal", False) is True:
                self.personal = True
        except Exception as e:  # noqa: BLE001 - a raising tool is a failed act, not a crashed loop
            success, text = False, f"Error: {type(e).__name__}"
        self.inflight = None
        self.tools_used.append(action.tool)
        self._resolve(action, success)
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
            action.prediction_id = predictions.predict(f"act:{action.tool}",
                                                       {"success": True, "capability": PLANNING}, confidence)
        except Exception as e:  # noqa: BLE001
            logger.warning("loop could not write its prediction: %s", type(e).__name__)
            raise _Stop(STOP_UNRECORDED, f"prediction not written ({type(e).__name__})") from None

    def _resolve(self, action: _Action, success: bool) -> None:
        if action.prediction_id is None or action.resolved:
            return
        action.resolved = True
        try:
            predictions.resolve(action.prediction_id, {"success": success})
        except Exception as e:  # noqa: BLE001 - the act happened; a lost score must not undo it
            logger.warning("loop could not resolve its prediction: %s", type(e).__name__)

    def _fail_open_acts(self) -> None:
        """The task ended (cancelled, timed out, broken) with an act predicted but not scored: it did not succeed."""
        for action in (self.inflight, self.pending):
            if action is not None:
                self._resolve(action, False)

    def _prompt(self, final: bool = False) -> str:
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
        if final:
            parts.append("You have used all your steps and cannot use tools any more. "
                         "Reply with your final answer as plain text, from what you have.")
        elif "act" in lp._order:
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


def _search_all(memory: Any, queries: list[str]) -> list[list[Any]]:
    """One hit list per query, in query order. Runs in a worker thread."""
    search = memory.recall if callable(getattr(memory, "recall", None)) else memory.search
    return [list(search(q, RECALL_LIMIT) or []) for q in queries]


def _rank(found: list[list[Any]]) -> list[str]:
    """Memory texts from all queries, the ones more queries found first, then in the order they were found."""
    count: dict[str, int] = {}
    for hits in found:
        for text in dict.fromkeys(getattr(h, "content", None) for h in hits[:RECALL_LIMIT]):
            if isinstance(text, str) and text.strip():
                count[text] = count.get(text, 0) + 1
    return sorted(count, key=lambda t: -count[t])


def _tool_specs(tools: Any) -> list[dict]:
    try:
        specs = tools.get_claude_tools() if callable(getattr(tools, "get_claude_tools", None)) else []
    except Exception:  # noqa: BLE001
        specs = []
    return [s for s in specs or [] if isinstance(s, dict) and isinstance(s.get("name"), str)]


def _tool_names(tools: Any) -> set[str] | None:
    """The tools a reply may name, or None when the tool set lists none (then any name is passed to it)."""
    return {s["name"] for s in _tool_specs(tools)} or None


def _describe_tools(tools: Any) -> str:
    lines = []
    for spec in _tool_specs(tools):
        schema = json.dumps(spec.get("input_schema", {}), ensure_ascii=False)
        lines.append(f"- {spec['name']}: {spec.get('description', '')} input schema: {schema}")
    return "\n".join(lines) or "(none listed)"


def _apology(reason: str, detail: str) -> str:
    if reason == STOP_BUDGET:
        return "I ran out of steps before I could finish this."
    if reason == STOP_REFUSED:
        return f"I can't work on this right now: {detail}"
    if reason == STOP_TIMEOUT:
        return "I ran out of time before I could finish this."
    return f"I stopped before finishing ({reason})."
