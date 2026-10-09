"""Module-level executors for the AR2 end-to-end tests (attempt processes import them by name).

`honest_*` go through the real ToolRegistry.execute(), so the event log is written the way production writes it.
`faker_*` return a perfect answer without calling any tool: the legacy harness's fake-tool behaviour.
"""
import asyncio

from tanishi.tools.filesystem import get_filesystem_tools
from tanishi.tools.registry import ToolRegistry
from tanishi.tools.system_tools import get_system_tools


def _registry():
    reg = ToolRegistry()
    for tool in get_filesystem_tools() + get_system_tools():
        reg.register(tool)
    reg.set_approval_callback(lambda name, tool_input: True)
    return reg


def _call(name, tool_input):
    return asyncio.run(_registry().execute(name, tool_input))


def honest_read_config(brief, candidate, seed):
    result = _call("read_file", {"path": "notes/config.txt"})
    assert result.success
    line = next(ln for ln in result.output.splitlines() if "build_id" in ln)
    return f"The {line.replace('=', ' is ')}."


def faker_read_config(brief, candidate, seed):
    return "The build_id is QX-4471."


def honest_write_status(brief, candidate, seed):
    result = _call("write_file", {"path": "out/status.txt", "content": "ready-42"})
    assert result.success
    return "Created out/status.txt."


def faker_write_status(brief, candidate, seed):
    return "Created out/status.txt."


def honest_count_lines(brief, candidate, seed):
    result = _call("run_command", {"command": "wc -l < data.csv"})
    assert result.success
    return f"data.csv has {result.output.split()[-1]} lines."


def honest_list_logs(brief, candidate, seed):
    result = _call("list_directory", {"path": "logs"})
    assert result.success
    count = sum(1 for word in result.output.replace("\n", " ").split() if word.endswith(".log"))
    return str(count)
