"""Exam for CS2: the append-only, hash-chained event log (tanishi/core_state/events.py).

Written from the spec alone. Spec facts used:
  - emit(kind, payload, actor="tanishi", session_id=None) -> int
  - verify_chain(conn) -> (ok, first_bad_event_id)
  - iter_events(kind=None, since=None) -> Iterator[Event]
  - hash = sha256(prev_hash + canonical JSON of the row without hash); first prev_hash is "GENESIS"
  - editing or deleting any row makes verify_chain return the first broken id
  - think() emits task_start/task_end; ToolRegistry.execute() emits tool_call/tool_result
  - payloads are redacted for API-key patterns before writing
  - property: any emit sequence verifies; any single-byte tamper fails

The spec does not pin the exact canonical-JSON byte layout, so the recompute test accepts the
common canonical layouts. Events are read through the shared Core State db (TANISHI_CORE_STATE_DB,
set per test by tests/conftest.py); emit() takes no connection.
"""
import asyncio
import hashlib
import itertools
import json
import random
import sqlite3
import threading
import types

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.events import emit, iter_events, verify_chain

COLUMNS = ("id", "ts", "kind", "actor", "session_id", "payload", "prev_hash", "hash")


@pytest.fixture
def conn():
    c = open_db()
    migrate(c)
    yield c
    c.close()


def rows(c):
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id")]
    finally:
        c.row_factory = None


def open_tamper_door(c):
    """The append-only triggers stop honest writers; an attacker with file access can drop them."""
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='events'").fetchall():
        c.execute(f'DROP TRIGGER "{name}"')
    c.commit()


def fill(n, kind="step"):
    return [emit(kind, {"i": i, "word": "w" + "abcdefghij"[i % 10] * 3}) for i in range(n)]


# ---------------------------------------------------------------- emit basics

def test_emit_returns_increasing_int_ids_and_stores_row(conn):
    a = emit("first", {"x": 1})
    b = emit("second", {"y": [1, 2, 3]}, actor="warden", session_id="s-1")
    assert isinstance(a, int) and isinstance(b, int)
    assert b > a
    r = rows(conn)
    assert [x["id"] for x in r] == [a, b]
    assert (r[0]["kind"], r[0]["actor"], r[0]["session_id"]) == ("first", "tanishi", None)
    assert (r[1]["kind"], r[1]["actor"], r[1]["session_id"]) == ("second", "warden", "s-1")
    assert json.loads(r[0]["payload"]) == {"x": 1}
    assert json.loads(r[1]["payload"]) == {"y": [1, 2, 3]}
    assert all(x["ts"] for x in r)


def test_first_event_uses_genesis_and_links_follow(conn):
    fill(4)
    r = rows(conn)
    assert r[0]["prev_hash"] == "GENESIS"
    for prev, cur in itertools.pairwise(r):
        assert cur["prev_hash"] == prev["hash"]
    assert len({x["hash"] for x in r}) == 4
    assert all(len(x["hash"]) == 64 and int(x["hash"], 16) >= 0 for x in r)


def _canonical_variants(row):
    body = {k: row[k] for k in COLUMNS if k != "hash"}
    as_obj = dict(body)
    as_obj["payload"] = json.loads(body["payload"]) if body["payload"] is not None else None
    for b in (body, as_obj):
        for seps in ((",", ":"), (", ", ": ")):
            for ascii_ in (True, False):
                yield json.dumps(b, sort_keys=True, separators=seps, ensure_ascii=ascii_)


def test_hash_is_sha256_of_prev_hash_plus_canonical_row(conn):
    emit("a", {"k": "v", "n": 1})
    emit("b", {"é": "ü"}, actor="x", session_id="s")
    for row in rows(conn):
        candidates = {hashlib.sha256((row["prev_hash"] + c).encode()).hexdigest() for c in _canonical_variants(row)}
        assert row["hash"] in candidates


def test_emit_does_not_mutate_callers_payload():
    payload = {"nested": {"k": ["a", "b"]}, "n": 1}
    snapshot = json.loads(json.dumps(payload))
    emit("a", payload)
    assert payload == snapshot


