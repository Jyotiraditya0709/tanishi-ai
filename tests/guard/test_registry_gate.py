"""The Warden gate in ToolRegistry.execute() (node W1).

guard_check is monkeypatched: no real Warden runs. The switch is forced through
registry._GUARD_ON, not the TANISHI_GUARD env var, which is read once at import.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from tanishi.guard import Decision
from tanishi.tools import registry
from tanishi.tools.registry import ToolDefinition, ToolRegistry


def _registry(ran: list) -> ToolRegistry:
    def handler(**kwargs):
        ran.append(kwargs)
        return "done"

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="probe", description="test tool", input_schema={}, handler=handler))
    return reg


@pytest.fixture
def guard(monkeypatch):
    """Turn the gate on; returns (calls, set_decision)."""
    calls: list[tuple] = []
    state = {"d": Decision("allow", "ok")}

    def fake_check(tool_name, args, actor="tanishi", session_id=None):
        calls.append((tool_name, args, actor, session_id))
        return state["d"]

    monkeypatch.setattr(registry, "_GUARD_ON", True)
    monkeypatch.setattr(registry, "guard_check", fake_check)

    def set_decision(decision: str, reason: str = "policy"):
        state["d"] = Decision(decision, reason)

    return calls, set_decision


def test_deny_refuses_and_never_runs_the_tool(guard):
    calls, set_decision = guard
    set_decision("deny", "tier 3 forbidden")
    ran: list = []
    result = asyncio.run(_registry(ran).execute("probe", {"x": 1}))
    assert result.success is False
    assert "Warden denied" in result.error
    assert "tier 3 forbidden" in result.error
    assert ran == []
    assert calls and calls[0][0] == "probe" and calls[0][1] == {"x": 1}


def test_allow_runs_the_tool(guard):
    calls, set_decision = guard
    set_decision("allow")
    ran: list = []
    result = asyncio.run(_registry(ran).execute("probe", {"x": 1}))
    assert result.success is True
    assert result.output == "done"
    assert ran == [{"x": 1}]
    assert len(calls) == 1


def test_ask_without_approver_is_denied(guard):
    _, set_decision = guard
    set_decision("ask")
    ran: list = []
    result = asyncio.run(_registry(ran).execute("probe", {}))
    assert result.success is False
    assert "requires approval" in result.error
    assert ran == []


def test_ask_with_approver_that_says_yes_runs_the_tool(guard):
    _, set_decision = guard
    set_decision("ask")
    ran: list = []
    asked: list = []
    reg = _registry(ran)
    reg.set_approval_callback(lambda name, args: asked.append(name) or True)
    result = asyncio.run(reg.execute("probe", {}))
    assert result.success is True
    assert asked == ["probe"]
    assert ran == [{}]


def test_ask_with_approver_that_says_no_is_denied(guard):
    _, set_decision = guard
    set_decision("ask")
    ran: list = []
    reg = _registry(ran)
    reg.set_approval_callback(lambda name, args: False)
    result = asyncio.run(reg.execute("probe", {}))
    assert result.success is False
    assert ran == []


def test_actor_and_session_reach_the_guard(guard):
    calls, _ = guard
    asyncio.run(_registry([]).execute("probe", {}, actor="builder", session_id="s-1"))
    assert calls[0][2:] == ("builder", "s-1")


def test_gate_off_runs_without_calling_the_guard(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("guard_check must not be called when the gate is off")

    monkeypatch.setattr(registry, "_GUARD_ON", False)
    monkeypatch.setattr(registry, "guard_check", boom)
    ran: list = []
    result = asyncio.run(_registry(ran).execute("probe", {"x": 2}))
    assert result.success is True
    assert ran == [{"x": 2}]


@pytest.mark.parametrize("value, expected", [(None, False), ("", False), ("0", False), ("1", True), (" On ", True)])
def test_switch_reads_tanishi_guard_once_at_import(value, expected):
    # A fresh interpreter, so this suite's already-imported registry module is left alone.
    env = {k: v for k, v in os.environ.items() if k != "TANISHI_GUARD"}
    if value is not None:
        env["TANISHI_GUARD"] = value
    out = subprocess.run(
        [sys.executable, "-c", "import tanishi.tools.registry as r; print(r._GUARD_ON)"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == str(expected)
