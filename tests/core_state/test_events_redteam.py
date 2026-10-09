"""Red-team attacks on CS2 (the hash-chained event log and its wiring).

Each test asserts the CORRECT behaviour. Tests for open breaks are marked xfail(strict=True): the suite
stays green today, and the moment someone fixes a break the test XPASSes, strict mode fails the run, and
the mark must be removed. Report: build/memory/runs/redteam-CS2-20261009.md
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import types

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.events import emit, iter_events, redact, verify_chain

BREAK = pytest.mark.xfail(strict=True, reason="red-team break, see build/memory/runs/redteam-CS2-20261009.md")


@pytest.fixture
def conn():
    c = open_db()
    migrate(c)
    yield c
    c.close()


def drop_triggers(c):
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='events'").fetchall():
        c.execute(f'DROP TRIGGER "{name}"')
    c.commit()


def stored(c) -> str:
    return " ".join(str(v) for row in c.execute("SELECT * FROM events").fetchall() for v in row)


# ---------- chain integrity ----------

def test_deleting_the_tail_then_emitting_hides_the_deletion(conn):
    """C1: verify_chain never checks that ids are contiguous. Delete the last row, let the next honest
    emit() run: it takes id max(head, seq)+1, links to the new head, and sqlite_sequence catches up."""
    for i in range(5):
        emit("step", {"i": i})
    drop_triggers(conn)
    conn.execute("DELETE FROM events WHERE id = 5")
    conn.commit()
    assert verify_chain(conn) == (False, 5)  # held: the bare truncation is seen
    emit("step", {"i": "after"})
    ok, bad = verify_chain(conn)
    assert not ok and bad == 5


def test_deleting_every_row_then_emitting_hides_the_wipe(conn):
    """C1b: same hole, whole log. The new first row has id 4, prev GENESIS, a valid hash."""
    for i in range(3):
        emit("step", {"i": i})
    drop_triggers(conn)
    conn.execute("DELETE FROM events")
    conn.commit()
    emit("step", {"i": "fresh"})
    assert verify_chain(conn)[0] is False


def test_verify_chain_gives_no_false_alarm_while_a_writer_is_active(tmp_path):
    """C2: verify_chain reads the rows in one statement and sqlite_sequence in another. An emit() that
    lands between them makes seq > last_id and a healthy log is reported tampered."""
    for i in range(20):
        emit("seed", {"i": i})
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            emit("live", {"t": time.time()})

    t = threading.Thread(target=writer)
    t.start()
    false_alarms = 0
    try:
        c = open_db()
        for _ in range(300):
            if verify_chain(c)[0] is False:
                false_alarms += 1
        c.close()
    finally:
        stop.set()
        t.join()
    assert false_alarms == 0


@BREAK
def test_one_stray_unhashed_row_must_not_stop_all_later_logging(conn):
    """C3: a raw INSERT with no hash (the CS1 triggers allow deleting it, and UNIQUE allows many NULLs) makes
    emit() raise TypeError on `None + canonical` for ever. The wiring fails open, so Tanishi then acts with
    no events and one warning line."""
    emit("step", {"i": 0})
    conn.execute("INSERT INTO events (ts, kind, actor) VALUES ('t', 'stray', 'x')")
    conn.commit()
    emit("step", {"i": 1})  # must not raise
    assert verify_chain(conn)[0] is False  # and the stray row is still reported


def test_insert_or_replace_edit_of_a_middle_row_is_caught(conn):
    """Held: REPLACE deletes without firing the delete trigger, but the chain still sees the edit."""
    for i in range(4):
        emit("step", {"i": i})
    row = conn.execute("SELECT * FROM events WHERE id = 2").fetchone()
    conn.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?)",
                 (row[0], row[1], "forged", row[3], row[4], row[5], row[6], row[7]))
    conn.commit()
    assert verify_chain(conn) == (False, 2)


def test_concurrent_processes_and_threads_do_not_fork_the_chain(tmp_path):
    """Held: 8 threads x 25 emits, each opening its own connection."""
    errs = []

    def work(n):
        try:
            for i in range(25):
                emit("t", {"n": n, "i": i})
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    c = open_db()
    assert not errs
    assert c.execute("SELECT count(*) FROM events").fetchone()[0] == 200
    assert verify_chain(c) == (True, None)
    c.close()


# ---------- audit evasion: input that makes emit() raise (the wiring then fails open) ----------

def test_lone_surrogate_in_a_payload_is_still_logged(conn):
    """E1: '\\ud800' passes json.dumps(ensure_ascii=False) and then dies in .encode('utf-8'). A tool input
    or user message carrying one is not logged at all (the wiring swallows the error)."""
    emit("tool_call", {"tool": "shell", "input": {"cmd": "rm -rf x \ud800"}})
    assert [e.kind for e in iter_events()] == ["tool_call"]


def test_deeply_nested_tool_input_is_still_logged(conn):
    """E2: a tool input nested 3000 deep runs the tool, but redact() hits RecursionError, the wiring swallows
    it, and no tool_call event exists. A model (or an injected prompt) can hide a call by nesting it."""
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="t", input_schema={}, handler=lambda **kw: "ran"))
    deep: object = "x"
    for _ in range(3000):
        deep = [deep]
    res = asyncio.run(reg.execute("t", {"a": deep}))
    assert res.success
    assert "tool_call" in [e.kind for e in iter_events()]


def test_deeply_nested_payload_is_logged_not_dropped(conn):
    """E2b: redact() recurses without a bound; 1000+ levels raise RecursionError and the event is lost."""
    deep: object = "x"
    for _ in range(1200):
        deep = {"k": deep}
    emit("big", {"a": deep})
    assert [e.kind for e in iter_events()] == ["big"]


def test_nan_and_non_json_payloads_write_nothing(conn):
    for bad in ({"x": float("nan")}, {"x": object()}, {"x": {1, 2}}):
        with pytest.raises((TypeError, ValueError)):
            emit("k", bad)
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


# ---------- redaction ----------

LEAKS = {
    "short_password_assignment": "run it with password=hunter2 now",
    "secret_key_assignment": "SECRET_KEY=django-insecure-abcdefghijklmnop",
    "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
    "private_key_assignment": "private_key=MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
    "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    "basic_auth_header": "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==",
    "url_token_param": "https://api.example.com/v1/x?token=abcdef0123456789abcdef",
    "url_userinfo": "postgres://admin:s3cr3tpassw0rd@db.internal/prod",
}


@pytest.mark.parametrize("name", sorted(LEAKS))
def test_secret_shapes_in_free_text(conn, name):
    text = LEAKS[name]
    emit("tool_call", {"input": text})
    secretish = {
        "short_password_assignment": "hunter2",
        "secret_key_assignment": "django-insecure-abcdefghijklmnop",
        "aws_secret_access_key": "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
        "private_key_assignment": "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
        "jwt": "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "basic_auth_header": "dXNlcjpwYXNzd29yZDEyMw==",
        "url_token_param": "abcdef0123456789abcdef",
        "url_userinfo": "s3cr3tpassw0rd",
    }[name]
    assert secretish not in stored(conn)


@pytest.mark.parametrize(
    "payload,needle",
    [
        ({"password": {"value": "hunter2hunter2"}}, "hunter2hunter2"),
        ({"password": ["hunter2hunter2"]}, "hunter2hunter2"),
        ({"password": 123456789}, "123456789"),
        ({"token": "abcdef0123456789abcdef"}, "abcdef0123456789abcdef"),
        ({"headers": {"Authorization": "Basic dXNlcjpwYXNzd29yZDEyMw=="}}, "dXNlcjpwYXNzd29yZDEyMw"),
        ({"cookie": "session=abcdef0123456789abcdef"}, "abcdef0123456789abcdef"),
        ({"credentials": "abcdef0123456789abcdef"}, "abcdef0123456789abcdef"),
    ],
)
def test_secret_values_under_secret_looking_keys(conn, payload, needle):
    emit("tool_call", payload)
    assert needle not in stored(conn)


def test_redaction_survives_a_large_hostile_input_quickly():
    """Held: no catastrophic backtracking on pathological text."""
    cases = ["password" + " " * 200_000, "-----BEGIN PRIVATE KEY-----" * 20_000, "sk-" * 100_000,
             "api_key=" * 50_000, "bearer " * 50_000, "a" * 1_000_000]
    for text in cases:
        t0 = time.perf_counter()
        redact(text)
        assert time.perf_counter() - t0 < 2.0, text[:20]


def test_known_key_shapes_are_redacted_in_every_text_column(conn):
    key = "sk-ant-api03-" + "A1b2C3d4" * 6
    emit(f"kind {key}", {"a": 1}, actor=f"actor {key}", session_id=f"s {key}")
    assert key not in stored(conn)


# ---------- wiring ----------

def _stub_brain(monkeypatch, reply):
    from tanishi.core import brain as brain_mod
    from tanishi.tools.registry import ToolRegistry

    b = object.__new__(brain_mod.TanishiBrain)
    b.config = types.SimpleNamespace(max_conversation_history=5)
    b.conversation_history = []
    b.memory_manager = None
    b.tool_registry = ToolRegistry()

    async def fake_ollama(self, system_prompt, messages):
        return reply(brain_mod)

    monkeypatch.setattr(brain_mod.TanishiBrain, "_select_model", lambda self, text: "ollama")
    monkeypatch.setattr(brain_mod.TanishiBrain, "_think_ollama", fake_ollama)
    monkeypatch.setattr(brain_mod.TanishiBrain, "_response_ok_for_skill_learning", staticmethod(lambda r: False))
    return b


@BREAK
def test_task_end_success_is_false_when_the_model_call_failed(conn, monkeypatch):
    """W1: _think swallows API and Ollama errors into a normal BrainResponse(model_used='... (error)').
    task_end then says success=True, so failure analysis (the whole M1 loop) never sees the failure."""
    b = _stub_brain(monkeypatch, lambda m: m.BrainResponse(content="Local brain is offline. Error: x",
                                                          model_used="ollama (error)"))
    asyncio.run(b.think("do the thing"))
    end = list(iter_events(kind="task_end"))[-1]
    assert end.payload["success"] is False


@BREAK
def test_tool_events_can_be_tied_to_their_task_and_to_each_other(conn, monkeypatch):
    """W2: neither task nor tool events carry session_id or a shared id, and tool_call / tool_result carry
    no call id. With two concurrent tasks, or two parallel calls of one tool, the pairs cannot be matched."""
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()

    async def slow(ms: int = 0):
        await asyncio.sleep(ms / 1000)
        return str(ms)

    reg.register(ToolDefinition(name="slow", description="s", input_schema={}, handler=slow))

    async def both():
        await asyncio.gather(reg.execute("slow", {"ms": 60}), reg.execute("slow", {"ms": 5}))

    asyncio.run(both())
    evs = list(iter_events())
    calls = [e for e in evs if e.kind == "tool_call"]
    results = [e for e in evs if e.kind == "tool_result"]
    ids = {json.dumps(e.payload.get("call_id")) for e in calls + results}
    assert "null" not in ids and len(ids) == 2


def test_failed_tool_and_cancelled_tool_both_get_a_result_event(conn):
    """Held."""
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()

    async def hang():
        await asyncio.sleep(30)

    reg.register(ToolDefinition(name="hang", description="h", input_schema={}, handler=hang,
                                timeout_override=0.05))
    res = asyncio.run(reg.execute("hang", {}))
    assert not res.success
    assert [e.kind for e in iter_events()] == ["tool_call", "tool_result"]


def test_tool_output_with_odd_types_does_not_break_execute(conn):
    """A handler may return non-str output (dict, None, bytes); the logger must not turn that into a crash."""
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    for name, val in (("d", {"k": "v" * 5000}), ("n", None), ("b", b"\xff" * 10)):
        reg.register(ToolDefinition(name=name, description=name, input_schema={}, handler=lambda v=val: v))
        res = asyncio.run(reg.execute(name, {}))
        assert res.tool_name == name


def test_tool_input_is_clipped_like_tool_output(conn):
    """P1: output is clipped to 4000 chars, input is not. One write_file of 5 MB puts 5 MB in the log,
    twice hashed, and every verify_chain pays for it for ever."""
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="w", description="w", input_schema={}, handler=lambda body="": "ok"))
    asyncio.run(reg.execute("w", {"body": "z" * 5_000_000}))
    size = conn.execute("SELECT max(length(payload)) FROM events").fetchone()[0]
    assert size < 100_000


# ---------- reads and performance ----------

def test_since_filter_with_z_suffix_does_not_skip_events_in_that_second(conn):
    emit("a", {})
    first = next(iter_events()).ts  # e.g. 2026-10-09T12:00:00.123456+00:00
    since = first[:19] + "Z"  # a normal ISO-8601 spelling of the same second
    assert [e.kind for e in iter_events(since=since)] == ["a"]


def test_abandoned_iterator_does_not_pin_the_database(conn):
    it = iter_events()
    emit("a", {})
    next(it)
    del it  # generator finalised, connection closed
    emit("b", {})
    assert verify_chain(conn) == (True, None)


def test_emit_cost_is_bounded(conn):
    """P2 (measured, not failing): emit() reopens the db and re-runs migrate() on every call."""
    t0 = time.perf_counter()
    for i in range(200):
        emit("p", {"i": i})
    per_call_ms = (time.perf_counter() - t0) / 200 * 1000
    assert per_call_ms < 25, per_call_ms


def test_verify_chain_scales_linearly(conn):
    for i in range(3000):
        conn.execute("SELECT 1")
        emit("p", {"i": i})
    t0 = time.perf_counter()
    assert verify_chain(conn) == (True, None)
    assert time.perf_counter() - t0 < 2.0


def test_sqlite_error_type_is_stable_on_corrupt_db(tmp_path, monkeypatch):
    """A garbage db file must raise from emit(), never write elsewhere."""
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"not a database" * 100)
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(bad))
    with pytest.raises(sqlite3.DatabaseError):
        emit("k", {})
