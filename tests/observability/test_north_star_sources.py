"""OBS2 implementer tests: where compute() reads each number from (decision 0014).

The exam (test_north_star.py) cannot seed rows because the card names no tables. These pin the sources this
build chose: the OBS3 ledger for the RCR funnel, `capabilities` for B and the effort events for the timer.
"""
from datetime import UTC, datetime, timedelta

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.observability import effort, north_star


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "core_state.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(path))
    monkeypatch.setenv("TANISHI_HOME", str(tmp_path / "home"))
    c = open_db(str(path))
    migrate(c)
    yield c
    c.close()


def _seed_ledger(conn):
    """Three experiments: a clear win, a flat one, and one with too few seeds to judge (rows as OBS3 writes them)."""
    conn.executemany(
        "INSERT INTO experiments (id, ts, candidate, baseline, task_set, seed, score, meta) VALUES (?,?,?,?,?,?,?,?)",
        [(f"{name}-{arm}-{seed}", datetime.now(UTC).isoformat(), f"{name}-cand", f"{name}-base", "t1", seed, score,
          f'{{"arm": "{name}-{arm}", "task": "t1"}}')
         for name, base, cand in (("win", [0.5, 0.51, 0.49], [0.8, 0.81, 0.79]),
                                  ("flat", [0.5, 0.6, 0.4], [0.5, 0.6, 0.41]),
                                  ("thin", [0.5, 0.5], [0.9, 0.9]))
         for arm, scores in (("base", base), ("cand", cand))
         for seed, score in enumerate(scores)],
    )
    conn.commit()


def test_funnel_counts_real_gains_as_discoveries(db):
    _seed_ledger(db)
    funnel = north_star.compute()["RCR"]["inputs"]["funnel"]
    assert funnel == {"experiments": 3, "judged": 2, "discoveries": 1, "unreadable": 0}


def test_rcr_divides_discoveries_by_timed_hours(db):
    _seed_ledger(db)
    now = datetime.now(UTC)
    for kind, at in ((effort.START, now - timedelta(hours=10)), (effort.STOP, now)):
        db.execute("INSERT INTO events (ts, kind, actor) VALUES (?, ?, ?)", (at.isoformat(), kind, effort.ACTOR))
    db.commit()
    rcr = north_star.compute()["RCR"]
    assert rcr["inputs"]["human_effort_hours"] == pytest.approx(10, rel=1e-6)
    assert rcr["value"] == pytest.approx(north_star.rcr(1, 10), rel=1e-6)


def test_discoveries_without_hours_are_unknown_not_zero(db):
    _seed_ledger(db)
    rcr = north_star.compute()["RCR"]
    assert rcr["value"] is None
    assert "no Human Effort hours" in rcr["explanation"]


def test_old_experiments_fall_out_of_the_window(db):
    _seed_ledger(db)
    db.execute("UPDATE experiments SET ts = ?", ((datetime.now(UTC) - timedelta(days=100)).isoformat(),))
    db.commit()
    assert north_star.compute(90)["RCR"]["inputs"]["funnel"]["experiments"] == 0
    assert north_star.compute(120)["RCR"]["inputs"]["funnel"]["experiments"] == 3


def test_effort_window_clips_a_session_that_started_before_it(db):
    now = datetime.now(UTC)
    for kind, at in ((effort.START, now - timedelta(days=2)), (effort.STOP, now)):
        db.execute("INSERT INTO events (ts, kind, actor) VALUES (?, ?, ?)", (at.isoformat(), kind, effort.ACTOR))
    db.commit()
    assert effort.hours(1, now=now) == pytest.approx(24)
    assert north_star.compute(window_days=1)["human_effort"]["value"] == pytest.approx(24, rel=1e-3)
    assert effort.report()["hours"] == pytest.approx(48, rel=1e-3)


def test_replay_ignores_double_start_and_lone_stop(db):
    now = datetime.now(UTC)
    rows = [(effort.STOP, now - timedelta(hours=5)), (effort.START, now - timedelta(hours=4)),
            (effort.START, now - timedelta(hours=3)), (effort.STOP, now - timedelta(hours=2)),
            (effort.STOP, now - timedelta(hours=1))]
    db.executemany("INSERT INTO events (ts, kind, actor) VALUES (?, ?, ?)",
                   [(at.isoformat(), kind, effort.ACTOR) for kind, at in rows])
    db.commit()
    assert effort.hours() == pytest.approx(2)


