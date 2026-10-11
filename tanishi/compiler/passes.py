"""The Learning Compiler (L1): passes 1, 3 and 4, a versioned registry, and the landing law.

A pass is `run(exp) -> list[{"kind", "payload", "gain"}] | None` plus `check(exp, item) -> (ok, reason)`.
`compile_experience` records the Experience once, resolves its prediction, runs the latest version of every pass,
and lands an item only when its gain is a finite real, its own check says exactly `True`, and the kind's lander
accepts it (the landing law). Every item, landed or refused, is logged as one `compiler_emit` event with its pass,
version, gain and compute. A pass or check that raises is a refusal, never a crash, and never stops the others.

Passes here are deterministic code: no model call. Gain is the pass's own estimate (v1 uses the surprise of the
experience); whether a candidate really improves anything is measured later, in the Arena, through OBS3.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tanishi.compiler import experience as xp
from tanishi.compiler.experience import Experience
from tanishi.core_state import beliefs, events, predictions

logger = logging.getLogger(__name__)

ACTOR = "compiler.passes"
EMIT_EVENT = "compiler_emit"
PASS_FAILED_EVENT = "compiler_pass_failed"
SKILL_TIMEOUT_S = 20.0
SIGNATURE_CHARS = 120  # how much of a failing output a belief or skill candidate keeps
_SKILL_OK = "L1-SKILL-TESTS-PASSED"

Item = dict[str, Any]
RunFn = Callable[[Experience], "list[Item] | None"]
CheckFn = Callable[[Experience, Item], "tuple[bool, str]"]


# ---------------------------------------------------------------- registry


@dataclass(frozen=True)
class Pass:
    name: str
    version: int
    run: RunFn
    check: CheckFn


class Registry:
    """Passes by name and integer version (>= 1). `get(name)` is the latest version."""

    def __init__(self) -> None:
        self._passes: dict[str, dict[int, Pass]] = {}

    def register(self, name: str, version: int, run: RunFn, check: CheckFn) -> Pass:
        if not isinstance(name, str):
            raise TypeError("pass name must be a str")
        if not name.strip():
            raise ValueError("pass name must not be blank")
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("version must be an int")
        if version < 1:
            raise ValueError("version must be >= 1")
        if not callable(run) or not callable(check):
            raise TypeError("run and check must be callable")
        versions = self._passes.setdefault(name, {})
        if version in versions:
            raise ValueError(f"pass {name!r} version {version} is already registered")
        versions[version] = p = Pass(name, version, run, check)
        return p

    def get(self, name: str, version: int | None = None) -> Pass:
        versions = self._passes.get(name)
        if not versions:
            raise KeyError(name)
        if version is None:
            return versions[max(versions)]
        if version not in versions:
            raise KeyError(f"{name} v{version}")
        return versions[version]

    def names(self) -> list[str]:
        """Pass names in registration order: the order compile_experience runs them."""
        return list(self._passes)

    def versions(self, name: str) -> list[int]:
        return sorted(self._passes.get(name, {}))


@dataclass(frozen=True)
class Emission:
    pass_name: str
    version: int
    kind: str | None
    payload: Any
    gain: float | None  # None when the pass gave no finite real gain (then it did not land)
    compute: float  # seconds: the pass's run time plus this item's check and landing
    landed: bool
    reason: str


# ---------------------------------------------------------------- pass 1: outcome analysis


def outcome_analysis(exp: Experience) -> dict:
    """Success (the verifier's verdict), surprise (CS5 Brier score of the prediction) and the decisive step.

    The decisive step on a failure is the first action that failed, or the last action when every action said ok
    (the answer itself was wrong); None on a success or when there were no actions. Pure.
    """
    success = exp.passed
    p = exp.prediction
    forecast = p["confidence"] if p["expected"]["success"] else 1.0 - p["confidence"]
    surprise = (float(forecast) - (1.0 if success else 0.0)) ** 2
    decisive = None
    if not success and exp.actions:
        decisive = next((i for i, a in enumerate(exp.actions) if a["ok"] is False), len(exp.actions) - 1)
    return {"success": success, "surprise": surprise, "decisive_step": decisive}


def _pass1_run(exp: Experience) -> list[Item]:
    analysis = outcome_analysis(exp)
    return [{"kind": "outcome", "payload": analysis, "gain": analysis["surprise"]}]


def _pass1_check(exp: Experience, item: Item) -> tuple[bool, str]:
    if item.get("kind") != "outcome":
        return False, "pass 1 emits only outcome items"
    if item.get("payload") != outcome_analysis(exp):
        return False, "analysis does not match the experience"
    return True, "analysis recomputed and matches"


# ---------------------------------------------------------------- pass 3: knowledge with evidence


def _signature(value: Any) -> str:
    """A short, redacted text form of a tool output, for beliefs and skill candidates."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    text = " ".join(str(events.redact(text)).split())
    return text[:SIGNATURE_CHARS] or "(empty output)"


def _failed_steps(exp: Experience) -> list[int]:
    return [i for i, a in enumerate(exp.actions) if a["ok"] is False]


def _pass3_run(exp: Experience) -> list[Item]:
    """One belief per failed action: `tool:<name> failed_with <output signature>`, with the step as evidence.

    Confidence is 0.6: one observation is weak evidence. A success, or a failure with no failed action, teaches
    nothing this pass can name, so it emits nothing.
    """
    if exp.passed:
        return []
    gain = outcome_analysis(exp)["surprise"]
    items = []
    for i in _failed_steps(exp):
        a = exp.actions[i]
        items.append({"kind": "belief", "gain": gain, "payload": {
            "subject": f"tool:{a['tool']}", "predicate": "failed_with", "object": _signature(a.get("output")),
            "confidence": 0.6, "evidence": {"step": i, "tool": a["tool"], "ok": False},
        }})
    return items


def _pass3_check(exp: Experience, item: Item) -> tuple[bool, str]:
    """A belief lands only with evidence that points at a failed action of this experience, on the same tool."""
    if item.get("kind") != "belief":
        return False, "pass 3 emits only beliefs"
    p = item.get("payload")
    if not isinstance(p, dict):
        return False, "payload must be a dict"
    ev = p.get("evidence")
    if not isinstance(ev, dict):
        return False, "no evidence"
    step = ev.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < len(exp.actions):
        return False, "evidence does not name a step of this experience"
    action = exp.actions[step]
    if action["ok"] is not False:
        return False, "evidence step did not fail"
    if ev.get("tool") != action["tool"] or p.get("subject") != f"tool:{action['tool']}":
        return False, "belief is not about the tool that failed"
    return True, f"supported by failed step {step}"


# ---------------------------------------------------------------- pass 4: skill candidate as code plus a test


def _input_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


_SKILL_CODE = '''\
"""Skill candidate (L1 pass 4): recognise a tool call that has failed before, before making it again.

Learned from one failed call of {tool!r}. Inputs are matched by digest, so no input text is kept here.
"""
import hashlib
import json

FAILED_TOOL = {tool!r}
FAILED_INPUT_SHA256 = {digest!r}
FAILED_OUTPUT = {output!r}


def input_digest(tool_input):
    return hashlib.sha256(json.dumps(tool_input, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def seen_failing(tool, tool_input=None, output=None):
    """True when this call looks like the known failure: same tool, and the same input or the same output."""
    if tool != FAILED_TOOL:
        return False
    if tool_input is not None and input_digest(tool_input) == FAILED_INPUT_SHA256:
        return True
    return output is not None and " ".join(str(output).split())[:{chars}] == FAILED_OUTPUT
'''

_SKILL_TEST = '''\
def test_flags_the_failed_call_by_input():
    assert seen_failing({tool!r}, json.loads({input_json!r}))


def test_flags_the_failed_call_by_output():
    assert seen_failing({tool!r}, output={output!r})


def test_ignores_another_tool():
    assert not seen_failing({other!r}, json.loads({input_json!r}), {output!r})


def test_ignores_a_new_input_with_a_new_output():
    assert not seen_failing({tool!r}, {{"l1-unseen-input": 1}}, "l1-unseen-output")
'''


def _pass4_run(exp: Experience) -> list[Item]:
    """For the decisive failed action, a guard that recognises the same failing call, plus its own tests."""
    if exp.passed:
        return []
    failed = _failed_steps(exp)
    if not failed:
        return []
    step = failed[0]
    a = exp.actions[step]
    tool, tool_input = a["tool"], a.get("input")
    output = " ".join(str(a.get("output")).split())[:SIGNATURE_CHARS]
    if output == "l1-unseen-output":  # would make the negative test contradict the positive one
        return []
    code = _SKILL_CODE.format(tool=tool, digest=_input_digest(tool_input), output=output, chars=SIGNATURE_CHARS)
    test = _SKILL_TEST.format(tool=tool, other=tool + "-other",
                              input_json=json.dumps(tool_input, sort_keys=True, default=str), output=output)
    return [{"kind": "skill_candidate", "gain": outcome_analysis(exp)["surprise"], "payload": {
        "name": f"seen_failing_{tool}", "code": code, "test": test, "step": step,
    }}]


def _pass4_check(exp: Experience, item: Item) -> tuple[bool, str]:
    if item.get("kind") != "skill_candidate":
        return False, "pass 4 emits only skill candidates"
    p = item.get("payload")
    if not isinstance(p, dict):
        return False, "payload must be a dict"
    return run_skill_test(p.get("code"), p.get("test"))


_DRIVER = textwrap.dedent(
    """
    import json, sys
    src = json.load(sys.stdin)
    ns = {"__name__": "skill"}
    exec(compile(src["code"], "<skill>", "exec"), ns)
    exec(compile(src["test"], "<skill test>", "exec"), ns)
    tests = [v for k, v in list(ns.items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    sys.stdout.write("\\n" + src["marker"] + " " + str(len(tests)) + "\\n")
    """
)


def run_skill_test(code: Any, test: Any, timeout_s: float = SKILL_TIMEOUT_S) -> tuple[bool, str]:
    """Run a candidate's test against its code in a clean, isolated interpreter (`python -I`, temp cwd and HOME,
    no inherited environment). True only when both parse, the test is not blank, at least one `test_*` callable ran,
    and every one returned. Not a sandbox: the code runs as this OS user (see known-bugs/l1-compiler-limits.md).
    """
    if not isinstance(code, str) or not code.strip():
        return False, "no code"
    if not isinstance(test, str) or not test.strip():
        return False, "no test"
    for label, src in (("code", code), ("test", test)):
        try:
            ast.parse(src)
        except (SyntaxError, ValueError):
            return False, f"{label} does not parse"
    marker = f"{_SKILL_OK}-{os.urandom(8).hex()}"
    with tempfile.TemporaryDirectory() as tmp:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "TMPDIR": tmp, "LANG": "C.UTF-8"}
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", _DRIVER], cwd=tmp, env=env,
                                  input=json.dumps({"code": code, "test": test, "marker": marker}),
                                  capture_output=True, text=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            return False, "test timed out"
    if proc.returncode != 0:
        return False, "test failed"
    last = proc.stdout.rstrip("\n").rsplit("\n", 1)[-1]
    if not last.startswith(marker + " ") or last[len(marker) + 1:] in ("", "0"):
        return False, "no test ran"
    return True, f"{last[len(marker) + 1:]} test(s) passed"


