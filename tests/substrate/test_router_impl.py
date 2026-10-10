"""SUB3 implementer's tests: behaviour the exam leaves open (refusal shape, event payload, size choice, report_spend).

Names are looked up on the module at call time (`router.X`): the exam reloads the router, which replaces its classes.
"""
import pytest

from tanishi.config import models as M
from tanishi.config.routing import COMPLEXITY_THRESHOLD_TOKENS
from tanishi.core_state import events
from tanishi.guard import Decision, client
from tanishi.substrate import router
from tanishi.substrate.state import Step


@pytest.fixture
def warden(monkeypatch):
    """Gate on, with a fake Warden whose answer the test sets."""
    calls = []
    state = {"answer": "allow"}

    def fake_check(tool_name, args, actor="tanishi", session_id=None):
        calls.append(dict(args))
        return Decision(state["answer"], "fake")

    monkeypatch.setattr(client, "check", fake_check)
    monkeypatch.setattr(router, "_GUARD_ON", True)
    return calls, state


@pytest.fixture
def gate_off(monkeypatch):
    monkeypatch.setattr(router, "_GUARD_ON", False)


def ctx(**kw):
    return router.RouteContext(**kw)


def last_event():
    return list(events.iter_events(kind=router.EVENT_KIND))[-1]


def test_short_step_gets_simple_model_long_step_gets_complex(gate_off):
    assert router.route(Step("a", "hi"), ctx()).model_id == M.CLAUDE_SIMPLE
    long_text = "x" * (COMPLEXITY_THRESHOLD_TOKENS * 4)
    assert router.route(Step("b", long_text), ctx()).model_id == M.CLAUDE_COMPLEX


def test_personal_and_tools_fails_without_waiting(gate_off):
    with pytest.raises(router.RouteRefused) as e:
        router.route(Step("a", "x"), ctx(needs_tools=True, personal=True))
    assert e.value.wait is False
    assert last_event().payload["outcome"] == "fail"


def test_personal_with_local_down_waits(gate_off):
    with pytest.raises(router.RouteRefused) as e:
        router.route(Step("a", "x"), ctx(personal=True, local_up=False))
    assert e.value.wait is True
    assert last_event().payload["outcome"] == "wait"


def test_ask_is_not_allow(warden):
    _, state = warden
    state["answer"] = "ask"
    d = router.route(Step("a", "x"), ctx())
    assert d.model_id == M.OLLAMA_DEFAULT and d.provider == router.LOCAL_PROVIDER


def test_spend_deny_on_tool_step_waits(warden):
    _, state = warden
    state["answer"] = "deny"
    with pytest.raises(router.RouteRefused) as e:
        router.route(Step("a", "x"), ctx(needs_tools=True))
    assert e.value.wait is True


def test_event_records_the_spend_check_but_not_the_step_text(warden):
    router.route(Step("step-7", "a private sentence"), ctx(needs_tools=True))
    ev = last_event()
    assert ev.actor == "router"
    assert ev.payload["step_id"] == "step-7"
    assert ev.payload["spend"]["allowed"] is True
    assert ev.payload["spend"]["estimate"] > 0
    assert "a private sentence" not in str(ev.payload)


def test_event_carries_the_current_task(gate_off):
    with events.task_scope("task-1", "sess-1"):
        router.route(Step("a", "x"), ctx())
    ev = last_event()
    assert ev.payload["task_id"] == "task-1" and ev.session_id == "sess-1"


def test_failed_event_write_means_no_decision(gate_off, monkeypatch):
    def boom(*a, **k):
        raise OSError("db down")

    monkeypatch.setattr(events, "emit", boom)
    with pytest.raises(OSError):
        router.route(Step("a", "x"), ctx())


def test_unknown_model_is_priced_as_the_dearest():
    assert router.estimate_cost("zz-unknown", "hello") == max(
        router.estimate_cost(m, "hello") for m in (M.CLAUDE_SIMPLE, M.CLAUDE_DEFAULT, M.CLAUDE_COMPLEX))


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "1"])
def test_report_spend_rejects_bad_costs(warden, bad):
    with pytest.raises(ValueError):
        router.report_spend(router.RouteDecision(M.CLAUDE_SIMPLE, router.PAID_PROVIDER, "r"), bad)


def test_report_spend_sends_the_actual_cost(warden):
    calls, _ = warden
    router.report_spend(router.RouteDecision(M.CLAUDE_SIMPLE, router.PAID_PROVIDER, "r"), 2)
    assert calls == [{"actual_cost": 2.0}]


def spend_events():
    return list(events.iter_events(kind=router.SPEND_EVENT_KIND))


