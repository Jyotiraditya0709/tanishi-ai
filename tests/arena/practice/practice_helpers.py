"""Helpers for the AR2 exam: fake the attempt's event log the way ToolRegistry.execute() writes it."""
import uuid

from tanishi.arena.practice import load_practice
from tanishi.arena.verifiers import normalize, resolve
from tanishi.core_state.events import emit

TASKS = {t.id: t for t in load_practice()}


def record_call(tool, tool_input=None, output="", success=True, *, finish=True, result_id=None):
    """Write tool_call then tool_result with one call_id, as the registry does (decision: see tools/registry.py)."""
    call_id = uuid.uuid4().hex
    emit("tool_call", {"tool": tool, "call_id": call_id, "task_id": None, "input": tool_input or {}})
    if finish:
        emit("tool_result", {"tool": tool, "call_id": result_id or call_id, "task_id": None, "success": success,
                             "output": output, "error": None if success else "boom", "ms": 1.0})
    return call_id


def score(name, output):
    """Run a task's verifier the way the runner does and return (score, reason)."""
    task = TASKS[f"practice.{name}"]
    verdict = normalize(resolve(task.verifier)(task, output))
    return verdict.score, verdict.reason


DATETIME_OUTPUT = (
    "Current Date & Time:\n  date: Friday, October 09, 2026\n  time: 03:45:12 PM\n  iso: 2026-10-09T15:45:12.123456\n"
    "  timestamp: 1791560712\n  day_of_year: 282\n  week_number: 40"
)
SYSINFO_OUTPUT = (
    "System Information:\n  os: Darwin 25.2.0\n  machine: arm64\n  processor: arm\n  python: 3.12.4\n"
    "  hostname: box\n  cwd: /tmp/x\n  user: someone\n  home: /tmp/x\n  disk_total: 500.0 GB"
)

# name -> (correct answer, [(tool, input, output)] a real run would record, a wrong answer for the same task)
TOOL_CASES = {
    "read_config_value": (
        "The build_id is QX-4471.",
        [("read_file", {"path": "notes/config.txt"}, "region=eu-west\nbuild_id=QX-4471\nretries=3\n")],
        "The build_id is QX-4417.",
    ),
    "count_matching_files": (
        "7",
        [("list_directory", {"path": "logs"}, "run1.log run2.log run3.log note1.txt")],
        "10",
    ),
    "find_todo_file": (
        "c.py",
        [("search_files", {"directory": "src", "pattern": "*.py", "text": "FIXME-ORCHID"}, "  c.py:3  # FIXME-ORCHID")],
        "a.py",
    ),
    "read_two_sum": (
        "173 + 269 = 442",
        [("read_file", {"path": "data/left.txt"}, "173\n"), ("read_file", {"path": "data/right.txt"}, "269\n")],
        "441",
    ),
    "latest_report": (
        "The March code word is COBALT.",
        [("list_directory", {"path": "reports"}, "2026-01.txt 2026-02.txt 2026-03.txt"),
         ("read_file", {"path": "reports/2026-03.txt"}, "code: COBALT\n")],
        "The code word is AMBER.",
    ),
    "today_weekday": (
        "Today is Friday, and it is day 282 of the year.",
        [("get_datetime", {}, DATETIME_OUTPUT)],
        "Today is Monday, day 100.",
    ),
    "os_python": (
        "macOS (Darwin) running Python 3.12.4.",
        [("get_system_info", {}, SYSINFO_OUTPUT)],
        "Windows with Python 2.7.",
    ),
    "count_lines_shell": (
        "data.csv has 12 lines.",
        [("run_command", {"command": "wc -l < data.csv"}, "Output:\n12")],
        "data.csv has 13 lines.",
    ),
    "get_time": (
        "It is 3:45 PM.",
        [("get_datetime", {}, DATETIME_OUTPUT)],
        "It is 9:10 AM.",
    ),
    "flaky_probe": (
        "The probe says PROBE OK.",
        [("autoresearch_flaky_probe", {}, "PROBE OK")],
        "The probe failed.",
    ),
}
