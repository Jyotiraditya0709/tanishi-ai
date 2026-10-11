"""L1 implementer's tests: the edges the exam does not pin, plus a small evaluation of pass 4's candidates."""
from __future__ import annotations

import json

import pytest

from tanishi.compiler import experience as xp
from tanishi.compiler import passes
from tanishi.core_state import beliefs, events, predictions


def _exp(*, passed=False, actions=None, task="sum two numbers"):
    if actions is None:
        actions = [{"tool": "calc", "input": {"a": 1}, "output": "division by zero", "ok": False}]
    pid = predictions.predict("task:l1-impl", {"success": True}, 0.7)
    return xp.Experience(task=task, context={}, actions=actions, outcome={"success": passed},
                         verifier_result={"passed": passed, "score": float(passed), "reason": "r"},
                         cost={"usd": 0.0}, prediction={"id": pid, "expected": {"success": True}, "confidence": 0.7})


def _kinds(kind):
    return [e.payload for e in events.iter_events(kind=kind)]


@pytest.mark.parametrize("bad", [
    {"tool": "x", "ok": "yes"}, {"tool": "", "ok": True}, {"ok": True}, "not a dict",
])
def test_malformed_actions_are_refused(bad):
    d = _exp().to_dict()
    d["actions"] = [bad]
    with pytest.raises((ValueError, TypeError)):
        xp.Experience.from_dict(d)


@pytest.mark.parametrize("pred", [
    {"id": "pred_x", "expected": {"success": "yes"}, "confidence": 0.5},
    {"id": "pred_x", "expected": {"success": True}, "confidence": 1.5},
    {"id": "pred_x", "expected": {"success": True}, "confidence": True},
    {"id": "", "expected": {"success": True}, "confidence": 0.5},
])
def test_malformed_predictions_are_refused(pred):
    d = _exp().to_dict()
    d["prediction"] = pred
    with pytest.raises((ValueError, TypeError)):
        xp.Experience.from_dict(d)


def test_non_json_values_are_refused():
    d = _exp().to_dict()
    d["cost"] = {"usd": float("nan")}
    with pytest.raises(ValueError):
        xp.Experience.from_dict(d)


def test_compile_reuses_an_existing_record():
    exp = _exp()
    eid = xp.record(exp)
    passes.compile_experience(exp)
    assert [e.id for e in events.iter_events(kind="experience")] == [eid]
    assert all(p["experience_event_id"] == eid for p in _kinds("compiler_emit"))


def test_failure_with_no_failed_action_points_at_the_last_step_and_teaches_no_tool_belief():
    acts = [{"tool": "a", "input": {}, "output": "", "ok": True}, {"tool": "b", "input": {}, "output": "", "ok": True}]
    exp = _exp(actions=acts)
    assert passes.outcome_analysis(exp)["decisive_step"] == 1
    out = passes.compile_experience(exp)
    assert [e.pass_name for e in out] == ["pass1"]


def test_success_emits_only_the_outcome():
    out = passes.compile_experience(_exp(passed=True, actions=[{"tool": "a", "input": {}, "output": "", "ok": True}]))
    assert [(e.pass_name, e.landed) for e in out] == [("pass1", True)]
    assert out[0].payload["decisive_step"] is None


def test_beliefs_do_not_carry_secrets_from_tool_output():
    secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    exp = _exp(actions=[{"tool": "http", "input": {}, "output": f"401 for key {secret}", "ok": False}])
    passes.compile_experience(exp)
    stored = beliefs.find(subject="tool:http")
    assert stored and secret not in json.dumps([b.object for b in stored])


def test_pass3_check_refuses_evidence_from_a_step_that_succeeded():
    exp = _exp(actions=[{"tool": "calc", "input": {}, "output": "4", "ok": True},
                        {"tool": "calc", "input": {}, "output": "boom", "ok": False}])
    item = passes.default_registry().get("pass3").run(exp)[0]
    forged = dict(item, payload=dict(item["payload"], evidence={"step": 0, "tool": "calc", "ok": False}))
    ok, _ = passes.default_registry().get("pass3").check(exp, forged)
    assert ok is False