def test_a_truthy_odd_rule_value_keeps_the_rule_on_and_the_event_says_so(gate_off):
    d = router.route(Step("a", "x"), ctx(personal=True, personal_local_only="no"))
    assert d.model_id == M.OLLAMA_DEFAULT
    assert last_event().payload["personal_local_only"] is True


def test_a_recorded_spend_report_is_an_event(warden):
    router.report_spend(router.RouteDecision(M.CLAUDE_SIMPLE, router.PAID_PROVIDER, "r"), 0.25, step_id="s-9")
    ev = spend_events()[-1]
    assert ev.actor == "router"
    assert ev.payload["actual_cost"] == 0.25 and ev.payload["recorded"] is True
    assert ev.payload["model_id"] == M.CLAUDE_SIMPLE and ev.payload["step_id"] == "s-9"


def test_a_spend_report_the_warden_did_not_take_warns_and_says_not_recorded(warden, caplog):
    _, state = warden
    state["answer"] = "deny"
    with caplog.at_level("WARNING", logger=router.__name__):
        answer = router.report_spend(router.RouteDecision(M.CLAUDE_SIMPLE, router.PAID_PROVIDER, "r"), 0.5)
    assert answer.decision == "deny"
    assert spend_events()[-1].payload["recorded"] is False
    assert any("not recorded" in r.message for r in caplog.records)


def test_a_warden_that_raises_on_report_is_not_recorded(warden, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(client, "check", boom)
    answer = router.report_spend(router.RouteDecision(M.CLAUDE_SIMPLE, router.PAID_PROVIDER, "r"), 0.5)
    assert answer.allowed is False
    assert spend_events()[-1].payload["recorded"] is False


def test_no_spend_event_for_a_local_call_or_with_the_gate_off(gate_off):
    before = len(spend_events())
    router.report_spend(router.RouteDecision(M.CLAUDE_SIMPLE, router.PAID_PROVIDER, "r"), 0.5)
    router.report_spend(router.RouteDecision(M.OLLAMA_DEFAULT, router.LOCAL_PROVIDER, "r"), 0.0)
    assert len(spend_events()) == before


def test_aroute_gives_the_same_decision_and_keeps_the_task_scope(gate_off):
    import asyncio

    async def main():
        with events.task_scope("task-a", "sess-a"):
            return await router.aroute(Step("a", "hi"), ctx())

    assert asyncio.run(main()) == router.route(Step("a", "hi"), ctx())
    evs = list(events.iter_events(kind=router.EVENT_KIND))
    assert evs[-2].payload["task_id"] == "task-a" and evs[-2].session_id == "sess-a"


def test_aroute_raises_the_same_refusal(gate_off):
    import asyncio

    with pytest.raises(router.RouteRefused) as e:
        asyncio.run(router.aroute(Step("a", "x"), ctx(needs_tools=True, personal=True)))
    assert e.value.wait is False


def test_estimate_counts_utf8_bytes_and_survives_a_lone_surrogate(gate_off):
    assert router.estimate_tokens("abcd" * 10) == 10
    assert router.estimate_tokens("é" * 40) == 20  # two bytes each
    assert router.estimate_tokens("\ud800" * 4) == 3
    assert router.route(Step("a", "broken \ud800 text"), ctx()).model_id == M.CLAUDE_SIMPLE


def test_input_tokens_drive_size_and_estimate_when_given(warden):
    calls, _ = warden
    d = router.route(Step("a", "hi"), ctx(needs_tools=True, input_tokens=COMPLEXITY_THRESHOLD_TOKENS * 3))
    assert d.model_id == M.CLAUDE_COMPLEX
    big = calls[-1]["cost_estimate"]
    router.route(Step("b", "hi"), ctx(needs_tools=True))
    assert calls[-1]["cost_estimate"] < big
    assert big == router.estimate_cost(M.CLAUDE_COMPLEX, "hi", COMPLEXITY_THRESHOLD_TOKENS * 3)
    ev = last_event()
    assert ev.payload["input_tokens_given"] is False and ev.payload["input_tokens"] == router.estimate_tokens("hi")


def test_without_input_tokens_the_step_text_is_the_estimate(gate_off):
    assert router.RouteContext().input_tokens is None
    assert router.route(Step("a", "x" * (COMPLEXITY_THRESHOLD_TOKENS * 4)), ctx()).model_id == M.CLAUDE_COMPLEX


@pytest.mark.parametrize("bad, err", [(-1, ValueError), (True, TypeError), (1.5, TypeError), ("100", TypeError)])
def test_bad_input_tokens_are_rejected(bad, err):
    with pytest.raises(err):
        router.RouteContext(input_tokens=bad)
