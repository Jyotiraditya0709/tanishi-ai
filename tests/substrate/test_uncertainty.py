"""Exam for SUB4: uncertainty and Self Model v0 (tanishi/substrate/uncertainty.py).

Written from the spec alone, without reading an implementation. Spec acceptance:
  1. Resolving predictions updates capabilities.calibration_error for the capability tagged on the prediction.
  2. The router can read ability and calibration for a capability (used by SUB3 later).

The spec gives no interface, so the exam pins the readings in build/memory/open-problems/SUB4-exam-assumptions.md:
  - the tag is expected["capability"]; the capability row id is the tag text
  - resolve_prediction(prediction_id, actual) -> float      CS5 resolve() plus the capability update
  - update_calibration(capability) -> float | None          recompute from resolved evidence, idempotent
  - get_capability(capability) -> obj(.ability, .calibration_error)    None fields for an unknown capability
  - calibration_error is the expected calibration error over CS5's 10 equal-width forecast bins
"""
import random

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state import predictions as P
from tanishi.substrate import uncertainty as U

N_BINS = 10
TOL = 1e-9


# ---------------------------------------------------------------- helpers


def forecast_of(success_expected: bool, confidence: float) -> float:
    return confidence if success_expected else 1.0 - confidence


def reference_ece(pairs):
    """pairs: [(forecast, outcome_bool)]. Expected calibration error over 10 equal-width bins."""
    if not pairs:
        return None
    bins = {}
    for p, ok in pairs:
        bins.setdefault(min(int(p * N_BINS), N_BINS - 1), []).append((p, 1.0 if ok else 0.0))
    total = len(pairs)
    err = 0.0
    for members in bins.values():
        mean_f = sum(p for p, _ in members) / len(members)
        rate = sum(o for _, o in members) / len(members)
        err += len(members) / total * abs(mean_f - rate)
    return err


def make(cap, confidence, outcome, expected_success=True, resolve=True, via=None, **extra):
    """Predict (tagged with cap unless cap is None), optionally resolve. Returns (id, forecast)."""
    expected = {"success": expected_success, **extra}
    if cap is not None:
        expected["capability"] = cap
    pid = P.predict("self:probe", expected, confidence)
    if resolve:
        (via or U.resolve_prediction)(pid, {"success": outcome})
    return pid, forecast_of(expected_success, confidence)


def row(cap):
    conn = open_db()
    migrate(conn)
    try:
        return conn.execute(
            "SELECT id, ability, calibration_error, updated_at FROM capabilities WHERE id = ?", (cap,)
        ).fetchone()
    finally:
        conn.close()


def calibration_error_in_db(cap):
    r = row(cap)
    return None if r is None else r[2]


def n_capability_rows():
    conn = open_db()
    migrate(conn)
    try:
        return conn.execute("SELECT COUNT(*) FROM capabilities").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- acceptance 1: resolving updates the row


def test_resolve_creates_capability_row_with_calibration_error():
    assert row("web_search") is None
    make("web_search", 0.95, False)
    r = row("web_search")
    assert r is not None
    assert r[2] == pytest.approx(0.95, abs=TOL)  # said 0.95, was wrong: one bin, gap 0.95


def test_resolve_returns_the_cs5_brier_score():
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.8)
    score = U.resolve_prediction(pid, {"success": False})
    assert score == pytest.approx(0.64, abs=TOL)


def test_resolution_is_recorded_in_the_ledger_too():
    pid, _ = make("code", 0.8, True)
    assert pid not in [u["id"] for u in P.unresolved_older_than(hours=-1.0)]
    with pytest.raises(ValueError):  # AlreadyResolvedError is a ValueError
        P.resolve(pid, {"success": True})


def test_perfectly_calibrated_history_has_zero_error():
    # 20 predictions at 0.85, 17 of them succeed: mean forecast 0.85 == success rate 0.85
    for i in range(20):
        make("planning", 0.85, i < 17)
    assert calibration_error_in_db("planning") == pytest.approx(0.0, abs=TOL)


def test_overconfidence_is_error_and_matches_hand_computation():
    # 4 at 0.85 (3 succeed -> gap 0.10), 4 at 0.25 (1 succeeds -> gap 0.0 exactly) => (4*.10 + 4*0)/8 = 0.05
    for ok in (True, True, True, False):
        make("math", 0.85, ok)
    for ok in (True, False, False, False):
        make("math", 0.25, ok)
    assert calibration_error_in_db("math") == pytest.approx(0.05, abs=TOL)


