"""The Learning Compiler (L1): passes 1, 3 and 4, a versioned registry, and the landing law.

A pass is `run(exp) -> list[{"kind", "payload", "gain"}] | None` plus `check(exp, item) -> (ok, reason)`.
`compile_experience` trusts only the CS5 ledger for the prediction, records the Experience once per attempt,
resolves the task's prediction, runs the latest version of every pass, and lands an item only when its gain is a
finite real, its own check says exactly `True` on the item as it will be stored, and the kind's lander accepts it
(the landing law). Every item, landed or refused, is logged as one `compiler_emit` event with its pass, version,
gain and compute. A pass or check that raises is a refusal, never a crash, and never stops the others.

Passes here are deterministic code: no model call. Gain is the pass's own estimate (v1 uses the surprise of the
experience); whether a candidate really improves anything is measured later, in the Arena, through OBS3.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
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
from tanishi.core_state.db import migrate, open_db

logger = logging.getLogger(__name__)

ACTOR = "compiler.passes"
EMIT_EVENT = "compiler_emit"
PASS_FAILED_EVENT = "compiler_pass_failed"
REFUSED_EVENT = "compiler_refused"
UNSANDBOXED_EVENT = "skill_test_unsandboxed"  # a template skill test ran without SB1 (decision 0026, seam round)
TASK_PREFIX = "task:"  # only a task-level prediction is scored with the task's verdict; tool calls are the registry's
SKILL_TIMEOUT_S = 20.0
SIGNATURE_CHARS = 120  # how much of a failing output a belief or skill candidate keeps
MAX_BELIEFS = 10  # per experience and pass: a retry storm is aggregated, not landed N times (red team R9)

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
    payload: Any  # as stored: redacted and clipped exactly as the event log holds it
    gain: float | None  # None when the pass gave no finite real gain (then it did not land)
    compute: float  # seconds: the pass's run time plus this item's check and landing
    landed: bool
    reason: str


# ---------------------------------------------------------------- actions: failures, and failures recovered from


def _input_of(action: dict) -> Any:
    """A failed call with no `input` key (a no-argument tool) is the call with `{}` (red team R9)."""
    value = action.get("input")
    return {} if value is None else value


def _recovered(actions: list) -> set[int]:
    """Failed steps whose exact call (same tool, same input) succeeded later: transient, not the cause (R6)."""
    later_ok: set[tuple[str, str]] = set()
    out: set[int] = set()
    for i in range(len(actions) - 1, -1, -1):
        a = actions[i]
        key = (a["tool"], xp.digest(_input_of(a)))
        if a["ok"] is True:
            later_ok.add(key)
        elif key in later_ok:
            out.add(i)
    return out


def _failed_steps(actions: list) -> list[int]:
    return [i for i, a in enumerate(actions) if a["ok"] is False]


def _decisive_failures(actions: list) -> list[int]:
    """Failed steps the agent did not recover from, in order."""
    recovered = _recovered(actions)
    return [i for i in _failed_steps(actions) if i not in recovered]


# ---------------------------------------------------------------- pass 1: outcome analysis


def outcome_analysis(exp: Experience) -> dict:
    """Success (the verifier's verdict), surprise (CS5 Brier score of the prediction) and the decisive step.

    The decisive step on a failure is the first failed action the agent did not recover from (a failure followed by
    a successful identical call is transient), or the last action when there is none (the answer itself was wrong);
    None on a success or when there were no actions. Pure. `compile_experience` hands passes an Experience whose
    prediction is the ledger's row, so in the compiler the surprise is the ledger's.
    """
    success = exp.passed
    p = exp.prediction
    forecast = p["confidence"] if p["expected"]["success"] else 1.0 - p["confidence"]
    surprise = (float(forecast) - (1.0 if success else 0.0)) ** 2
    decisive = None
    actions = exp.actions
    if not success and actions:
        decisive = next(iter(_decisive_failures(actions)), len(actions) - 1)
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
    """A short, redacted text form of a tool output, for beliefs and skill candidates.

    Redacted as a structure first, so a secret-named key hides its whole value (a list, a dict) at any depth, then
    as text (red team R3). Keys of any type are sorted as strings (R9)."""
    clean = events.redact(xp.canonical(value))
    text = clean if isinstance(clean, str) else json.dumps(clean, sort_keys=True, default=str, ensure_ascii=False)
    text = " ".join(str(events.redact(text)).split())
    return text[:SIGNATURE_CHARS] or "(empty output)"


def _pass3_run(exp: Experience) -> list[Item]:
    """One belief per distinct failure, `tool:<name> failed_with <output signature>`, with its steps as evidence.

    Identical failures (same tool, same signature) are one observation with a count, and at most MAX_BELIEFS
    distinct ones are emitted, so a retry storm is not N beliefs. Confidence is 0.6: one observation is weak
    evidence. A success, or a failure with no failed action, teaches nothing this pass can name.
    """
    if exp.passed:
        return []
    actions = exp.actions
    gain = outcome_analysis(exp)["surprise"]
    groups: dict[tuple[str, str], list[int]] = {}
    for i in _failed_steps(actions):
        key = (actions[i]["tool"], _signature(actions[i].get("output")))
        if key in groups or len(groups) < MAX_BELIEFS:
            groups.setdefault(key, []).append(i)
    return [{"kind": "belief", "gain": gain, "payload": {
        "subject": f"tool:{tool}", "predicate": "failed_with", "object": sig, "confidence": 0.6,
        "evidence": {"step": steps[0], "tool": tool, "ok": False, "count": len(steps), "steps": steps[:MAX_BELIEFS]},
    }} for (tool, sig), steps in groups.items()]


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
    actions = exp.actions
    step = ev.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < len(actions):
        return False, "evidence does not name a step of this experience"
    action = actions[step]
    if action["ok"] is not False:
        return False, "evidence step did not fail"
    if ev.get("tool") != action["tool"] or p.get("subject") != f"tool:{action['tool']}":
        return False, "belief is not about the tool that failed"
    return True, f"supported by failed step {step}"


# ---------------------------------------------------------------- pass 4: skill candidate as code plus a test


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _output_text(output: Any) -> str:
    return " ".join(str(output).split())[:SIGNATURE_CHARS]


# The candidate matches tool, input and output by sha256 only, so no input or output text is kept in it (decision
# 0026 item 10, red team R3). FAILED_TOOL and FAILED_OUTPUT are redacted, clipped labels for a reader; they never
# match anything. `_canonical` here must stay the same function as experience.canonical (the eval test checks it).
_SKILL_CODE = '''\
"""Skill candidate (L1 pass 4): recognise a tool call that has failed before, before making it again.

Learned from one failed call. The tool, its input and its output are matched by sha256 digest, so no input or
output text is kept here. FAILED_TOOL and FAILED_OUTPUT are redacted labels for a reader, never used to match.
"""
import hashlib
import json

FAILED_TOOL = {tool_label!r}
FAILED_OUTPUT = {output_label!r}
FAILED_TOOL_SHA256 = {tool_sha!r}
FAILED_INPUT_SHA256 = {input_sha!r}
FAILED_OUTPUT_SHA256 = {output_sha!r}


def _canonical(value, depth=0):
    if depth > {max_depth}:
        return "[too deep]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: (str(kv[0]), type(kv[0]).__name__))
        return {{str(k): _canonical(v, depth + 1) for k, v in items}}
    if isinstance(value, (list, tuple)):
        return [_canonical(v, depth + 1) for v in value]
    return value


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def tool_digest(tool):
    return _sha256(str(tool))


def input_digest(tool_input):
    """A missing input is the call with {{}}, as it was when this guard was learned."""
    value = {{}} if tool_input is None else tool_input
    return _sha256(json.dumps(_canonical(value), sort_keys=True, default=str, ensure_ascii=False))


def output_digest(output):
    return _sha256(" ".join(str(output).split())[:{chars}])


def seen_failing_digests(tool_sha256, input_sha256=None, output_sha256=None):
    """True for the known failure: same tool, and the same input or the same output."""
    if tool_sha256 != FAILED_TOOL_SHA256:
        return False
    if input_sha256 is not None and input_sha256 == FAILED_INPUT_SHA256:
        return True
    return output_sha256 is not None and output_sha256 == FAILED_OUTPUT_SHA256


def seen_failing(tool, tool_input=None, output=None):
    """True when this call looks like the known failure: same tool, and the same input or the same output."""
    return seen_failing_digests(tool_digest(tool), None if tool_input is None else input_digest(tool_input),
                                None if output is None else output_digest(output))
'''

_SKILL_TEST = '''\
def test_flags_the_failed_call_by_input():
    assert seen_failing_digests({tool_sha!r}, input_sha256={input_sha!r})


def test_flags_the_failed_call_by_output():
    assert seen_failing_digests({tool_sha!r}, output_sha256={output_sha!r})


def test_ignores_another_tool():
    assert not seen_failing_digests({other_sha!r}, {input_sha!r}, {output_sha!r})


def test_ignores_a_new_input_with_a_new_output():
    assert not seen_failing_digests({tool_sha!r}, input_digest({{"l1-unseen-input": 1}}),
                                    output_digest("l1-unseen-output"))


def test_digests_are_canonical():
    assert input_digest({{"b": 1, "a": [1, {{2: "x", "y": 3}}]}}) == input_digest({{"a": [1, {{"y": 3, 2: "x"}}], "b": 1}})
    assert input_digest(None) == input_digest({{}})
    assert output_digest("  a\\n  b ") == output_digest("a b")
    assert tool_digest(FAILED_TOOL + "-other") != FAILED_TOOL_SHA256
'''

# Added when the labels are the real tool name and output text (nothing redacted or clipped): then the guard can be
# tested end to end from text without the test holding anything the code does not already show.
_SKILL_TEST_END_TO_END = '''

def test_flags_the_failed_call_end_to_end():
    assert seen_failing(FAILED_TOOL, output=FAILED_OUTPUT)
    assert not seen_failing(FAILED_TOOL + "-other", output=FAILED_OUTPUT)
'''

_UNSEEN_INPUT_SHA = xp.digest({"l1-unseen-input": 1})
_UNSEEN_OUTPUT_SHA = _sha256(_output_text("l1-unseen-output"))


def _pass4_run(exp: Experience) -> list[Item]:
    """For the decisive failed action, a guard that recognises the same failing call, plus its own tests.

    A failure the agent recovered from (the identical call later worked) gets no guard (red team R6)."""
    if exp.passed:
        return []
    actions = exp.actions
    decisive = _decisive_failures(actions)
    if not decisive:
        return []
    step = decisive[0]
    a = actions[step]
    tool, raw_output = a["tool"], a.get("output")
    input_sha, output_sha = xp.digest(_input_of(a)), _sha256(_output_text(raw_output))
    if input_sha == _UNSEEN_INPUT_SHA or output_sha == _UNSEEN_OUTPUT_SHA:
        return []  # would make the negative test contradict the positive one
    tool_label = " ".join(str(events.redact(tool)).split())[:SIGNATURE_CHARS]
    output_label = _signature(raw_output)
    shas = {"tool_sha": _sha256(tool), "input_sha": input_sha, "output_sha": output_sha}
    code = _SKILL_CODE.format(tool_label=tool_label, output_label=output_label, max_depth=xp.MAX_DEPTH,
                              chars=SIGNATURE_CHARS, **shas)
    test = _SKILL_TEST.format(other_sha=_sha256(tool + "-other"), **shas)
    if tool_label == tool and output_label == _output_text(raw_output):
        test += _SKILL_TEST_END_TO_END
    return [{"kind": "skill_candidate", "gain": outcome_analysis(exp)["surprise"], "payload": {
        "name": f"seen_failing_{tool_label}", "code": code, "test": test, "step": step,
    }}]


def _pass4_check(exp: Experience, item: Item) -> tuple[bool, str]:
    if item.get("kind") != "skill_candidate":
        return False, "pass 4 emits only skill candidates"
    p = item.get("payload")
    if not isinstance(p, dict):
        return False, "payload must be a dict"
    return run_skill_test(p.get("code"), p.get("test"), from_template=True)


# ---------------------------------------------------------------- the skill-test harness


# The driver runs in the child. It reads its request, keeps the nonce and the test names in locals only, installs
# an audit hook, then runs the code, then the test, then exactly the tests the harness named. The hook refuses the
# ways code could reach the driver's locals (frames, gc, tracers, ctypes, new code objects, /proc) or another
# process; its tables are literals, so code cannot edit them, and replacing its __code__ is itself refused.
# This is defence in depth inside one process, not a sandbox: SB1's sandbox is used when it is present.
_DRIVER = textwrap.dedent(
    r'''
    import hashlib, json, os, sys

    def _guard(event, args):
        if event in ("sys._getframe", "sys._getframemodulename", "sys._current_frames", "sys._current_exceptions",
                     "sys.settrace", "sys.setprofile", "gc.get_objects", "gc.get_referrers", "gc.get_referents",
                     "os.system", "os.fork", "os.forkpty", "os.kill", "os.killpg", "subprocess.Popen", "pty.spawn",
                     "code.__new__", "marshal.load", "marshal.loads", "function.__new__", "sys.addaudithook"):
            raise RuntimeError("refused by the skill harness: " + event)
        if event.startswith(("ctypes.", "os.exec", "os.spawn", "os.posix_spawn", "_winapi.")):
            raise RuntimeError("refused by the skill harness: " + event)
        if event == "object.__getattr__" and args[1] in (
                "tb_frame", "gi_frame", "cr_frame", "ag_frame", "f_back", "f_locals", "f_globals"):
            raise RuntimeError("refused by the skill harness: frame access")
        if event in ("object.__setattr__", "object.__delattr__") and args[1] in (
                "__code__", "__closure__", "__globals__", "__defaults__", "__kwdefaults__"):
            raise RuntimeError("refused by the skill harness: function rewrite")
        if event == "import" and args[0] in ("ctypes", "_ctypes", "_testcapi", "_testinternalcapi", "_xxsubinterpreters"):
            raise RuntimeError("refused by the skill harness: import " + args[0])
        if event == "open" and isinstance(args[0], str) and args[0].startswith(("/proc", "/dev/mem", "/dev/kmem")):
            raise RuntimeError("refused by the skill harness: open")

    def _main():
        req = json.loads(sys.stdin.read())
        nonce, names, code, test = req.pop("nonce"), req.pop("tests"), req.pop("code"), req.pop("test")
        del req
        write, exit_, exec_, compile_, getattr_, str_ = os.write, os._exit, exec, compile, getattr, str
        sys.addaudithook(_guard)
        ns = {"__name__": "skill"}
        try:
            exec_(compile_(code, "<skill>", "exec"), ns)
            exec_(compile_(test, "<skill test>", "exec"), ns)
            tests = [ns.get(name) for name in names]  # taken before any test runs: a test cannot swap another
            for t in tests:
                if getattr_(getattr_(t, "__code__", None), "co_filename", None) != "<skill test>":
                    exit_(3)
            for t in tests:
                t()
        except BaseException:
            exit_(1)
        write(1, ("\n" + nonce + " " + str_(len(tests)) + "\n").encode())
        exit_(0)

    _main()
    '''
)

_DYNAMIC_NAMES = frozenset({"globals", "vars", "exec", "eval", "compile", "__import__", "__builtins__", "locals"})


def _bound_names(tree: ast.Module) -> set[str]:
    """Names a module binds at its top level (defs, classes, assignments, imports, loop and `with` targets), plus any
    name a function declares `global`: everything it could rebind in the namespace it shares with the other source."""
    names: set[str] = set()

    def visit(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in node.names)
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                    names.add(sub.id)
            for field in ("body", "orelse", "finalbody", "handlers"):
                visit([n for n in getattr(node, field, []) if isinstance(n, ast.stmt)])

    visit(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def _test_names(test_tree: ast.Module, code_tree: ast.Module) -> tuple[list[str], str]:
    """The tests the harness will run, found in the test source only (red team R5), or ([], why it is refused)."""
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Name) and node.id in _DYNAMIC_NAMES:
            return [], f"test uses {node.id}()"
    shadowed = _bound_names(test_tree) & _bound_names(code_tree)
    if shadowed:
        return [], f"test redefines {min(shadowed)!r} from the code it tests"
    names = [n.name for n in test_tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    return names, "" if names else "no test ran"


def _sandbox() -> Callable[..., tuple[int, str]] | None:
    """SB1's runner, when it has landed: `tanishi.sandbox.run_python(script, *, stdin, timeout_s) -> (returncode,
    stdout)`, raising TimeoutError on timeout. The interface is L1's reading (open-problems/L1-repair-conflicts.md)."""
    try:
        mod = importlib.import_module("tanishi.sandbox")
    except ImportError:
        return None
    run = getattr(mod, "run_python", None)
    if not callable(run):
        logger.warning("tanishi.sandbox has no run_python(); skill tests run without the sandbox")
        return None
    return run


def run_skill_test(code: Any, test: Any, timeout_s: float = SKILL_TIMEOUT_S, *,
                   from_template: bool = False) -> tuple[bool, str]:
    """Run a candidate's test against its code, in SB1's sandbox when it is present, else in a clean, isolated
    interpreter (`python -I`, temp cwd and HOME, no inherited environment).

    If the sandbox raises SandboxUnavailable (macOS without Docker), only code the compiler wrote from its own
    template (`from_template=True`, pass 4's check) falls back to the isolated interpreter, with a logged
    `skill_test_unsandboxed` event; anything else is refused (human decision, decision 0026).

    The harness owns discovery: only the `test_*` functions defined at the top level of the test source run, and a
    test that rebinds a name the code defines is refused. True only when every one of them returned and the child
    printed this run's nonce, which the candidate cannot read (see `_DRIVER`). Not a sandbox by itself: without SB1
    the code runs as this OS user (see known-bugs/l1-compiler-limits.md).
    """
    if not isinstance(code, str) or not code.strip():
        return False, "no code"
    if not isinstance(test, str) or not test.strip():
        return False, "no test"
    trees = {}
    for label, src in (("code", code), ("test", test)):
        try:
            trees[label] = ast.parse(src)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return False, f"{label} does not parse"
    names, why = _test_names(trees["test"], trees["code"])
    if not names:
        return False, why
    nonce = os.urandom(16).hex()
    request = json.dumps({"code": code, "test": test, "tests": names, "nonce": nonce})
    try:
        returncode, stdout = _run_driver(request, timeout_s, from_template)
    except (subprocess.TimeoutExpired, TimeoutError):
        return False, "test timed out"
    except Exception as e:  # noqa: BLE001 - a sandbox that fails is a refusal, never a pass
        return False, f"test could not run: {type(e).__name__}"
    if returncode != 0:
        return False, "test failed"
    if stdout.rstrip("\n").rsplit("\n", 1)[-1] != f"{nonce} {len(names)}":
        return False, "no test ran"
    return True, f"{len(names)} test(s) passed"


def _run_driver(request: str, timeout_s: float, from_template: bool = False) -> tuple[int, str]:
    sandboxed = _sandbox()
    if sandboxed is None:
        return _run_isolated(_DRIVER, request, timeout_s)
    try:
        return sandboxed(_DRIVER, stdin=request, timeout_s=timeout_s)
    except Exception as e:
        unavailable = getattr(importlib.import_module("tanishi.sandbox"), "SandboxUnavailable", ())
        if not from_template or not isinstance(e, unavailable):
            raise
        reason = str(e)[:SIGNATURE_CHARS] or type(e).__name__
    logger.warning("sandbox unavailable; template skill test runs unsandboxed: %s", reason)
    _log(UNSANDBOXED_EVENT, {"reason": reason})
    return _run_isolated(_DRIVER, request, timeout_s)


def _run_isolated(script: str, stdin: str, timeout_s: float) -> tuple[int, str]:
    """Without SB1: `python -I`, a temp cwd and HOME, and only PATH/HOME/TMPDIR/LANG in the environment."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "TMPDIR": tmp, "LANG": "C.UTF-8"}
        proc = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp, env=env, input=stdin,
                              capture_output=True, text=True, timeout=timeout_s, check=False)
    return proc.returncode, proc.stdout


# ---------------------------------------------------------------- the compiler


def default_registry() -> Registry:
    reg = Registry()
    reg.register("pass1", 1, _pass1_run, _pass1_check)
    reg.register("pass3", 1, _pass3_run, _pass3_check)
    reg.register("pass4", 1, _pass4_run, _pass4_check)
    return reg


class _Refused(Exception):
    """The experience cannot be trusted; nothing is recorded or landed."""


def compile_experience(exp: Experience, registry: Registry | None = None) -> list[Emission]:
    """Check `exp` against the ledger, record it once per attempt, resolve its prediction, run every pass and land
    what passes its check. An experience whose prediction the ledger does not hold as a task prediction is refused:
    one `compiler_refused` event, and nothing else is written."""
    if not isinstance(exp, Experience):
        raise TypeError("compile_experience needs an Experience")
    reg = default_registry() if registry is None else registry
    try:
        exp = _trusted(exp)
    except _Refused as r:
        _log(REFUSED_EVENT, {"prediction_id": exp.prediction["id"], "reason": str(r)})
        return []
    event_id = xp.record_once(exp)  # no evidence without it: a failure here raises and nothing lands
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


def _ledger_row(prediction_id: str) -> tuple[str, dict, float] | None:
    """(about, expected, confidence) of a CS5 prediction, read from the ledger itself, or None if it is not there."""
    conn = open_db()
    try:
        migrate(conn)
        row = conn.execute("SELECT about, expected, confidence FROM predictions WHERE id = ?",
                           (prediction_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    expected = json.loads(row[1]) if row[1] else None
    return row[0], expected, row[2]


def _trusted(exp: Experience) -> Experience:
    """The Experience the passes may read: its prediction is the ledger's row (never what the caller typed, red team
    R1), and a Frontier or Sealed Vault tier keeps only the verifier's verdict. Raises _Refused."""
    pred = exp.prediction
    row = _ledger_row(pred["id"])
    if row is None:
        raise _Refused("prediction is not in the ledger")
    about, expected, confidence = row
    if not isinstance(about, str) or not about.startswith(TASK_PREFIX):
        raise _Refused("prediction is not a task prediction")
    if not isinstance(expected, dict) or not isinstance(expected.get("success"), bool) or not _is_real(confidence):
        raise _Refused("ledger row is malformed")
    if expected != pred["expected"] or float(confidence) != float(pred["confidence"]):
        d = exp.to_dict()
        d["prediction"] = {"id": pred["id"], "expected": expected, "confidence": confidence}
        exp = Experience.from_dict(d)
    return xp.sealed(exp)


def _resolve_prediction(exp: Experience) -> None:
    """Score the task prediction against the verified outcome, once. Already resolved is fine."""
    try:
        predictions.resolve(exp.prediction["id"], {"success": exp.passed})
    except predictions.AlreadyResolvedError:
        pass


def _clip(value: Any, depth: int = 0) -> Any:
    """Every string and key clipped as emit() clips, so the log stores exactly this."""
    if isinstance(value, str):
        if len(value) <= events.MAX_CHARS:
            return value
        tail = f"... [{len(value) - events.MAX_CHARS} more chars]"
        return value[:max(events.MAX_CHARS - len(tail), 0)] + tail
    if depth > events.MAX_DEPTH:
        return value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            key = _clip(k, depth + 1) if isinstance(k, str) else k
            while key in out:
                key = f"{key}#"
            out[key] = _clip(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_clip(v, depth + 1) for v in value]
    return value


def _stored_form(payload: Any) -> Any:
    """`payload` as the event log will hold it: redacted, then clipped, to a fixed point (red team R4)."""
    stored = payload
    for _ in range(4):
        again = _clip(events.redact(stored))
        if again == stored:
            break
        stored = again
    return stored


def _land(exp: Experience, p: Pass, it: Any, run_time: float, event_id: int) -> Emission:
    start = time.perf_counter()
    kind = it.get("kind") if isinstance(it, dict) else None
    raw_gain = it.get("gain") if isinstance(it, dict) else None
    gain = float(raw_gain) if _is_real(raw_gain) else None
    payload: Any = None
    landed, reason, extra = False, "", {}
    record: dict[str, Any] = {}
    try:
        payload = _stored_form(it.get("payload")) if isinstance(it, dict) else None
        if not isinstance(it, dict) or not isinstance(kind, str) or not kind:
            reason = "item must be a dict with a str kind"
        elif gain is None:
            reason = "gain must be a finite real number"
        else:
            # The check sees exactly the item that will be stored: clipped and redacted first (red team R4).
            verdict = p.check(exp, {**it, "payload": payload})
            if not (isinstance(verdict, tuple) and len(verdict) == 2):
                reason = "check must return (ok, reason)"
            elif verdict[0] is not True:
                reason = str(verdict[1]) or "refused by its check"
            else:
                key = xp.digest([p.name, p.version, kind, payload])
                with xp.attempt_lock():  # find, land and log as one critical section (red team R8)
                    earlier = _landed_before(event_id, key)
                    if earlier is not None:
                        reason = f"already landed (event {earlier})"
                    else:
                        extra = _LANDERS.get(kind, _land_logged)(payload, p, event_id)
                        landed, reason = True, str(verdict[1]) or "passed its check"
                        record = _emit_record(p, kind, gain, run_time + (time.perf_counter() - start), landed,
                                              reason, event_id, key, payload, extra)
                        _log(EMIT_EVENT, record)
    except Exception as e:  # noqa: BLE001 - a raising check or lander is a refusal, not a landing
        landed, reason, extra = False, f"refused: {type(e).__name__}", {}
    compute = record["compute"] if record else run_time + (time.perf_counter() - start)
    if not record:
        _log(EMIT_EVENT, _emit_record(p, kind, gain, compute, landed, reason, event_id, None, payload, extra))
    return Emission(p.name, p.version, kind, payload, gain, compute, landed, reason)


def _emit_record(p: Pass, kind: Any, gain: float | None, compute: float, landed: bool, reason: str, event_id: int,
                 key: str | None, payload: Any, extra: dict) -> dict:
    record = {"pass": p.name, "version": p.version, "kind": kind, "gain": gain, "compute": compute,
              "landed": landed, "reason": reason, "experience_event_id": event_id, **extra}
    if key is not None:
        record["digest"] = key
    if landed and kind == "belief" and isinstance(payload, dict) and isinstance(payload.get("evidence"), dict):
        record["evidence"] = payload["evidence"]  # the step(s) and count; the belief store holds the triple
    elif landed and kind != "belief":
        record["payload"] = payload
    return record


def _landed_before(event_id: int, key: str) -> int | None:
    """The `compiler_emit` event that already landed this exact item for this experience, or None."""
    conn = open_db()
    try:
        migrate(conn)
        row = conn.execute(
            "SELECT id FROM events WHERE kind = ? AND actor = ? "
            "AND json_extract(payload, '$.experience_event_id') = ? AND json_extract(payload, '$.digest') = ? "
            "AND json_extract(payload, '$.landed') = 1 ORDER BY id LIMIT 1",
            (EMIT_EVENT, ACTOR, event_id, key),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


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
