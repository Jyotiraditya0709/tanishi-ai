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


def test_no_executor_is_refused_and_records_nothing(outer_db):
    with pytest.raises(ValueError):
        run([_task("always_one")], "cfg", seeds=1)
    assert _rows(outer_db) == []


def test_executor_gets_a_brief_without_verifier_or_setup():
    t = _task("output_is_reason", setup="true")
    seen = json.loads(run([t], "cfg", seeds=1, executor=_ex("report_brief")).results[0].reason)
    assert seen["type"] == "TaskBrief"
    assert seen["fields"] == ["family", "id", "prompt", "tags", "tier", "timeout_s"]


def test_unpicklable_executor_is_refused_before_anything_runs(outer_db):
    with pytest.raises(TypeError):
        run([_task("always_one")], "cfg", seeds=1, executor=lambda t, c, s: "x")
    assert _rows(outer_db) == []


def test_unresolvable_verifier_scores_zero(outer_db):
    t = Task(id="unresolvable", family="impl", tier="practice", prompt="p", verifier="no_such_module_anywhere.check",
             timeout_s=5)
    res = run([t], "cfg", seeds=2, executor=_ex("plain_string"))
    assert [r.score for r in res.results] == [0.0, 0.0]
    assert {r.failure_kind for r in res.results} == {"verifier"}


def test_duplicate_task_ids_are_refused(outer_db):
    with pytest.raises(ValueError, match="unique"):
        run([_task("always_one"), _task("always_one")], "cfg", seeds=1, executor=_ex("plain_string"))
    assert _rows(outer_db) == []


def test_attempt_is_isolated_from_callers_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("TANISHI_DB_PATH", str(tmp_path / "legacy.db"))
    monkeypatch.setenv("SKILLS_PATH", str(tmp_path / "skills"))
    caller_home = os.environ["HOME"]
    res = run([_task("report_env")], "cfg", seeds=1, executor=_ex("plain_string"))
    seen = json.loads(res.results[0].reason)
    assert seen["home"] != caller_home
    assert seen["tanishi_home_in_home"] and seen["db_in_tanishi_home"] and seen["cwd_is_home"]
    assert seen["legacy"] == []
    assert not seen["has_empty_path"]
    assert seen["tmpdir_in_home"]
    assert not Path(seen["home"]).exists(), "the temp home is removed after the attempt"
    assert os.environ["TANISHI_DB_PATH"] == str(tmp_path / "legacy.db"), "caller env untouched"


MINIMAL_ENV = {"PATH", "LANG", "HOME", "TANISHI_HOME", "TANISHI_CORE_STATE_DB", "TMPDIR"}
# Set by macOS itself in every process that loads CoreFoundation, not inherited from the caller.
PLATFORM_ENV = {"__CF_USER_TEXT_ENCODING"}


def test_attempt_processes_get_a_minimal_environment(monkeypatch):
    monkeypatch.setenv("ARENA_TEST_SECRET", "not-for-attempts")
    monkeypatch.setenv("ARENA_TEST_PASSED", "named-by-caller")
    res = run([_task("report_env")], "cfg", seeds=1, executor=_ex("plain_string"))
    assert set(json.loads(res.results[0].reason)["env"]) - PLATFORM_ENV <= MINIMAL_ENV
    seen = json.loads(run([_task("output_is_reason", 2)], "cfg", seeds=1, executor=_ex("report_brief"))
                      .results[0].reason)
    assert set(seen["env"]) - PLATFORM_ENV <= MINIMAL_ENV and seen["passed"] is None


def test_env_passthrough_names_variables_the_attempt_may_see(monkeypatch):
    monkeypatch.setenv("ARENA_TEST_SECRET", "not-for-attempts")
    monkeypatch.setenv("ARENA_TEST_PASSED", "named-by-caller")
    res = run([_task("output_is_reason")], "cfg", seeds=1, executor=_ex("report_brief"),
              env_passthrough=["ARENA_TEST_PASSED", "ARENA_TEST_NOT_SET"])
    seen = json.loads(res.results[0].reason)
    assert seen["passed"] == "named-by-caller"
    assert "ARENA_TEST_SECRET" not in seen["env"] and "ARENA_TEST_NOT_SET" not in seen["env"]


