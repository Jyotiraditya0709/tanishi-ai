"""AR2 exam: tool tasks must fail when the tool was not really called (spec: the legacy harness faked tool calls).

A "real" call is what ToolRegistry.execute() leaves in the Core State event log: tool_call, then tool_result with the same
call_id and success true. The answer text alone never counts.
"""
import os
import random
import sqlite3

import pytest
from practice_helpers import TASKS, TOOL_CASES, record_call, score

from tanishi.arena.practice import needs_tool
from tanishi.core_state import open_db

NAMES = sorted(TOOL_CASES)


def real_run(name):
    _, calls, _ = TOOL_CASES[name]
    for tool, tool_input, output in calls:
        record_call(tool, tool_input, output)


def test_the_cases_cover_every_tool_task_that_needs_no_files_or_fields():
    tool_tasks = {t.id.removeprefix("practice.") for t in TASKS.values() if needs_tool(t)}
    # write_exact_file, append_line (files), log_expense (fields) and system_check (real RAM) have their own tests below
    assert tool_tasks - set(NAMES) == {"write_exact_file", "append_line", "log_expense", "system_check"}


@pytest.mark.parametrize("name", NAMES)
def test_right_answer_with_a_real_call_scores_1(name):
    real_run(name)
    assert score(name, TOOL_CASES[name][0])[0] == 1.0


@pytest.mark.parametrize("name", NAMES)
def test_right_answer_with_no_tool_call_scores_0(name):
    """The fake-tool case: the answer is perfect, the log is empty."""
    value, reason = score(name, TOOL_CASES[name][0])
    assert value == 0.0
    assert "tool" in reason.lower() or "call" in reason.lower() or "probe" in reason.lower()


@pytest.mark.parametrize("name", NAMES)
def test_wrong_answer_with_a_real_call_scores_0(name):
    real_run(name)
    assert score(name, TOOL_CASES[name][2])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_empty_or_blank_answer_scores_0(name):
    real_run(name)
    for blank in ("", "   \n", None):
        assert score(name, blank)[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_a_call_whose_result_failed_does_not_count(name):
    for tool, tool_input, output in TOOL_CASES[name][1]:
        record_call(tool, tool_input, output, success=False)
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_a_call_with_no_result_does_not_count(name):
    for tool, tool_input, output in TOOL_CASES[name][1]:
        record_call(tool, tool_input, output, finish=False)
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_a_result_for_a_different_call_id_does_not_count(name):
    for tool, tool_input, output in TOOL_CASES[name][1]:
        record_call(tool, tool_input, output, result_id="not-the-same-id")
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_the_wrong_tool_does_not_count(name):
    for _, tool_input, output in TOOL_CASES[name][1]:
        record_call("get_clipboard", tool_input, output)
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_the_answer_pasted_into_a_note_event_is_not_a_tool_call(name):
    from tanishi.core_state.events import emit
    emit("note", {"tool": "read_file", "text": TOOL_CASES[name][0]})
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_an_edited_event_log_earns_nothing(name):
    """Hash chain: someone who drops the append-only triggers and rewrites a row is caught."""
    real_run(name)
    conn = open_db(os.environ["TANISHI_CORE_STATE_DB"])
    try:
        conn.execute("DROP TRIGGER events_no_update")
        conn.execute("UPDATE events SET actor = 'forged' WHERE id = (SELECT MIN(id) FROM events)")
        conn.commit()
    finally:
        conn.close()
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


@pytest.mark.parametrize("name", NAMES)
def test_a_missing_or_unmigrated_log_scores_0_and_does_not_raise(name, monkeypatch, tmp_path):
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "nope" / "other.db"))
    assert score(name, TOOL_CASES[name][0])[0] == 0.0
    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).close()
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(bare))
    assert score(name, TOOL_CASES[name][0])[0] == 0.0


# --- tasks whose proof is partly a file or a field ---------------------------------------------------------------


