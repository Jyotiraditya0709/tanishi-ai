"""AR1 exam: run(task_set, candidate, seeds=3) -> RunResult.

One check per acceptance line, plus edges the spec implies and one property test (row accounting).
"""
import dataclasses
import math
import os
import random
import time
from pathlib import Path

import pytest
from arena_helpers import CANDIDATE, experiments, stub_executor

from tanishi.arena.runner import run as _run


def run(task_set, candidate, seeds=3):
    """The exam's run(): the runner refuses to run without an executor (RT-2), so every call passes the stub."""
    return _run(task_set, candidate, seeds=seeds, executor=stub_executor)


def _scores(conn):
    return [r[4] for r in experiments(conn)]


# --- acceptance 2: one experiments row per seed, with score and cost ---------------------------------------------


def test_default_is_three_seeds_per_task(make_task, outer_db):
    run([make_task("full")], CANDIDATE)
    assert len(experiments(outer_db)) == 3


@pytest.mark.parametrize("seeds", [1, 2, 5])
def test_one_row_per_seed_per_task(make_task, outer_db, seeds):
    tasks = [make_task("full"), make_task("half")]
    run(tasks, CANDIDATE, seeds=seeds)
    rows = experiments(outer_db)
    assert len(rows) == 2 * seeds
    for t in tasks:
        task_rows = [r for r in rows if t.id in (r[2] or "") or t.id in (r[6] or "")]
        assert len(task_rows) == seeds, "every row must be attributable to its task"
        assert len({r[3] for r in task_rows}) == seeds, "seeds must be distinct per task"


def test_rows_carry_candidate_score_and_cost(make_task, outer_db):
    run([make_task("half")], CANDIDATE, seeds=2)
    for _id, candidate, _ts, seed, score, cost, _meta in experiments(outer_db):
        assert candidate == CANDIDATE
        assert seed is not None
        assert score == pytest.approx(0.5)
        assert cost is not None and math.isfinite(cost) and cost >= 0


def test_row_ids_are_unique_and_a_second_run_adds_rows(make_task, outer_db):
    t = make_task("full")
    run([t], CANDIDATE, seeds=2)
    run([t], CANDIDATE, seeds=2)
    rows = experiments(outer_db)
    assert len(rows) == 4
    assert len({r[0] for r in rows}) == 4


def test_run_without_an_executor_is_refused_and_records_nothing(make_task, outer_db):
    with pytest.raises(ValueError):
        _run([make_task("full")], CANDIDATE, seeds=2)
    assert experiments(outer_db) == []


def test_run_returns_a_result_object(make_task):
    assert run([make_task("full")], CANDIDATE, seeds=1) is not None


@pytest.mark.parametrize("seeds", [0, -1])
def test_non_positive_seeds_is_an_error_and_records_nothing(make_task, outer_db, seeds):
    with pytest.raises(ValueError):
        run([make_task("full")], CANDIDATE, seeds=seeds)
    assert experiments(outer_db) == []


def test_empty_candidate_is_an_error_and_records_nothing(make_task, outer_db):
    with pytest.raises(ValueError):
        run([make_task("full")], "", seeds=1)
    assert experiments(outer_db) == []


def test_empty_task_set_records_nothing(outer_db):
    try:
        run([], CANDIDATE, seeds=2)
    except ValueError:
        pass  # refusing is as acceptable as a no-op
    assert experiments(outer_db) == []


# --- verifier scores are taken as returned when valid ------------------------------------------------------------


def test_scores_follow_the_verifier(make_task, outer_db):
    run([make_task("full"), make_task("half"), make_task("zero")], CANDIDATE, seeds=1)
    assert sorted(_scores(outer_db)) == [0.0, 0.5, 1.0]


# --- acceptance 3: a crashed or empty run scores 0, never positive -----------------------------------------------


def test_verifier_exception_scores_zero_and_is_still_recorded(make_task, outer_db):
    run([make_task("boom")], CANDIDATE, seeds=3)
    assert _scores(outer_db) == [0.0, 0.0, 0.0]


def test_one_crash_does_not_stop_or_taint_other_tasks(make_task, outer_db):
    good, bad = make_task("full"), make_task("boom")
    run([bad, good, make_task("full")], CANDIDATE, seeds=2)
    scores = sorted(_scores(outer_db))
    assert scores == [0.0, 0.0, 1.0, 1.0, 1.0, 1.0]


@pytest.mark.parametrize("verifier", ["nan", "wrong_shape"])
def test_malformed_verifier_output_scores_zero(make_task, outer_db, verifier):
    run([make_task(verifier)], CANDIDATE, seeds=1)
    assert _scores(outer_db) == [0.0]


@pytest.mark.parametrize("verifier", ["too_high", "negative", "inf"])
def test_out_of_range_score_never_escapes_unit_interval(make_task, outer_db, verifier):
    run([make_task(verifier)], CANDIDATE, seeds=1)
    (score,) = _scores(outer_db)
    assert 0.0 <= score <= 1.0 and not math.isnan(score)


def test_negative_score_is_never_stored_as_negative_or_positive_credit(make_task, outer_db):
    run([make_task("negative")], CANDIDATE, seeds=1)
    assert _scores(outer_db) == [0.0]


def test_unresolvable_verifier_scores_zero(make_task, outer_db):
    t = dataclasses.replace(make_task("full"), verifier="no_such_module_anywhere.check")
    run([t], CANDIDATE, seeds=2)
    assert _scores(outer_db) == [0.0, 0.0]