def test_unicode_payload_roundtrips(conn):
    p = {"text": "naïve café — 你好 🚀", "nul": "a\u0000b"}
    emit("u", p)
    assert json.loads(rows(conn)[0]["payload"]) == p
    assert verify_chain(conn) == (True, None)


def test_unserialisable_payload_is_rejected_and_writes_nothing(conn):
    emit("ok", {"a": 1})
    with pytest.raises((TypeError, ValueError)):
        emit("bad", {"obj": object()})
    with pytest.raises((TypeError, ValueError)):
        emit("nan", {"x": float("nan")})
    assert [r["kind"] for r in rows(conn)] == ["ok"]
    assert verify_chain(conn) == (True, None)
    emit("after", {})
    assert verify_chain(conn) == (True, None)


def test_ids_are_never_reused_after_failed_emit(conn):
    a = emit("a", {})
    with pytest.raises((TypeError, ValueError)):
        emit("bad", {"o": object()})
    b = emit("b", {})
    assert b > a


# ---------------------------------------------------------------- verify_chain

def test_empty_log_verifies(conn):
    assert verify_chain(conn) == (True, None)


def test_intact_log_verifies(conn):
    fill(25)
    assert verify_chain(conn) == (True, None)


def test_verify_returns_tuple_of_bool_and_none_or_int(conn):
    fill(2)
    ok, bad = verify_chain(conn)
    assert ok is True and bad is None
    open_tamper_door(conn)
    conn.execute("UPDATE events SET kind='evil' WHERE id=1")
    conn.commit()
    ok, bad = verify_chain(conn)
    assert ok is False and isinstance(bad, int)


@pytest.mark.parametrize("column", ["ts", "kind", "actor", "session_id", "payload", "prev_hash", "hash"])
def test_editing_any_field_reports_that_row(conn, column):
    ids = fill(6)
    target = ids[3]
    open_tamper_door(conn)
    new = '{"i": 999}' if column == "payload" else "tampered"
    conn.execute(f"UPDATE events SET {column}=? WHERE id=?", (new, target))
    conn.commit()
    assert verify_chain(conn) == (False, target)


def test_editing_first_row_reports_first_row(conn):
    ids = fill(4)
    open_tamper_door(conn)
    conn.execute("UPDATE events SET actor='mallory' WHERE id=?", (ids[0],))
    conn.commit()
    assert verify_chain(conn) == (False, ids[0])


def test_editing_last_row_reports_last_row(conn):
    ids = fill(4)
    open_tamper_door(conn)
    conn.execute("UPDATE events SET payload='{\"i\": -1}' WHERE id=?", (ids[-1],))
    conn.commit()
    assert verify_chain(conn) == (False, ids[-1])


def test_forged_row_with_recomputed_hash_still_breaks_the_next_link(conn):
    """Rewriting one row's hash to hide the edit breaks the following row's prev_hash link."""
    ids = fill(5)
    open_tamper_door(conn)
    conn.execute("UPDATE events SET kind='evil' WHERE id=?", (ids[1],))
    new_hash = "f" * 64
    conn.execute("UPDATE events SET hash=? WHERE id=?", (new_hash, ids[1]))
    conn.commit()
    ok, bad = verify_chain(conn)
    assert ok is False
    assert bad in (ids[1], ids[2])


@pytest.mark.parametrize("pos", [0, 1, 3])
def test_deleting_a_row_is_detected_at_the_gap(conn, pos):
    ids = fill(6)
    open_tamper_door(conn)
    conn.execute("DELETE FROM events WHERE id=?", (ids[pos],))
    conn.commit()
    ok, bad = verify_chain(conn)
    assert ok is False
    assert bad in (ids[pos], ids[pos + 1])


def test_first_bad_id_is_the_earliest_of_several_edits(conn):
    ids = fill(8)
    open_tamper_door(conn)
    conn.execute("UPDATE events SET actor='b' WHERE id=?", (ids[6],))
    conn.execute("UPDATE events SET actor='a' WHERE id=?", (ids[2],))
    conn.execute("DELETE FROM events WHERE id=?", (ids[5],))
    conn.commit()
    assert verify_chain(conn) == (False, ids[2])