def test_write_exact_file_needs_both_the_file_and_the_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert score("write_exact_file", "done")[0] == 0.0  # nothing at all
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "status.txt").write_text("ready-42\n")
    assert score("write_exact_file", "done")[0] == 0.0  # file but no tool call: the model could not have faked this in text
    record_call("write_file", {"path": "out/status.txt", "content": "ready-42"}, "Wrote 8 bytes")
    assert score("write_exact_file", "done")[0] == 1.0


def test_write_exact_file_rejects_wrong_content_even_with_a_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "status.txt").write_text("ready-43")
    record_call("write_file", {"path": "out/status.txt", "content": "ready-43"}, "ok")
    assert score("write_exact_file", "done")[0] == 0.0


def test_write_exact_file_rejects_a_call_with_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record_call("write_file", {"path": "out/status.txt", "content": "ready-42"}, "ok")
    assert score("write_exact_file", "I wrote the file")[0] == 0.0


def test_append_line_needs_the_file_to_be_exactly_alpha_beta_gamma(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record_call("write_file", {"path": "journal.txt", "content": "gamma\n", "mode": "append"}, "ok")
    (tmp_path / "journal.txt").write_text("alpha\nbeta\n")
    assert score("append_line", "done")[0] == 0.0  # nothing appended
    (tmp_path / "journal.txt").write_text("gamma\n")
    assert score("append_line", "done")[0] == 0.0  # existing lines lost
    (tmp_path / "journal.txt").write_text("alpha\nbeta\ngamma\n")
    assert score("append_line", "done")[0] == 1.0


def test_append_line_without_a_call_scores_0(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "journal.txt").write_text("alpha\nbeta\ngamma\n")  # e.g. left behind by something that was not a tool call
    assert score("append_line", "done")[0] == 0.0


def test_log_expense_checks_the_fields_of_the_real_call():
    assert score("log_expense", "Logged.")[0] == 0.0  # no call
    record_call("log_expense", {"amount": 125.0, "payment_method": "card"}, "Logged")
    assert score("log_expense", "Logged.")[0] == 0.0  # wrong amount
    record_call("log_expense", {"amount": 12.5, "payment_method": "cash"}, "Logged")
    assert score("log_expense", "Logged.")[0] == 0.0  # wrong method
    record_call("log_expense", {"amount": 12.5, "payment_method": "card", "description": "lunch"}, "Logged")
    assert score("log_expense", "Logged.")[0] == 1.0


def test_log_expense_ignores_a_failed_call():
    record_call("log_expense", {"amount": 12.5, "payment_method": "card"}, "", success=False)
    assert score("log_expense", "Logged.")[0] == 0.0


# --- tool-specific answer rules ----------------------------------------------------------------------------------


def test_get_time_accepts_24_hour_and_12_hour_forms_of_the_tools_time():
    real_run("get_time")
    for text in ("15:45", "It's 3:45 PM", "03:45 pm right now"):
        assert score("get_time", text)[0] == 1.0, text
    for text in ("15:46", "4:45 PM", "It is afternoon"):
        assert score("get_time", text)[0] == 0.0, text


def test_get_time_uses_the_time_the_tool_returned_not_the_clock_now():
    record_call("get_datetime", {}, "date: Friday, October 09, 2026\n  iso: 2026-10-09T01:02:03.000000\n  day_of_year: 282")
    assert score("get_time", "1:02 AM")[0] == 1.0


def test_today_weekday_needs_both_the_weekday_and_the_day_number():
    real_run("today_weekday")
    assert score("today_weekday", "Friday")[0] == 0.0
    assert score("today_weekday", "day 282")[0] == 0.0
    assert score("today_weekday", "Friday, day 282")[0] == 1.0
    assert score("today_weekday", "Friday, day 283")[0] == 0.0


def test_os_python_needs_the_python_version_and_os_from_the_tool():
    real_run("os_python")
    assert score("os_python", "Darwin")[0] == 0.0
    assert score("os_python", "Python 3.12")[0] == 0.0
    assert score("os_python", "Darwin, Python 3.12.4")[0] == 1.0
    assert score("os_python", "macOS, Python 3.11.9")[0] == 0.0


def test_find_todo_file_rejects_listing_every_file():
    real_run("find_todo_file")
    assert score("find_todo_file", "a.py, b.py, c.py, d.py")[0] == 0.0
    assert score("find_todo_file", "src/c.py")[0] == 1.0
    assert score("find_todo_file", "abc.py")[0] == 0.0


def test_count_matching_files_rejects_a_list_of_numbers():
    real_run("count_matching_files")
    assert score("count_matching_files", "7 log files and 3 text files")[0] == 0.0
    assert score("count_matching_files", "There are 7 .log files.")[0] == 1.0


def test_count_lines_shell_needs_the_shell():
    record_call("read_file", {"path": "data.csv"}, "row,1\n" * 12)
    assert score("count_lines_shell", "12")[0] == 0.0
    record_call("run_command", {"command": "wc -l data.csv"}, "Output:\n12 data.csv")
    assert score("count_lines_shell", "12")[0] == 1.0


def test_latest_report_rejects_older_codes_in_the_answer():
    real_run("latest_report")
    assert score("latest_report", "COBALT (not AMBER)")[0] == 0.0


def test_system_check_needs_the_real_amount_of_ram_and_a_system_call():
    try:
        gib = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError, AttributeError):
        pytest.skip("cannot read physical memory here")
    right, wrong = f"{gib:.0f} GB", f"{gib * 2:.0f} GB"
    assert score("system_check", right)[0] == 0.0  # no call
    record_call("get_system_info", {}, "System Information:\n  os: Darwin 25.2.0")
    assert score("system_check", right)[0] == 1.0
    assert score("system_check", wrong)[0] == 0.0
    assert score("system_check", "plenty")[0] == 0.0


