"""Code verifiers: plain Python functions that score an attempt.

A verifier is named in a task file by dotted path (`package.module.function`) and is called as
`verifier(task, output)`, where `output` is the candidate's text (None when the run has no executor).
It returns `(score, reason)` with score a float in [0, 1]. It runs inside the attempt's temp
TANISHI_HOME, so it may also inspect files the attempt wrote there.

Anything else a verifier does (raise, return NaN, a score outside [0, 1], the wrong shape) scores 0:
a broken check must never hand out credit.

A reason says what was wrong, never what was right. Reasons are stored and read back by failure analysis,
so an expected answer in a reason would leak to the next candidate.
"""
from __future__ import annotations

import importlib
import math
import re
from collections.abc import Callable
from typing import NamedTuple


class Verdict(NamedTuple):
    score: float
    reason: str


Verifier = Callable[..., tuple[float, str]]


def resolve(path: str) -> Verifier:
    """Import `package.module.function`. Raises ImportError or AttributeError if it is not there."""
    module_name, _, func_name = path.rpartition(".")
    if not module_name or not func_name:
        raise ImportError(f"verifier path must be module.function, got {path!r}")
    fn = getattr(importlib.import_module(module_name), func_name)
    if not callable(fn):
        raise TypeError(f"verifier {path} is not callable")
    return fn


def problem_with(result: object) -> str | None:
    """Why a verifier's return value earns no credit, or None if it is a well-formed (score, reason)."""
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        return f"verifier returned {type(result).__name__}, not (score, reason)"
    score, reason = result
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return f"verifier score is {type(score).__name__}, not a number; {reason}"
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return f"verifier score {float(score)!r} is outside [0, 1]; {reason}"
    return None


def normalize(result: object) -> Verdict:
    """Turn whatever a verifier returned into a Verdict with score in [0, 1]; malformed means 0."""
    problem = problem_with(result)
    if problem is not None:
        return Verdict(0.0, problem)
    score, reason = result
    return Verdict(float(score), str(reason))


# Building blocks for task verifiers. Each returns a Verdict, so a task verifier can be one line:
#     def check(task, output): return exact(output, "4")


def _text(output: object) -> str | None:
    return output.strip() if isinstance(output, str) and output.strip() else None


def exact(output: object, expected: str, *, case: bool = False) -> Verdict:
    """1 if the trimmed output equals `expected` (case-insensitive unless case=True)."""
    got = _text(output)
    if got is None:
        return Verdict(0.0, "empty output")
    same = got == expected.strip() if case else got.casefold() == expected.strip().casefold()
    return Verdict(1.0, "exact match") if same else Verdict(0.0, "does not match")


def contains(output: object, *needles: str) -> Verdict:
    """Fraction of `needles` found in the output, case-insensitive."""
    got = _text(output)
    if got is None:
        return Verdict(0.0, "empty output")
    if not needles:
        raise ValueError("contains() needs at least one needle")
    found = [n for n in needles if n.casefold() in got.casefold()]
    return Verdict(len(found) / len(needles), f"found {len(found)} of {len(needles)}")


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def number(output: object, expected: float, *, tol: float = 1e-9) -> Verdict:
    """1 if the last number in the output is within `tol` of `expected`."""
    got = _text(output)
    if got is None:
        return Verdict(0.0, "empty output")
    found = _NUMBER.findall(got.replace(",", ""))
    if not found:
        return Verdict(0.0, "no number in output")
    value = float(found[-1])
    if math.isclose(value, expected, rel_tol=0.0, abs_tol=tol):
        return Verdict(1.0, "number matches")
    return Verdict(0.0, "wrong number")
