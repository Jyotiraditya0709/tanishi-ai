"""Builder's tests for CS2, beside the exam in test_events.py: the edges the exam leaves open."""
import asyncio
import base64
import json
import logging
import sqlite3

import pytest

from tanishi.core_state import events as ev
from tanishi.core_state import migrate, open_db
from tanishi.core_state.events import emit, iter_events, redact, verify_chain


@pytest.fixture
def conn():
    c = open_db()
    migrate(c)
    yield c
    c.close()


def _drop_triggers(c):
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='events'").fetchall():
        c.execute(f'DROP TRIGGER "{name}"')
    c.commit()


def _payloads(c):
    return [r[0] for r in c.execute("SELECT payload FROM events ORDER BY id")]


# ---------------------------------------------------------------- chain edges

def test_deleting_the_last_row_is_detected(conn):
    ids = [emit("a", {"i": i}) for i in range(4)]
    _drop_triggers(conn)
    conn.execute("DELETE FROM events WHERE id=?", (ids[-1],))
    conn.commit()
    assert verify_chain(conn) == (False, ids[-1])


def test_deleting_every_row_is_detected(conn):
    ids = [emit("a", {}) for _ in range(3)]
    _drop_triggers(conn)
    conn.execute("DELETE FROM events")
    conn.commit()
    assert verify_chain(conn) == (False, ids[0])


def test_whitespace_only_edit_of_payload_text_is_detected(conn):
    ids = [emit("a", {"k": 1}) for _ in range(3)]
    _drop_triggers(conn)
    conn.execute("UPDATE events SET payload='{\"k\":1}' WHERE id=?", (ids[1],))
    conn.commit()
    assert verify_chain(conn) == (False, ids[1])


def test_renumbering_a_row_is_detected(conn):
    ids = [emit("a", {}) for _ in range(3)]
    _drop_triggers(conn)
    conn.execute("UPDATE events SET id=? WHERE id=?", (ids[-1] + 10, ids[-1]))
    conn.commit()
    assert verify_chain(conn)[0] is False


def test_a_column_rewritten_as_a_blob_is_reported_not_raised(conn):
    ids = [emit("a", {}) for _ in range(2)]
    _drop_triggers(conn)
    conn.execute("UPDATE events SET ts=? WHERE id=?", (b"\x00\x01", ids[0]))
    conn.commit()
    assert verify_chain(conn) == (False, ids[0])


def test_verify_ignores_the_callers_row_factory(conn):
    emit("a", {})
    conn.row_factory = lambda cur, row: dict(zip([d[0] for d in cur.description], row, strict=True))
    assert verify_chain(conn) == (True, None)


def test_payload_need_not_be_a_dict(conn):
    for p in (None, 3, "text", [1, 2]):
        emit("a", p)
    assert [e.payload for e in iter_events()] == [None, 3, "text", [1, 2]]
    assert verify_chain(conn) == (True, None)


@pytest.mark.parametrize("bad", [{"kind": ""}, {"kind": 3}, {"actor": None}, {"session_id": 7}])
def test_bad_header_fields_are_rejected(conn, bad):
    args = {"kind": "k", "payload": {}, **bad}
    with pytest.raises(TypeError):
        emit(**args)
    assert _payloads(conn) == []


def test_lone_surrogates_are_replaced_everywhere(conn):
    emit("a\udc80", {"s": "x\ud800y", "\udfff": 1}, actor="b\ud800", session_id="s\ud800")
    e = next(iter_events())
    assert (e.kind, e.actor, e.session_id) == ("a�", "b�", "s�")
    assert e.payload == {"s": "x�y", "�": 1}
    assert verify_chain(conn) == (True, None)


def test_nesting_deeper_than_the_cap_becomes_a_marker(conn):
    deep: object = "x"
    for _ in range(ev.MAX_DEPTH + 5):
        deep = [deep]
    emit("a", deep)
    stored = next(iter_events()).payload
    for _ in range(ev.MAX_DEPTH):
        assert isinstance(stored, list)
        stored = stored[0]
    assert stored == ev._TOO_DEEP


def test_redact_alone_never_hits_the_recursion_limit():
    deep: object = {}
    for _ in range(5000):
        deep = {"k": deep}
    assert "TRUNCATED" in json.dumps(redact(deep))