def test_verifier_missing_function_scores_zero(make_task, outer_db):
    run([make_task("does_not_exist")], CANDIDATE, seeds=1)
    assert _scores(outer_db) == [0.0]


def test_timeout_scores_zero_and_does_not_hang(make_task, outer_db):
    start = time.monotonic()
    run([make_task("slow", timeout_s=1)], CANDIDATE, seeds=1)
    assert time.monotonic() - start < 20
    assert _scores(outer_db) == [0.0]


# --- acceptance 1: fresh TANISHI_HOME and fresh core_state db per task -------------------------------------------


def test_each_task_and_seed_gets_its_own_home_and_db(make_task, probe_module, outer_db):
    outer_home = Path(os.environ["HOME"], ".tanishi")
    outer_db_path = os.environ["TANISHI_CORE_STATE_DB"]
    run([make_task("full"), make_task("full")], CANDIDATE, seeds=2)
    seen = probe_module.read()
    assert len(seen) == 4
    homes = [s["home"] for s in seen]
    dbs = [s["db"] for s in seen]
    assert all(homes) and all(dbs)
    assert len(set(homes)) == 4, "no two runs may share a TANISHI_HOME"
    assert len(set(dbs)) == 4, "no two runs may share a core_state db"
    for s in seen:
        assert Path(s["home"]).resolve() != outer_home.resolve()
        assert s["db"] != outer_db_path
        assert not Path(s["home"]).resolve().is_relative_to(outer_home.resolve())
        assert s["home_is_dir"]


def test_environment_is_restored_after_run(make_task):
    before = (os.environ.get("TANISHI_HOME"), os.environ.get("TANISHI_CORE_STATE_DB"))
    run([make_task("full")], CANDIDATE, seeds=1)
    assert (os.environ.get("TANISHI_HOME"), os.environ.get("TANISHI_CORE_STATE_DB")) == before


def test_environment_is_restored_even_when_everything_crashes(make_task):
    before = (os.environ.get("TANISHI_HOME"), os.environ.get("TANISHI_CORE_STATE_DB"))
    run([make_task("boom")], CANDIDATE, seeds=1)
    assert (os.environ.get("TANISHI_HOME"), os.environ.get("TANISHI_CORE_STATE_DB")) == before


def test_state_written_by_a_task_never_reaches_real_home_or_next_task(make_task, probe_module, outer_db):
    real_home = Path(os.environ["HOME"], ".tanishi")
    run([make_task("pollute"), make_task("pollute")], CANDIDATE, seeds=2)
    assert not (real_home / "skills").exists(), "the legacy benchmark leaked skills into real chats"
    homes = {s["home"] for s in probe_module.read()}
    assert len(homes) == 4


def test_temp_homes_are_not_left_inside_the_real_home(make_task):
    real_home = Path(os.environ["HOME"], ".tanishi")
    run([make_task("full")], CANDIDATE, seeds=2)
    leftovers = list(real_home.rglob("*")) if real_home.exists() else []
    # only the caller's own core_state db (and its wal/shm) may live there
    assert all(p.name.startswith("core_state.db") for p in leftovers if p.is_file())


def test_legacy_databases_are_never_created(make_task):
    run([make_task("full")], CANDIDATE, seeds=1)
    home = Path(os.environ["HOME"])
    assert not (home / ".tanishi" / "tanishi.db").exists()
    assert not (home / ".tanishi" / "finance.db").exists()


def test_callers_existing_experiments_are_kept_and_one_row_is_added(make_task, outer_db):
    outer_db.execute(
        "INSERT INTO experiments (id, ts, candidate, baseline, task_set, seed, score, cost, meta) "
        "VALUES ('pre', 't', 'old', NULL, 'x', 0, 0.9, 0.0, NULL)"
    )
    outer_db.commit()
    run([make_task("full")], CANDIDATE, seeds=1)
    ids = [r[0] for r in experiments(outer_db)]
    assert "pre" in ids and len(ids) == 2, "caller's pre-existing rows are kept, and exactly one is added"


# --- property: row accounting and score bounds hold for any mix -------------------------------------------------


VERIFIERS = ["full", "half", "zero", "boom", "nan", "wrong_shape", "too_high", "negative", "does_not_exist"]
ZERO_ALWAYS = {"zero", "boom", "nan", "wrong_shape", "does_not_exist"}


@pytest.mark.parametrize("rng_seed", range(8))
def test_property_rows_equal_tasks_times_seeds_and_scores_are_bounded(make_task, outer_db, rng_seed):
    rng = random.Random(rng_seed)
    names = [rng.choice(VERIFIERS) for _ in range(rng.randint(1, 5))]
    seeds = rng.randint(1, 3)
    tasks = [make_task(n) for n in names]
    run(tasks, CANDIDATE, seeds=seeds)
    rows = experiments(outer_db)
    assert len(rows) == len(tasks) * seeds
    for r in rows:
        score, cost = r[4], r[5]
        assert score is not None and 0.0 <= score <= 1.0 and not math.isnan(score)
        assert cost is not None and cost >= 0 and math.isfinite(cost)
    zero_tasks = sum(1 for n in names if n in ZERO_ALWAYS)
    assert sum(1 for s in _scores(outer_db) if s == 0.0) >= zero_tasks * seeds