# ---------------------------------------------------------------- the compiler


def default_registry() -> Registry:
    reg = Registry()
    reg.register("pass1", 1, _pass1_run, _pass1_check)
    reg.register("pass3", 1, _pass3_run, _pass3_check)
    reg.register("pass4", 1, _pass4_run, _pass4_check)
    return reg


def compile_experience(exp: Experience, registry: Registry | None = None) -> list[Emission]:
    """Record `exp` (unless it already is), resolve its prediction, run every pass and land what passes its check."""
    if not isinstance(exp, Experience):
        raise TypeError("compile_experience needs an Experience")
    reg = default_registry() if registry is None else registry
    event_id = xp.find_recorded(exp)
    if event_id is None:
        event_id = xp.record(exp)  # no evidence without it: a failure here raises and nothing lands
    _resolve_prediction(exp)
    out: list[Emission] = []
    for name in reg.names():
        p = reg.get(name)
        start = time.perf_counter()
        try:
            items = p.run(exp)
            if items is None:
                items = []
            if not isinstance(items, list):
                raise TypeError("run must return a list or None")
        except Exception as e:  # noqa: BLE001 - one broken pass must not stop the others
            _log(PASS_FAILED_EVENT, {"pass": p.name, "version": p.version, "error": type(e).__name__,
                                     "experience_event_id": event_id})
            continue
        run_time = time.perf_counter() - start
        for it in items:
            out.append(_land(exp, p, it, run_time, event_id))
    return out


