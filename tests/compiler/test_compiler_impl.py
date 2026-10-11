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