def test_flaky_probe_needs_a_successful_call_that_said_probe_ok():
    record_call("autoresearch_flaky_probe", {}, "", success=False)  # the odd call fails, the registry retries
    assert score("flaky_probe", "PROBE OK")[0] == 0.0
    record_call("autoresearch_flaky_probe", {}, "PROBE OK")
    assert score("flaky_probe", "PROBE OK")[0] == 1.0
    assert score("flaky_probe", "it did not work")[0] == 0.0


# --- properties --------------------------------------------------------------------------------------------------


ALL_TOOL_NAMES = sorted(t.id.removeprefix("practice.") for t in TASKS.values() if needs_tool(t))


def _noise(rng):
    pool = ["QX-4471", "COBALT", "c.py", "442", "7", "12", "PROBE OK", "Friday", "282", "3:45", "ready-42", "gamma",
            "Darwin", "3.12", "GB", "done", "yes", "\n", " ", "the", "42.5"]
    return " ".join(rng.choice(pool) for _ in range(rng.randint(1, 12)))


def test_property_no_text_ever_earns_credit_without_a_real_call():
    """Whatever the answer says, an empty event log (and no files) means 0 for every tool task."""
    rng = random.Random(20261009)
    for name in ALL_TOOL_NAMES:
        for _ in range(60):
            assert score(name, _noise(rng))[0] == 0.0, name


def test_property_scores_stay_in_range_and_reasons_never_leak_the_answer():
    rng = random.Random(7)
    secrets = ["QX-4471", "COBALT", "FIXME-ORCHID", "ready-42", "442", "PROBE OK"]
    for name in ALL_TOOL_NAMES:
        for _ in range(30):
            value, reason = score(name, _noise(rng))
            assert 0.0 <= value <= 1.0
            assert not any(s in reason for s in secrets), (name, reason)


@pytest.mark.parametrize("name", NAMES)
def test_property_extra_unrelated_calls_and_repeats_do_not_change_the_score(name):
    """Monotone and idempotent: more events around the real call neither remove nor add credit."""
    record_call("get_clipboard", {}, "noise")
    record_call(*(TOOL_CASES[name][1][0][0], {"path": "elsewhere"}, "nothing useful"), success=False)
    real_run(name)
    first = score(name, TOOL_CASES[name][0])
    record_call("get_clipboard", {}, "more noise")
    assert score(name, TOOL_CASES[name][0]) == first == (1.0, first[1])
    assert score(name, TOOL_CASES[name][0]) == first
