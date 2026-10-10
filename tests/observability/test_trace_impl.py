"""OBS1 implementer's tests: what the exam (test_trace.py) does not pin."""
import json
import os
import threading

from tanishi.core_state import events
from tanishi.observability import trace
from tanishi.observability.trace import span


def recs() -> list[dict]:
    out = []
    for f in sorted(trace.traces_dir().glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text().splitlines()]
    return out


def one(name: str) -> dict:
    [r] = [r for r in recs() if r["name"] == name]
    return r


def test_set_adds_values_known_only_after_the_call():
    with span("think", model="m") as s:
        s.set(tokens_in=10, tokens_out=3)
    r = one("think")
    assert (r["model"], r["tokens_in"], r["tokens_out"]) == ("m", 10, 3)


def test_error_status_and_type_are_recorded_but_not_the_message():
    try:
        with span("fails"):
            raise KeyError("private detail")
    except KeyError:
        pass
    r = one("fails")
    assert (r["status"], r["error"]) == ("error", "KeyError")
    assert "private detail" not in json.dumps(r)


def test_explicit_task_id_wins_over_the_scope():
    with events.task_scope("scope-task"), span("x", task_id="explicit"):
        pass
    assert one("x")["task_id"] == "explicit"
    assert "task_id" not in one("x")["attrs"]


def test_events_from_another_thread_do_not_land_in_this_span():
    with span("main"):
        t = threading.Thread(target=lambda: events.emit("note", {}))
        t.start()
        t.join()
    assert one("main")["event_ids"] == []


def test_a_listener_that_raises_never_fails_emit():
    def bad(*_):
        raise RuntimeError

    events.add_listener(bad)
    try:
        assert isinstance(events.emit("note", {}), int)
    finally:
        events.remove_listener(bad)
    assert bad not in events._listeners


def test_long_strings_are_clipped_and_redacted_first():
    with span("big", blob="x" * 10_000, key="api_key=" + "sk-ant-" + "Z" * 40):
        pass
    r = one("big")
    assert len(r["attrs"]["blob"]) < 3000
    assert "Z" * 40 not in json.dumps(r)


def test_tree_puts_orphans_at_the_root_and_children_after_parents():
    rs = [
        {"span_id": "c", "parent_id": "p", "name": "child", "start_ts": "2"},
        {"span_id": "p", "parent_id": None, "name": "parent", "start_ts": "1"},
        {"span_id": "o", "parent_id": "gone", "name": "orphan", "start_ts": "3"},
    ]
    assert [(d, r["name"]) for d, r in trace.tree(rs)] == [(0, "parent"), (1, "child"), (0, "orphan")]


def test_tree_survives_a_parent_cycle():
    rs = [{"span_id": "a", "parent_id": "b", "name": "a"}, {"span_id": "b", "parent_id": "a", "name": "b"}]
    assert sorted(r["name"] for _, r in trace.tree(rs)) == ["a", "b"]


def test_cli_main_usage_and_unknown_task(capsys):
    assert trace.main([]) == 2
    assert trace.main(["nope"]) == 1
    assert "no trace" in capsys.readouterr().err


def test_printable_escapes_c0_c1_and_line_separators_only():
    assert trace.printable("a\nb\x1b[2J\x9b é") == "a\\nb\\x1b[2J\\x9b\\u2028é"


def test_every_printed_field_is_escaped(capsys):
    with events.task_scope("t-esc"), span("n\x07", model="m\x1b", tool_calls=["t\x85"], note="\x9b") as s:
        s.set(tokens_in="1\n2")
    assert trace.main(["t-esc"]) == 0
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 2
    assert not any(ch in out for ch in "\x07\x1b\x85\x9b")


def test_tree_is_iterative_and_keeps_order():
    rs = [{"span_id": str(i), "parent_id": str(i - 1) if i else None, "name": str(i)} for i in range(5000)]
    got = trace.tree(rs)
    assert [d for d, _ in got] == list(range(5000))


def test_line_breaks_other_than_newline_are_escaped_on_disk():
    seps = "a b c\x85d"
    with span("sep", note=seps):
        pass
    raw = "".join(f.read_text(encoding="utf-8") for f in trace.traces_dir().glob("*.jsonl"))
    assert not any(ch in raw for ch in (" ", " ", "\x85"))
    assert one("sep")["attrs"]["note"] == seps


def test_one_bad_attr_becomes_a_placeholder_and_the_rest_stays():
    with span("mixed", huge=10**5000, fine="kept"):
        pass
    attrs = one("mixed")["attrs"]
    assert attrs["fine"] == "kept"
    assert attrs["huge"] == "[UNSERIALISABLE: int]"


def test_a_record_that_cannot_be_built_is_still_written_bare(monkeypatch):
    monkeypatch.setattr(trace, "_record", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    with span("bare", a=1):
        eid = events.emit("note", {})
    r = one("bare")
    assert r["event_ids"] == [eid] and r["attrs"] == {} and r["status"] == "ok"


def test_an_oversized_record_drops_attrs_and_says_so():
    with span("fat", payload=["x" * 1500] * 150, keep="no"):
        pass
    r = one("fat")
    assert r["attrs"] == {}
    assert r["truncated"]["dropped"] == ["attrs"] and r["truncated"]["bytes"] > trace.MAX_RECORD_BYTES
    assert one("fat")["name"] == "fat"


def test_event_count_and_truncation_are_recorded():
    with span("few"):
        events.emit("note", {})
    r = one("few")
    assert (r["event_count"], r["events_truncated"], r["truncated"]) == (1, 0, None)
    with span("many"):
        for _ in range(trace.MAX_ITEMS + 5):
            events.emit("note", {})
    r = one("many")
    assert len(r["event_ids"]) == trace.MAX_ITEMS
    assert (r["event_count"], r["events_truncated"]) == (trace.MAX_ITEMS + 5, 5)


def test_write_finishes_a_line_over_many_short_writes(monkeypatch):
    real = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real(fd, bytes(data[:7])))
    with span("dribble", k="v" * 50):
        pass
    monkeypatch.undo()
    assert one("dribble")["attrs"]["k"] == "v" * 50
