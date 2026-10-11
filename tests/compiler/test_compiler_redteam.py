"""Red-team proofs for L1 (Learning Compiler passes 1, 3, 4). A test here FAILS today and shows a real break.

Findings and fixes: build/memory/runs/redteam-L1-20261011.md. Each test names its finding (R1, R2, ...).
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from tanishi.compiler import experience as xp
from tanishi.compiler import passes
from tanishi.core_state import beliefs, events, predictions

SECRET = "sk-ant-api03-REDTEAMSECRETVALUE0123456789abcdef"


def _exp(*, passed=False, actions=None, task="sum two numbers", pid=None, expected=True, conf=0.7, context=None):
    if actions is None:
        actions = [{"tool": "calc", "input": {"a": 1}, "output": "division by zero", "ok": False}]
    if pid is None:
        pid = predictions.predict("task:l1-rt", {"success": expected}, conf)
    return xp.Experience(task=task, context=context or {}, actions=actions, outcome={"success": passed},
                         verifier_result={"passed": passed, "score": float(passed), "reason": "r"},
                         cost={"usd": 0.0},
                         prediction={"id": pid, "expected": {"success": expected}, "confidence": conf})


def _logged(kind):
    return list(events.iter_events(kind=kind))


def _by_kind(emissions, kind):
    return [e for e in emissions if e.kind == kind]


# R1 High: the prediction on the Experience is self-reported; the ledger row is never read ------------------------


def test_R1_prediction_must_match_the_ledger_row():
    """Ledger: success at 0.95. Experience claims it expected failure, so a failure scores surprise 0 (hindsight)."""
    pid = predictions.predict("task:l1-rt", {"success": True}, 0.95)
    exp = _exp(pid=pid, expected=False, conf=0.95)  # what the ledger holds is the opposite
    out = passes.compile_experience(exp)
    p1 = _by_kind(out, "outcome")[0]
    real = 0.95 ** 2
    assert not p1.landed or p1.payload["surprise"] == pytest.approx(real), \
        f"surprise {p1.payload['surprise']} came from a forecast the ledger never held (ledger says {real})"


def test_R1_a_prediction_id_not_in_the_ledger_must_not_land_anything():
    exp = _exp(pid="pred_never_written", expected=True, conf=0.99)
    out = passes.compile_experience(exp)
    assert not any(e.landed and e.gain and e.gain > 0 for e in out), "gain from a prediction nobody wrote"


def test_R1_compile_must_not_resolve_an_unrelated_ledger_row_with_a_task_outcome():
    """A tool-call prediction id on an Experience is resolved with the *task's* outcome, so the tool call's own
    result (success, latency_ms) can no longer be written to its row."""
    tool_pid = predictions.predict("tool:calc", {"success": True}, 0.9)
    passes.compile_experience(_exp(pid=tool_pid))  # the task failed; the tool call itself may have been fine
    try:
        predictions.resolve(tool_pid, {"success": True, "latency_ms": 12})
    except predictions.AlreadyResolvedError:
        pytest.fail("the compiler resolved a tool:calc prediction with the task's verdict; the tool's own result is lost")


# R2 Medium: identity is the prediction id alone -----------------------------------------------------------------


def test_R2_two_attempts_with_one_prediction_id_are_both_recorded():
    pid = predictions.predict("task:l1-rt", {"success": True}, 0.7)
    a = _exp(pid=pid, actions=[{"tool": "alpha", "input": 1, "output": "boom a", "ok": False}])
    b = _exp(pid=pid, actions=[{"tool": "beta", "input": 2, "output": "boom b", "ok": False}], task="second task")
    passes.compile_experience(a)
    passes.compile_experience(b)
    recorded = [e.payload["task"] for e in _logged("experience")]
    assert "second task" in recorded, "attempt b was never recorded: matched to a's record by prediction id"


def test_R2_belief_evidence_points_at_the_experience_that_holds_the_failed_step():
    pid = predictions.predict("task:l1-rt", {"success": True}, 0.7)
    passes.compile_experience(_exp(pid=pid, actions=[{"tool": "alpha", "input": 1, "output": "a", "ok": False}]))
    passes.compile_experience(_exp(pid=pid, task="second",
                                   actions=[{"tool": "beta", "input": 2, "output": "b", "ok": False}]))
    for b in beliefs.find(subject="tool:beta"):
        for ev in beliefs.evidence_for(b.id):
            tools = [a["tool"] for e in _logged("experience") if e.id == ev.event_id
                     for a in e.payload["actions"]]
            assert "beta" in tools, "the belief about tool beta cites an experience with no beta action"


def test_R2_find_recorded_does_not_adopt_an_event_from_another_actor():
    exp = _exp()
    forged = events.emit("experience", {"prediction": {"id": exp.prediction["id"]}, "task": "forged"},
                         actor="intruder")
    assert xp.find_recorded(exp) != forged, "an event written by another actor became this attempt's record"


# R3 High: secrets and inputs in skill candidates and beliefs ---------------------------------------------------


def test_R3_skill_candidate_does_not_carry_a_secret_from_a_failed_output():
    exp = _exp(actions=[{"tool": "http", "input": {"u": "x"}, "output": f"401 for Bearer {SECRET}", "ok": False}])
    cand = _by_kind(passes.compile_experience(exp), "skill_candidate")
    assert cand, "no candidate"
    assert SECRET not in json.dumps(cand[0].payload), "raw secret in the returned skill candidate (code and test)"


def test_R3_skill_candidate_keeps_no_input_text():
    """Decision 0026 item 10 says inputs are kept as a digest, never as text. The test source embeds the input."""
    private = "my private note about Alice diagnosis"
    exp = _exp(actions=[{"tool": "notes", "input": {"text": private}, "output": "write failed", "ok": False}])
    cand = _by_kind(passes.compile_experience(exp), "skill_candidate")[0]
    assert private not in json.dumps(cand.payload), "tool input text is inside the candidate's test"
    logged = json.dumps([e.payload for e in _logged("compiler_emit")])
    assert private not in logged, "tool input text persisted in the event log inside the skill test"


@pytest.mark.parametrize("output", [
    {"token": ["abcdefSECRETvalue1", "abcdefSECRETvalue2"]},
    {"credentials": {"user": "alice", "pw": "hunter2-REDTEAM"}},
])
def test_R3_belief_object_does_not_leak_structured_secrets(output):
    exp = _exp(actions=[{"tool": "login", "input": {}, "output": output, "ok": False}])
    passes.compile_experience(exp)
    stored = json.dumps([b.object for b in beliefs.find(subject="tool:login")])
    assert "SECRET" not in stored and "hunter2" not in stored and "alice" not in stored, stored


# R4 Medium: the item that is checked is not the item that is stored -------------------------------------------


def test_R4_a_landed_skill_candidate_replays_from_its_logged_form():
    """emit() redacts and clips; the check ran on the raw item. What the log holds must still pass its own test."""
    exp = _exp(actions=[{"tool": "t" * 3000, "input": {"n": 1}, "output": "bad", "ok": False}])
    cand = _by_kind(passes.compile_experience(exp), "skill_candidate")[0]
    assert cand.landed
    rows = [e.payload for e in _logged("compiler_emit") if e.payload.get("kind") == "skill_candidate"]
    logged = rows[0]["payload"]
    ok, why = passes.run_skill_test(logged["code"], logged["test"])
    assert ok, f"landed candidate does not pass its test as stored: {why}"


# R5 Medium: the skill-test check can be faked ------------------------------------------------------------------


def test_R5_code_cannot_forge_the_pass_marker_through___main__():
    code = ("import __main__, os, sys\n"
            "sys.stdout.write('\\n' + __main__.src['marker'] + ' 3\\n'); sys.stdout.flush(); os._exit(0)\n")
    ok, _ = passes.run_skill_test(code, "def test_fails():\n    assert False\n")
    assert not ok, "candidate code read the marker from the driver and printed its own success line"


def test_R5_test_must_not_shadow_the_code_it_tests():
    ok, _ = passes.run_skill_test("def f():\n    return 1\n",
                                  "def f():\n    return 2\ndef test_f():\n    assert f() == 2\n")
    assert not ok, "the test redefined f, then tested its own f"


def test_R5_tests_must_come_from_the_test_not_the_code():
    ok, _ = passes.run_skill_test("def test_self_graded():\n    pass\n", "X = 1\n")
    assert not ok, "the code supplied its own test_* and the 'test' was a bare assignment"


# R6 Medium: a recovered transient failure becomes a guard against a call that worked ---------------------------

_RECOVERED = [{"tool": "fetch", "input": {"id": 7}, "output": "timeout", "ok": False},
              {"tool": "fetch", "input": {"id": 7}, "output": "row 7", "ok": True},
              {"tool": "answer", "input": {}, "output": "wrong", "ok": True}]


def test_R6_guard_does_not_flag_an_input_that_later_succeeded():
    out = passes.compile_experience(_exp(actions=[dict(a) for a in _RECOVERED]))
    for c in _by_kind(out, "skill_candidate"):
        ns: dict = {}
        exec(c.payload["code"], ns)  # noqa: S102 - our own generated code, test only
        assert not ns["seen_failing"]("fetch", {"id": 7}), "guard blocks the exact call that succeeded on retry"


def test_R6_decisive_step_is_not_a_failure_the_agent_recovered_from():
    assert passes.outcome_analysis(_exp(actions=[dict(a) for a in _RECOVERED]))["decisive_step"] == 2


# R7 Medium: Experience is only shallowly frozen -------------------------------------------------------------


def test_R7_a_recorded_verdict_cannot_be_changed_before_compiling():
    exp = _exp(passed=False)
    xp.record(exp)
    exp.verifier_result["passed"] = True  # frozen=True guards the attribute, not the dict inside it
    out = passes.compile_experience(exp)
    stored = _logged("experience")[0].payload["verifier_result"]["passed"]
    assert _by_kind(out, "outcome")[0].payload["success"] == stored


def test_R7_the_callers_own_dict_cannot_change_a_built_experience():
    verdict = {"passed": False, "score": 0.0, "reason": "r"}
    pid = predictions.predict("task:l1-rt", {"success": True}, 0.7)
    exp = xp.Experience(task="t", context={}, actions=[], outcome={}, verifier_result=verdict, cost={},
                        prediction={"id": pid, "expected": {"success": True}, "confidence": 0.7})
    verdict["passed"] = True
    assert exp.passed is False


# R8 Medium: check-then-act on the record --------------------------------------------------------------------


def test_R8_concurrent_compiles_of_one_attempt_write_one_record_and_one_belief(monkeypatch):
    exp = _exp()
    gate = threading.Barrier(2, timeout=10)
    real = xp.find_recorded

    def slow_find(e):
        r = real(e)
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return r

    monkeypatch.setattr(xp, "find_recorded", slow_find)
    ts = [threading.Thread(target=passes.compile_experience, args=(exp,)) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(_logged("experience")) == 1
    assert len(beliefs.find(subject="tool:calc")) == 1


def test_R8_compiling_the_same_experience_twice_does_not_duplicate_beliefs():
    exp = _exp()
    passes.compile_experience(exp)
    passes.compile_experience(exp)
    assert len(beliefs.find(subject="tool:calc")) == 1, "each re-compile adds another copy of the same belief"


# R9 Low: input shapes that cost the learning ----------------------------------------------------------------


def test_R9_mixed_type_keys_are_refused_up_front_or_compile_fully():
    try:
        exp = _exp(actions=[{"tool": "x", "input": {}, "output": {1: "a", "b": 2}, "ok": False}])
    except (ValueError, TypeError):
        return  # refused up front is fine
    out = passes.compile_experience(exp)
    failed = _logged("compiler_pass_failed")
    assert not failed, f"passes died on a record the constructor accepted: {[e.payload for e in failed]}"
    assert out


def test_R9_deeply_nested_input_is_a_value_error_not_a_recursion_error():
    deep: dict = {}
    cur = deep
    for _ in range(5000):
        cur["k"] = {}
        cur = cur["k"]
    with pytest.raises((ValueError, TypeError)):
        _exp(context=deep)


def test_R9_a_failed_action_with_no_input_still_yields_a_candidate():
    exp = _exp(actions=[{"tool": "ping", "output": "refused", "ok": False}])
    cand = _by_kind(passes.compile_experience(exp), "skill_candidate")
    assert cand and cand[0].landed, [(c.landed, c.reason) for c in cand]


def test_R9_a_retry_storm_is_one_observation_not_two_hundred_beliefs():
    actions = [{"tool": "flaky", "input": {}, "output": "503", "ok": False} for _ in range(200)]
    passes.compile_experience(_exp(actions=actions))
    assert len(beliefs.find(subject="tool:flaky")) <= 1


# R10 Low (perf): find_recorded is a full scan ---------------------------------------------------------------


def test_R10_finding_a_record_does_not_decode_every_past_experience():
    for i in range(1500):
        events.emit("experience", {"prediction": {"id": f"pred_old{i}"}, "task": "x" * 200,
                                   "actions": [{"tool": "t", "input": "i" * 500, "output": "o" * 500, "ok": True}] * 5},
                    actor=xp.ACTOR)
    exp = _exp(passed=True, actions=[])
    t0 = time.perf_counter()
    xp.find_recorded(exp)
    took = time.perf_counter() - t0
    assert took < 0.05, f"find_recorded took {took * 1000:.0f} ms over 1500 experiences: it decodes every one"