def test_two_bins_weighted_by_count():
    # 3 at 0.95, all fail: gap 0.95. 1 at 0.15, fails: gap 0.15. => (3*.95 + .15)/4 = 0.75
    for _ in range(3):
        make("vision", 0.95, False)
    make("vision", 0.15, False)
    assert calibration_error_in_db("vision") == pytest.approx(0.75, abs=TOL)


def test_error_is_updated_on_each_resolution_not_just_the_first():
    make("memory", 0.95, False)
    first = calibration_error_in_db("memory")
    for _ in range(9):
        make("memory", 0.95, True)
    second = calibration_error_in_db("memory")
    assert first == pytest.approx(0.95, abs=TOL)
    assert second == pytest.approx(abs(0.95 - 0.9), abs=TOL)
    assert second != first


def test_expected_failure_uses_the_complement_as_forecast():
    # expected success False at confidence 0.9 => forecast of success 0.1. Outcome success: gap 0.9.
    make("refusal", 0.9, True, expected_success=False)
    assert calibration_error_in_db("refusal") == pytest.approx(0.9, abs=TOL)
    # Outcome failure: gap 0.1 on top of that: bin 1 holds forecasts 0.1, 0.1: rate 0.5 -> gap 0.4
    make("refusal", 0.9, False, expected_success=False)
    assert calibration_error_in_db("refusal") == pytest.approx(0.4, abs=TOL)


def test_only_the_tagged_capability_changes():
    make("alpha", 0.95, False)
    before = row("alpha")
    make("beta", 0.85, True)
    assert row("alpha")[:3] == before[:3]
    assert row("beta") is not None
    assert row("beta")[2] == pytest.approx(0.15, abs=TOL)


def test_untagged_prediction_resolves_and_touches_no_capability():
    pid, _ = make(None, 0.9, True)
    assert n_capability_rows() == 0
    # and it is resolved
    with pytest.raises(ValueError):
        P.resolve(pid, {"success": True})


def test_unresolved_predictions_are_not_evidence():
    make("planning", 0.95, False)  # error 0.95
    for _ in range(5):
        make("planning", 0.95, True, resolve=False)
    assert U.update_calibration("planning") == pytest.approx(0.95, abs=TOL)
    assert calibration_error_in_db("planning") == pytest.approx(0.95, abs=TOL)


def test_other_expected_fields_do_not_disturb_the_score():
    make("tools", 0.85, True, latency_ms=120, note="x")
    assert calibration_error_in_db("tools") == pytest.approx(0.15, abs=TOL)


def test_second_resolve_raises_and_leaves_the_row_alone():
    pid, _ = make("code", 0.95, False)
    before = row("code")
    with pytest.raises(ValueError):
        U.resolve_prediction(pid, {"success": True})
    assert row("code")[:3] == before[:3]
    assert calibration_error_in_db("code") == pytest.approx(0.95, abs=TOL)


def test_unknown_prediction_id_raises_lookup_error_and_writes_nothing():
    with pytest.raises(LookupError):
        U.resolve_prediction("pred_" + "0" * 32, {"success": True})
    assert n_capability_rows() == 0


def test_bad_actual_raises_and_prediction_stays_resolvable():
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.9)
    with pytest.raises((TypeError, ValueError)):
        U.resolve_prediction(pid, {"no_success_here": 1})
    assert row("code") is None
    U.resolve_prediction(pid, {"success": True})
    assert calibration_error_in_db("code") == pytest.approx(0.1, abs=TOL)


def test_resolving_through_cs5_directly_is_picked_up_by_update_calibration():
    pid = P.predict("self:probe", {"success": True, "capability": "planning"}, 0.95)
    P.resolve(pid, {"success": False})
    got = U.update_calibration("planning")
    assert got == pytest.approx(0.95, abs=TOL)
    assert calibration_error_in_db("planning") == pytest.approx(0.95, abs=TOL)


def test_update_calibration_is_idempotent():
    for ok in (True, False, True):
        make("planning", 0.65, ok)
    a = U.update_calibration("planning")
    b = U.update_calibration("planning")
    assert a == b == pytest.approx(calibration_error_in_db("planning"), abs=TOL)
    assert n_capability_rows() == 1


def test_update_calibration_without_evidence_is_none_and_invents_no_error():
    assert U.update_calibration("never_seen") is None
    assert calibration_error_in_db("never_seen") is None
    make("seen_once", 0.5, True, resolve=False)
    assert U.update_calibration("seen_once") is None


def test_updated_at_is_set():
    make("code", 0.9, True)
    assert row("code")[3]


# ---------------------------------------------------------------- acceptance 2: the router reads ability and calibration


