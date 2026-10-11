"""The Experience record (L1): one attempt at one task, as the Learning Compiler reads it.

An Experience holds the task, its context, every action in order, the outcome the agent claimed, the verifier's
result, the cost, and the prediction written through the CS5 ledger before acting. It is plain JSON, so it round
trips through `to_dict()` / `from_dict()` and is stored as one CS2 event of kind `experience` (redacted by `emit()`).

An Experience is deeply immutable: the constructor keeps its own copies, and every dict or list field is handed out
as a fresh copy, so neither the caller's dicts nor a mutated read can change what the compiler sees (red team R7).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from tanishi.core_state import events
from tanishi.core_state.db import migrate, open_db, resolve_path

try:
    import fcntl
except ImportError:  # Windows: the thread lock alone, so one process at a time compiles safely
    fcntl = None  # type: ignore[assignment]

EVENT_KIND = "experience"
ACTOR = "compiler.experience"
MAX_DEPTH = events.MAX_DEPTH  # deeper nesting would be truncated in the log, so the record would not round trip
SEALED_TIERS = frozenset({"frontier", "sealed", "sealed_vault"})  # only pass or fail may reach an agent-readable log

_CONTAINERS = frozenset({"context", "actions", "outcome", "verifier_result", "cost", "prediction"})


@dataclass(frozen=True)
class Experience:
    """One attempt. Every field is required; `__post_init__` refuses a malformed record.

    actions:         list of {"tool": str, "input", "output", "ok": bool}, in the order they ran.
    verifier_result: {"passed": bool, "score", "reason"}. Success is the verifier's `passed`, not the agent's claim.
    prediction:      {"id": str, "expected": {"success": bool, ...}, "confidence": float in [0, 1]}, from CS5.
    context:         free JSON; `context["tier"]` names the Arena tier, and a Frontier or Sealed Vault tier keeps
                     only the verdict of `verifier_result` (see `sealed()`).
    """

    task: str
    context: dict
    actions: list
    outcome: dict
    verifier_result: dict
    cost: dict
    prediction: dict

    def __getattribute__(self, name: str) -> Any:
        value = object.__getattribute__(self, name)
        return copy.deepcopy(value) if name in _CONTAINERS else value

    def __post_init__(self) -> None:
        if not isinstance(self.task, str):
            raise TypeError("task must be a str")
        if not self.task.strip():
            raise ValueError("task must not be blank")
        for name in _CONTAINERS:
            value = object.__getattribute__(self, name)
            if not isinstance(value, list if name == "actions" else dict):
                kind = "a list" if name == "actions" else "a dict"
                raise TypeError(f"{name} must be {kind}, got {type(value).__name__}")
            if _depth(value) > MAX_DEPTH - 1:  # the field sits one level inside the logged record
                raise ValueError(f"{name} is nested deeper than {MAX_DEPTH - 1} levels")
            object.__setattr__(self, name, copy.deepcopy(value))  # the caller's dicts can no longer reach us
        for i, action in enumerate(object.__getattribute__(self, "actions")):
            if not isinstance(action, dict):
                raise TypeError(f"action {i} must be a dict")
            if not isinstance(action.get("tool"), str) or not action["tool"].strip():
                raise ValueError(f"action {i} needs a non-blank str 'tool'")
            if not isinstance(action.get("ok"), bool):
                raise TypeError(f"action {i} needs a bool 'ok'")
        if not isinstance(object.__getattribute__(self, "verifier_result").get("passed"), bool):
            raise TypeError("verifier_result needs a bool 'passed'")
        _check_prediction(object.__getattribute__(self, "prediction"))
        try:
            json.dumps(self.to_dict(), allow_nan=False)
        except (TypeError, ValueError) as e:
            raise ValueError(f"an Experience must be plain JSON: {e}") from None

    @property
    def passed(self) -> bool:
        """What the verifier said. The agent does not grade itself."""
        return object.__getattribute__(self, "verifier_result")["passed"]

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}  # containers come back as copies

    @classmethod
    def from_dict(cls, d: dict) -> Experience:
        """Build from a dict holding every field. A missing field is a KeyError; unknown keys are ignored."""
        if not isinstance(d, dict):
            raise TypeError("from_dict needs a dict")
        return cls(**{f.name: d[f.name] for f in fields(cls)})  # the constructor copies


def _depth(value: Any) -> int:
    """How deeply `value` nests dicts and lists, without recursion (a 5000-deep input is a ValueError, red team R9).
    A cycle never ends, so it counts as too deep."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, (dict, list, tuple)):
            if d > MAX_DEPTH:
                return d
            deepest = max(deepest, d)
            stack.extend((x, d + 1) for x in (v.values() if isinstance(v, dict) else v))
    return deepest