def test_every_string_and_key_is_clipped_after_redaction(conn):
    key = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    emit("a", {"v": "y" * 3900 + " " + key + " " + "y" * 50, "k" * 9000: ["z" * 9000]})
    (_, v), (long_key, (long_item,)) = next(iter_events()).payload.items()
    assert len(v) <= ev.MAX_CHARS and "REDACTED" in v and key[:12] not in v
    assert len(long_key) <= ev.MAX_CHARS and len(long_item) <= ev.MAX_CHARS
    assert long_item.endswith("more chars]")


# ---------------------------------------------------------------- redaction

def _keys():
    return {
        "stripe": "sk" + "_live_" + "a1B2c3D4e5F6g7H8i9J0",
        "huggingface": "hf" + "_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "github_pat": "github" + "_pat_" + "11ABCDEFG0123456789_abcdefghijklmnop",
        "bearer": "Authorization: Bearer " + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_part_here",
        "assigned": "api_key=" + "q8w7e6r5t4y3u2i1",
        "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow" + "IBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    }


@pytest.mark.parametrize("name", sorted(_keys()))
def test_more_key_shapes_are_redacted(conn, name):
    text = _keys()[name]
    emit("x", {"t": text})
    stored = _payloads(conn)[0]
    secret = text.split("Bearer ")[-1].split("=")[-1] if name in ("bearer", "assigned") else text
    assert secret not in stored
    assert "REDACTED" in stored


@pytest.mark.parametrize("key", ["password", "api_key", "OPENAI_API_KEY", "client_secret", "access_token"])
def test_values_under_secret_named_keys_are_redacted(key):
    assert redact({key: "hunter2", "other": "hunter2"}) == {key: "[REDACTED]", "other": "hunter2"}


def test_counts_under_token_like_keys_are_kept():
    assert redact({"max_tokens": 512, "access_token": None}) == {"max_tokens": 512, "access_token": None}


@pytest.mark.parametrize("key", ["author", "csrftoken_name", "tokens_used", "authority", "oauth_provider"])
def test_keys_that_only_contain_a_secret_word_are_kept(key):
    assert redact({key: "plain"}) == {key: "plain"}


@pytest.mark.parametrize("text", [
    "a basic understanding of the problem",
    "see https://example.com/path?page=2&sort=asc",
    "ssh://host.example.com:22/repo and http://localhost:8080/x",
    "basic misunderstandings happen",
])
def test_ordinary_text_near_the_new_patterns_is_kept(text):
    assert redact(text) == text


def test_basic_credential_without_a_header_is_redacted():
    cred = base64.b64encode(b"user:pa55word").decode()
    assert cred not in redact(f"curl with basic {cred} here")


def test_kind_actor_and_session_are_redacted_too(conn):
    key = "sk-" + "ant-api03-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    emit(f"k {key}", {}, actor=key, session_id=key)
    row = conn.execute("SELECT * FROM events").fetchone()
    assert key not in " ".join(str(v) for v in row)
    assert verify_chain(conn) == (True, None)


def test_redacted_keys_do_not_collide():
    a = "sk-" + "Zy9Xw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"
    b = "sk-" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv"
    assert len(redact({a: 1, b: 2})) == 2


# ---------------------------------------------------------------- gap marker

def _break_the_db(monkeypatch):
    def broken():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ev, "_open", broken)


def test_failed_writes_are_noted_without_their_payload(conn, monkeypatch):
    _break_the_db(monkeypatch)
    for kind in ("tool_call", "task_end"):
        with pytest.raises(sqlite3.OperationalError):
            emit(kind, {"input": "private words"})
    text = ev._gaps_path().read_text()
    lines = [json.loads(line) for line in text.splitlines()]
    assert [sorted(x) for x in lines] == [["kind", "ts"], ["kind", "ts"]]
    assert [x["kind"] for x in lines] == ["tool_call", "task_end"]
    assert "private" not in text


