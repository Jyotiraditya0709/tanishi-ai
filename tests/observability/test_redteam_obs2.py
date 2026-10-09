"""Red-team attacks on OBS2. A failing test here proves a break; see build/memory/runs/redteam-OBS2-*.md."""
import json
import os
import subprocess
import sys

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.observability import effort, north_star
from tanishi.observability.experiments import Run, record_run


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "core_state.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(path))
    monkeypatch.setenv("TANISHI_HOME", str(tmp_path))
    c = open_db(str(path))
    migrate(c)
    yield c
    c.close()


def test_huge_int_is_a_value_error_not_overflow():
    for fn, args in ((north_star.cei, (10**400, 1, 1, 1, 1, 1)), (north_star.rcr, (10**400, 1)),
                     (north_star.rcr, (1, 10**400))):
        with pytest.raises(ValueError):
            fn(*args)


def test_cei_overflow_to_inf_is_refused_or_finite():
    out = north_star.cei(1e200, 1e200, 1, 1, 1, 1)
    assert out != float("inf")  # inf is not JSON and not a measurement


def test_rcr_overflow_to_inf_is_refused_or_finite():
    try:
        out = north_star.rcr(1e308, 1e-300)
    except ValueError:
        return
    assert out != float("inf")


@pytest.mark.parametrize("days", [1e9, 1e12, 10**400])
def test_compute_huge_window_is_value_error(db, days):
    with pytest.raises(ValueError):
        north_star.compute(days)


def test_effort_huge_window_is_value_error(db):
    with pytest.raises(ValueError):
        effort.hours(1e9)


def test_cli_report_huge_window_does_not_traceback(db):
    r = subprocess.run([sys.executable, "-m", "tanishi.cli", "time", "report", "1e9"], capture_output=True,
                       text=True, env=os.environ.copy(), check=False)
    assert "Traceback" not in r.stderr
    assert r.returncode in (1, 2)


def test_a_one_second_session_cannot_inflate_rcr(db):
    """RCR = d / hours: timing 1 second makes any discovery worth ~100000 per month. Hours need a floor."""
    for seed in (1, 2, 3):
        record_run(db, Run("a", "t", seed), 0.1 + seed * 0.0001, baseline="a", candidate="b")
        record_run(db, Run("b", "t", seed), 0.9 + seed * 0.0001, baseline="a", candidate="b")
    from tanishi.core_state import events
    events.emit("effort_start", {}, actor="human")
    events.emit("effort_stop", {}, actor="human")
    r = north_star.compute(90)["RCR"]
    assert r["value"] is None or r["value"] < 1000, r["value"]


def test_event_forgery_by_other_actor_does_not_count(db):
    from tanishi.core_state import events
    events.emit("effort_start", {}, actor="tanishi")
    events.emit("effort_stop", {}, actor="tanishi")
    assert effort.sessions(db) == []


def test_compute_does_not_write_an_existing_db(db, tmp_path):
    path = tmp_path / "core_state.db"
    db.close()
    before = path.read_bytes()
    north_star.compute(90)
    effort.report()
    assert path.read_bytes() == before


def test_result_is_strict_json_with_experiments(db):
    json.dumps(north_star.compute(90), allow_nan=False)