def _resolve_prediction(exp: Experience) -> None:
    """Score the prediction against the verified outcome, once. Already resolved is fine; unknown only warns."""
    try:
        predictions.resolve(exp.prediction["id"], {"success": exp.passed})
    except predictions.AlreadyResolvedError:
        pass
    except LookupError:
        logger.warning("experience prediction is not in the ledger; compiling without resolving it")


def _land(exp: Experience, p: Pass, it: Any, run_time: float, event_id: int) -> Emission:
    start = time.perf_counter()
    kind = it.get("kind") if isinstance(it, dict) else None
    payload = it.get("payload") if isinstance(it, dict) else None
    raw_gain = it.get("gain") if isinstance(it, dict) else None
    gain = float(raw_gain) if _is_real(raw_gain) else None
    landed, reason, extra = False, "", {}
    try:
        if not isinstance(it, dict) or not isinstance(kind, str) or not kind:
            reason = "item must be a dict with a str kind"
        elif gain is None:
            reason = "gain must be a finite real number"
        else:
            verdict = p.check(exp, it)
            if not (isinstance(verdict, tuple) and len(verdict) == 2):
                reason = "check must return (ok, reason)"
            elif verdict[0] is not True:
                reason = str(verdict[1]) or "refused by its check"
            else:
                extra = _LANDERS.get(kind, _land_logged)(payload, p, event_id)
                landed, reason = True, str(verdict[1]) or "passed its check"
    except Exception as e:  # noqa: BLE001 - a raising check or lander is a refusal, not a landing
        landed, reason, extra = False, f"refused: {type(e).__name__}", {}
    compute = run_time + (time.perf_counter() - start)
    record = {"pass": p.name, "version": p.version, "kind": kind, "gain": gain, "compute": compute,
              "landed": landed, "reason": reason, "experience_event_id": event_id, **extra}
    if landed and kind != "belief":
        record["payload"] = payload  # the belief store already holds a landed belief
    _log(EMIT_EVENT, record)
    return Emission(p.name, p.version, kind, payload, gain, compute, landed, reason)


