"""Honest executors for every Practice tool task, built on the real ToolRegistry and the real tools.

Each one calls the tools a careful agent would call, then writes its answer from what the tools really returned.
They prove the verifiers parse live tool output (after the event log's redaction), not only the exam's fixtures.
"""
import asyncio
import re

from tanishi.tools.filesystem import get_filesystem_tools
from tanishi.tools.finance import get_finance_tools
from tanishi.tools.registry import ToolDefinition, ToolRegistry
from tanishi.tools.system_tools import get_system_tools

_probe_calls = 0


async def _flaky_probe() -> str:
    """Same behaviour as tanishi.autoresearch.benchmark.autoresearch_flaky_probe, without importing that module
    (it loads the repo's .env and the brain on import)."""
    global _probe_calls
    _probe_calls += 1
    if _probe_calls % 2 == 1:
        raise RuntimeError("simulated failure")
    return "PROBE OK"


def _registry():
    reg = ToolRegistry()
    for tool in get_filesystem_tools() + get_system_tools() + get_finance_tools():
        reg.register(tool)
    reg.register(ToolDefinition(
        name="autoresearch_flaky_probe", description="probe",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_flaky_probe, category="system", risk_level="low",
    ))
    reg.set_approval_callback(lambda name, tool_input: True)
    return reg


def _call(name, tool_input=None):
    result = asyncio.run(_registry().execute(name, tool_input or {}))
    return result


def _field(text, name):
    m = re.search(rf"^\s*{name}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def find_todo_file(brief, candidate, seed):
    out = _call("search_files", {"directory": "src", "pattern": "*.py", "text": "FIXME-ORCHID"}).output
    return re.search(r"(\w+\.py):\d+", out).group(1)


def read_two_sum(brief, candidate, seed):
    left = int(_call("read_file", {"path": "data/left.txt"}).output.split()[-1])
    right = int(_call("read_file", {"path": "data/right.txt"}).output.split()[-1])
    return f"{left} + {right} = {left + right}"


def latest_report(brief, candidate, seed):
    listing = _call("list_directory", {"path": "reports"}).output
    newest = max(re.findall(r"\d{4}-\d{2}\.txt", listing))
    code = _field(_call("read_file", {"path": f"reports/{newest}"}).output, "code")
    return f"The latest report's code word is {code}."


def append_line(brief, candidate, seed):
    _call("run_command", {"command": "printf 'gamma\\n' >> journal.txt"})
    return "Added gamma to journal.txt."


def today_weekday(brief, candidate, seed):
    out = _call("get_datetime").output
    weekday = _field(out, "date").split(",")[0]
    return f"Today is {weekday}, day {int(_field(out, 'day_of_year'))} of the year."


def get_time(brief, candidate, seed):
    out = _call("get_datetime").output
    hh, mm, _ = _field(out, "time").split()[0].split(":")
    return f"It is {hh}:{mm} {_field(out, 'time').split()[-1]}."


def os_python(brief, candidate, seed):
    out = _call("get_system_info").output
    return f"This machine runs {_field(out, 'os')} with Python {_field(out, 'python')}."


def system_check(brief, candidate, seed):
    out = _call("run_command", {
        "command": "sysctl -n hw.memsize 2>/dev/null || awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo",
    }).output
    total = int(float(re.findall(r"\d+(?:\.\d+)?", out)[-1]))
    return f"You have about {total / 2**30:.1f} GB of RAM."


def log_expense(brief, candidate, seed):
    result = _call("log_expense", {"amount": 12.5, "description": "lunch", "category": "food",
                                   "payment_method": "card"})
    return "Logged it." if result.success else "Could not log it."


def flaky_probe(brief, candidate, seed):
    for _ in range(2):  # the probe fails on odd calls; a careful agent retries once
        result = _call("autoresearch_flaky_probe")
        if result.success:
            return f"The probe says {result.output}."
    return "The probe failed twice."


def wrong_but_real_call(brief, candidate, seed):
    """Really calls a tool, then gives an answer that is wrong for every task."""
    _call("get_datetime")
    return "I am not sure: maybe 3 or 4, around 1999-01-01."
