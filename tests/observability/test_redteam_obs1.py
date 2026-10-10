# ruff: noqa: SIM117  (nested with-blocks keep the task scope and the span visibly separate)
"""Red-team attacks on OBS1 traces. See build/memory/runs/redteam-OBS1-20261010.md.

Each test asserts the CORRECT behaviour. A test for an open break is marked xfail(strict=True): the suite stays
green now, and when a fix lands the test XPASSes and strict mode forces the mark off.
"""
import contextlib
import io
import json
import os
import time

import pytest

from tanishi.core_state import events, open_db
from tanishi.observability import trace
from tanishi.observability.trace import span

BREAK = pytest.mark.xfail(strict=True, reason="red-team break, see build/memory/runs/redteam-OBS1-20261010.md")


def raw_lines() -> list[str]:
    out: list[str] = []
    for f in sorted(trace.traces_dir().glob("*.jsonl")):
        out += f.read_bytes().decode("utf-8").split("\n")
    return [x for x in out if x]


def recs() -> list[dict]:
    return [json.loads(x) for x in raw_lines()]


def cli(task_id: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = trace.main([task_id])
    return code, buf.getvalue()


# ---------------------------------------------------------------- reading


@pytest.mark.parametrize("sep", [" ", " ", "\x85"])
def test_r1_unicode_line_separator_in_a_value_does_not_hide_the_span(sep):
    # json.dumps(ensure_ascii=False) leaves U+2028/U+2029/U+0085 raw; str.splitlines() cuts the record there.
    with events.task_scope("t-sep"):
        with span("a", note=f"before{sep}after"):
            pass
    assert len(trace.load("t-sep")) == 1


@BREAK
def test_r2_a_name_with_a_newline_cannot_forge_tree_lines():
    # Names, models and tool names come from callers and from the model (an unknown tool name is still emitted).
    evil = "think\n    shell_exec  0.0 ms  tools=approved  events=1"
    with events.task_scope("t-inj"):
        with span(evil):
            pass
    code, out = cli("t-inj")
    assert code == 0
    assert len(out.splitlines()) == 2  # header + one span


@BREAK
def test_r3_terminal_escape_in_a_tool_name_is_not_printed_raw():
    with events.task_scope("t-esc"):
        with span("step"):
            events.emit("tool_call", {"tool": "\x1b[2J\x1b[Hok", "call_id": "c"})
    _, out = cli("t-esc")
    assert "\x1b" not in out


@BREAK
def test_r4_a_deeply_nested_trace_still_prints():
    with events.task_scope("t-deep"):
        with contextlib.ExitStack() as stack:
            for i in range(1500):  # sub-agent calling sub-agent; tree() and walk() are recursive
                stack.enter_context(span(f"s{i}"))
    code, out = cli("t-deep")
    assert code == 0, out
    assert len(out.splitlines()) == 1501


# ---------------------------------------------------------------- writing


def test_r5_a_short_write_does_not_glue_two_records(monkeypatch):
    real = os.write
    calls = {"n": 0}

    def short(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:  # disk-full or signal: half the line, no newline
            return real(fd, data[: len(data) // 2])
        return real(fd, data)

    monkeypatch.setattr(os, "write", short)
    with events.task_scope("t-short"):
        with span("first"):
            pass
        with span("second"):
            pass
    monkeypatch.undo()
    names = {r["name"] for r in trace.load("t-short")}
    assert "second" in names  # today the half line swallows the next record too


def test_r6_one_unserialisable_attr_does_not_drop_the_span_and_its_event_links():
    with events.task_scope("t-big"):
        with span("work", huge=10**5000) as s:  # json.dumps: int exceeds 4300 digits -> ValueError
            eid = events.emit("note", {"x": 1})
            s.set(ok=1)
    got = trace.load("t-big")
    assert got, "the whole span vanished because of one attr"
    assert eid in got[0]["event_ids"]


def test_r7_a_record_is_size_bounded():
    big = [[list(range(200)) for _ in range(200)] for _ in range(20)]  # 800k ints, within every per-container cap
    with events.task_scope("t-size"):
        with span("fat", payload=big):
            pass
    assert sum(len(x) for x in raw_lines()) < 1_000_000


def test_r8_event_id_list_truncation_is_visible():
    with events.task_scope("t-many"):
        with span("busy"):
            ids = [events.emit("note", {"i": i}) for i in range(230)]
    [r] = trace.load("t-many")
    kept = r["event_ids"]
    # either all ids are linked, or the record says how many were cut
    assert len(kept) == len(ids) or any(k in r for k in ("event_count", "events_dropped", "truncated"))


# ---------------------------------------------------------------- attribution


@BREAK
def test_r9_span_opened_before_task_scope_is_found_by_its_task():
    with span("outer"):
        with events.task_scope("t-late"):
            with span("inner"):
                pass
    names = {r["name"] for r in trace.load("t-late")}
    assert "outer" in names  # the root of the task is invisible today (task_id null)


@BREAK
def test_r10_a_generator_span_does_not_steal_the_consumers_events():
    def stream():
        with span("model_call"):
            yield 1
            yield 2

    with events.task_scope("t-gen"):
        with span("consumer"):
            g = stream()
            next(g)
            eid = events.emit("tool_call", {"tool": "t", "call_id": "c"})  # the consumer's own work
            g.close()
    by = {r["name"]: r for r in trace.load("t-gen")}
    assert eid in by["consumer"]["event_ids"]


@BREAK
def test_r11_integer_task_id_is_found_by_the_cli():
    with span("x", task_id=42):
        pass
    code, _ = cli("42")
    assert code == 0


@BREAK
def test_r12_tool_is_counted_once_when_caller_and_registry_both_report_it():
    with events.task_scope("t-dup"):
        with span("step", tool_calls=["search"]):
            events.emit("tool_call", {"tool": "search", "call_id": "c1"})
    [r] = trace.load("t-dup")
    assert r["tool_calls"].count("search") == 1


# ---------------------------------------------------------------- held up (regression guards)


def test_h1_secrets_in_name_model_tool_keys_and_values_never_reach_disk():
    key = "sk-ant-api03-" + "A" * 40
    with events.task_scope("t-sec"):
        with span(f"call https://x.test/?api_key={key}", model=f"m {key}", **{f"k {key}": 1}) as s:
            s.set(headers={"Authorization": f"Bearer {key}"}, blob=repr(Exception(key)), raw=key.encode())
            events.emit("tool_call", {"tool": f"t-{key}", "call_id": "c"})
    assert key not in "".join(raw_lines())
    assert "A" * 40 not in "".join(raw_lines())


def test_h2_body_exception_propagates_and_span_closes_even_if_write_fails(monkeypatch):
    monkeypatch.setattr(trace, "_write", lambda rec: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(KeyError):
        with span("x"):
            raise KeyError("k")
    with span("after"):  # context was reset
        pass


def test_h3_trace_never_touches_the_event_chain():
    with span("x"):
        events.emit("note", {"a": 1})
    conn = open_db()
    try:
        assert events.verify_chain(conn) == (True, None)
    finally:
        conn.close()


def test_h4_cancelled_and_baseexception_are_recorded_and_propagate():
    class Stop(BaseException):
        pass

    with pytest.raises(Stop):
        with span("c"):
            raise Stop
    assert recs()[-1]["status"] == "error" and recs()[-1]["error"] == "Stop"


def test_h5_span_overhead_is_small():
    t = time.perf_counter()
    for _ in range(500):
        with span("hot", model="m", tokens_in=1, tokens_out=1):
            pass
    assert (time.perf_counter() - t) / 500 < 0.005
