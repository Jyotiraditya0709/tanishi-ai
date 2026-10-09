"""AR1 implementer tests: executors, setup, isolation details and RunResult."""
import json
import os
import time
from pathlib import Path

import pytest

from tanishi.arena.runner import RunResult, run
from tanishi.arena.task import Task


def _task(verifier, n=1, **over):
    fields = {
        "id": f"impl-{n}", "family": "impl", "tier": "practice", "prompt": "2+2?",
        "verifier": f"arena_executors.{verifier}", "timeout_s": 5, "tags": ["impl"],
    }
    fields.update(over)
    return Task(**fields)


def _ex(name):
    import arena_executors

    return getattr(arena_executors, name)


def _rows(conn):
    return conn.execute("SELECT task_set, seed, score, cost, meta FROM experiments ORDER BY seed").fetchall()


def test_executor_output_reaches_the_verifier_and_cost_is_recorded(outer_db):
    res = run([_task("is_four")], "cfg", seeds=2, executor=_ex("plain_string"))
    assert [r.score for r in res.results] == [1.0, 1.0]
    res = run([_task("always_one", 2)], "cfg", seeds=1, executor=_ex("echo"))
    assert res.results[0].cost == 0.25
    assert sorted(r[3] for r in _rows(outer_db)) == [0.0, 0.0, 0.25]


@pytest.mark.parametrize("name", ["crash", "empty", "none_output", "wrong_type", "hard_exit"])
def test_crashed_or_empty_candidate_scores_zero_even_if_verifier_would_pay(outer_db, name):
    res = run([_task("always_one")], "cfg", seeds=2, executor=_ex(name))
    assert [r.score for r in res.results] == [0.0, 0.0]
    assert [r[2] for r in _rows(outer_db)] == [0.0, 0.0]


def test_empty_output_keeps_its_cost(outer_db):
    res = run([_task("always_one")], "cfg", seeds=1, executor=_ex("empty"))
    assert res.results[0].cost == 0.5 and res.results[0].reason == "empty output"


def test_bad_cost_is_stored_as_zero(outer_db):
    run([_task("always_one")], "cfg", seeds=1, executor=_ex("bad_cost"))
    assert _rows(outer_db)[0][3] == 0.0


def test_hanging_candidate_times_out_at_zero():
    start = time.monotonic()
    res = run([_task("always_one", timeout_s=1)], "cfg", seeds=1, executor=_ex("hang"))
    assert time.monotonic() - start < 20
    assert res.results[0].score == 0.0 and "timed out" in res.results[0].reason


def test_no_executor_means_verifier_gets_none():
    assert run([_task("output_is_none")], "cfg", seeds=1).results[0].score == 1.0


def test_unpicklable_executor_is_refused_before_anything_runs(outer_db):
    with pytest.raises(TypeError):
        run([_task("always_one")], "cfg", seeds=1, executor=lambda t, c, s: "x")
    assert _rows(outer_db) == []


def test_unresolvable_verifier_scores_zero(outer_db):
    t = Task(id="unresolvable", family="impl", tier="practice", prompt="p", verifier="no_such_module_anywhere.check",
             timeout_s=5)
    assert [r.score for r in run([t], "cfg", seeds=2).results] == [0.0, 0.0]


def test_duplicate_task_ids_are_refused(outer_db):
    with pytest.raises(ValueError):
        run([_task("always_one"), _task("always_one")], "cfg", seeds=1)
    assert _rows(outer_db) == []


def test_attempt_is_isolated_from_callers_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("TANISHI_DB_PATH", str(tmp_path / "legacy.db"))
    monkeypatch.setenv("SKILLS_PATH", str(tmp_path / "skills"))
    caller_home = os.environ["HOME"]
    res = run([_task("report_env")], "cfg", seeds=1)
    seen = json.loads(res.results[0].reason)
    assert seen["home"] != caller_home
    assert seen["tanishi_home"] == str(Path(seen["home"], ".tanishi"))
    assert seen["db"] == str(Path(seen["tanishi_home"], "core_state.db")) and seen["db_exists"]
    assert Path(seen["cwd"]).resolve() == Path(seen["home"]).resolve()
    assert seen["legacy"] == []
    assert not seen["has_empty_path"]
    assert not Path(seen["home"]).exists(), "the temp home is removed after the attempt"
    assert os.environ["TANISHI_DB_PATH"] == str(tmp_path / "legacy.db"), "caller env untouched"


def test_setup_runs_in_the_temp_home_first():
    t = _task("setup_marker", setup="echo ready > prepared.txt")
    assert run([t], "cfg", seeds=1).results[0].score == 1.0


def test_failing_setup_scores_zero():
    t = _task("always_one", setup="exit 7")
    res = run([t], "cfg", seeds=1)
    assert res.results[0].score == 0.0 and "setup exited 7" in res.results[0].reason


def test_setup_that_hangs_is_killed_with_its_children(tmp_path):
    marker = tmp_path / "late"
    t = _task("always_one", setup=f"sleep 3; touch {marker}", timeout_s=1)
    start = time.monotonic()
    assert run([t], "cfg", seeds=1).results[0].score == 0.0
    assert time.monotonic() - start < 10
    time.sleep(3)
    assert not marker.exists()


def test_background_jobs_do_not_outlive_the_attempt(tmp_path):
    marker = tmp_path / "late"
    res = run([_task("always_one", prompt=str(marker))], "cfg", seeds=1, executor=_ex("leave_background_job"))
    assert res.results[0].score == 1.0
    time.sleep(3)
    assert not marker.exists()


def test_run_result_summaries(outer_db):
    tasks = [_task("always_one", 1), _task("is_four", 2)]
    res = run(tasks, "cfg", seeds=2, executor=_ex("echo"))
    assert isinstance(res, RunResult)
    assert len(res.results) == 4 and res.seeds == 2 and res.candidate == "cfg"
    assert res.by_task() == {"impl-1": 1.0, "impl-2": 0.0}
    assert res.score == 0.5 and res.cost == 1.0
    rows = _rows(outer_db)
    assert {r[0] for r in rows} == {res.task_set}
    metas = [json.loads(r[4]) for r in rows]
    assert {m["run_id"] for m in metas} == {res.run_id}
    assert {m["task_id"] for m in metas} == {"impl-1", "impl-2"}


def test_empty_task_set_has_zero_score():
    res = run([], "cfg", seeds=1)
    assert res.results == () and res.score == 0.0
