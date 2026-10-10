"""SUB4 builder tests: tag edge cases, failing open, raw rows, and agreement with CS5. The exam is test_uncertainty.py."""
import logging
import random
from contextlib import closing

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state import predictions as P
from tanishi.substrate import uncertainty as U


def _db():
    conn = open_db()
    migrate(conn)
    return closing(conn)


def _cap_rows():
    with _db() as conn:
        return conn.execute("SELECT id, name, family, state FROM capabilities ORDER BY id").fetchall()


@pytest.mark.parametrize("tag", [5, "", "   ", None, ["code"], {"x": 1}, True])
def test_a_tag_that_is_not_a_non_blank_str_is_untagged(tag):
    pid = P.predict("self:probe", {"success": True, "capability": tag}, 0.9)
    U.resolve_prediction(pid, {"success": False})
    assert _cap_rows() == []


def test_tag_matching_is_exact_text():
    pid = P.predict("self:probe", {"success": True, "capability": "Code"}, 0.95)
    U.resolve_prediction(pid, {"success": False})
    assert U.get_capability("code").calibration_error is None
    assert U.get_capability("Code").calibration_error == pytest.approx(0.95)


def test_new_row_is_named_by_its_tag_and_an_update_keeps_other_columns():
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.9)
    U.resolve_prediction(pid, {"success": True})
    assert _cap_rows() == [("code", "code", None, None)]
    with _db() as conn, conn:
        conn.execute("UPDATE capabilities SET name = 'Coding', family = 'craft', state = 'active' WHERE id = 'code'")
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.9)
    U.resolve_prediction(pid, {"success": False})
    assert _cap_rows() == [("code", "Coding", "craft", "active")]
    assert U.get_capability("code").ability == pytest.approx(0.5)


def test_a_failed_capability_update_still_returns_the_score(monkeypatch, caplog):
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.8)

    def boom(_):
        raise RuntimeError("disk full")

    with monkeypatch.context() as m, caplog.at_level(logging.WARNING):  # not undo(): that would drop the temp db
        m.setattr(U, "update_calibration", boom)
        assert U.resolve_prediction(pid, {"success": False}) == pytest.approx(0.64)
    assert "capability update failed" in caplog.text
    assert U.update_calibration("code") == pytest.approx(0.8)  # the row heals on the next update


def test_malformed_resolved_rows_are_skipped_not_fatal():
    good = P.predict("self:probe", {"success": True, "capability": "code"}, 0.95)
    bad = P.predict("self:probe", {"success": True, "capability": "code"}, 0.95)
    P.resolve(bad, {"success": True})
    with _db() as conn, conn:  # only raw SQL can store a non-object actual
        conn.execute("UPDATE predictions SET actual = '5' WHERE id = ?", (bad,))
    U.resolve_prediction(good, {"success": False})
    assert U.get_capability("code").calibration_error == pytest.approx(0.95)


@pytest.mark.parametrize("bad, exc", [(5, TypeError), (None, TypeError), ("", ValueError), ("  ", ValueError)])
def test_read_and_update_refuse_a_bad_capability_name(bad, exc):
    with pytest.raises(exc):
        U.get_capability(bad)
    with pytest.raises(exc):
        U.update_calibration(bad)


def test_reading_is_frozen():
    reading = U.get_capability("code")
    with pytest.raises(AttributeError):
        reading.ability = 1.0


def test_agrees_with_cs5_calibration_bins():
    """Evaluation: the stored error is the ECE of the very bins CS5's calibration() reports for the same rows."""
    rng = random.Random(3)
    for _ in range(300):
        conf = rng.random()
        exp_ok = rng.random() < 0.7
        p = conf if exp_ok else 1 - conf
        pid = P.predict("cap_eval:x", {"success": exp_ok, "capability": "eval"}, conf)
        U.resolve_prediction(pid, {"success": rng.random() < min(1.0, p + 0.2)})  # an underconfident forecaster
    report = P.calibration("cap_eval:")
    want = sum(b["n"] / report["n"] * abs(b["mean_forecast"] - b["success_rate"]) for b in report["bins"] if b["n"])
    got = U.get_capability("eval").calibration_error
    assert got == pytest.approx(want, abs=1e-9)
    assert got > 0.1  # the bias shows up
