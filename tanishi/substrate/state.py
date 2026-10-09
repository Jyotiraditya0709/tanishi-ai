"""Working, planning and goal state of one task, saved in the Core State (node SUB1).

One row of `substrate_state` (migration 0001) per task. `save()` replaces the whole row in one transaction,
so a process killed at any instant leaves the last saved state whole, never half of it. A caller saves after
each step completes; after a crash, `load()` returns that state and `Plan.next_step()` is where to resume.

Only plain JSON goes in: dicts with str keys, lists, str, int, finite float, bool and None. Anything else
(a tuple, an int key, NaN) is refused on save, because it would not come back unchanged: a wrong type raises
TypeError, a bad value (NaN, a blank task id, a cycle) raises ValueError.

Secrets never reach the db: text that `events.redact()` would change (an API key, `PASSWORD=...`) is stored
redacted, so that one string does not come back unchanged. Everything else round-trips exactly.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tanishi.core_state.db import migrate, open_db
from tanishi.core_state.events import redact

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

# Beyond these words a status is opaque text, but it must be lowercase with no surrounding blanks, so "DONE"
# or " done" cannot pass for done in the caller's eyes while next_step() re-runs the step.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

_STEP_FIELDS = ("id", "description", "status", "model", "result_ref")


@dataclass
class Step:
    id: str
    description: str
    status: str = PENDING
    model: str | None = None
    result_ref: str | None = None  # where the step's output lives (an event id, a file), not the output


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)

    def next_step(self) -> Step | None:
        """The first step that is not done: where a resumed task carries on. None when every step is done."""
        return next((s for s in self.steps if s.status != DONE), None)


@dataclass
class TaskState:
    task_id: str
    working: dict[str, Any]
    plan: Plan
    goal_ids: list[str]
    hypotheses: list[dict[str, Any]]


def _connect() -> sqlite3.Connection:
    conn = open_db()
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _check_json(value: object, where: str) -> None:
    """Raise TypeError or ValueError unless `value` survives json.dumps / json.loads unchanged, type for type.

    Scalars must be exactly str, bool, int or float: a subclass such as an IntEnum would come back as its base.
    """
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{where}: {value!r} is not valid JSON")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json(item, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for k, item in value.items():
            if type(k) is not str:
                raise TypeError(f"{where}: key {k!r} is not a str")
            _check_json(item, f"{where}[{k!r}]")
        return
    raise TypeError(f"{where}: {type(value).__name__} is not plain JSON")


def _check_text(value: object, where: str, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not str:
        raise TypeError(f"{where} must be a str{' or None' if optional else ''}, got {type(value).__name__}")


def _without_secrets(value: Any) -> Any:
    """`value` with every secret-shaped string redacted, as events.redact() would. A string with no secret is
    kept exactly, lone surrogates and all, so state without secrets still round-trips unchanged."""
    if isinstance(value, str):
        clean = _LONE_SURROGATE.sub("�", value)  # what redact() does to them anyway
        return redact(clean) if redact(clean) != clean else value
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    if isinstance(value, dict):
        out = {}
        for k, item in value.items():
            key = _without_secrets(k)
            while key in out:  # two redacted keys must not overwrite each other
                key = f"{key}#"
            # A key such as "password" or "api_key": redact() hides any value under it; here, only text values.
            named_secret = isinstance(item, str) and item and [*redact({k: 1}).values()] == ["[REDACTED]"]
            out[key] = "[REDACTED]" if named_secret else _without_secrets(item)
        return out
    return value


def _check_type(value: object, kind: type, where: str) -> None:
    if not isinstance(value, kind):
        raise TypeError(f"{where} must be a {kind.__name__}, got {type(value).__name__}")


def _validate(state: TaskState) -> None:
    try:
        _validate_shape(state)
    except RecursionError:  # a structure that contains itself, or one nested too deep to store
        raise ValueError("state is cyclic or nested too deeply to save") from None


def _validate_shape(state: TaskState) -> None:
    _check_type(state, TaskState, "state")
    _check_text(state.task_id, "task_id")
    if not state.task_id.strip():
        raise ValueError("task_id must not be blank")
    _check_type(state.working, dict, "working")
    _check_json(state.working, "working")
    _check_type(state.plan, Plan, "plan")
    _check_type(state.plan.steps, list, "plan.steps")
    for i, step in enumerate(state.plan.steps):
        _check_type(step, Step, f"plan.steps[{i}]")
        for name in _STEP_FIELDS:
            _check_text(getattr(step, name), f"plan.steps[{i}].{name}", optional=name in ("model", "result_ref"))
        # Step ids are not checked for emptiness or uniqueness: the exam round-trips both
        # (open-problems/SUB1-repair-conflicts.md). Steps are identified by position.
        if not step.status or step.status != step.status.strip().lower():
            raise ValueError(f"plan.steps[{i}].status {step.status!r} must be non-empty lowercase with no blanks")
    _check_type(state.goal_ids, list, "goal_ids")
    for i, goal_id in enumerate(state.goal_ids):
        _check_text(goal_id, f"goal_ids[{i}]")
    _check_type(state.hypotheses, list, "hypotheses")
    for i, hypothesis in enumerate(state.hypotheses):
        _check_type(hypothesis, dict, f"hypotheses[{i}]")
    _check_json(state.hypotheses, "hypotheses")


def _dumps(value: object) -> str:
    return json.dumps(value, allow_nan=False)


def save(state: TaskState) -> None:
    """Replace the saved state of `state.task_id` with a snapshot of `state`, atomically.

    Raises TypeError or ValueError, before the db is touched, if any part is not plain JSON of the declared shape.
    """
    _validate(state)
    plan = {"steps": [{name: getattr(s, name) for name in _STEP_FIELDS} for s in state.plan.steps]}
    row = (
        state.task_id,
        _dumps(_without_secrets(state.working)),
        _dumps(_without_secrets(plan)),
        _dumps(_without_secrets(state.goal_ids)),
        _dumps(_without_secrets(state.hypotheses)),
        datetime.now(UTC).isoformat(),
    )
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO substrate_state (task_id, working, plan, goal, hypotheses, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )


def load(task_id: str) -> TaskState:
    """The last saved state of `task_id`, as fresh objects.

    Raises LookupError if it was never saved, ValueError if the stored row is not a state save() could write.
    """
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT working, plan, goal, hypotheses FROM substrate_state WHERE task_id = ?", (task_id,)
        ).fetchone()
    if row is None:
        raise LookupError(f"no saved state for task {task_id!r}")
    if any(col is None for col in row):
        raise ValueError(f"saved state for task {task_id!r} is incomplete")
    try:  # a row edited by raw SQL can be valid JSON of the wrong shape
        working, plan, goal_ids, hypotheses = (json.loads(col) for col in row)
        state = TaskState(
            task_id=task_id,
            working=working,
            plan=Plan(steps=[Step(**{name: s[name] for name in _STEP_FIELDS}) for s in plan["steps"]]),
            goal_ids=goal_ids,
            hypotheses=hypotheses,
        )
        _validate(state)
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise ValueError(f"saved state for task {task_id!r} is malformed: {type(e).__name__}") from None
    return state
