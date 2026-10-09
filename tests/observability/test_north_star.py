"""OBS2 exam: North Star numbers and the effort timer. Written by the testing agent from the spec card alone.

Assumptions the card leaves open are listed in build/memory/open-problems/OBS2-exam-assumptions.md (A1 to A8).
"""
import contextlib
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.observability import effort, north_star

REPO_ROOT = Path(__file__).resolve().parents[2]
SIX = ("cei", "rcr", "car", "ar", "ia", "humaneffort")


def _norm(key):
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _entries(result):
    """Map the six normalised names to their entries (A2: top-level keys, any case or spacing)."""
    return {_norm(k): v for k, v in result.items()}


def _numbers(node):
    """Every int or float (not bool) anywhere under node."""
    if isinstance(node, bool):
        return []
    if isinstance(node, (int, float)):
        return [node]
    if isinstance(node, dict):
        return [n for v in node.values() for n in _numbers(v)]
    if isinstance(node, (list, tuple)):
        return [n for v in node for n in _numbers(v)]
    return []


def _hours_in(node):
    """First number found under a key that contains 'hour' (A5)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if "hour" in str(k).lower() and _numbers(v):
                return _numbers(v)[0]
        for v in node.values():
            found = _hours_in(v)
            if found is not None:
                return found
    return None


@pytest.fixture
def conn(tmp_path):
    c = open_db(str(tmp_path / "core_state.db"))
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    """A migrated Core State db that compute() will find through the environment (A1)."""
    path = tmp_path / "ns" / "core_state.db"
    path.parent.mkdir()
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(path))
    c = open_db(str(path))
    migrate(c)
    c.close()
    return path


def _cli(args, home, db, timeout=60):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), TANISHI_HOME=str(home / ".tanishi"),
               TANISHI_CORE_STATE_DB=str(db))
    return subprocess.run([sys.executable, "-m", "tanishi.cli", *args], cwd=REPO_ROOT, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)


# ---------------------------------------------------------------- CEI: B x T x H x N x A x P


def test_cei_worked_example():
    assert north_star.cei(3, 1.2, 1.5, 0.5, 0.8, 0.9) == pytest.approx(1.944, rel=1e-9)


def test_cei_is_the_plain_product_in_that_order():
    # Distinct primes make any dropped or doubled factor show up.
    assert north_star.cei(2, 3, 5, 7, 11, 13) == 2 * 3 * 5 * 7 * 11 * 13


def test_cei_any_zero_factor_gives_zero():
    base = [3, 1.2, 1.5, 0.5, 0.8, 0.9]
    for i in range(6):
        factors = list(base)
        factors[i] = 0
        assert north_star.cei(*factors) == 0


def test_cei_property_order_does_not_matter_and_each_factor_is_linear():
    rng = random.Random(20261009)
    for _ in range(200):
        f = [rng.uniform(0.01, 5) for _ in range(6)]
        value = north_star.cei(*f)
        assert value == pytest.approx(math.prod(f), rel=1e-9)
        shuffled = list(f)
        rng.shuffle(shuffled)
        assert north_star.cei(*shuffled) == pytest.approx(value, rel=1e-9)
        i, k = rng.randrange(6), rng.uniform(0.1, 10)
        scaled = list(f)
        scaled[i] *= k
        assert north_star.cei(*scaled) == pytest.approx(value * k, rel=1e-9)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, "2", None, True])
def test_cei_refuses_a_bad_factor(bad):
    # A8: the repo convention (decision 0011 rule 12) is ValueError for any bad number.
    with pytest.raises(ValueError):
        north_star.cei(3, 1.2, bad, 0.5, 0.8, 0.9)


# ---------------------------------------------------------------- RCR: funnel counts and effort hours


def test_rcr_worked_example():
    assert north_star.rcr(3, 10) == pytest.approx(72)


def test_rcr_eight_hours_is_one_researcher_day():
    # Anchored on the example: 72 at 10 h means 3 discoveries per 8 h is 72 * 10 / 8 = 90.
    assert north_star.rcr(3, 8) == pytest.approx(90)
    # Same discoveries in a day-length and in a two-day-length effort: halves.
    assert north_star.rcr(3, 16) == pytest.approx(45)


def test_rcr_property_linear_in_discoveries_and_inverse_in_hours():
    rng = random.Random(7)
    for _ in range(200):
        d, h = rng.randint(1, 50), rng.uniform(0.5, 400)
        k = rng.uniform(0.2, 9)
        assert north_star.rcr(d, h) == pytest.approx(72 * d / 3 * 10 / h, rel=1e-9)
        assert north_star.rcr(d, h * k) == pytest.approx(north_star.rcr(d, h) / k, rel=1e-9)
        assert north_star.rcr(2 * d, h) == pytest.approx(2 * north_star.rcr(d, h), rel=1e-9)


def test_rcr_no_discoveries_is_zero():
    assert north_star.rcr(0, 10) == 0


def test_rcr_zero_hours_does_not_crash_with_a_division_error():
    # A7: free choice between a ValueError, None, inf or 0, but never ZeroDivisionError.
    try:
        out = north_star.rcr(3, 0)
    except ValueError:
        return
    assert out is None or out == 0 or math.isinf(out)


@pytest.mark.parametrize("bad", [(-1, 10), (3, -1), (math.nan, 10), (3, math.nan), (True, 10), ("3", 10)])
def test_rcr_refuses_bad_numbers(bad):
    with pytest.raises(ValueError):
        north_star.rcr(*bad)


# ---------------------------------------------------------------- compute(): six numbers with their inputs


def test_compute_returns_all_six_numbers(db_env):
    result = north_star.compute()
    assert isinstance(result, dict)
    entries = _entries(result)
    for name in SIX:
        assert name in entries, f"missing {name}; got {sorted(entries)}"


def test_compute_result_can_be_shown_as_json(db_env):
    json.dumps(north_star.compute())


def test_every_number_shows_a_value_and_its_inputs(db_env):
    for name, entry in _entries(north_star.compute()).items():
        if name not in SIX:
            continue
        assert isinstance(entry, dict), name
        assert "value" in entry, name
        assert entry.get("inputs"), f"{name} shows no inputs"


def test_a_zero_is_explained_on_an_empty_core_state(db_env):
    entries = _entries(north_star.compute())
    for name in SIX:
        entry = entries[name]
        value = entry["value"]
        assert value is None or value == 0, f"{name} should be zero with nothing in the Core State, got {value}"
        words = [s for s in _strings(entry) if len(s.split()) >= 3]
        assert words, f"{name} is {value} with no sentence explaining why"
    text = json.dumps(entries).lower()
    assert "mastery" in text or "reality" in text  # the spec's own example: "no Reality-verified mastery yet"


def _strings(node):
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _strings(v)]
    return []


def test_cei_inputs_are_the_six_factors(db_env):
    inputs = _entries(north_star.compute())["cei"]["inputs"]
    keys = {_norm(k) for k in inputs} if isinstance(inputs, dict) else set()
    assert {"b", "t", "h", "n", "a", "p"} <= keys, f"CEI inputs must name B, T, H, N, A, P; got {inputs}"


def test_compute_is_read_only(db_env):
    def snapshot():
        c = open_db(str(db_env))
        try:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        finally:
            c.close()

    before = snapshot()
    north_star.compute()
    north_star.compute(window_days=7)
    assert snapshot() == before


def test_compute_is_repeatable(db_env):
    a, b = _entries(north_star.compute()), _entries(north_star.compute(window_days=90))
    for name in SIX:
        assert a[name]["value"] == b[name]["value"], name


@pytest.mark.parametrize("bad", [0, -1, -90, math.nan, "90", None, True])
def test_compute_refuses_a_bad_window(db_env, bad):
    with pytest.raises(ValueError):
        north_star.compute(window_days=bad)


# ---------------------------------------------------------------- effort timer


def test_timer_start_stop_report_counts_the_elapsed_time(db_env):
    effort.start()
    time.sleep(1.2)
    effort.stop()
    hours = _hours_in(effort.report())
    assert hours is not None, "report() must show hours (A5)"
    assert 1.0 / 3600 <= hours <= 120 / 3600


def test_timer_report_with_no_sessions_is_zero_hours(db_env):
    assert _hours_in(effort.report()) == 0


def test_timer_stop_without_start_adds_nothing(db_env):
    with contextlib.suppress(Exception):  # A6: raising is fine, silently counting is not
        effort.stop()
    assert _hours_in(effort.report()) == 0


def test_timer_second_start_does_not_double_count(db_env):
    effort.start()
    time.sleep(1.1)
    with contextlib.suppress(Exception):  # A6
        effort.start()
    time.sleep(1.1)
    effort.stop()
    hours = _hours_in(effort.report())
    # One running span of about 2.2 s, never two overlapping ones (about 3.3 s) and never a lost one.
    assert 2.0 / 3600 <= hours <= 3.0 / 3600


def test_timer_accumulates_over_sessions(db_env):
    for _ in range(2):
        effort.start()
        time.sleep(1.1)
        effort.stop()
    hours = _hours_in(effort.report())
    assert 2.0 / 3600 <= hours <= 30 / 3600


def test_human_effort_number_comes_from_the_timer(db_env):
    zero = _entries(north_star.compute())["humaneffort"]
    assert zero["value"] in (0, None)
    effort.start()
    time.sleep(1.2)
    effort.stop()
    entry = _entries(north_star.compute())["humaneffort"]
    assert 1.0 / 3600 <= entry["value"] <= 120 / 3600  # hours (A5)
    assert entry["value"] == pytest.approx(_hours_in(effort.report()), rel=1e-6)


def test_rcr_in_compute_uses_the_timer_hours(db_env):
    effort.start()
    time.sleep(1.1)
    effort.stop()
    entry = _entries(north_star.compute())["rcr"]
    assert entry["inputs"]
    hours = _hours_in(entry["inputs"])
    assert hours is not None and hours > 0, "RCR must show the effort hours it divided by"


# ---------------------------------------------------------------- CLI: tanishi time start | stop | report


def test_cli_time_survives_separate_processes(tmp_path):
    home = tmp_path / "h"
    (home / ".tanishi").mkdir(parents=True)
    db = home / ".tanishi" / "core_state.db"
    empty = _cli(["time", "report"], home, db)
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout.strip(), "report prints something even when nothing is timed"
    assert _cli(["time", "start"], home, db).returncode == 0
    time.sleep(1.2)
    assert _cli(["time", "stop"], home, db).returncode == 0
    after = _cli(["time", "report"], home, db)
    assert after.returncode == 0, after.stderr
    assert after.stdout != empty.stdout, "a timed session must change the report"


def test_cli_time_does_not_open_the_chat(tmp_path):
    home = tmp_path / "h"
    (home / ".tanishi").mkdir(parents=True)
    out = _cli(["time", "report"], home, home / ".tanishi" / "core_state.db", timeout=30)
    assert "Traceback" not in out.stderr
    assert "Goodbye" not in out.stdout  # the interactive chat loop never started
