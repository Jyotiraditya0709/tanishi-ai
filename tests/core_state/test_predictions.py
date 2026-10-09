"""Exam for CS5: the prediction ledger (tanishi/core_state/predictions.py).

Written from the spec alone, without reading the implementation. Spec facts used:
  - predict(about, expected: dict, confidence: float) -> str          (prediction id)
  - resolve(prediction_id, actual: dict) -> float                     (the score)
  - calibration(about_prefix) -> dict                                 (Brier score and bins)
  - ToolRegistry.execute() writes a prediction (expected success, expected latency) before each
    tool call and resolves it after
  - the score is the Brier score of the success field; other fields are stored for later scorers
  - a prediction resolved twice raises; unresolved predictions older than 24 hours are listed
    by a helper
  - builds on CS2: the ledger writes to the hash-chained event log

Where the spec is silent the exam pins the reading below (see build/memory/open-problems/CS5-exam-assumptions.md):
  - `confidence` is the confidence that `expected` holds. The forecast probability of success is
    `confidence` when expected["success"] is true and `1 - confidence` when it is false.
    score = (forecast - outcome) ** 2, outcome = 1 if actual["success"] else 0.
  - confidence outside [0, 1] or NaN raises ValueError; blank `about` raises ValueError.
  - an unknown prediction id raises LookupError; a non-dict `actual` raises TypeError or ValueError.
  - calibration() returns a dict with "brier" (mean score of resolved predictions whose `about`
    starts with the prefix, None when there are none) and "bins" (a list of dicts, each with a
    count under "n" or "count"; counts add up to the number of scored predictions).
  - the stale helper is the one public callable in predictions.py whose name contains
    "unresolved", "stale" or "overdue". It takes no required argument and returns ids (or records
    holding the id).
Predictions are read back only through the public interface, plus a scan of the Core State db
for the "latency" field and to age a prediction by 25 hours (the schema is not pinned).
"""
import asyncio
import inspect
import json
import math
import random
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tanishi.core_state import open_db
from tanishi.core_state import predictions as P
from tanishi.core_state.events import iter_events, verify_chain

# ---------------------------------------------------------------- helpers

_ANY_ERROR = Exception  # the spec says "raises" without naming a type

def _count(cal):
    total = 0
    for b in cal["bins"]:
        total += b["n"] if "n" in b else b["count"]
    return total


def _stale_helper():
    names = [n for n, f in inspect.getmembers(P, inspect.isfunction)
             if not n.startswith("_") and f.__module__ == P.__name__
             and any(w in n for w in ("unresolved", "stale", "overdue"))]
    assert names, "predictions.py needs a public helper that lists old unresolved predictions"
    return getattr(P, names[0])


def _stale():
    return _stale_helper()()


def _ids_in(listing):
    return json.dumps(listing, default=str)


def _db_path():
    import os
    return os.environ["TANISHI_CORE_STATE_DB"]


def _age_everything(hours):
    """Move every timestamp in every prediction table back by `hours` (ISO text or epoch floats)."""
    con = sqlite3.connect(_db_path())
    con.execute("PRAGMA foreign_keys=OFF")
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%predict%'")]
        assert tables, "no prediction table found in the Core State db"
        changed = 0
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            for row in con.execute(f'SELECT rowid, * FROM "{t}"').fetchall():
                rid, vals = row[0], row[1:]
                for c, v in zip(cols, vals):
                    new = None
                    if isinstance(v, str):
                        try:
                            dt = datetime.fromisoformat(v)
                        except ValueError:
                            continue
                        new = (dt - timedelta(hours=hours)).isoformat(timespec="microseconds")
                        if dt.tzinfo is None:
                            new = new.replace("+00:00", "")
                    elif isinstance(v, float) and abs(v - datetime.now(UTC).timestamp()) < 86400 * 2:
                        new = v - hours * 3600
                    if new is not None:
                        try:
                            con.execute(f'UPDATE "{t}" SET "{c}"=? WHERE rowid=?', (new, rid))
                            changed += 1
                        except sqlite3.DatabaseError:
                            pass
        con.commit()
        assert changed, "found no timestamp to age"
    finally:
        con.close()


def _all_db_text():
    con = sqlite3.connect(_db_path())
    try:
        out = []
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            out.extend(map(str, con.execute(f'SELECT * FROM "{t}"').fetchall()))
        return "\n".join(out)
    finally:
        con.close()


