"""Builder tests for CS5 (the exam is test_predictions.py): edge cases of the ledger and an evaluation of
tool_forecast(), the day-one forecaster that ToolRegistry.execute() uses."""
import asyncio
import json
import random
import sqlite3
import threading

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state import predictions as P
from tanishi.core_state.events import iter_events, task_scope


def _rows():
    conn = open_db()
    try:
        migrate(conn)
        return conn.execute("SELECT id, about, expected, actual, resolved_at, score FROM predictions").fetchall()
    finally:
        conn.close()


def test_expected_without_success_is_refused_and_nothing_written():
    with pytest.raises(ValueError):
        P.predict("tool:x", {"latency_ms": 3}, 0.5)
    with pytest.raises(TypeError):
        P.predict("tool:x", {"success": 1}, 0.5)
    with pytest.raises(TypeError):
        P.predict("tool:x", {"success": True}, True)
    with pytest.raises(ValueError):
        P.predict("tool:x", {"success": True, "latency_ms": float("nan")}, 0.5)
    assert _rows() == []


def test_actual_with_non_bool_success_is_refused_and_prediction_survives():
    pid = P.predict("tool:x", {"success": True}, 0.5)
    with pytest.raises(TypeError):
        P.resolve(pid, {"success": "yes"})
    with pytest.raises(ValueError):
        P.resolve(pid, {"success": True, "latency_ms": float("inf")})
    assert P.resolve(pid, {"success": True}) == pytest.approx(0.25)


def test_double_resolve_raises_already_resolved_and_keeps_first_score():
    pid = P.predict("tool:x", {"success": True}, 0.9)
    P.resolve(pid, {"success": True})
    with pytest.raises(P.AlreadyResolvedError):
        P.resolve(pid, {"success": False})
    (row,) = _rows()
    assert json.loads(row[3]) == {"success": True} and row[5] == pytest.approx(0.01)


def test_concurrent_resolves_score_exactly_once():
    pid = P.predict("tool:race", {"success": True}, 0.5)
    P.calibration("")  # migrate once before the threads start
    wins, losses = [], []

    def go():
        try:
            wins.append(P.resolve(pid, {"success": True}))
        except P.AlreadyResolvedError:
            losses.append(1)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(losses) == 7
    assert len(list(iter_events(kind=P.RESOLVE_EVENT))) == 1


def test_secrets_are_redacted_in_the_table_too():
    secret = "sk-ant-api03-" + "B" * 40
    pid = P.predict("tool:x", {"success": True, "note": secret}, 0.5)
    P.resolve(pid, {"success": True, "note": secret})
    assert secret not in repr(_rows())


def test_events_carry_the_task_and_session():
    with task_scope("task-1", "sess-1"):
        pid = P.predict("tool:x", {"success": True}, 0.5)
        P.resolve(pid, {"success": False})
    evs = [e for e in iter_events() if e.kind in (P.PREDICT_EVENT, P.RESOLVE_EVENT)]
    assert [e.kind for e in evs] == [P.PREDICT_EVENT, P.RESOLVE_EVENT]
    assert all(e.payload["id"] == pid and e.payload["task_id"] == "task-1" and e.session_id == "sess-1" for e in evs)
    assert evs[1].payload["score"] == pytest.approx(0.25)


