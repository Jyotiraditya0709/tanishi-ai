"""OBS1 implementer's tests: what the exam (test_trace.py) does not pin."""
import json
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
