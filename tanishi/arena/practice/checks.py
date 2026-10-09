"""Verifiers for the 20 new Practice tasks. Plain code, no LLM.

Nine tasks need no tool. Eleven need one, and their verifiers read the event log (`tooltrace.real_calls`): a task
that was answered without the registry really running the tool scores 0, however right the answer looks.
The planted values in the tool tasks (QX-4471, COBALT, 173 and 269, ...) are written by each task's `setup` script;
tests/arena/practice checks that script and the constant here agree.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from tanishi.arena.practice._common import (
    has_token,
    run_python_tests,
    standalone_ints,
    text_of,
    words,
)
from tanishi.arena.practice.tooltrace import ToolCall, real_calls
from tanishi.arena.verifiers import Verdict, number

FAIL = "answer is wrong"

# --- no tool ----------------------------------------------------------------------------------------------------


def json_extract(task, output):
    return number(output, 12)


def unit_convert(task, output):
    return number(output, 42.2, tol=0.05)


def date_arith(task, output):
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    want = (date(2024, 1, 15) + timedelta(days=45)).isoformat()
    found = set(re.findall(r"\d{4}-\d{2}-\d{2}", got))
    return Verdict(1.0, "date matches") if found == {want} else Verdict(0.0, "date is wrong or ambiguous")


def slugify_fn(task, output):
    return run_python_tests(output, '''
assert slugify("Hello, World!") == "hello-world"
assert slugify("  many   spaces here ") == "many-spaces-here"
assert slugify("---Already--Dashed---") == "already-dashed"
assert slugify("Rock & Roll 101") == "rock-roll-101"
assert slugify("") == ""
assert slugify("!!!") == ""
''')


def dedupe_fn(task, output):
    return run_python_tests(output, '''
assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]
assert dedupe(["a", "A", "a"]) == ["a", "A"]
assert dedupe([]) == []
xs = [1, 2, 3]
assert dedupe(xs) == [1, 2, 3] and xs == [1, 2, 3]
assert dedupe(list(range(2000)) * 3) == list(range(2000))
''')


def fix_bug_fn(task, output):
    return run_python_tests(output, '''
assert moving_avg([1, 2, 3, 4, 5], 2) == [1.5, 2.5, 3.5, 4.5]
assert moving_avg([2, 4, 6], 3) == [4.0]
assert moving_avg([5], 1) == [5.0]
assert moving_avg([1, 2], 3) == []
assert moving_avg([], 2) == []
''')


_ORDERS = [
    (1, "ada", 60.0, "paid"), (2, "ada", 70.0, "paid"), (3, "bo", 500.0, "refunded"), (4, "bo", 90.0, "paid"),
    (5, "cy", 300.0, "paid"), (6, "cy", 20.0, "pending"), (7, "di", 100.0, "paid"), (8, "ed", 101.0, "paid"),
]


def sql_query(task, output):
    """Customers whose paid orders total over 100, largest total first, as (customer, total)."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    query = re.sub(r"^```[A-Za-z]*\n|```\s*$", "", got.strip()).strip().rstrip(";")
    if not re.match(r"(?is)\s*(select|with)\b", query) or ";" in query:
        return Verdict(0.0, "not a single SELECT")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, amount REAL, status TEXT)")
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", _ORDERS)
        conn.set_authorizer(lambda action, *a: sqlite3.SQLITE_OK if action in (
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE)
            else sqlite3.SQLITE_DENY)
        rows = conn.execute(query).fetchall()
    except sqlite3.Error:
        return Verdict(0.0, "query failed to run")
    finally:
        conn.close()
    want = [("cy", 300.0), ("ada", 130.0), ("ed", 101.0)]
    return Verdict(1.0, "rows match") if rows == want else Verdict(0.0, "rows do not match")


def json_only_reply(task, output):
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", got.strip())
    try:
        data = json.loads(body)
    except ValueError:
        return Verdict(0.0, "not valid JSON")
    if not isinstance(data, dict) or set(data) != {"word", "vowels"}:
        return Verdict(0.0, "wrong keys")
    ok = data["word"] == "verification" and type(data["vowels"]) is int and data["vowels"] == 6
    return Verdict(1.0, "object matches") if ok else Verdict(0.0, "values are wrong")


def bullet_count(task, output):
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    lines = [ln for ln in got.splitlines() if ln.strip()]
    if len(lines) != 3:
        return Verdict(0.0, "not exactly three lines")
    if not all(ln.startswith("- ") for ln in lines):
        return Verdict(0.0, "a line is not a '- ' bullet")
    if any(len(words(ln[2:])) > 12 or not ln[2:].strip() for ln in lines):
        return Verdict(0.0, "a bullet is empty or too long")
    return Verdict(1.0, "format matches")


