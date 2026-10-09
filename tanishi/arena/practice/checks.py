"""Verifiers for the 20 new Practice tasks. Plain code, no LLM.

Nine tasks need no tool. The three code tasks are run against held-out cases drawn fresh for every check and
compared, type and value, with a reference implementation here; `sql_query` runs on a fixed table plus random ones.

Eleven need a tool, and their verifiers read the event log (`tooltrace.real_calls`): a task that was answered without
the registry really running the tool scores 0, however right the answer looks. The values the file tasks plant are
random per attempt: each task's `setup` writes them to the fixture and to `.arena/truth` in the attempt's home, and
the verifier reads them from there, so no answer sits in this repo. A call only counts as evidence when the value
came out of the tool: it is in the call's output and not in its input, so `echo <answer>` proves nothing.
"""
from __future__ import annotations

import json
import random
import re
import secrets
import sqlite3
import string
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from tanishi.arena.practice._common import (
    has_token,
    run_python_tests,
    same,
    standalone_ints,
    text_of,
    words,
)
from tanishi.arena.practice.tooltrace import ToolCall, real_calls
from tanishi.arena.verifiers import Verdict, candidate_fault, number

FAIL = "answer is wrong"
TRUTH = Path(".arena") / "truth"  # relative to the attempt's home, the verifier's cwd


def _rng() -> random.Random:
    return random.Random(secrets.randbits(64))


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


# Code tasks: the public cases from the prompt's spirit, then cases the candidate has never seen.

_SLUG_PUBLIC = ["Hello, World!", "  many   spaces here ", "---Already--Dashed---", "Rock & Roll 101", "", "!!!"]
_SLUG_CHARS = string.ascii_letters + string.digits + " -_!&.,'/\t"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def slugify_fn(task, output):
    rng = _rng()
    cases = _SLUG_PUBLIC + ["".join(rng.choice(_SLUG_CHARS) for _ in range(rng.randint(0, 30))) for _ in range(200)]

    def tests(ns):
        for text in cases:
            assert same(ns["slugify"](text), _slugify(text))

    return run_python_tests(output, tests)


DEDUPE_BIG = 200_000  # a quadratic dedupe cannot finish this inside DEDUPE_TIMEOUT_S ("must stay fast")
DEDUPE_TIMEOUT_S = 6


def _dedupe(items: list) -> list:
    return list(dict.fromkeys(items))