def test_get_capability_reports_what_the_row_holds():
    for ok in (True, True, False, True):
        make("summarise", 0.75, ok)
    cap = U.get_capability("summarise")
    r = row("summarise")
    assert cap.calibration_error == pytest.approx(r[2], abs=TOL)
    assert cap.ability == pytest.approx(r[1], abs=TOL)
    assert cap.calibration_error == pytest.approx(reference_ece([(0.75, True)] * 3 + [(0.75, False)]), abs=TOL)
    assert 0.0 <= cap.ability <= 1.0


def test_unknown_capability_reads_as_unknown_not_as_an_error():
    cap = U.get_capability("no_such_capability")
    assert cap.ability is None
    assert cap.calibration_error is None


def test_read_follows_the_latest_resolution():
    make("code", 0.95, False)
    assert U.get_capability("code").calibration_error == pytest.approx(0.95, abs=TOL)
    for _ in range(9):
        make("code", 0.95, True)
    assert U.get_capability("code").calibration_error == pytest.approx(0.05, abs=TOL)


def test_ability_orders_capabilities_by_how_often_they_succeed():
    for i in range(10):
        make("strong", 0.5, i < 9)
        make("weak", 0.5, i < 2)
    strong, weak = U.get_capability("strong"), U.get_capability("weak")
    assert 0.0 <= weak.ability <= strong.ability <= 1.0
    assert strong.ability > weak.ability


def test_ability_moves_when_outcomes_change():
    for _ in range(5):
        make("code", 0.5, True)
    high = U.get_capability("code").ability
    for _ in range(15):
        make("code", 0.5, False)
    assert U.get_capability("code").ability < high


def test_router_can_tell_calibrated_from_overconfident_at_equal_ability():
    # both succeed 50% of the time; one says 0.5, the other says 0.95
    for i in range(10):
        make("humble", 0.5, i % 2 == 0)
        make("boastful", 0.95, i % 2 == 0)
    humble, boastful = U.get_capability("humble"), U.get_capability("boastful")
    assert humble.ability == pytest.approx(boastful.ability, abs=0.2)
    assert humble.calibration_error == pytest.approx(0.0, abs=TOL)
    assert boastful.calibration_error == pytest.approx(0.45, abs=TOL)


# ---------------------------------------------------------------- property tests


def _random_history(rng):
    caps = ["c_a", "c_b", "c_c"]
    events = []
    for _ in range(rng.randint(1, 40)):
        cap = rng.choice(caps)
        conf = rng.choice([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 0.0, 1.0, rng.random()])
        exp_ok = rng.random() < 0.8
        events.append((cap, conf, exp_ok, rng.random() < 0.6))
    return events


@pytest.mark.parametrize("seed", range(40))
def test_property_row_equals_reference_ece_for_random_histories(seed):
    rng = random.Random(seed)
    history = _random_history(rng)
    seen = {}
    for cap, conf, exp_ok, outcome in history:
        make(cap, conf, outcome, expected_success=exp_ok)
        seen.setdefault(cap, []).append((forecast_of(exp_ok, conf), outcome))
    for cap, pairs in seen.items():
        want = reference_ece(pairs)
        got = calibration_error_in_db(cap)
        assert got == pytest.approx(want, abs=1e-9), (seed, cap)
        assert 0.0 <= got <= 1.0
        read = U.get_capability(cap)
        assert read.calibration_error == pytest.approx(want, abs=1e-9)
        assert 0.0 <= read.ability <= 1.0
        assert U.update_calibration(cap) == pytest.approx(want, abs=1e-9)  # idempotent / same as incremental


@pytest.mark.parametrize("seed", range(10))
def test_property_resolution_order_does_not_change_the_result(seed):
    rng = random.Random(1000 + seed)
    spec = [(rng.choice([0.15, 0.55, 0.85, 0.95]), rng.random() < 0.5) for _ in range(rng.randint(2, 20))]
    ids = [P.predict("self:probe", {"success": True, "capability": "order"}, c) for c, _ in spec]
    order = list(range(len(spec)))
    rng.shuffle(order)
    for i in order:
        U.resolve_prediction(ids[i], {"success": spec[i][1]})
    want = reference_ece([(c, ok) for c, ok in spec])
    assert calibration_error_in_db("order") == pytest.approx(want, abs=1e-9)


def test_property_perfect_forecaster_beats_a_bad_one_on_error():
    rng = random.Random(7)
    for _ in range(200):
        p = rng.choice([0.15, 0.55, 0.85])
        make("oracle", p, rng.random() < p)
        make("liar", p, rng.random() >= p)  # outcomes opposite to its stated confidence
    assert U.get_capability("oracle").calibration_error < U.get_capability("liar").calibration_error