def test_a_broken_event_log_does_not_lose_the_prediction(monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("log down")

    monkeypatch.setattr(P, "emit", boom)
    pid = P.predict("tool:x", {"success": True}, 0.5)
    assert P.resolve(pid, {"success": True}) == pytest.approx(0.25)


def test_calibration_bins_report_forecast_and_outcome():
    for ok in (True, True, True, False):
        P.resolve(P.predict("cal:b", {"success": True}, 0.75), {"success": ok})
    P.resolve(P.predict("cal:b", {"success": False}, 1.0), {"success": False})  # forecast 0.0
    cal = P.calibration("cal:b")
    assert cal["n"] == 5 and len(cal["bins"]) == P.N_BINS
    (b7,) = [b for b in cal["bins"] if b["lo"] == pytest.approx(0.7)]
    assert b7["n"] == 4 and b7["mean_forecast"] == pytest.approx(0.75) and b7["success_rate"] == pytest.approx(0.75)
    assert cal["bins"][0]["n"] == 1 and cal["bins"][0]["success_rate"] == 0.0
    assert all(b["mean_forecast"] is None for b in cal["bins"] if b["n"] == 0)


def test_forecast1_lands_in_the_top_bin():
    P.resolve(P.predict("cal:top", {"success": True}, 1.0), {"success": True})
    assert P.calibration("cal:top")["bins"][-1]["n"] == 1


def test_unresolved_older_than_takes_a_custom_age():
    pid = P.predict("tool:x", {"success": True}, 0.5)
    assert [r["id"] for r in P.unresolved_older_than(0)] == [pid]
    assert P.unresolved_older_than() == []


def test_tool_forecast_starts_at_the_prior_and_learns_latency():
    assert P.tool_forecast("t") == (pytest.approx(P.PRIOR_SUCCESS), None)
    for ms in (10.0, 20.0, 300.0):
        P.resolve(P.predict("tool:t", {"success": True}, 0.5), {"success": True, "latency_ms": ms})
    p, latency = P.tool_forecast("t")
    assert p > P.PRIOR_SUCCESS and latency == pytest.approx(20.0)
    assert P.tool_forecast("other") == (pytest.approx(P.PRIOR_SUCCESS), None)


def test_rows_with_a_non_object_actual_or_expected_are_skipped_not_counted():
    good = P.predict("tool:s", {"success": True}, 0.5)
    P.resolve(good, {"success": True})
    bad = P.predict("tool:s", {"success": True}, 0.5)
    odd = P.predict("tool:s", {"success": True}, 0.5)
    P.resolve(odd, {"success": False})
    conn = open_db()
    try:
        conn.execute("UPDATE predictions SET actual = '[0]', resolved_at = ?, score = 1.0 WHERE id = ?",
                     (P._now(), bad))
        conn.execute("UPDATE predictions SET expected = '7' WHERE id = ?", (odd,))
        conn.commit()
    finally:
        conn.close()
    # the forecaster reads only `actual`: the bad row is skipped, the odd one (bad expected) still counts
    assert P.tool_forecast("s")[0] == pytest.approx((1 + P.PRIOR_SUCCESS * P.PRIOR_WEIGHT) / (2 + P.PRIOR_WEIGHT))
    cal = P.calibration("tool:s")
    assert cal["n"] == 1 and cal["brier"] == pytest.approx(0.25)


def test_stale_helper_with_minus_infinity_lists_every_unresolved_prediction():
    pid = P.predict("tool:x", {"success": True}, 0.5)
    assert [r["id"] for r in P.unresolved_older_than(float("-inf"))] == [pid]
    assert P.unresolved_older_than(float("nan")) == [] and P.unresolved_older_than(float("inf")) == []


def test_registry_prediction_carries_forecast_and_actual_latency():
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="t", input_schema={"type": "object"}, handler=lambda: "ok"))
    asyncio.run(reg.execute("t", {}))
    asyncio.run(reg.execute("t", {}))
    first, second = sorted(_rows(), key=lambda r: r[4])
    assert json.loads(first[2]) == {"success": True, "latency_ms": None}
    assert isinstance(json.loads(second[2])["latency_ms"], float)
    assert isinstance(json.loads(first[3])["latency_ms"], float)


def test_registry_still_runs_the_tool_when_the_ledger_is_down(monkeypatch):
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    def boom(*a, **k):
        raise sqlite3.OperationalError("ledger down")

    monkeypatch.setattr(P, "predict", boom)
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="t", input_schema={"type": "object"}, handler=lambda: "ok"))
    assert asyncio.run(reg.execute("t", {})).success


def test_registry_leaves_a_cancelled_call_unscored():
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    async def slow():
        await asyncio.sleep(10)

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="slow", description="s", input_schema={"type": "object"}, handler=slow,
                                timeout_override=0))

    async def run():
        task = asyncio.create_task(reg.execute("slow", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    (row,) = _rows()
    assert row[3] is None and row[5] is None and P.calibration("tool:")["n"] == 0
    assert [r["id"] for r in P.unresolved_older_than(0)] == [row[0]]


def test_registry_leaves_a_denied_call_unscored():
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="t", input_schema={"type": "object"}, handler=lambda: "ok",
                                requires_approval=True))
    reg.set_approval_callback(lambda name, args: False)
    assert not asyncio.run(reg.execute("t", {})).success
    assert [(r[3], r[5]) for r in _rows()] == [(None, None)]
    reg.set_approval_callback(lambda name, args: True)
    assert asyncio.run(reg.execute("t", {})).success
    assert P.calibration("tool:t")["n"] == 1


def test_registry_scores_a_handler_that_fails():
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    def broken():
        raise RuntimeError("down")

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="t", input_schema={"type": "object"}, handler=broken))
    assert not asyncio.run(reg.execute("t", {})).success
    (row,) = _rows()
    assert json.loads(row[3])["success"] is False and row[5] is not None


# ---------------------------------------------------------------- evaluation: does the forecaster learn?

@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("rate", [0.95, 0.6, 0.2])
def test_eval_tool_forecast_learns_the_success_rate(seed, rate):
    """Simulated tool with a hidden success rate, 60 calls. The learned forecaster must never do worse than a 0.5
    coin or the fixed prior by more than noise (0.02), and must clearly beat the coin (by 0.05) when the rate is far
    from 0.5. Near 0.5 nobody can beat the coin by much: the best possible Brier at 0.6 is 0.24 against 0.25."""
    rng = random.Random(seed)
    coin, fixed = [], []
    for _ in range(60):
        p, _latency = P.tool_forecast("sim")
        ok = rng.random() < rate
        P.resolve(P.predict("tool:sim", {"success": True}, p), {"success": ok})
        coin.append((0.5 - ok) ** 2)
        fixed.append((P.PRIOR_SUCCESS - ok) ** 2)
    learned = P.calibration("tool:sim")["brier"]
    coin_b, fixed_b = sum(coin) / len(coin), sum(fixed) / len(fixed)
    assert learned <= coin_b + 0.02
    assert learned <= fixed_b + 0.02
    if abs(rate - 0.5) >= 0.3:
        assert learned <= coin_b - 0.05
