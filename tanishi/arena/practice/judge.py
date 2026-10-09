"""The LLM judge for style tasks (greeting, personality, poem), kept from the legacy benchmark.

Style cannot be checked by code, so these tasks keep a judge. They are tagged `judge:llm` and left out of CEI
(`tanishi.arena.practice.cei_eligible`). Code still decides what it can: a cheap floor check runs first, and an answer
that fails it scores 0 without any judge call.

The judge is a function named in $TANISHI_PRACTICE_JUDGE as `package.module:function`. It receives
`(prompt, criteria, output)` and returns a float in [0, 1]. Attempts do not inherit the caller's environment, so a
caller that wants judging names that variable in `run(..., env_passthrough=...)`. With no judge configured, or one that
fails, the score is 0: a missing judge never hands out credit.
"""
from __future__ import annotations

import importlib
import math
import os

from tanishi.arena.practice._common import text_of
from tanishi.arena.verifiers import Verdict

JUDGE_ENV = "TANISHI_PRACTICE_JUDGE"
MAX_CHARS = 1200  # a longer reply is not "concise" and is not worth a judge call


def judged(task, output: object, criteria: list[str], floor) -> Verdict:
    """Floor check in code, then the judge. `floor(text)` returns None when fine, else the reason it fails."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    if len(got) > MAX_CHARS:
        return Verdict(0.0, "reply too long")
    problem = floor(got)
    if problem:
        return Verdict(0.0, problem)
    spec = os.environ.get(JUDGE_ENV, "").strip()
    if not spec or ":" not in spec:
        return Verdict(0.0, "no judge configured")
    try:
        module, _, name = spec.partition(":")
        value = getattr(importlib.import_module(module), name)(task.prompt, list(criteria), got)
    except Exception:  # noqa: BLE001 - a judge that fails gives no credit
        return Verdict(0.0, "judge failed")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return Verdict(0.0, "judge returned a bad score")
    return Verdict(min(1.0, max(0.0, float(value))), "judged")
