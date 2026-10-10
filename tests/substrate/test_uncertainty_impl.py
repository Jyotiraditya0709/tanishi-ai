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


# ---------------------------------------------------------------- repair round (decision 0021)


def _resolve_without_hook(monkeypatch, cap, confidence, outcome):
    """A resolve in a process that never imported uncertainty: CS5 runs no hook."""
    pid = P.predict("self:probe", {"success": True, "capability": cap}, confidence)
    with monkeypatch.context() as m:
        m.setattr(P, "_resolve_hooks", [])
        P.resolve(pid, {"success": outcome})


def test_read_through_picks_up_a_resolve_the_hook_never_saw(monkeypatch):
    _resolve_without_hook(monkeypatch, "code", 0.9, False)
    with _db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM capabilities").fetchone()[0] == 0
    r = U.get_capability("code")
    assert r.calibration_error == pytest.approx(0.9) and r.evidence == 1
    _resolve_without_hook(monkeypatch, "code", 0.9, True)
    assert U.get_capability("code").evidence == 2


def test_read_through_sees_evidence_older_than_a_row_someone_else_made(monkeypatch):
    _resolve_without_hook(monkeypatch, "code", 0.95, False)
    with _db() as conn, conn:
        conn.execute("INSERT INTO capabilities (id, name, family, updated_at) VALUES ('code', 'Coding', 'craft', ?)",
                     ("2999-01-01T00:00:00.000000+00:00",))
    r = U.get_capability("code")
    assert r.calibration_error == pytest.approx(0.95)
    assert _cap_rows() == [("code", "Coding", "craft", None)]


def test_a_cs5_resolve_updates_the_row_through_the_hook():
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.8)
    P.resolve(pid, {"success": False})
    with _db() as conn:
        assert conn.execute("SELECT calibration_error FROM capabilities WHERE id = 'code'").fetchone()[0] == \
            pytest.approx(0.8)


def test_a_raising_hook_never_breaks_cs5_resolve(monkeypatch, caplog):
    def boom(*_):
        raise RuntimeError("sk-" + "z" * 30)

    pid = P.predict("self:probe", {"success": True}, 0.8)
    with monkeypatch.context() as m, caplog.at_level(logging.WARNING):
        m.setattr(P, "_resolve_hooks", [boom])
        assert P.resolve(pid, {"success": False}) == pytest.approx(0.64)
    assert "RuntimeError" in caplog.text and "zzzz" not in caplog.text


def test_evidence_count_and_low_evidence_flag():
    assert U.get_capability("code").evidence == 0 and U.get_capability("code").low_evidence
    for i in range(U.MIN_EVIDENCE):
        pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.9)
        U.resolve_prediction(pid, {"success": True})
        r = U.get_capability("code")
        assert r.evidence == i + 1
        assert r.low_evidence is (i + 1 < U.MIN_EVIDENCE)
    assert r.ability == 1.0  # enough evidence: the plain rate


def test_low_evidence_ability_is_laplace_and_count_survives_a_cold_cache():
    for ok in (True, True, False):
        pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.9)
        U.resolve_prediction(pid, {"success": ok})
    U._counts.clear()
    r = U.get_capability("code")
    assert r.ability == pytest.approx(3 / 5) and r.evidence == 3


@pytest.mark.parametrize("tag", ["[REDACTED:openai]", "[REDACTED]", "sk-" + "q" * 30, "co\udc80de"])
def test_a_tag_redaction_would_change_is_untagged(tag):
    pid = P.predict("self:probe", {"success": True, "capability": tag}, 0.9)
    U.resolve_prediction(pid, {"success": False})
    assert _cap_rows() == []
    assert U.get_capability("[REDACTED:openai]").evidence == 0


def test_lone_surrogate_name_is_a_value_error():
    for call in (U.get_capability, U.update_calibration):
        with pytest.raises(ValueError) as e:
            call("co\udc80de")
        assert not isinstance(e.value, UnicodeError)
