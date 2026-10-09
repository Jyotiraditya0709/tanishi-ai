"""Builder's tests for CS2, beside the exam in test_events.py: the edges the exam leaves open."""
import asyncio
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


def test_lone_surrogate_writes_nothing(conn):
    with pytest.raises(ValueError):
        emit("a", {"s": "\ud800"})
    assert _payloads(conn) == []


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


def test_a_broken_log_does_not_break_the_tool(conn, monkeypatch, caplog):
    from tanishi.tools import registry as reg_mod

    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(reg_mod, "emit", broken)
    with caplog.at_level(logging.WARNING):
        res = asyncio.run(_registry().execute("t", {"text": "a"}))
    assert res.success and res.output == "a" * 5000
    assert "OperationalError" in caplog.text


def test_task_events_share_a_task_id(conn, monkeypatch):
    import types

    from tanishi.core import brain as brain_mod

    b = object.__new__(brain_mod.TanishiBrain)
    b.config = types.SimpleNamespace(max_conversation_history=5)
    b.conversation_history = []
    b.memory_manager = None

    async def fake(self, system_prompt, messages):
        return brain_mod.BrainResponse(content="ok", model_used="ollama")

    monkeypatch.setattr(brain_mod.TanishiBrain, "_select_model", lambda self, text: "ollama")
    monkeypatch.setattr(brain_mod.TanishiBrain, "_think_ollama", fake)
    monkeypatch.setattr(brain_mod.TanishiBrain, "_response_ok_for_skill_learning", staticmethod(lambda r: False))
    asyncio.run(b.think("hi"))
    start, end = list(iter_events(kind="task_start")), list(iter_events(kind="task_end"))
    assert start[0].payload["task_id"] == end[0].payload["task_id"]
    assert end[0].payload["success"] is True and end[0].payload["model_used"] == "ollama"


def test_emit_uses_the_core_state_db_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "tanishi.db"))
    with pytest.raises(ValueError):
        ev.emit("a", {})
