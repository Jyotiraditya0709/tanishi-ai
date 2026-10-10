"""SUB3 red-team: attacks on the model router. See build/memory/runs/redteam-SUB3-20261010.md.

Each test asserts the CORRECT behaviour. Tests for open breaks are xfail(strict=True): the suite stays green now,
and when a fix lands the test XPASSes and strict mode forces the marker to be removed.
"""
import asyncio
import json
import time

import pytest

from tanishi.config import models as M
from tanishi.config import routing
from tanishi.core_state import events
from tanishi.guard import Decision, client
from tanishi.substrate import router
from tanishi.substrate.state import Step

KEY = "sk-ant-api03-" + "A" * 40
BREAK = pytest.mark.xfail(strict=True, reason="red-team break, see build/memory/runs/redteam-SUB3-20261010.md")


@pytest.fixture
def warden(monkeypatch):
    state = {"answer": "allow", "reason": "fake", "sleep": 0.0, "calls": []}

    def fake_check(tool_name, args, actor="tanishi", session_id=None):
        state["calls"].append(dict(args))
        time.sleep(state["sleep"])
        return Decision(state["answer"], state["reason"])

    monkeypatch.setattr(client, "check", fake_check)
    monkeypatch.setattr(router, "_GUARD_ON", True)
    return state


@pytest.fixture
def gate_off(monkeypatch):
    monkeypatch.setattr(router, "_GUARD_ON", False)


def _events():
    return list(events.iter_events(kind=router.EVENT_KIND))


# --- B1 · route() blocks the event loop on a slow Warden -------------------------------------------------------


@BREAK
def test_b1_route_has_an_async_entry_that_keeps_the_loop_alive(warden):
    """ToolRegistry.execute() runs the Warden check via asyncio.to_thread because a hung Warden (5 s timeout) must not
    freeze the runtime. route() does the same socket call synchronously, so an async caller freezes for the timeout.
    The correct behaviour: an awaitable entry point (`aroute`) that lets other tasks run during the check."""
    warden["sleep"] = 0.4
    assert hasattr(router, "aroute"), "no async entry point; async callers block the loop for up to 5 s"

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        t = asyncio.create_task(ticker())
        await router.aroute(Step("a", "hi"), router.RouteContext())
        t.cancel()
        return ticks

    assert asyncio.run(main()) >= 5


# --- B2 · a malformed "rule" flag silently switches the privacy rule off ---------------------------------------


@pytest.mark.parametrize("flag", [None, 0, "", []])
def test_b2_only_a_real_false_switches_the_personal_rule_off(gate_off, flag):
    """personal_local_only=None (an unset config value) or 0 is falsy, so `context.personal_local_only` is False and a
    personal step goes to the cloud. Switching a privacy rule off must need an explicit `False`; anything else is ON."""
    ctx = router.RouteContext(personal=True, personal_local_only=flag)
    try:
        d = router.route(Step("a", "my diary entry"), ctx)
    except router.RouteRefused:
        return
    assert d.model_id == M.OLLAMA_DEFAULT, f"personal step went to {d.model_id} with personal_local_only={flag!r}"


# --- B3 · the token estimate undercounts non-ASCII text, which skews both the size choice and the spend check ---


@BREAK
def test_b3_estimate_tokens_for_non_ascii_text():
    """len//4 assumes ASCII. CJK, emoji and accented text cost about one token per character or more, so a 4000
    character CJK step is 'estimated' at 1000 tokens: the Warden is asked about a quarter of the real input cost
    and the step goes to the simple model although it is long."""
    assert router.estimate_tokens("字" * 4000) >= 2000


@BREAK
def test_b3_long_non_ascii_step_gets_the_complex_model(gate_off):
    n = routing.COMPLEXITY_THRESHOLD_TOKENS * 2  # 1000 chars of CJK ~ 1000+ tokens, twice the threshold
    d = router.route(Step("a", "字" * n), router.RouteContext())
    assert d.model_id == M.CLAUDE_COMPLEX


# --- B4 · the actual spend report is not in the log and a failed report is silent ------------------------------


def test_b4_report_spend_leaves_a_trace(warden):
    """record_spend is the only thing that keeps the Warden's daily total honest. When it is denied (Warden down: the
    client fails closed and returns deny, it does not raise) report_spend returns the Decision and nothing else: no
    event, no log line, no exception. The cost of a paid call is then in no record at all, and the cap undercounts."""
    d = router.route(Step("a", "hi"), router.RouteContext())
    warden["answer"] = "deny"
    warden["reason"] = "warden unreachable (FileNotFoundError); failing closed"
    before = len(list(events.iter_events()))
    try:
        router.report_spend(d, 0.37)
    except Exception:  # noqa: BLE001 - raising is also a visible failure
        return
    after = list(events.iter_events())[before:]
    assert any("0.37" in json.dumps(e.payload) for e in after), "a lost spend report left no event"


# --- B5 · routing config mutated at runtime is ignored ---------------------------------------------------------


@BREAK
def test_b5_complexity_threshold_is_read_at_call_time(gate_off, monkeypatch):
    """tanishi/config/routing.py says 'Mutated by autoresearch'. The router did `from routing import
    COMPLEXITY_THRESHOLD_TOKENS`, a copy taken at import, so an autoresearch change (or a test that patches the
    module) is never seen. Read `routing.COMPLEXITY_THRESHOLD_TOKENS` on each call."""
    monkeypatch.setattr(routing, "COMPLEXITY_THRESHOLD_TOKENS", 10)
    d = router.route(Step("a", "x" * 400), router.RouteContext())  # 100 tokens, over the patched threshold
    assert d.model_id == M.CLAUDE_COMPLEX


# --- attacks that held (kept as regression tests) --------------------------------------------------------------


def test_secret_in_step_id_and_warden_reason_is_redacted_in_the_event(warden):
    warden["answer"] = "deny"
    warden["reason"] = "over cap for " + KEY
    with pytest.raises(router.RouteRefused):
        router.route(Step(KEY, "hi"), router.RouteContext(needs_tools=True))
    assert KEY not in json.dumps(_events()[-1].payload)


def test_step_text_never_reaches_the_event(gate_off):
    router.route(Step("a", "my private diary: SECRETWORD123"), router.RouteContext(personal=True))
    assert "SECRETWORD123" not in json.dumps(_events()[-1].payload)


@pytest.mark.parametrize("answer", ["ask", "deny"])
def test_tool_step_is_never_sent_local_when_spend_is_not_allowed(warden, answer):
    warden["answer"] = answer
    with pytest.raises(router.RouteRefused) as e:
        router.route(Step("a", "hi"), router.RouteContext(needs_tools=True))
    assert e.value.wait is True


def test_personal_with_rule_on_never_asks_the_warden(warden):
    d = router.route(Step("a", "x" * 5000), router.RouteContext(personal=True))
    assert d.model_id == M.OLLAMA_DEFAULT
    assert warden["calls"] == []


def test_unusable_text_is_a_type_error_and_asks_nobody(warden):
    with pytest.raises(TypeError):
        router.route(Step("a", None), router.RouteContext())  # type: ignore[arg-type]
    assert warden["calls"] == []