def test_pass1_check_refuses_a_doctored_analysis():
    exp = _exp()
    item = passes.default_registry().get("pass1").run(exp)[0]
    doctored = dict(item, payload=dict(item["payload"], success=True))
    assert passes.default_registry().get("pass1").check(exp, doctored)[0] is False


@pytest.mark.parametrize("test_src,why", [
    ("import os\nos._exit(0)", "a test that exits early has not passed"),
    ("x = 1", "a test with no test_* callable ran nothing"),
    ("def test_loops():\n    while True:\n        pass", "a test that hangs is refused"),
])
def test_skill_runner_refuses_tests_that_did_not_really_pass(test_src, why):
    ok, _ = passes.run_skill_test("def f():\n    return 1\n", test_src, timeout_s=3)
    assert ok is False, why


def test_a_truthy_but_not_true_verdict_does_not_land():
    reg = passes.Registry()
    reg.register("p", 1, lambda e: [{"kind": "note", "payload": {}, "gain": 0.1}], lambda e, i: (1, "yes"))
    assert [e.landed for e in passes.compile_experience(_exp(), registry=reg)] == [False]


def test_a_raising_or_bad_run_is_logged_by_type_only():
    reg = passes.Registry()
    reg.register("boom", 1, lambda e: (_ for _ in ()).throw(RuntimeError("secret detail")), lambda e, i: (True, ""))
    reg.register("tuple", 1, lambda e: ({"kind": "note", "payload": {}, "gain": 0.1},), lambda e, i: (True, ""))
    assert passes.compile_experience(_exp(), registry=reg) == []
    failed = _kinds("compiler_pass_failed")
    assert [f["pass"] for f in failed] == ["boom", "tuple"]
    assert "secret detail" not in json.dumps(failed)


def test_only_the_latest_version_of_a_pass_runs():
    reg = passes.Registry()
    reg.register("p", 1, lambda e: [{"kind": "note", "payload": {"v": 1}, "gain": 0.1}], lambda e, i: (True, ""))
    reg.register("p", 2, lambda e: [{"kind": "note", "payload": {"v": 2}, "gain": 0.1}], lambda e, i: (True, ""))
    out = passes.compile_experience(_exp(), registry=reg)
    assert [(e.version, e.payload) for e in out] == [(2, {"v": 2})]


# ---------------------------------------------------------------- evaluation: do pass 4's guards generalise?

EVAL_FAILURES = [
    ("web_search", {"q": "example.org"}, "timeout after 30s"),
    ("calc", {"expr": "1/0"}, "ZeroDivisionError"),
    ("read_file", {"path": "/missing"}, "No such file or directory"),
    ("http_get", {"url": "https://example.com/x"}, "HTTP 503"),
    ("tool with spaces", {"k": ["nested", {"x": 1}]}, "it's \"quoted\"\nand multi-line"),
]


def test_eval_pass4_candidates_catch_repeats_and_ignore_other_calls():
    """For each failure, the landed candidate must flag the same call again (recall) and pass on calls to other tools
    and on fresh calls of the same tool (no false alarms). Measured in-process on the candidate's own code."""
    hits = false_alarms = 0
    for tool, tool_input, output in EVAL_FAILURES:
        exp = _exp(actions=[{"tool": tool, "input": tool_input, "output": output, "ok": False}])
        cands = [e for e in passes.compile_experience(exp) if e.pass_name == "pass4" and e.landed]
        assert len(cands) == 1, tool
        ns: dict = {}
        exec(cands[0].payload["code"], ns)  # noqa: S102 - our own generated code, already run by its check
        seen = ns["seen_failing"]
        hits += bool(seen(tool, json.loads(json.dumps(tool_input)))) + bool(seen(tool, output=output))
        others = [t for t, _, _ in EVAL_FAILURES if t != tool]
        false_alarms += sum(bool(seen(t, tool_input, output)) for t in others)
        false_alarms += bool(seen(tool, {"fresh": True}, "a different error"))
    assert hits == 2 * len(EVAL_FAILURES)
    assert false_alarms == 0