def _chain_ok():
    conn = open_db()
    try:
        return verify_chain(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------- predict

def test_predict_returns_nonempty_string_id():
    pid = P.predict("tool:web_search", {"success": True}, 0.8)
    assert isinstance(pid, str) and pid


def test_predict_ids_are_unique():
    ids = {P.predict("tool:x", {"success": True}, 0.5) for _ in range(50)}
    assert len(ids) == 50


@pytest.mark.parametrize("conf", [-0.01, 1.01, 2, -1, float("nan"), float("inf")])
def test_predict_rejects_bad_confidence(conf):
    with pytest.raises(ValueError):
        P.predict("tool:x", {"success": True}, conf)


@pytest.mark.parametrize("conf", [0, 1, 0.0, 1.0, 0.5])
def test_predict_accepts_boundary_confidence(conf):
    assert P.predict("tool:x", {"success": True}, conf)


@pytest.mark.parametrize("about", ["", "   "])
def test_predict_rejects_blank_about(about):
    with pytest.raises(ValueError):
        P.predict(about, {"success": True}, 0.5)


def test_predict_rejects_non_dict_expected():
    with pytest.raises((TypeError, ValueError)):
        P.predict("tool:x", "success", 0.5)


def test_predict_rejects_non_numeric_confidence():
    with pytest.raises((TypeError, ValueError)):
        P.predict("tool:x", {"success": True}, "high")


def test_predict_does_not_mutate_expected():
    exp = {"success": True, "latency_ms": 12}
    P.predict("tool:x", exp, 0.5)
    assert exp == {"success": True, "latency_ms": 12}


# ---------------------------------------------------------------- resolve and the Brier score

@pytest.mark.parametrize("conf,success,want", [
    (1.0, True, 0.0),
    (1.0, False, 1.0),
    (0.0, True, 1.0),
    (0.0, False, 0.0),
    (0.5, True, 0.25),
    (0.5, False, 0.25),
    (0.8, True, 0.04),
    (0.8, False, 0.64),
    (0.3, True, 0.49),
    (0.3, False, 0.09),
])
def test_resolve_returns_brier_score_for_expected_success(conf, success, want):
    pid = P.predict("tool:x", {"success": True}, conf)
    assert P.resolve(pid, {"success": success}) == pytest.approx(want)


def test_resolve_expected_failure_flips_forecast():
    # Confident (0.9) the tool will fail; it fails: tiny error. It succeeds: large error.
    ok = P.predict("tool:x", {"success": False}, 0.9)
    assert P.resolve(ok, {"success": False}) == pytest.approx(0.01)
    bad = P.predict("tool:x", {"success": False}, 0.9)
    assert P.resolve(bad, {"success": True}) == pytest.approx(0.81)


def test_resolve_returns_float_in_unit_interval():
    pid = P.predict("tool:x", {"success": True}, 0.7)
    s = P.resolve(pid, {"success": True})
    assert isinstance(s, float) and 0.0 <= s <= 1.0


def test_other_fields_do_not_change_the_score():
    a = P.predict("tool:x", {"success": True, "latency_ms": 10}, 0.6)
    b = P.predict("tool:x", {"success": True}, 0.6)
    sa = P.resolve(a, {"success": True, "latency_ms": 99999, "extra": [1, 2]})
    sb = P.resolve(b, {"success": True})
    assert sa == pytest.approx(sb)


def test_resolve_unknown_id_raises_lookup_error():
    with pytest.raises(LookupError):
        P.resolve("no-such-prediction", {"success": True})


@pytest.mark.parametrize("actual", [None, "ok", 3, [True]])
def test_resolve_rejects_non_dict_actual(actual):
    pid = P.predict("tool:x", {"success": True}, 0.5)
    with pytest.raises((TypeError, ValueError)):
        P.resolve(pid, actual)
    # a rejected resolve must not burn the prediction
    assert P.resolve(pid, {"success": True}) == pytest.approx(0.25)


def test_resolve_twice_raises():
    pid = P.predict("tool:x", {"success": True}, 0.9)
    P.resolve(pid, {"success": True})
    with pytest.raises(_ANY_ERROR):
        P.resolve(pid, {"success": False})


def test_second_resolve_leaves_calibration_unchanged():
    pid = P.predict("cal:twice", {"success": True}, 0.9)
    P.resolve(pid, {"success": True})
    before = P.calibration("cal:twice")
    with pytest.raises(_ANY_ERROR):
        P.resolve(pid, {"success": False})
    assert P.calibration("cal:twice") == before


def test_resolve_twice_with_identical_actual_still_raises():
    pid = P.predict("tool:x", {"success": True}, 0.9)
    P.resolve(pid, {"success": True})
    with pytest.raises(_ANY_ERROR):
        P.resolve(pid, {"success": True})


def test_other_fields_are_stored_for_later_scorers():
    marker = "latency_marker_7731"
    pid = P.predict("tool:x", {"success": True, "latency_ms": 11.5, "note": "exp_marker_4412"}, 0.6)
    P.resolve(pid, {"success": True, "latency_ms": 23.25, "note": marker})
    text = _all_db_text()
    assert "exp_marker_4412" in text
    assert marker in text
    assert "23.25" in text and "11.5" in text


def test_resolve_with_missing_success_field_does_not_corrupt_ledger():
    pid = P.predict("cal:nosuccess", {"success": True}, 0.9)
    try:
        s = P.resolve(pid, {"latency_ms": 5})
    except (KeyError, ValueError, TypeError):
        # allowed: refuse, and the prediction stays resolvable
        assert P.resolve(pid, {"success": True}) == pytest.approx(0.01)
    else:
        assert isinstance(s, float) and 0.0 <= s <= 1.0 and not math.isnan(s)


# ---------------------------------------------------------------- calibration

def test_calibration_shape():
    pid = P.predict("cal:shape", {"success": True}, 0.7)
    P.resolve(pid, {"success": True})
    cal = P.calibration("cal:shape")
    assert isinstance(cal, dict)
    assert "brier" in cal and "bins" in cal
    assert isinstance(cal["bins"], list) and cal["bins"]
    assert all(isinstance(b, dict) for b in cal["bins"])


def test_calibration_with_no_data_does_not_raise():
    cal = P.calibration("cal:nothing-here")
    assert cal["brier"] is None
    assert _count(cal) == 0


def test_calibration_brier_is_mean_of_scores():
    scores = []
    for conf, ok in [(0.9, True), (0.9, False), (0.2, False), (0.6, True)]:
        pid = P.predict("cal:mean", {"success": True}, conf)
        scores.append(P.resolve(pid, {"success": ok}))
    cal = P.calibration("cal:mean")
    assert cal["brier"] == pytest.approx(sum(scores) / len(scores))
    assert _count(cal) == 4


def test_calibration_ignores_unresolved():
    done = P.predict("cal:unres", {"success": True}, 1.0)
    P.resolve(done, {"success": True})
    P.predict("cal:unres", {"success": True}, 0.0)  # never resolved: would score 1.0
    cal = P.calibration("cal:unres")
    assert cal["brier"] == pytest.approx(0.0)
    assert _count(cal) == 1


def test_calibration_filters_by_prefix():
    for about, ok in [("cal:p:a", True), ("cal:p:b", False), ("cal:q:a", True)]:
        pid = P.predict(about, {"success": True}, 1.0)
        P.resolve(pid, {"success": ok})
    assert P.calibration("cal:p:")["brier"] == pytest.approx(0.5)
    assert _count(P.calibration("cal:p:")) == 2
    assert P.calibration("cal:p:b")["brier"] == pytest.approx(1.0)
    assert P.calibration("cal:q:")["brier"] == pytest.approx(0.0)
    assert _count(P.calibration("cal:")) == 3
    assert _count(P.calibration("cal:p:a-longer-than-any-about")) == 0


def test_calibration_prefix_is_literal_not_a_pattern():
    pid = P.predict("lit:a_b", {"success": True}, 1.0)
    P.resolve(pid, {"success": True})
    pid = P.predict("lit:aXb", {"success": True}, 1.0)
    P.resolve(pid, {"success": False})
    assert _count(P.calibration("lit:a_")) == 1
    assert _count(P.calibration("lit:a%")) == 0


def test_calibration_empty_prefix_covers_everything():
    pid = P.predict("anything", {"success": True}, 0.5)
    P.resolve(pid, {"success": True})
    assert _count(P.calibration("")) >= 1


def test_calibration_separates_confident_from_unsure():
    # Confident and right (x20) vs unsure and right (x20): the bins must not be one lump.
    for _ in range(20):
        P.resolve(P.predict("cal:bins", {"success": True}, 0.95), {"success": True})
        P.resolve(P.predict("cal:bins", {"success": True}, 0.05), {"success": True})
    cal = P.calibration("cal:bins")
    assert len([b for b in cal["bins"] if (b.get("n", b.get("count", 0)) > 0)]) >= 2
    assert _count(cal) == 40


def test_calibration_is_read_only_and_idempotent():
    pid = P.predict("cal:idem", {"success": True}, 0.75)
    P.resolve(pid, {"success": False})
    first = P.calibration("cal:idem")
    for _ in range(3):
        assert P.calibration("cal:idem") == first


# ---------------------------------------------------------------- stale unresolved predictions

def test_fresh_unresolved_prediction_is_not_listed():
    pid = P.predict("stale:fresh", {"success": True}, 0.5)
    assert pid not in _ids_in(_stale())


def test_prediction_older_than_24h_is_listed_until_resolved():
    pid = P.predict("stale:old", {"success": True}, 0.5)
    _age_everything(25)
    assert pid in _ids_in(_stale())
    P.resolve(pid, {"success": True})
    assert pid not in _ids_in(_stale())


def test_prediction_younger_than_24h_is_not_listed():
    pid = P.predict("stale:23h", {"success": True}, 0.5)
    _age_everything(23)
    assert pid not in _ids_in(_stale())


def test_only_old_unresolved_ones_are_listed():
    old = P.predict("stale:mix", {"success": True}, 0.5)
    old_done = P.predict("stale:mix", {"success": True}, 0.5)
    P.resolve(old_done, {"success": True})
    _age_everything(25)
    new = P.predict("stale:mix", {"success": True}, 0.5)
    listing = _ids_in(_stale())
    assert old in listing
    assert old_done not in listing
    assert new not in listing


def test_stale_listing_does_not_resolve_or_score():
    pid = P.predict("stale:keep", {"success": True}, 0.9)
    _age_everything(30)
    _stale()
    _stale()
    assert _count(P.calibration("stale:keep")) == 0
    assert P.resolve(pid, {"success": True}) == pytest.approx(0.01)


# ---------------------------------------------------------------- event log (CS2)

def test_predict_and_resolve_write_to_the_event_log_and_chain_verifies():
    n0 = len(list(iter_events()))
    pid = P.predict("ev:one", {"success": True}, 0.5)
    n1 = len(list(iter_events()))
    assert n1 > n0
    P.resolve(pid, {"success": True})
    assert len(list(iter_events())) > n1
    assert _chain_ok() == (True, None)


def test_failed_resolve_does_not_break_the_chain():
    pid = P.predict("ev:two", {"success": True}, 0.5)
    P.resolve(pid, {"success": True})
    with pytest.raises(_ANY_ERROR):
        P.resolve(pid, {"success": True})
    assert _chain_ok() == (True, None)


def test_secrets_in_fields_do_not_reach_the_event_log():
    secret = "sk-ant-api03-" + "A" * 40
    pid = P.predict("ev:secret", {"success": True, "note": secret}, 0.5)
    P.resolve(pid, {"success": True, "note": secret})
    assert secret not in json.dumps([e.payload for e in iter_events()], default=str)


# ---------------------------------------------------------------- ToolRegistry wiring

def _registry():
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()

    def ok_tool(text: str = ""):
        return {"echo": text}

    def bad_tool(text: str = ""):
        raise RuntimeError("boom")

    for name, fn in (("ok_tool", ok_tool), ("bad_tool", bad_tool)):
        reg.register(ToolDefinition(name=name, description=name, input_schema={"type": "object"}, handler=fn))
    return reg


def test_execute_writes_and_resolves_one_prediction_per_call():
    reg = _registry()
    assert _count(P.calibration("")) == 0
    res = asyncio.run(reg.execute("ok_tool", {"text": "hi"}))
    assert res.success
    cal = P.calibration("")
    assert _count(cal) == 1
    assert 0.0 <= cal["brier"] <= 1.0
    assert not _stale()
    asyncio.run(reg.execute("ok_tool", {"text": "again"}))
    assert _count(P.calibration("")) == 2


def test_execute_prediction_mentions_the_tool_and_carries_expected_latency():
    reg = _registry()
    asyncio.run(reg.execute("ok_tool", {}))
    text = _all_db_text()
    assert "ok_tool" in text
    assert "latency" in text.lower()


def test_execute_prediction_is_written_before_the_tool_call_event():
    reg = _registry()
    asyncio.run(reg.execute("ok_tool", {"text": "x"}))
    kinds = [e.kind for e in iter_events()]
    assert "tool_call" in kinds and "tool_result" in kinds
    pred = [i for i, k in enumerate(kinds) if "predict" in k]
    assert pred, f"no prediction event among {kinds}"
    assert pred[0] < kinds.index("tool_call")


def test_execute_resolves_with_the_real_outcome_for_a_failing_tool():
    reg = _registry()
    res = asyncio.run(reg.execute("bad_tool", {}))
    assert not res.success
    cal = P.calibration("")
    assert _count(cal) == 1
    # The tool was expected to work with some confidence c < 1, so a failure scores c**2 > 0 ...
    assert cal["brier"] > 0.0
    # ... and it is resolved, not left dangling.
    _age_everything(25)
    assert not _stale()


def test_execute_still_returns_the_tool_result_unchanged():
    reg = _registry()
    res = asyncio.run(reg.execute("ok_tool", {"text": "hi"}))
    assert res.success and res.tool_name == "ok_tool"
    assert "hi" in res.output


def test_execute_chain_still_verifies():
    reg = _registry()
    asyncio.run(reg.execute("ok_tool", {}))
    asyncio.run(reg.execute("bad_tool", {}))
    assert _chain_ok() == (True, None)


def test_registry_failures_score_worse_than_successes():
    reg = _registry()
    for _ in range(5):
        asyncio.run(reg.execute("ok_tool", {}))
    b_ok = P.calibration("")["brier"]
    for _ in range(5):
        asyncio.run(reg.execute("bad_tool", {}))
    cal = P.calibration("")
    assert _count(cal) == 10
    assert cal["brier"] > b_ok


# ---------------------------------------------------------------- property tests (seeded, no extra dependency)

@pytest.mark.parametrize("seed", range(8))
def test_property_brier_matches_formula_and_bins_account_for_everything(seed):
    rng = random.Random(seed)
    about = f"prop:{seed}:"
    scores = []
    for i in range(rng.randint(5, 40)):
        conf = rng.choice([0.0, 1.0, rng.random(), rng.random()])
        exp_ok = rng.random() < 0.8
        act_ok = rng.random() < 0.5
        pid = P.predict(about + str(i % 3), {"success": exp_ok, "latency_ms": rng.random() * 100}, conf)
        s = P.resolve(pid, {"success": act_ok, "latency_ms": rng.random() * 100})
        forecast = conf if exp_ok else 1 - conf
        assert s == pytest.approx((forecast - (1.0 if act_ok else 0.0)) ** 2)
        assert 0.0 <= s <= 1.0
        scores.append(s)
    cal = P.calibration(about)
    assert cal["brier"] == pytest.approx(sum(scores) / len(scores))
    assert _count(cal) == len(scores)
    assert 0.0 <= cal["brier"] <= 1.0


@pytest.mark.parametrize("seed", range(4))
def test_property_every_prediction_resolves_exactly_once(seed):
    rng = random.Random(100 + seed)
    pids = [P.predict("once:", {"success": True}, rng.random()) for _ in range(15)]
    for pid in pids:
        P.resolve(pid, {"success": rng.random() < 0.5})
    for pid in rng.sample(pids, 15):
        with pytest.raises(_ANY_ERROR):
            P.resolve(pid, {"success": True})
    assert _count(P.calibration("once:")) == 15
    assert _chain_ok() == (True, None)


@pytest.mark.parametrize("seed", range(4))
def test_property_prefix_counts_are_monotone(seed):
    rng = random.Random(200 + seed)
    for _ in range(25):
        about = "mono:" + "".join(rng.choice("abc") for _ in range(3))
        P.resolve(P.predict(about, {"success": True}, rng.random()), {"success": rng.random() < 0.5})
    prev = None
    for k in range(len("mono:") + 3, len("mono:") - 1, -1):
        c = _count(P.calibration("mono:" + "abc"[:max(0, k - 5)]))
        if prev is not None:
            assert c >= prev
        prev = c
    assert _count(P.calibration("mono:")) == 25


def test_ledger_survives_reopening_the_database():
    pid = P.predict("persist:x", {"success": True}, 0.5)
    # a second import-level access uses a fresh connection every call; resolve must find it
    assert P.resolve(pid, {"success": True}) == pytest.approx(0.25)
    assert _count(P.calibration("persist:")) == 1