def test_swapping_two_rows_payloads_is_detected(conn):
    ids = fill(5)
    open_tamper_door(conn)
    p1, p3 = (conn.execute("SELECT payload FROM events WHERE id=?", (i,)).fetchone()[0] for i in (ids[1], ids[3]))
    conn.execute("UPDATE events SET payload=? WHERE id=?", (p3, ids[1]))
    conn.execute("UPDATE events SET payload=? WHERE id=?", (p1, ids[3]))
    conn.commit()
    assert verify_chain(conn) == (False, ids[1])


def test_inserting_a_forged_row_into_the_middle_is_detected(conn):
    ids = fill(4)
    open_tamper_door(conn)
    conn.execute(
        "INSERT INTO events (ts, kind, actor, session_id, payload, prev_hash, hash) "
        "VALUES ('2026-01-01T00:00:00', 'forged', 'x', NULL, '{}', 'aa', 'bb')"
    )
    conn.commit()
    ok, bad = verify_chain(conn)
    assert ok is False
    assert bad is not None and bad > ids[-1]


def test_honest_writers_cannot_update_or_delete(conn):
    ids = fill(3)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE events SET kind='x' WHERE id=?", (ids[0],))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM events WHERE id=?", (ids[1],))
    conn.rollback()
    assert verify_chain(conn) == (True, None)
    assert len(rows(conn)) == 3


def test_verify_is_read_only(conn):
    fill(5)
    before = rows(conn)
    verify_chain(conn)
    verify_chain(conn)
    assert rows(conn) == before


def test_log_continues_and_still_verifies_after_reopen(conn):
    fill(3)
    conn.close()
    fill(3)
    c2 = open_db()
    try:
        r = rows(c2)
        assert len(r) == 6 and r[3]["prev_hash"] == r[2]["hash"]
        assert verify_chain(c2) == (True, None)
    finally:
        c2.close()


def test_concurrent_emits_keep_one_linear_chain(conn):
    errors = []

    def worker(n):
        try:
            for i in range(10):
                emit("t", {"w": n, "i": i})
        except BaseException as e:  # noqa: BLE001 - reported on the main thread
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    r = rows(conn)
    assert len(r) == 60
    assert len({x["id"] for x in r}) == 60
    assert verify_chain(conn) == (True, None)
    seen = {(json.loads(x["payload"])["w"], json.loads(x["payload"])["i"]) for x in r}
    assert len(seen) == 60


# ---------------------------------------------------------------- iter_events

def test_iter_events_is_a_lazy_iterator_in_id_order(conn):
    ids = fill(5)
    it = iter_events()
    assert iter(it) is it and hasattr(it, "__next__")
    events = list(it)
    assert [e.id for e in events] == ids


def test_iter_events_exposes_row_fields(conn):
    emit("hello", {"a": [1, {"b": 2}]}, actor="me", session_id="sess")
    emit("again", {})
    first, second = list(iter_events())
    assert (first.kind, first.actor, first.session_id) == ("hello", "me", "sess")
    assert first.payload == {"a": [1, {"b": 2}]}
    assert first.prev_hash == "GENESIS"
    assert second.prev_hash == first.hash
    r = rows(conn)[0]
    assert first.ts == r["ts"] and first.hash == r["hash"]


def test_iter_events_filters_by_kind():
    for k in ("a", "b", "a", "c", "a"):
        emit(k, {"k": k})
    assert [e.kind for e in iter_events(kind="a")] == ["a", "a", "a"]
    assert [e.kind for e in iter_events(kind="c")] == ["c"]
    assert list(iter_events(kind="missing")) == []
    assert len(list(iter_events(kind=None))) == 5


def test_iter_events_kind_filter_is_exact_not_prefix():
    emit("tool_call", {})
    emit("tool_call_extra", {})
    assert [e.kind for e in iter_events(kind="tool_call")] == ["tool_call"]


def test_iter_events_since_filters_by_timestamp():
    emit("a", {})
    emit("b", {})
    emit("c", {})
    events = list(iter_events())
    assert list(iter_events(since="1970-01-01T00:00:00")) == events
    assert list(iter_events(since="9999-12-31T23:59:59")) == []
    last = events[-1]
    got = list(iter_events(since=last.ts))
    assert last.id in [e.id for e in got]
    assert all(e.ts >= last.ts for e in got)


