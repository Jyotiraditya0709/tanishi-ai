"""The Experience record (L1): one attempt at one task, as the Learning Compiler reads it.

An Experience holds the task, its context, every action in order, the outcome the agent claimed, the verifier's
result, the cost, and the prediction written through the CS5 ledger before acting. It is plain JSON, so it round
trips through `to_dict()` / `from_dict()` and is stored as one CS2 event of kind `experience` (redacted by `emit()`).
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, fields

from tanishi.core_state import events

EVENT_KIND = "experience"
ACTOR = "compiler.experience"


@dataclass(frozen=True)
class Experience:
    """One attempt. Every field is required; `__post_init__` refuses a malformed record.

    actions:         list of {"tool": str, "input", "output", "ok": bool}, in the order they ran.
    verifier_result: {"passed": bool, "score", "reason"}. Success is the verifier's `passed`, not the agent's claim.
    prediction:      {"id": str, "expected": {"success": bool, ...}, "confidence": float in [0, 1]}, from CS5.
    """

    task: str
    context: dict
    actions: list
    outcome: dict
    verifier_result: dict
    cost: dict
    prediction: dict

    def __post_init__(self) -> None:
        if not isinstance(self.task, str):
            raise TypeError("task must be a str")
        if not self.task.strip():
            raise ValueError("task must not be blank")
        for name in ("context", "outcome", "verifier_result", "cost", "prediction"):
            if not isinstance(getattr(self, name), dict):
                raise TypeError(f"{name} must be a dict, got {type(getattr(self, name)).__name__}")
        if not isinstance(self.actions, list):
            raise TypeError("actions must be a list")
        for i, action in enumerate(self.actions):
            if not isinstance(action, dict):
                raise TypeError(f"action {i} must be a dict")
            if not isinstance(action.get("tool"), str) or not action["tool"].strip():
                raise ValueError(f"action {i} needs a non-blank str 'tool'")
            if not isinstance(action.get("ok"), bool):
                raise TypeError(f"action {i} needs a bool 'ok'")
        if not isinstance(self.verifier_result.get("passed"), bool):
            raise TypeError("verifier_result needs a bool 'passed'")
        _check_prediction(self.prediction)
        try:
            json.dumps(self.to_dict(), allow_nan=False)
        except (TypeError, ValueError) as e:
            raise ValueError(f"an Experience must be plain JSON: {e}") from None

    @property
    def passed(self) -> bool:
        """What the verifier said. The agent does not grade itself."""
        return self.verifier_result["passed"]

    def to_dict(self) -> dict:
        return {f.name: copy.deepcopy(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> Experience:
        """Build from a dict holding every field. A missing field is a KeyError; unknown keys are ignored."""
        if not isinstance(d, dict):
            raise TypeError("from_dict needs a dict")
        return cls(**{f.name: copy.deepcopy(d[f.name]) for f in fields(cls)})


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


def record(exp: Experience) -> int:
    """Write one `experience` event (redacted by emit()) and return its id."""
    if not isinstance(exp, Experience):
        raise TypeError("record() needs an Experience")
    return events.emit(EVENT_KIND, exp.to_dict(), actor=ACTOR)


def find_recorded(exp: Experience) -> int | None:
    """The id of an `experience` event already written for this attempt, matched on its prediction id, or None.

    The prediction id is unique per attempt (CS5 makes a fresh one per predict()), so it identifies the attempt.
    This scans every `experience` event: fine for now, see known-bugs/l1-compiler-limits.md.
    """
    pid = exp.prediction["id"]
    for ev in events.iter_events(kind=EVENT_KIND):
        p = ev.payload
        if isinstance(p, dict) and isinstance(p.get("prediction"), dict) and p["prediction"].get("id") == pid:
            return ev.id
    return None
