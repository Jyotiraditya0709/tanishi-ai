"""AR2 implementer tests: every Practice tool task, end to end, with the real tools behind ToolRegistry.execute().

The exam drives four tool tasks this way and checks the rest against hand-written tool output. These run the rest live,
so a change to a tool's output format (get_datetime, get_system_info, search_files, ...) breaks a test here rather
than silently zeroing a task in the Arena.
"""
import pytest
import real_tool_executors as ex
from practice_helpers import TASKS

from tanishi.arena.practice import load_practice, needs_tool
from tanishi.arena.runner import run

HONEST = ["find_todo_file", "read_two_sum", "latest_report", "append_line", "today_weekday", "get_time",
          "os_python", "system_check", "log_expense", "flaky_probe"]


def one(name, executor):
    res = run([TASKS[f"practice.{name}"]], "ar2-impl", seeds=1, executor=executor, env_passthrough=())
    return res.results[0]


@pytest.mark.parametrize("name", HONEST)
def test_an_honest_agent_using_the_real_tool_scores_1(outer_db, name):
    result = one(name, getattr(ex, name))
    assert result.failure_kind is None, result
    assert result.score == 1.0, result.reason


@pytest.mark.parametrize("name", sorted(t.id.removeprefix("practice.") for t in load_practice() if needs_tool(t)))
def test_a_real_call_with_a_wrong_answer_scores_0(outer_db, name):
    assert one(name, ex.wrong_but_real_call).score == 0.0


def test_at_least_8_of_the_20_new_tasks_need_a_tool():
    new = [t for t in load_practice() if "legacy" not in t.tags]
    assert len(new) == 20
    assert sum(needs_tool(t) for t in new) >= 8