# ---------------------------------------------------------------- repair round (red team 2026-10-11)


def _ledger_exp(about="task:l1-impl", expected=True, conf=0.7, **kw):
    pid = predictions.predict(about, {"success": expected}, conf)
    d = _exp(**kw).to_dict()
    d["prediction"] = {"id": pid, "expected": {"success": expected}, "confidence": conf}
    return xp.Experience.from_dict(d)


def test_surprise_and_the_record_use_the_ledger_row_not_the_experience():
    exp = _ledger_exp(expected=True, conf=0.9)
    d = exp.to_dict()
    d["prediction"] = dict(d["prediction"], expected={"success": False}, confidence=0.5)
    out = passes.compile_experience(xp.Experience.from_dict(d))
    assert out[0].payload["surprise"] == pytest.approx(0.81)
    assert _kinds("experience")[0]["prediction"]["confidence"] == 0.9


def test_a_tool_prediction_is_refused_unresolved_and_nothing_is_recorded():
    exp = _ledger_exp(about="tool:calc")
    assert passes.compile_experience(exp) == []
    assert _kinds("experience") == [] and _kinds("compiler_emit") == []
    assert [r["reason"] for r in _kinds("compiler_refused")] == ["prediction is not a task prediction"]
    predictions.resolve(exp.prediction["id"], {"success": True})  # still open for the registry


@pytest.mark.parametrize("tier", ["frontier", "Sealed Vault", "sealed-vault", "sealed"])
def test_sealed_tiers_log_only_the_verdict(tier):
    reason = "expected 42 for hidden case 7"
    d = _ledger_exp().to_dict()
    d["context"] = {"tier": tier}
    d["verifier_result"] = {"passed": False, "score": 0.25, "reason": reason}
    passes.compile_experience(xp.Experience.from_dict(d))
    assert _kinds("experience")[0]["verifier_result"] == {"passed": False}
    assert reason not in json.dumps([e.payload for e in events.iter_events()])


def test_a_practice_tier_keeps_the_verifier_reason():
    d = _ledger_exp().to_dict()
    d["context"] = {"tier": "practice"}
    passes.compile_experience(xp.Experience.from_dict(d))
    assert _kinds("experience")[0]["verifier_result"]["reason"] == "r"


def test_an_experience_hands_out_copies_at_every_depth():
    exp = _exp(actions=[{"tool": "a", "input": {"deep": {"x": [1]}}, "output": "", "ok": False}])
    exp.actions[0]["input"]["deep"]["x"].append(2)
    exp.actions[0]["ok"] = True
    exp.context["k"] = "v"
    assert exp.actions[0] == {"tool": "a", "input": {"deep": {"x": [1]}}, "output": "", "ok": False}
    assert exp.context == {}


def test_a_cycle_is_refused_up_front():
    ctx: dict = {}
    ctx["self"] = ctx
    d = _exp().to_dict()
    d["context"] = ctx
    with pytest.raises(ValueError):
        xp.Experience.from_dict(d)


def test_compiling_again_lands_nothing_new_and_says_why():
    exp = _exp()
    first = passes.compile_experience(exp)
    again = passes.compile_experience(exp)
    assert all(e.landed for e in first)
    assert not any(e.landed for e in again)
    assert all(e.reason.startswith("already landed") for e in again)
    assert len(_kinds("experience")) == 1


def test_many_distinct_failures_land_at_most_ten_beliefs_and_a_storm_keeps_its_count():
    acts = [{"tool": "t", "input": {"i": i}, "output": f"error {i}", "ok": False} for i in range(30)]
    acts += [{"tool": "storm", "input": {}, "output": "503", "ok": False}] * 5
    passes.compile_experience(_exp(actions=acts))
    assert len(beliefs.find(predicate="failed_with")) == 10
    exp2 = _exp(actions=[{"tool": "storm", "input": {}, "output": "503", "ok": False}] * 5)
    passes.compile_experience(exp2)
    storm = [p for p in _kinds("compiler_emit") if p.get("evidence", {}).get("tool") == "storm"]
    assert storm[-1]["evidence"]["count"] == 5