def test_next_successful_emit_writes_a_log_gap_first(conn, monkeypatch):
    emit("before", {})
    with monkeypatch.context() as m:
        _break_the_db(m)
        for _ in range(3):
            with pytest.raises(sqlite3.OperationalError):
                emit("tool_call", {})
    new_id = emit("after", {})
    events = list(iter_events())
    assert [e.kind for e in events] == ["before", "log_gap", "after"]
    assert events[1].payload["count"] == 3 and events[1].payload["kinds"] == {"tool_call": 3}
    assert new_id == events[2].id
    assert not ev._gaps_path().exists()
    assert list(ev._gaps_path().parent.glob("*.claim")) == []
    emit("later", {})
    assert [e.kind for e in iter_events()][-1] == "later" and len(list(iter_events(kind="log_gap"))) == 1
    assert verify_chain(conn) == (True, None)


def test_a_rejected_payload_also_leaves_a_gap(conn):
    with pytest.raises(TypeError):
        emit("bad", {"o": object()})
    emit("ok", {})
    assert [(e.kind, e.payload.get("count")) for e in iter_events()] == [("log_gap", 1), ("ok", None)]


def test_gaps_stay_pending_when_the_log_gap_write_fails(conn, monkeypatch):
    with monkeypatch.context() as m:
        _break_the_db(m)
        with pytest.raises(sqlite3.OperationalError):
            emit("a", {})
    real_insert = ev._insert

    def fail_on_event(c, id_, kind, *rest):
        if kind == "b":
            raise sqlite3.OperationalError("full")
        return real_insert(c, id_, kind, *rest)

    with monkeypatch.context() as m:
        m.setattr(ev, "_insert", fail_on_event)
        with pytest.raises(sqlite3.OperationalError):
            emit("b", {})
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    emit("c", {})
    gap = next(iter_events(kind="log_gap"))
    assert gap.payload["count"] == 2 and gap.payload["kinds"] == {"a": 1, "b": 1}


def test_an_unwritable_home_does_not_mask_the_real_error(conn, monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    monkeypatch.setenv("TANISHI_HOME", str(blocker / "home"))
    _break_the_db(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        emit("a", {})


def test_verify_chain_never_cries_wolf_under_a_busy_writer(tmp_path):
    import threading

    for i in range(10):
        emit("seed", {"i": i})
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            emit("live", {})

    t = threading.Thread(target=writer)
    t.start()
    c = open_db()
    try:
        results = [verify_chain(c) for _ in range(1500)]
    finally:
        stop.set()
        t.join()
    assert set(results) == {(True, None)}
    assert verify_chain(c) == (True, None)
    c.close()


# ---------------------------------------------------------------- wiring

def _registry(**tool_kwargs):
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="t", input_schema={"type": "object"},
                                handler=lambda text="": text * 5000, **tool_kwargs))
    return reg


def _tool_events():
    return [(e.kind, e.payload) for e in iter_events() if e.kind.startswith("tool_")]


def test_unknown_tool_still_records_a_pair(conn):
    reg = _registry()
    res = asyncio.run(reg.execute("nope", {}))
    assert not res.success
    kinds = [k for k, _ in _tool_events()]
    assert kinds == ["tool_call", "tool_result"]


def test_denied_tool_records_a_failed_result(conn):
    reg = _registry(requires_approval=True)
    reg.set_approval_callback(lambda name, args: False)
    asyncio.run(reg.execute("t", {"text": "x"}))
    (k1, _), (k2, p2) = _tool_events()
    assert (k1, k2) == ("tool_call", "tool_result") and p2["success"] is False and "denied" in p2["error"]


def test_raising_approval_callback_still_closes_the_call(conn):
    reg = _registry(requires_approval=True)

    def boom(name, args):
        raise RuntimeError("no")

    reg.set_approval_callback(boom)
    with pytest.raises(RuntimeError):
        asyncio.run(reg.execute("t", {}))
    assert [k for k, _ in _tool_events()] == ["tool_call", "tool_result"]


def test_long_output_is_redacted_then_clipped(conn):
    key = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    reg = _registry()
    res = asyncio.run(reg.execute("t", {"text": "y" * 3990 + key}))
    assert len(res.output) > 4000  # the caller gets everything
    _, result = _tool_events()[1]
    assert len(result["output"]) < 4100
    assert key[:20] not in json.dumps(result)


def test_unserialisable_tool_input_is_still_recorded(conn):
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="b", description="b", input_schema={}, handler=lambda data=None: "ok"))
    res = asyncio.run(reg.execute("b", {"data": b"\x00raw"}))
    assert res.success
    (_, call), _ = _tool_events()
    assert "unserialisable" in call["input"]


