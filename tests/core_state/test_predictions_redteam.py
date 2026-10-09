"""Red-team proofs for CS5 prediction ledger. A test marked BREAK fails today and shows a real break."""
import asyncio
import json
import threading

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state import predictions as P
from tanishi.core_state.events import iter_events, verify_chain
from tanishi.tools import registry as R
from tanishi.tools.registry import ToolDefinition, ToolRegistry


def _reg(name="t", handler=lambda: "ok", **kw):
    reg = ToolRegistry()
    reg.register(ToolDefinition(name=name, description="d", input_schema={"type": "object"}, handler=handler, **kw))
    return reg


def _raw(sql, args=()):
    conn = open_db()
    try:
        migrate(conn)
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


# --- wrong beliefs -----------------------------------------------------------------------------------------------

def test_r1_user_denial_is_not_evidence_the_tool_is_unreliable():  # BREAK
    """A tool that needs approval, called with no approval callback, is 'denied'. Twenty denials drag the
    tool's forecast from 0.75 to ~0.13 although the tool never ran."""
    reg = _reg("danger", requires_approval=True)
    before, _ = P.tool_forecast("danger")
    for _ in range(20):
        assert not asyncio.run(reg.execute("danger", {})).success
    after, _ = P.tool_forecast("danger")
    assert after >= before - 0.05, f"forecast fell {before:.2f} -> {after:.2f} from calls that never ran the tool"


def test_r2_event_log_outage_is_not_evidence_the_tool_is_unreliable(monkeypatch):  # BREAK
    """When emit('tool_call') fails the tool does not run; the call is still resolved success=False and scored."""
    reg = _reg("fine")
    before, _ = P.tool_forecast("fine")

    def boom(*a, **k):
        raise OSError("disk full")
    with monkeypatch.context() as m:  # not monkeypatch.undo(): that would also undo the temp HOME
        m.setattr(R, "emit", boom)
        for _ in range(20):
            asyncio.run(reg.execute("fine", {}))
    after, _ = P.tool_forecast("fine")
    assert after >= before - 0.05, f"forecast fell {before:.2f} -> {after:.2f} from calls that never ran the tool"


def test_r3_hallucinated_tool_names_do_not_enter_the_tool_calibration():  # BREAK
    """Unknown tool names get predicted at 0.75 and resolved as failures, which worsens calibration('tool:')
    for reasons that have nothing to do with how well she knows her real tools, and adds a row per invented name."""
    reg = _reg("real")
    for i in range(10):
        asyncio.run(reg.execute(f"no_such_tool_{i}", {}))
    assert P.calibration("tool:")["n"] == 0


# --- crashes and poison ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad_actual", ["5", "[1]", '"x"', "null"])
def test_r4_one_malformed_resolved_row_blinds_the_forecaster_and_calibration(bad_actual):  # BREAK
    """actual only has to be valid JSON (CHECK json_valid), so a non-object makes .get() raise for every caller."""
    pid = P.predict("tool:poison", {"success": True}, 0.5)
    _raw("UPDATE predictions SET actual = ?, resolved_at = '2026-10-09T00:00:00+00:00', score = 0.25 WHERE id = ?",
         (bad_actual, pid))
    P.tool_forecast("poison")  # must not raise
    P.calibration("tool:")


@pytest.mark.parametrize("hours", [float("nan"), float("inf"), 1e12, -1.0])
def test_r6_stale_helper_survives_odd_hours(hours):
    P.predict("tool:x", {"success": True}, 0.5)
    assert isinstance(P.unresolved_older_than(hours), list)


# --- performance -------------------------------------------------------------------------------------------------

def test_r7_tool_forecast_uses_an_index():  # BREAK
    """No index on predictions: every tool call does a full table scan in tool_forecast."""
    conn = open_db()
    try:
        migrate(conn)
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT actual FROM predictions WHERE about = ? AND resolved_at IS NOT NULL "
            "ORDER BY resolved_at DESC LIMIT 50", ("tool:x",)))
    finally:
        conn.close()
    assert "SCAN" not in plan or "USING" in plan, plan


# --- held --------------------------------------------------------------------------------------------------------

def test_h1_secrets_never_reach_the_row_or_the_events():
    key = "sk-ant-api03-" + "A" * 40
    pid = P.predict("tool:x", {"success": True, "note": key, "api_key": "hunter2hunter2"}, 0.5)
    P.resolve(pid, {"success": True, "echo": key})
    conn = open_db()
    try:
        row = " ".join(map(str, conn.execute("SELECT * FROM predictions").fetchone()))
    finally:
        conn.close()
    assert key not in row and "hunter2" not in row
    assert key not in json.dumps([e.payload for e in iter_events()], default=str)


def test_h2_cyclic_and_deep_expected_are_handled():
    d: dict = {"success": True}
    d["self"] = d
    P.predict("tool:x", d, 0.5)
    deep: dict = {"success": True}
    cur = deep
    for _ in range(5000):
        cur["n"] = {}
        cur = cur["n"]
    P.predict("tool:x", deep, 0.5)


def test_h3_concurrent_predict_and_resolve_keep_the_chain_and_count():
    ids = [P.predict("tool:c", {"success": True}, 0.5) for _ in range(8)]
    errs = []

    def go(pid):
        try:
            P.resolve(pid, {"success": True})
            P.predict("tool:c", {"success": True}, 0.5)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go, args=(i,)) for i in ids]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and P.calibration("tool:c")["n"] == 8
    assert len(P.unresolved_older_than(-1)) == 8
    conn = open_db()
    try:
        assert verify_chain(conn)[0]
    finally:
        conn.close()