# --- tools: the call must be real -------------------------------------------------------------------------------


def _blob(value: object) -> str:
    return json.dumps(value, default=str)


def _tool_check(
    output: object, tools: tuple[str, ...], *, seen: tuple[str, ...] = (), aimed: tuple[str, ...] = (), answer=None
) -> Verdict:
    """1 only when a real, successful call of one of `tools` happened, its input names every `aimed` string, its
    outputs together hold every `seen` string, and `answer(text)` is true. The answer is checked last so a lucky
    guess without the tool still scores 0."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    calls = real_calls(*tools)
    if not calls:
        return Verdict(0.0, "no real tool call recorded")
    inputs = " ".join(_blob(c.input) for c in calls)
    if any(a not in inputs for a in aimed):
        return Verdict(0.0, "tool was not aimed at the right target")
    outputs = " ".join(c.output for c in calls)
    if any(s not in outputs for s in seen):
        return Verdict(0.0, "tool output lacks the data")
    if answer is not None and not answer(got):
        return Verdict(0.0, FAIL)
    return Verdict(1.0, "real call and right answer")


def read_config_value(task, output):
    return _tool_check(output, ("read_file", "run_command", "search_files"), seen=("QX-4471",),
                       aimed=("config.txt",), answer=lambda t: has_token(t, "QX-4471"))


def count_matching_files(task, output):
    return _tool_check(output, ("list_directory", "search_files", "run_command"), aimed=("logs",),
                       answer=lambda t: standalone_ints(t) == {7})


def find_todo_file(task, output):
    return _tool_check(
        output, ("search_files", "run_command", "read_file"), aimed=("src",), seen=("FIXME-ORCHID",),
        answer=lambda t: has_token(t, "c.py") and not any(has_token(t, n) for n in ("a.py", "b.py", "d.py")),
    )


def _file(name: str) -> str | None:
    p = Path(name)
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def write_exact_file(task, output):
    got = _file("out/status.txt")
    if got is None or got.strip() != "ready-42":
        return Verdict(0.0, "file is missing or wrong")
    return _tool_check(output, ("write_file", "run_command"), aimed=("status.txt",))


def append_line(task, output):
    got = _file("journal.txt")
    if got is None or got.splitlines() != ["alpha", "beta", "gamma"]:
        return Verdict(0.0, "file is missing or wrong")
    return _tool_check(output, ("write_file", "run_command"), aimed=("journal.txt",))


def read_two_sum(task, output):
    return _tool_check(output, ("read_file", "run_command", "search_files"), seen=("173", "269"),
                       answer=lambda t: 442 in standalone_ints(t) and standalone_ints(t) <= {173, 269, 442})


def latest_report(task, output):
    return _tool_check(
        output, ("read_file", "run_command", "search_files"), aimed=("reports",), seen=("COBALT",),
        answer=lambda t: has_token(t, "COBALT") and not has_token(t, "AMBER") and not has_token(t, "JADE"),
    )


def _tool_field(call: ToolCall, name: str) -> str | None:
    m = re.search(rf"^\s*{name}:\s*(.+)$", call.output, re.MULTILINE)
    return m.group(1).strip() if m else None


def today_weekday(task, output):
    """Weekday name and day-of-year, both taken from what get_datetime really returned."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    for call in real_calls("get_datetime"):
        full, doy = _tool_field(call, "date"), _tool_field(call, "day_of_year")
        if not full or not doy or not doy.isdigit():
            continue
        weekday = full.split(",")[0].strip()
        if has_token(got, weekday) and int(doy) in standalone_ints(got):
            return Verdict(1.0, "matches the tool result")
    return Verdict(0.0, "no real get_datetime call matches the answer")


def os_python(task, output):
    """OS name and Python version, both as get_system_info really reported them."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    for call in real_calls("get_system_info"):
        system, version = _tool_field(call, "os"), _tool_field(call, "python")
        if not system or not version:
            continue
        name = system.split()[0]
        names = {name.casefold()} | ({"macos", "mac os"} if name == "Darwin" else set())
        if any(n in got.casefold() for n in names) and ".".join(version.split(".")[:2]) in got:
            return Verdict(1.0, "matches the tool result")
    return Verdict(0.0, "no real get_system_info call matches the answer")


def log_expense(task, output):
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    for call in real_calls("log_expense"):
        args = call.input if isinstance(call.input, dict) else {}
        amount = args.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and abs(amount - 12.5) < 1e-9 \
                and str(args.get("payment_method", "")).casefold() == "card":
            return Verdict(1.0, "expense logged")
    return Verdict(0.0, "no real log_expense call with the right fields")


def count_lines_shell(task, output):
    return _tool_check(output, ("run_command",), aimed=("data.csv",), answer=lambda t: standalone_ints(t) == {12})

