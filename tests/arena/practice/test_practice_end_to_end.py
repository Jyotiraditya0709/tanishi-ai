"""AR2 exam: Practice tasks through the real Arena runner, with the real ToolRegistry writing the event log.

This is the check the unit tests cannot give: the setup script really builds the fixture, the attempt's own Core State
db receives the registry's events, and the verifier (a separate process) reads them. A candidate that answers
perfectly without calling the tool scores 0 here, exactly as the spec demands.
"""
import practice_executors as ex
import pytest
from practice_helpers import TASKS

from tanishi.arena.practice import load_practice
from tanishi.arena.runner import run


def one(name, executor):
    res = run([TASKS[f"practice.{name}"]], "exam-cfg", seeds=1, executor=executor, env_passthrough=())
    return res.results[0]


def test_honest_read_scores_1_and_the_fake_scores_0(outer_db):
    honest = one("read_config_value", ex.honest_read_config)
    assert honest.score == 1.0, honest.reason
    fake = one("read_config_value", ex.faker_read_config)
    assert fake.score == 0.0
    assert fake.failure_kind is None  # a scored 0, not a crash


def test_write_file_task_needs_the_real_call(outer_db):
    assert one("write_exact_file", ex.honest_write_status).score == 1.0
    assert one("write_exact_file", ex.faker_write_status).score == 0.0


def test_shell_count_task_runs_its_setup_and_the_shell(outer_db):
    result = one("count_lines_shell", ex.honest_count_lines)
    assert result.score == 1.0, result.reason


def test_list_directory_task_sees_exactly_the_planted_logs(outer_db):
    result = one("count_matching_files", ex.honest_list_logs)
    assert result.score == 1.0, result.reason


def test_every_setup_script_succeeds_in_a_fresh_home(outer_db):
    """Run each task with a trivial executor: setup must never be the reason for an infra failure."""
    from tanishi.arena.runner import run as run_tasks
    tasks = [t for t in load_practice() if t.setup]
    assert len(tasks) >= 6
    res = run_tasks(tasks, "exam-cfg", seeds=1, executor=ex.faker_read_config)
    assert all(r.failure_kind != "infra" for r in res.results), [r.task_id for r in res.results if r.failure_kind]


@pytest.mark.parametrize("name", ["read_config_value", "count_matching_files", "find_todo_file", "read_two_sum",
                                  "latest_report", "count_lines_shell", "write_exact_file", "append_line",
                                  "today_weekday", "os_python", "log_expense", "get_time", "flaky_probe"])
def test_a_faking_executor_scores_0_on_every_tool_task(outer_db, name):
    assert one(name, ex.faker_read_config).score == 0.0
