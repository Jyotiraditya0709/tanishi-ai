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