def dedupe_fn(task, output):
    rng = _rng()
    cases = [[3, 1, 3, 2, 1], ["a", "A", "a"], [], [1, 2, 3]]
    for _ in range(100):
        cases.append([rng.choice([rng.randrange(20), rng.choice("aAbBcC")]) for _ in range(rng.randint(0, 25))])
    big = [rng.randrange(DEDUPE_BIG * 3 // 4) for _ in range(DEDUPE_BIG)]

    def tests(ns):
        for items in [*cases, big]:
            before = list(items)
            assert same(ns["dedupe"](items), _dedupe(before))
            assert same(items, before)  # must not change its input

    return run_python_tests(output, tests, timeout_s=DEDUPE_TIMEOUT_S)


def _moving_avg(xs: list, k: int) -> list:
    return [sum(xs[i:i + k]) / k for i in range(len(xs) - k + 1)]


def fix_bug_fn(task, output):
    rng = _rng()
    cases = [([1, 2, 3, 4, 5], 2), ([2, 4, 6], 3), ([5], 1), ([1, 2], 3), ([], 2)]
    for _ in range(100):
        xs = [rng.randint(-50, 50) for _ in range(rng.randint(0, 15))]
        cases.append((xs, rng.randint(1, len(xs) + 2)))

    def tests(ns):
        for xs, k in cases:
            assert same(ns["moving_avg"](list(xs), k), _moving_avg(xs, k))

    return run_python_tests(output, tests)


# SQL: the query must be right on tables it has never seen, and must finish.

_ORDERS = [
    (1, "ada", 60.0, "paid"), (2, "ada", 70.0, "paid"), (3, "bo", 500.0, "refunded"), (4, "bo", 90.0, "paid"),
    (5, "cy", 300.0, "paid"), (6, "cy", 20.0, "pending"), (7, "di", 100.0, "paid"), (8, "ed", 101.0, "paid"),
]
_SQL_REFERENCE = ("SELECT customer, SUM(amount) AS total FROM orders WHERE status = 'paid' GROUP BY customer "
                  "HAVING total > 100 ORDER BY total DESC")
SQL_TIMEOUT_S = 3.0  # for every table together; a runaway query (a recursive CTE) is stopped here
SQL_MAX_ROWS = 10_000


def _random_orders(rng: random.Random) -> list[tuple]:
    """A table with a different shape every time: whole-number amounts (so sums are exact), unique paid totals (so
    the order is defined), and a customer on exactly 100 now and then (so > and >= differ)."""
    while True:
        names = rng.sample([a + b for a in string.ascii_lowercase for b in string.ascii_lowercase], rng.randint(4, 9))
        rows, paid = [], {}
        for name in names:
            for _ in range(rng.randint(1, 5)):
                amount, status = float(rng.randint(5, 150)), rng.choice(["paid", "paid", "refunded", "pending"])
                rows.append((len(rows) + 1, name, amount, status))
                paid[name] = paid.get(name, 0.0) + (amount if status == "paid" else 0.0)
        if rng.random() < 0.5:
            name = rng.choice(names)
            if paid.get(name, 0.0) < 100:
                rows.append((len(rows) + 1, name, 100.0 - paid.get(name, 0.0), "paid"))
                paid[name] = 100.0
        over = [t for t in paid.values() if t > 100]
        if over and len(set(over)) == len(over):
            rng.shuffle(rows)
            return rows


class _TooSlow(Exception):
    pass


def _run_sql(query: str, rows: list[tuple], deadline: float) -> list:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, amount REAL, status TEXT)")
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", rows)
        conn.set_authorizer(lambda action, *a: sqlite3.SQLITE_OK if action in (
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE)
            else sqlite3.SQLITE_DENY)
        stopped = []

        def check() -> int:
            if time.monotonic() > deadline:
                stopped.append(True)
                return 1  # nonzero interrupts the statement
            return 0

        conn.set_progress_handler(check, 1000)
        try:
            got = conn.execute(query).fetchmany(SQL_MAX_ROWS + 1)
        except sqlite3.OperationalError:
            if stopped:
                raise _TooSlow from None
            raise
        if len(got) > SQL_MAX_ROWS:
            raise _TooSlow
        return got
    finally:
        conn.close()


def sql_query(task, output):
    """Customers whose paid orders total over 100, largest total first, as (customer, total)."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    query = re.sub(r"^```[A-Za-z]*\n|```\s*$", "", got.strip()).strip().rstrip(";")
    if not re.match(r"(?is)\s*(select|with)\b", query) or ";" in query:
        return Verdict(0.0, "not a single SELECT")
    rng = _rng()
    deadline = time.monotonic() + SQL_TIMEOUT_S
    for rows in [_ORDERS] + [_random_orders(rng) for _ in range(4)]:
        want = _run_sql(_SQL_REFERENCE, rows, float("inf"))
        try:
            result = _run_sql(query, rows, deadline)
        except _TooSlow:
            return candidate_fault("query ran too long or returned too many rows")
        except sqlite3.Error:
            return Verdict(0.0, "query failed to run")
        if not same([tuple(r) for r in result], want):
            return Verdict(0.0, "rows do not match")
    return Verdict(1.0, "rows match")


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


# --- tools: the call must be real, and must be where the answer came from ---------------------------------------


def _blob(value: object) -> str:
    return json.dumps(value, default=str)


def truth() -> dict[str, list[str]]:
    """What this attempt's setup planted: `key=value` lines of $HOME/.arena/truth, a key may repeat."""
    planted: dict[str, list[str]] = {}
    try:
        text = TRUTH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return planted
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and value.strip():
            planted.setdefault(key.strip(), []).append(value.strip())
    return planted


def _one(planted: dict[str, list[str]], key: str) -> str | None:
    values = planted.get(key, [])
    return values[0] if len(values) == 1 else None


def produced(calls: list[ToolCall], value: str) -> bool:
    """Some call returned `value` without being handed it: it is in the output and not in the input."""
    return any(value in c.output and value not in _blob(c.input) for c in calls)


def produced_int(calls: list[ToolCall], n: int) -> bool:
    """Like `produced`, for an integer that must stand on its own in the output and be absent from the input."""
    return any(n in standalone_ints(c.output) and n not in standalone_ints(_blob(c.input)) for c in calls)


def _tool_check(
    output: object, tools: tuple[str, ...], *, aimed: tuple[str, ...] = (),
    evidence: Callable[[list[ToolCall]], bool] | None = None, answer: Callable[[str], bool] | None = None,
) -> Verdict:
    """1 only when a real, successful call of one of `tools` happened, their inputs name every `aimed` string,
    `evidence(calls)` shows the answer came out of a tool, and `answer(text)` is true. No call may touch the
    verifier's record of the planted values. The answer is checked last so a lucky guess still scores 0."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    calls = real_calls(*tools)
    if not calls:
        return Verdict(0.0, "no real tool call recorded")
    if any(".arena" in _blob(c.input) for c in real_calls()):
        return Verdict(0.0, "a tool call touched the verifier's records")
    inputs = " ".join(_blob(c.input) for c in calls)
    if any(a not in inputs for a in aimed):
        return Verdict(0.0, "tool was not aimed at the right target")
    if evidence is not None and not evidence(calls):
        return Verdict(0.0, "no tool output produced the answer")
    if answer is not None and not answer(got):
        return Verdict(0.0, FAIL)
    return Verdict(1.0, "real call and right answer")


_NO_FIXTURE = Verdict(0.0, "planted values are missing")


def read_config_value(task, output):
    value = _one(truth(), "build_id")
    if value is None:
        return _NO_FIXTURE
    return _tool_check(output, ("read_file", "run_command", "search_files"), aimed=("config.txt",),
                       evidence=lambda calls: produced(calls, f"build_id={value}"),
                       answer=lambda t: has_token(t, value))


def count_matching_files(task, output):
    planted = truth()
    count, logs = _one(planted, "count"), planted.get("log", [])
    if count is None or not count.isdigit() or len(logs) != int(count):
        return _NO_FIXTURE
    n = int(count)

    def evidence(calls):
        listed = " ".join(c.output for c in calls)
        if all(has_token(listed, name) for name in logs):
            return True  # a listing that shows every planted .log file
        return produced_int([c for c in calls if c.tool == "run_command"], n)  # e.g. `find logs -name ... | wc -l`

    return _tool_check(output, ("list_directory", "search_files", "run_command"), aimed=("logs",),
                       evidence=evidence, answer=lambda t: standalone_ints(t) == {n})


def find_todo_file(task, output):
    planted = truth()
    target, marker, decoys = _one(planted, "target"), _one(planted, "marker"), planted.get("decoy", [])
    if target is None or marker is None or not decoys:
        return _NO_FIXTURE
    return _tool_check(
        output, ("search_files", "run_command", "read_file"), aimed=("src",),
        evidence=lambda calls: produced(calls, marker) or produced(calls, target),
        answer=lambda t: has_token(t, target) and not any(has_token(t, n) for n in decoys),
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
    planted = truth()
    left, right = _one(planted, "left"), _one(planted, "right")
    if left is None or right is None or not left.isdigit() or not right.isdigit():
        return _NO_FIXTURE
    a, b = int(left), int(right)
    return _tool_check(output, ("read_file", "run_command", "search_files"),
                       evidence=lambda calls: produced_int(calls, a) and produced_int(calls, b),
                       answer=lambda t: a + b in standalone_ints(t) and standalone_ints(t) <= {a, b, a + b})


def latest_report(task, output):
    planted = truth()
    newest, older = _one(planted, "newest"), planted.get("older", [])
    if newest is None or not older:
        return _NO_FIXTURE
    return _tool_check(
        output, ("read_file", "run_command", "search_files"), aimed=("reports",),
        evidence=lambda calls: produced(calls, newest),
        answer=lambda t: has_token(t, newest) and not any(has_token(t, o) for o in older),
    )


def _tool_field(call: ToolCall, name: str) -> str | None:
    m = re.search(rf"^\s*{name}:\s*(.+)$", call.output, re.MULTILINE)
    return m.group(1).strip() if m else None


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def today_weekday(task, output):
    """Weekday name and day-of-year, both taken from what get_datetime really returned, and no other weekday."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    named = {d for d in _WEEKDAYS if has_token(got, d)}
    for call in real_calls("get_datetime"):
        full, doy = _tool_field(call, "date"), _tool_field(call, "day_of_year")
        if not full or not doy or not doy.isdigit():
            continue
        weekday = full.split(",")[0].strip().casefold()
        if named == {weekday} and int(doy) in standalone_ints(got):
            return Verdict(1.0, "matches the tool result")
    return Verdict(0.0, "no real get_datetime call matches the answer")


_OS_NAMES = {"darwin": ("darwin", "macos", "mac os"), "linux": ("linux",), "windows": ("windows",)}


def os_python(task, output):
    """OS name and Python version, both as get_system_info really reported them, and no other OS named."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    low = got.casefold()
    named = {os_ for os_, aliases in _OS_NAMES.items() if any(a in low for a in aliases)}
    for call in real_calls("get_system_info"):
        system, version = _tool_field(call, "os"), _tool_field(call, "python")
        if not system or not version:
            continue
        name = system.split()[0].casefold()
        if named <= {name} and any(a in low for a in _OS_NAMES.get(name, (name,))) \
                and ".".join(version.split(".")[:2]) in got:
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
    lines = _one(truth(), "lines")
    if lines is None or not lines.isdigit():
        return _NO_FIXTURE
    n = int(lines)
    return _tool_check(output, ("run_command",), aimed=("data.csv",), evidence=lambda calls: produced_int(calls, n),
                       answer=lambda t: standalone_ints(t) == {n})