def test_a_broken_log_stops_the_tool(conn, monkeypatch, caplog):
    from tanishi.tools import registry as reg_mod

    ran = []
    reg = reg_mod.ToolRegistry()
    reg.register(reg_mod.ToolDefinition(name="t", description="t", input_schema={},
                                        handler=lambda: ran.append(1) or "ran"))
    _break_the_db(monkeypatch)
    with caplog.at_level(logging.WARNING):
        res = asyncio.run(reg.execute("t", {}))
    assert ran == []
    assert not res.success and res.error == reg_mod.EVENT_LOG_UNAVAILABLE and res.tool_name == "t"
    assert "OperationalError" in caplog.text


def test_a_failed_tool_result_write_does_not_undo_the_result(conn, monkeypatch, caplog):
    from tanishi.tools import registry as reg_mod

    real_emit = reg_mod.emit

    def emit_calls_only(kind, *a, **k):
        if kind == "tool_result":
            raise sqlite3.OperationalError("disk I/O error")
        return real_emit(kind, *a, **k)

    monkeypatch.setattr(reg_mod, "emit", emit_calls_only)
    with caplog.at_level(logging.WARNING):
        res = asyncio.run(_registry().execute("t", {"text": "a"}))
    assert res.success and res.output == "a" * 5000
    assert "OperationalError" in caplog.text


def _brain(monkeypatch, reply, tool_calls=()):
    import types

    from tanishi.core import brain as brain_mod

    b = object.__new__(brain_mod.TanishiBrain)
    b.config = types.SimpleNamespace(max_conversation_history=5)
    b.conversation_history = []
    b.memory_manager = None
    b.tool_registry = _registry()

    async def fake(self, system_prompt, messages):
        for name in tool_calls:
            await self.tool_registry.execute(name, {"text": "x"})
        return reply(brain_mod)

    monkeypatch.setattr(brain_mod.TanishiBrain, "_select_model", lambda self, text: "ollama")
    monkeypatch.setattr(brain_mod.TanishiBrain, "_think_ollama", fake)
    monkeypatch.setattr(brain_mod.TanishiBrain, "_response_ok_for_skill_learning", staticmethod(lambda r: False))
    return b


def test_task_events_share_a_task_id(conn, monkeypatch):
    b = _brain(monkeypatch, lambda m: m.BrainResponse(content="ok", model_used="ollama"))
    asyncio.run(b.think("hi"))
    start, end = list(iter_events(kind="task_start")), list(iter_events(kind="task_end"))
    assert start[0].payload["task_id"] == end[0].payload["task_id"]
    assert end[0].payload["success"] is True and end[0].payload["model_used"] == "ollama"
    assert end[0].payload["error"] == ""


def test_tool_events_inside_a_task_carry_its_task_id_and_session(conn, monkeypatch):
    b = _brain(monkeypatch, lambda m: m.BrainResponse(content="ok", model_used="ollama"), tool_calls=("t", "t"))
    b._active_session_id = "sess-7"
    asyncio.run(b.think("hi"))
    events = list(iter_events())
    task_id = events[0].payload["task_id"]
    assert [e.kind for e in events] == ["task_start", "tool_call", "tool_result", "tool_call", "tool_result",
                                        "task_end"]
    assert {e.session_id for e in events} == {"sess-7"}
    assert {e.payload["task_id"] for e in events} == {task_id}
    calls = [e.payload["call_id"] for e in events if e.kind.startswith("tool_")]
    assert calls[0] == calls[1] and calls[2] == calls[3] and calls[0] != calls[2]


def test_tool_events_outside_a_task_have_no_task_id(conn):
    asyncio.run(_registry().execute("t", {}))
    assert [(e.payload["task_id"], e.session_id) for e in iter_events()] == [(None, None), (None, None)]


def test_explicit_error_field_marks_the_task_failed(conn, monkeypatch):
    b = _brain(monkeypatch, lambda m: m.BrainResponse(content="sorry", model_used="claude (opus)", error="APIError: x"))
    asyncio.run(b.think("hi"))
    end = next(iter_events(kind="task_end")).payload
    assert end["success"] is False and end["error"] == "APIError: x"


def test_emit_uses_the_core_state_db_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "tanishi.db"))
    with pytest.raises(ValueError):
        ev.emit("a", {})