def test_iter_events_combines_kind_and_since():
    emit("x", {})
    emit("y", {})
    assert list(iter_events(kind="x", since="9999-12-31T23:59:59")) == []
    assert [e.kind for e in iter_events(kind="y", since="1970-01-01T00:00:00")] == ["y"]


def test_iter_events_on_empty_log(conn):
    assert list(iter_events()) == []


# ---------------------------------------------------------------- redaction

def _secrets():
    """Built at runtime so no key-shaped literal sits in the repo."""
    return {
        "anthropic": "sk-" + "ant-api03-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCdEf",
        "openai": "sk-" + "proj-" + "Zy9Xw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe",
        "openai_legacy": "sk-" + "Zy9Xw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0FeDcBa",
        "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "github": "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
        "slack": "xo" + "xb-" + "1234567890-0987654321-AbCdEfGhIjKlMnOpQrStUvWx",
        "google": "AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    }


@pytest.mark.parametrize("name", sorted(_secrets()))
def test_api_key_patterns_are_redacted_before_writing(conn, name):
    secret = _secrets()[name]
    emit("tool_call", {"note": "keep me", "auth": secret})
    raw = conn.execute("SELECT * FROM events").fetchone()
    assert secret not in " ".join(str(v) for v in raw)
    p = json.loads(rows(conn)[0]["payload"])
    assert p["note"] == "keep me"
    assert secret not in json.dumps(p)
    assert "auth" in p
    assert verify_chain(conn) == (True, None)
    assert all(secret not in json.dumps(e.payload) for e in iter_events())


def test_secret_inside_longer_text_is_redacted_rest_is_kept(conn):
    secret = _secrets()["anthropic"]
    emit("x", {"cmd": f"curl -H 'x-api-key: {secret}' https://example.com/v1"})
    text = rows(conn)[0]["payload"]
    assert secret not in text
    assert "curl" in text and "https://example.com/v1" in text


def test_secret_in_nested_dicts_and_lists_is_redacted(conn):
    s1, s2, s3 = _secrets()["openai"], _secrets()["github"], _secrets()["aws"]
    emit("x", {"a": {"b": [{"c": s1}, "fine", [s2]]}, "d": [s3, 7, None, True]})
    text = rows(conn)[0]["payload"]
    for s in (s1, s2, s3):
        assert s not in text
    p = json.loads(text)
    assert p["a"]["b"][1] == "fine"
    assert p["d"][1:] == [7, None, True]


def test_repeated_and_multiple_secrets_all_redacted(conn):
    s = _secrets()
    emit("x", {"one": f"{s['openai']} and {s['openai']} and {s['github']}"})
    text = rows(conn)[0]["payload"]
    assert s["openai"] not in text and s["github"] not in text


def test_secret_as_dict_key_is_redacted(conn):
    s = _secrets()["anthropic"]
    emit("x", {s: "value"})
    assert s not in rows(conn)[0]["payload"]


def test_redaction_does_not_mutate_callers_payload():
    s = _secrets()["anthropic"]
    payload = {"auth": s}
    emit("x", payload)
    assert payload == {"auth": s}


def test_ordinary_text_is_not_redacted(conn):
    p = {
        "a": "risk-assessment-for-the-quarterly-planning-report",
        "b": "the task-force-for-reviewing-all-open-issues met",
        "c": "sk-short",
        "d": "AKIA",
        "e": "plain words, numbers 12345678901234567890 and a path /usr/local/bin",
    }
    emit("x", p)
    assert json.loads(rows(conn)[0]["payload"]) == p


def test_chain_hashes_cover_the_redacted_payload(conn):
    emit("x", {"auth": _secrets()["anthropic"]})
    emit("y", {"auth": _secrets()["aws"]})
    assert verify_chain(conn) == (True, None)


# ---------------------------------------------------------------- property tests

def _random_payload(rng, depth=0):
    kinds = ["int", "str", "none", "bool", "float"] + (["list", "dict"] if depth < 3 else [])
    k = rng.choice(kinds)
    if k == "int":
        return rng.randint(-10**6, 10**6)
    if k == "float":
        return rng.choice([0.5, -2.25, 1e10, 3.0])
    if k == "bool":
        return rng.random() < 0.5
    if k == "none":
        return None
    if k == "str":
        return "".join(rng.choice("abcxyz 0189é你-_/.") for _ in range(rng.randint(0, 12)))
    if k == "list":
        return [_random_payload(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice("abcdef") + str(i): _random_payload(rng, depth + 1) for i in range(rng.randint(0, 4))}


@pytest.mark.parametrize("seed", range(12))
def test_property_any_emit_sequence_verifies(conn, seed):
    rng = random.Random(seed)
    n = rng.randint(1, 30)
    ids = []
    for _ in range(n):
        ids.append(
            emit(
                rng.choice(["a", "b", "tool_call", "task_start", "x.y"]),
                _random_payload(rng) if rng.random() < 0.2 else {"v": _random_payload(rng)},
                actor=rng.choice(["tanishi", "warden", "arena"]),
                session_id=rng.choice([None, "s1", "s2"]),
            )
        )
    assert ids == sorted(ids) and len(set(ids)) == n
    assert verify_chain(conn) == (True, None)
    assert len(list(iter_events())) == n


def _flip(text, rng):
    i = rng.randrange(len(text))
    repl = "q" if text[i] != "q" else "z"
    return text[:i] + repl + text[i + 1:]


@pytest.mark.parametrize("seed", range(40))
def test_property_any_single_byte_tamper_fails(conn, seed):
    rng = random.Random(1000 + seed)
    n = rng.randint(2, 12)
    ids = [
        emit(
            rng.choice(["alpha", "beta", "gamma"]),
            {"word": "".join(rng.choice("abcdefgh") for _ in range(8)), "n": rng.randint(0, 99)},
            actor=rng.choice(["tanishi", "warden"]),
            session_id=rng.choice([None, "sess-one"]),
        )
        for _ in range(n)
    ]
    assert verify_chain(conn) == (True, None)
    target = rng.choice(ids)
    column = rng.choice(["ts", "kind", "actor", "session_id", "prev_hash", "hash", "word"])
    open_tamper_door(conn)
    if column == "word":
        text = conn.execute("SELECT payload FROM events WHERE id=?", (target,)).fetchone()[0]
        start = text.index('"word"') + len('"word": "')
        j = text.index('"', start) if text[start - 1] == '"' else start
        pos = rng.randrange(start, j)
        text = text[:pos] + ("q" if text[pos] != "q" else "z") + text[pos + 1:]
        conn.execute("UPDATE events SET payload=? WHERE id=?", (text, target))
    elif column == "session_id":
        old = conn.execute("SELECT session_id FROM events WHERE id=?", (target,)).fetchone()[0]
        new = "x" if old is None else _flip(old, rng)
        conn.execute("UPDATE events SET session_id=? WHERE id=?", (new, target))
    else:
        old = conn.execute(f"SELECT {column} FROM events WHERE id=?", (target,)).fetchone()[0]
        conn.execute(f"UPDATE events SET {column}=? WHERE id=?", (_flip(old, rng), target))
    conn.commit()
    ok, bad = verify_chain(conn)
    assert ok is False
    assert bad == target


@pytest.mark.parametrize("seed", range(15))
def test_property_deleting_any_non_final_row_fails(conn, seed):
    rng = random.Random(5000 + seed)
    ids = fill(rng.randint(3, 12))
    victim_ix = rng.randrange(len(ids) - 1)
    open_tamper_door(conn)
    conn.execute("DELETE FROM events WHERE id=?", (ids[victim_ix],))
    conn.commit()
    ok, bad = verify_chain(conn)
    assert ok is False
    assert bad in (ids[victim_ix], ids[victim_ix + 1])


# ---------------------------------------------------------------- wiring into legacy code

def _kinds():
    return [e.kind for e in iter_events()]


def _make_registry():
    from tanishi.tools.registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()

    def ok_tool(text: str = ""):
        return {"echo": text}

    def bad_tool(text: str = ""):
        raise RuntimeError("boom")

    for name, fn in (("ok_tool", ok_tool), ("bad_tool", bad_tool)):
        reg.register(ToolDefinition(name=name, description=name, input_schema={"type": "object"}, handler=fn))
    return reg


def test_registry_execute_emits_tool_call_then_tool_result(conn):
    reg = _make_registry()
    res = asyncio.run(reg.execute("ok_tool", {"text": "hi"}))
    assert res.success
    events = list(iter_events())
    assert [e.kind for e in events] == ["tool_call", "tool_result"]
    assert "ok_tool" in json.dumps(events[0].payload)
    assert "ok_tool" in json.dumps(events[1].payload)
    assert verify_chain(conn) == (True, None)


def test_registry_emits_tool_result_for_failing_tool(conn):
    reg = _make_registry()
    res = asyncio.run(reg.execute("bad_tool", {"text": "x"}))
    assert not res.success
    assert _kinds()[0] == "tool_call" and _kinds()[-1] == "tool_result"
    assert "boom" in json.dumps(list(iter_events(kind="tool_result"))[-1].payload)


def test_registry_redacts_secrets_in_tool_input(conn):
    secret = _secrets()["anthropic"]
    reg = _make_registry()
    asyncio.run(reg.execute("ok_tool", {"text": secret}))
    for e in iter_events():
        assert secret not in json.dumps(e.payload)
    for row in conn.execute("SELECT * FROM events").fetchall():
        assert secret not in " ".join(str(v) for v in row)


def test_registry_unchanged_result_with_emission(conn):
    reg = _make_registry()
    res = asyncio.run(reg.execute("ok_tool", {"text": "hi"}))
    assert json.loads(res.output) == {"echo": "hi"}
    assert res.tool_name == "ok_tool"


def _stub_brain(monkeypatch, fail=False):
    from tanishi.core import brain as brain_mod

    b = object.__new__(brain_mod.TanishiBrain)
    b.config = types.SimpleNamespace(max_conversation_history=5)
    b.conversation_history = []
    b.memory_manager = None
    b.tool_registry = _make_registry()

    async def fake_ollama(self, system_prompt, messages):
        if fail:
            raise RuntimeError("model down")
        return brain_mod.BrainResponse(content="hello back", model_used="ollama")

    monkeypatch.setattr(brain_mod.TanishiBrain, "_select_model", lambda self, text: "ollama")
    monkeypatch.setattr(brain_mod.TanishiBrain, "_think_ollama", fake_ollama)
    monkeypatch.setattr(brain_mod.TanishiBrain, "_response_ok_for_skill_learning", staticmethod(lambda r: False))
    return b


def test_think_emits_task_start_then_task_end(conn, monkeypatch):
    b = _stub_brain(monkeypatch)
    resp = asyncio.run(b.think("say hello please"))
    assert resp.content == "hello back"
    kinds = _kinds()
    assert "task_start" in kinds and "task_end" in kinds
    assert kinds.index("task_start") < kinds.index("task_end")
    assert kinds.count("task_start") == 1 and kinds.count("task_end") == 1
    assert verify_chain(conn) == (True, None)


def test_think_redacts_secrets_in_user_input(conn, monkeypatch):
    secret = _secrets()["openai"]
    b = _stub_brain(monkeypatch)
    asyncio.run(b.think(f"my key is {secret}, remember it"))
    for row in conn.execute("SELECT * FROM events").fetchall():
        assert secret not in " ".join(str(v) for v in row)


def test_think_still_ends_the_task_when_the_model_fails(conn, monkeypatch):
    b = _stub_brain(monkeypatch, fail=True)
    with pytest.raises(RuntimeError):
        asyncio.run(b.think("say hello please"))
    kinds = _kinds()
    assert "task_start" in kinds
    assert "task_end" in kinds
    assert verify_chain(conn) == (True, None)


def test_two_thinks_make_two_task_pairs_in_order(conn, monkeypatch):
    b = _stub_brain(monkeypatch)
    asyncio.run(b.think("one"))
    asyncio.run(b.think("two"))
    task_kinds = [k for k in _kinds() if k in ("task_start", "task_end")]
    assert task_kinds == ["task_start", "task_end", "task_start", "task_end"]