def _is_real(x: Any) -> bool:
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def _land_belief(payload: Any, p: Pass, event_id: int) -> dict:
    """Into the CS3 store, with the experience event as evidence. Out-of-range confidence is refused, not clamped."""
    if not isinstance(payload, dict):
        raise TypeError("belief payload must be a dict")
    for key in ("subject", "predicate", "object"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"belief needs a non-blank {key}")
    conf = payload.get("confidence")
    if not _is_real(conf) or not 0.0 <= conf <= 1.0:
        raise ValueError("belief confidence must be in [0, 1]")
    b = beliefs.add_belief(payload["subject"], payload["predicate"], payload["object"], float(conf),
                           source=f"compiler:{p.name}@v{p.version}", evidence_event_id=event_id)
    return {"belief_id": b.id}


def _land_logged(payload: Any, p: Pass, event_id: int) -> dict:
    """Outcomes, skill candidates and other kinds land as their logged `compiler_emit` event. Must be plain JSON."""
    json.dumps(payload, allow_nan=False)
    return {}


_LANDERS: dict[str, Callable[[Any, Pass, int], dict]] = {"belief": _land_belief}


def _log(kind: str, payload: dict) -> None:
    """Fails open, as the other Core State writers do (decision 0009 item 7); emit() notes the gap."""
    try:
        events.emit(kind, payload, actor=ACTOR)
    except Exception as e:  # noqa: BLE001 - never log the payload
        logger.warning("could not record %s event: %s", kind, type(e).__name__)