@pytest.mark.parametrize(
    "bad,error",
    [("ARENA_TEST_PASSED", TypeError), ({"A": "1"}, TypeError), (["A=1"], ValueError), ([""], ValueError),
     (["HOME"], ValueError), (["TANISHI_CORE_STATE_DB"], ValueError), ([3], ValueError)],
)
def test_env_passthrough_takes_names_only(outer_db, bad, error):
    with pytest.raises(error):
        run([_task("always_one")], "cfg", seeds=1, executor=_ex("plain_string"), env_passthrough=bad)
    assert _rows(outer_db) == []


def test_failures_store_only_a_kind_and_an_exception_class(outer_db):
    tasks = [_task("always_one", 1), _task("always_one", 2, setup="exit 3"), _task("raise_exit", 3, prompt="secret-x")]
    res = run(tasks, "cfg", seeds=1, executor=_ex("crash"))
    assert [r.failure_kind for r in res.results] == ["candidate", "infra", "candidate"]
    metas = [json.loads(r[4]) for r in _rows(outer_db)]
    assert sorted((m["failure_kind"], m["error"]) for m in metas) == [
        ("candidate", "RuntimeError"), ("candidate", "RuntimeError"), ("infra", None)]
    assert all("blew up" not in json.dumps(m) for m in metas)
    res = run([_task("raise_exit", 4, prompt="secret-x")], "cfg", seeds=1, executor=_ex("plain_string"))
    assert res.results[0].failure_kind == "verifier"
    meta = json.loads(_rows(outer_db)[-1][4])
    assert meta["error"] == "SystemExit" and "secret-x" not in json.dumps(meta)


def test_a_good_attempt_has_no_failure_kind(outer_db):
    res = run([_task("is_four")], "cfg", seeds=1, executor=_ex("plain_string"))
    assert res.results[0].failure_kind is None
    meta = json.loads(_rows(outer_db)[0][4])
    assert meta["failure_kind"] is None and meta["error"] is None


def test_a_run_that_dies_partway_leaves_no_rows(outer_db, monkeypatch):
    from tanishi.arena import runner

    real = runner._isolated_attempt
    calls = {"n": 0}

    def dies_on_the_second_attempt(*args):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(*args)

    monkeypatch.setattr(runner, "_isolated_attempt", dies_on_the_second_attempt)
    with pytest.raises(KeyboardInterrupt):
        run([_task("always_one", 1), _task("always_one", 2)], "cfg", seeds=1, executor=_ex("plain_string"))
    assert calls["n"] == 2
    assert _rows(outer_db) == []


def test_an_attempt_that_cannot_run_scores_zero_and_the_run_goes_on(outer_db, monkeypatch):
    from tanishi.arena import runner

    real = runner._isolated_attempt

    def overflows_on_task_1(task, *args):
        if task.id == "impl-1":
            raise OverflowError("timeout too large")
        return real(task, *args)

    monkeypatch.setattr(runner, "_isolated_attempt", overflows_on_task_1)
    res = run([_task("always_one", 1), _task("always_one", 2)], "cfg", seeds=1, executor=_ex("plain_string"))
    assert [(r.score, r.failure_kind) for r in res.results] == [(0.0, "infra"), (1.0, None)]
    assert len(_rows(outer_db)) == 2


def test_rows_of_one_run_share_a_run_id(outer_db):
    res = run([_task("always_one", 1), _task("is_four", 2)], "cfg", seeds=2, executor=_ex("plain_string"))
    assert {json.loads(r[4])["run_id"] for r in _rows(outer_db)} == {res.run_id}


def test_setup_runs_in_the_temp_home_first():
    t = _task("setup_marker", setup="echo ready > prepared.txt")
    assert run([t], "cfg", seeds=1, executor=_ex("plain_string")).results[0].score == 1.0


def test_failing_setup_scores_zero():
    t = _task("always_one", setup="exit 7")
    res = run([t], "cfg", seeds=1, executor=_ex("plain_string"))
    assert res.results[0].score == 0.0 and "setup exited 7" in res.results[0].reason
    assert res.results[0].failure_kind == "infra"


def test_setup_that_hangs_is_killed_with_its_children(tmp_path):
    marker = tmp_path / "late"
    t = _task("always_one", setup=f"sleep 3; touch {marker}", timeout_s=1)
    start = time.monotonic()
    assert run([t], "cfg", seeds=1, executor=_ex("plain_string")).results[0].score == 0.0
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
    res = run([], "cfg", seeds=1, executor=_ex("plain_string"))
    assert res.results == () and res.score == 0.0