def test_timer_events_from_another_actor_do_not_count(db):
    now = datetime.now(UTC)
    for kind, at in ((effort.START, now - timedelta(hours=3)), (effort.STOP, now)):
        db.execute("INSERT INTO events (ts, kind, actor) VALUES (?, ?, ?)", (at.isoformat(), kind, "tanishi"))
    db.commit()
    assert effort.hours() == 0


def test_cei_b_counts_mastered_capabilities(db):
    db.executemany("INSERT INTO capabilities (id, name, state) VALUES (?, ?, ?)",
                   [("c1", "a", "mastered"), ("c2", "b", "learning"), ("c3", "c", "mastered")])
    db.commit()
    entry = north_star.compute()["CEI"]
    assert entry["inputs"]["B"]["value"] == 2
    assert entry["value"] is None  # T, H, N, A, P are not measured yet, so the product is unknown, not 0
    assert "not measured" in entry["explanation"]


def test_compute_and_report_do_not_create_a_missing_db(tmp_path, monkeypatch):
    path = tmp_path / "absent" / "core_state.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(path))
    result = north_star.compute()
    assert result["CEI"]["value"] == 0
    assert effort.report()["hours"] == 0
    assert not path.exists()


def test_compute_on_an_unmigrated_db_is_empty_not_an_error(tmp_path, monkeypatch):
    path = tmp_path / "core_state.db"
    open_db(str(path)).close()
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(path))
    result = north_star.compute()
    assert result["human_effort"]["value"] == 0
    assert result["RCR"]["value"] == 0


def test_format_report_shows_every_number_and_its_reason(db):
    text = north_star.format_report(north_star.compute())
    for name in ("CEI", "RCR", "CAR", "AR", "IA", "human_effort"):
        assert name in text
    assert "no Reality-verified mastery yet" in text


def test_time_cli_rejects_an_unknown_verb(capsys):
    assert effort.cli(["pause"]) == 2
    assert "usage" in capsys.readouterr().err


def _timed(conn, minutes):
    now = datetime.now(UTC)
    for kind, at in ((effort.START, now - timedelta(minutes=minutes)), (effort.STOP, now)):
        conn.execute("INSERT INTO events (ts, kind, actor) VALUES (?, ?, ?)", (at.isoformat(), kind, effort.ACTOR))
    conn.commit()


@pytest.mark.parametrize("minutes", [0.05, 3, 5.9])
def test_rcr_below_the_hours_floor_is_unknown(db, minutes):
    _seed_ledger(db)
    _timed(db, minutes)
    rcr = north_star.compute()["RCR"]
    assert rcr["value"] is None
    assert "timer artefact" in rcr["explanation"]
    assert rcr["inputs"]["human_effort_hours"] > 0


def test_rcr_at_the_hours_floor_is_a_rate(db):
    _seed_ledger(db)
    _timed(db, 6.01)
    assert north_star.compute()["RCR"]["value"] == pytest.approx(north_star.rcr(1, 6.01 / 60), rel=1e-3)


def test_window_cap_is_inclusive_and_checked_everywhere(db):
    north_star.compute(effort.MAX_WINDOW_DAYS)
    effort.hours(effort.MAX_WINDOW_DAYS)
    for fn in (north_star.compute, effort.hours, effort.report):
        with pytest.raises(ValueError, match="at most"):
            fn(effort.MAX_WINDOW_DAYS + 1)


def test_time_report_with_a_huge_window_is_a_clear_error(db, capsys):
    assert effort.cli(["report", "1e9"]) == 1
    assert "at most" in capsys.readouterr().err


def test_rcr_overflow_is_a_value_error():
    with pytest.raises(ValueError, match="overflowed"):
        north_star.rcr(1e308, 1e-300)


def test_cei_overflow_is_none_not_inf():
    assert north_star.cei(1e200, 1e200, 1, 1, 1, 1) is None
    assert north_star.cei(10**200, 10**200, 1, 1, 1, 1) is None