def test_a_recovered_failure_still_lets_the_real_failure_be_guarded():
    acts = [{"tool": "fetch", "input": {"id": 7}, "output": "timeout", "ok": False},
            {"tool": "fetch", "input": {"id": 7}, "output": "row", "ok": True},
            {"tool": "parse", "input": {"s": "row"}, "output": "bad row", "ok": False}]
    exp = _exp(actions=acts)
    assert passes.outcome_analysis(exp)["decisive_step"] == 2
    cand = [e for e in passes.compile_experience(exp) if e.kind == "skill_candidate"]
    assert cand and cand[0].landed and cand[0].payload["step"] == 2


def test_the_candidates_digest_is_the_compilers_digest_for_mixed_keys():
    tool_input = {1: "a", "b": [{2: "x", "y": None}]}
    exp = _exp(actions=[{"tool": "x", "input": tool_input, "output": "no", "ok": False}])
    cand = next(e for e in passes.compile_experience(exp) if e.kind == "skill_candidate")
    assert cand.landed, cand.reason
    ns: dict = {}
    exec(cand.payload["code"], ns)  # noqa: S102 - our own generated code
    assert ns["input_digest"](tool_input) == xp.digest(tool_input)
    assert ns["seen_failing"]("x", tool_input)


@pytest.mark.parametrize("code,test,why", [
    ("import sys\nF = sys._getframe(1)\ndef f():\n    return 1\n", "def test_f():\n    assert f() == 1\n",
     "frames are out of reach"),
    ("import gc\nO = gc.get_objects()\ndef f():\n    return 1\n", "def test_f():\n    assert f() == 1\n",
     "gc is out of reach"),
    ("try:\n    raise ValueError\nexcept ValueError as e:\n    F = e.__traceback__.tb_frame\n",
     "def test_f():\n    pass\n", "a traceback's frame is out of reach"),
    ("def f():\n    globals()['test_b'].__code__ = (lambda: None).__code__\n",
     "def test_a():\n    f()\ndef test_b():\n    assert False\n", "code cannot rewrite a test"),
    ("def f():\n    globals()['test_b'] = lambda: None\n",
     "def test_a():\n    f()\ndef test_b():\n    assert False\n", "code cannot swap a test"),
    ("def f():\n    return 1\n", "def test_f():\n    global f\n    f = lambda: 2\n    assert f() == 2\n",
     "a test cannot rebind the code through global"),
    ("def f():\n    return 1\n", "def test_f():\n    globals()['f'] = lambda: 2\n    assert f() == 2\n",
     "a test cannot rebind the code through globals()"),
    ("import os\nos._exit(0)\n", "def test_f():\n    pass\n", "exiting early is not passing"),
    ("import ctypes\n", "def test_f():\n    pass\n", "ctypes is out of reach"),
])
def test_skill_harness_refuses_forgeries(code, test, why):
    ok, _ = passes.run_skill_test(code, test, timeout_s=10)
    assert ok is False, why


def test_skill_harness_passes_an_honest_candidate():
    ok, why = passes.run_skill_test("def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n")
    assert ok is True, why


def test_skill_harness_uses_the_sandbox_when_it_is_present(monkeypatch):
    calls = []

    def fake_sandbox(script, *, stdin, timeout_s):
        calls.append(timeout_s)
        return passes._run_isolated(script, stdin, timeout_s)

    monkeypatch.setattr(passes, "_sandbox", lambda: fake_sandbox)
    ok, why = passes.run_skill_test("def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n")
    assert ok is True, why
    assert calls == [passes.SKILL_TIMEOUT_S]


def test_a_sandbox_that_fails_is_a_refusal(monkeypatch):
    def broken(script, *, stdin, timeout_s):
        raise OSError("no sandbox today")

    monkeypatch.setattr(passes, "_sandbox", lambda: broken)
    assert passes.run_skill_test("def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n")[0] is False