def _check_prediction(p: dict) -> None:
    pid = p.get("id")
    if not isinstance(pid, str) or not pid.strip():
        raise ValueError("prediction needs the str 'id' the ledger gave it")
    expected = p.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected.get("success"), bool):
        raise TypeError("prediction needs 'expected' with a bool 'success'")
    conf = p.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise TypeError("prediction 'confidence' must be a number")
    if not (math.isfinite(conf) and 0.0 <= conf <= 1.0):
        raise ValueError("prediction 'confidence' must be in [0, 1]")


# ---------------------------------------------------------------- canonical form and digests


def canonical(value: Any, depth: int = 0) -> Any:
    """A JSON-ready copy whose dict keys are strings, so mixed-type keys (`{1: "a", "b": 2}`) sort instead of raising
    (red team R9). Keys are taken in (text, type) order, so the result is the same whatever order they came in."""
    if depth > MAX_DEPTH:
        return "[too deep]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: (str(kv[0]), type(kv[0]).__name__))
        return {str(k): canonical(v, depth + 1) for k, v in items}
    if isinstance(value, (list, tuple)):
        return [canonical(v, depth + 1) for v in value]
    return value


def digest(value: Any) -> str:
    """sha256 of the canonical JSON of `value`. Never raises on content."""
    text = json.dumps(canonical(value), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def attempt_key(exp: Experience) -> str:
    """The attempt's identity: its prediction id, the writer of the record, and a hash of its actions. A retry that
    reuses one prediction but acts differently is a different attempt (red team R2)."""
    return digest([exp.prediction["id"], ACTOR, digest(exp.actions)])


def content_hash(exp: Experience) -> str:
    return digest(exp.to_dict())


def is_sealed(exp: Experience) -> bool:
    tier = exp.context.get("tier")
    return isinstance(tier, str) and "_".join(tier.casefold().replace("-", " ").split()) in SEALED_TIERS


def sealed(exp: Experience) -> Experience:
    """For a Frontier or Sealed Vault task, the same Experience with only the verdict kept from the verifier: its
    reason and score may carry the hidden task's answer, and the log is agent-readable. Other tiers come back as is."""
    if not is_sealed(exp):
        return exp
    d = exp.to_dict()
    d["verifier_result"] = {"passed": exp.passed}
    return Experience.from_dict(d)


# ---------------------------------------------------------------- the record


def record(exp: Experience) -> int:
    """Write one `experience` event (redacted by emit()) and return its id.

    The payload is the Experience plus `attempt` (its `attempt_key`) and `content_sha256` (its `content_hash`), so
    `find_recorded` can tell this attempt's record from any other event. A sealed tier's verifier reason never
    reaches the log.
    """
    if not isinstance(exp, Experience):
        raise TypeError("record() needs an Experience")
    exp = sealed(exp)
    payload = {**exp.to_dict(), "attempt": attempt_key(exp), "content_sha256": content_hash(exp)}
    return events.emit(EVENT_KIND, payload, actor=ACTOR)


def find_recorded(exp: Experience) -> int | None:
    """The id of the `experience` event already written for this exact attempt, or None.

    Adopted only when the compiler wrote it (kind `experience`, actor `ACTOR`), its attempt key matches, and its
    content hash is this Experience's: an event from another writer, or another attempt that reused the prediction
    id, is never adopted (red team R2). The match runs in SQL, so past records are not decoded one by one (R10).
    """
    exp = sealed(exp)
    key, chash = attempt_key(exp), content_hash(exp)
    conn = open_db()
    try:
        migrate(conn)
        row = conn.execute(
            "SELECT id FROM events WHERE kind = ? AND actor = ? AND json_extract(payload, '$.attempt') = ? "
            "AND json_extract(payload, '$.content_sha256') = ? ORDER BY id LIMIT 1",
            (EVENT_KIND, ACTOR, key, chash),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def record_once(exp: Experience) -> int:
    """The id of this attempt's record, writing it only when none exists. Find and write happen under
    `attempt_lock()`, so two compilers of one attempt, in threads or processes, write one record (red team R8)."""
    with attempt_lock():
        found = find_recorded(exp)
        return record(exp) if found is None else found


_THREAD_LOCK = threading.Lock()


@contextmanager
def attempt_lock() -> Iterator[None]:
    """Serialise compilers that share one Core State db: a thread lock plus an flock on a file next to the db.

    CS2's emit() and CS3's add_belief() each open their own connection and transaction, so "find, then write" cannot
    be one SQL transaction from here; this lock makes it one critical section instead (L1-repair-conflicts.md)."""
    with _THREAD_LOCK:
        target = resolve_path()
        if target == ":memory:" or fcntl is None:
            yield
            return
        path = Path(target).with_name(Path(target).name + ".compiler.lock")
        if not path.parent.exists():
            open_db().close()  # creates the db's folder with the Core State's own permissions
        fd =os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing the descriptor drops the flock
