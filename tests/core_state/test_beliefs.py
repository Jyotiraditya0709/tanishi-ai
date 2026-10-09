"""Exam for CS3 (belief store with evidence), written from the spec alone.

Spec interface (build/graph.yaml, node CS3):
    add_belief(subject, predicate, object, confidence, source, evidence_event_id=None) -> Belief
    find(subject=None, predicate=None, text=None) -> list[Belief]
    contradictions(belief) -> list[Belief]   # same subject and predicate, different object, both active
    retire(belief_id, reason) -> None

Assumptions the spec forces (a test that needs more than this is a spec gap, not a test):
  * The functions take no connection, so they use the Core State database named by TANISHI_CORE_STATE_DB
    (tests/conftest.py points it at a temp file).
  * A Belief exposes id, subject, predicate, object, confidence, source and status.
  * "active" and "contested" are the status words the spec uses; retire() moves a belief to some other word.
  * The belief_conflict event is a row in `events` with kind == 'belief_conflict'.
  * The importer is run as `python scripts/import_legacy_memory.py <legacy db path>`.
"""
import json
import math
import random
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tanishi.core_state import beliefs as B
from tanishi.core_state import migrate, open_db

ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / "scripts" / "import_legacy_memory.py"
LIVE = ("active", "contested")


# ---------------------------------------------------------------- helpers


@pytest.fixture
def conn():
    c = open_db()
    migrate(c)
    yield c
    c.close()


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def status_of(conn, belief_id):
    r = rows(conn, "SELECT status FROM beliefs WHERE id = ?", belief_id)
    assert len(r) == 1, f"belief {belief_id} is missing: nothing may ever be deleted"
    return r[0][0]


def ids(beliefs):
    return {b.id for b in beliefs}


def conflict_events(conn):
    return rows(conn, "SELECT id, payload FROM events WHERE kind = 'belief_conflict' ORDER BY id")


def add_event(conn):
    """A bare event row to use as evidence (the hash chain is CS2's business, not this node's)."""
    cur = conn.execute(
        "INSERT INTO events(ts, kind, actor, session_id, payload) VALUES ('t', 'observation', 'test', NULL, '{}')"
    )
    conn.commit()
    return cur.lastrowid


def whole_database_text(conn):
    out = []
    for (name,) in rows(conn, "SELECT name FROM sqlite_master WHERE type = 'table'"):
        for row in rows(conn, f'SELECT * FROM "{name}"'):
            out.append(" ".join(str(v) for v in row))
    return "\n".join(out)


# ---------------------------------------------------------------- add_belief basics


def test_add_belief_returns_a_complete_active_belief(conn):
    b = B.add_belief("sky", "is", "blue", 0.8, "user")
    assert (b.subject, b.predicate, b.object) == ("sky", "is", "blue")
    assert b.confidence == pytest.approx(0.8)
    assert b.source == "user"
    assert b.status == "active"
    assert isinstance(b.id, str) and b.id


def test_add_belief_persists_one_row(conn):
    b = B.add_belief("sky", "is", "blue", 0.8, "user")
    r = rows(conn, "SELECT subject, predicate, object, confidence, source, status, created_at, updated_at "
                   "FROM beliefs WHERE id = ?", b.id)
    assert len(r) == 1
    subject, predicate, obj, conf, source, status, created, updated = r[0]
    assert (subject, predicate, obj, source, status) == ("sky", "is", "blue", "user", "active")
    assert conf == pytest.approx(0.8)
    assert created and updated


def test_ids_are_unique(conn):
    made = [B.add_belief(f"s{i}", "p", "o", 0.5, "t") for i in range(25)]
    assert len({b.id for b in made}) == 25


def test_positional_and_keyword_calls_agree(conn):
    b = B.add_belief(subject="a", predicate="b", object="c", confidence=0.3, source="s")
    assert (b.subject, b.predicate, b.object, b.source) == ("a", "b", "c", "s")


def test_unicode_and_hostile_text_round_trips(conn):
    nasty = "'; DROP TABLE beliefs; -- ünï©ode ✓ 日本語"
    b = B.add_belief(nasty, nasty, nasty, 0.5, nasty)
    got = B.find(subject=nasty)
    assert ids(got) == {b.id}
    assert got[0].object == nasty and got[0].source == nasty
    assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 1


# ---------------------------------------------------------------- confidence clamp


@pytest.mark.parametrize(
    "given, expected",
    [(0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (1.0000001, 1.0), (7, 1.0), (-0.0001, 0.0), (-5, 0.0),
     (math.inf, 1.0), (-math.inf, 0.0), (1, 1.0), (0, 0.0)],
)
def test_confidence_is_clamped(conn, given, expected):
    b = B.add_belief("s", "p", "o", given, "t")
    assert b.confidence == pytest.approx(expected)
    stored = rows(conn, "SELECT confidence FROM beliefs WHERE id = ?", b.id)[0][0]
    assert stored == pytest.approx(expected)


def test_nan_confidence_never_leaves_the_unit_interval(conn):
    """NaN has no clamp; refusing it or mapping it into [0, 1] are both fine. Storing it is not."""
    try:
        b = B.add_belief("s", "p", "o", math.nan, "t")
    except (ValueError, TypeError):
        assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 0
        return
    assert 0.0 <= b.confidence <= 1.0
    stored = rows(conn, "SELECT confidence FROM beliefs WHERE id = ?", b.id)[0][0]
    assert stored is not None and 0.0 <= stored <= 1.0


def test_confidence_clamp_property():
    rnd = random.Random(20261009)
    c = open_db()
    migrate(c)
    try:
        for _ in range(200):
            x = rnd.choice([rnd.uniform(-1e6, 1e6), rnd.uniform(-2, 3), rnd.uniform(0, 1)])
            b = B.add_belief(f"s{rnd.random()}", "p", "o", x, "t")
            assert b.confidence == pytest.approx(min(1.0, max(0.0, x)))
        assert all(0 <= r[0] <= 1 for r in rows(c, "SELECT confidence FROM beliefs"))
    finally:
        c.close()


# ---------------------------------------------------------------- source is required


@pytest.mark.parametrize("bad", [None, "", "   ", "\t\n"])
def test_source_is_required(conn, bad):
    with pytest.raises((ValueError, TypeError)):
        B.add_belief("s", "p", "o", 0.5, bad)
    assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 0


def test_omitting_source_is_an_error(conn):
    with pytest.raises(TypeError):
        B.add_belief("s", "p", "o", 0.5)  # type: ignore[call-arg]
    assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 0


# ---------------------------------------------------------------- evidence


def test_evidence_event_is_linked(conn):
    ev = add_event(conn)
    b = B.add_belief("s", "p", "o", 0.9, "user", evidence_event_id=ev)
    r = rows(conn, "SELECT belief_id, event_id FROM evidence WHERE belief_id = ?", b.id)
    assert r == [(b.id, ev)]


def test_no_evidence_event_means_no_evidence_row(conn):
    b = B.add_belief("s", "p", "o", 0.9, "user")
    assert rows(conn, "SELECT COUNT(*) FROM evidence WHERE belief_id = ?", b.id)[0][0] == 0


def test_same_event_can_support_two_beliefs(conn):
    ev = add_event(conn)
    a = B.add_belief("s1", "p", "o", 0.9, "user", evidence_event_id=ev)
    b = B.add_belief("s2", "p", "o", 0.9, "user", evidence_event_id=ev)
    got = rows(conn, "SELECT belief_id FROM evidence WHERE event_id = ?", ev)
    assert {r[0] for r in got} == {a.id, b.id}


# ---------------------------------------------------------------- contradictions


def test_contradicting_belief_keeps_both_and_marks_them_contested(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 2
    assert status_of(conn, a.id) == "contested"
    assert status_of(conn, b.id) == "contested"


def test_contradiction_emits_exactly_one_belief_conflict_event(conn):
    B.add_belief("sky", "color", "blue", 0.8, "user")
    assert conflict_events(conn) == []
    B.add_belief("sky", "color", "green", 0.6, "user")
    assert len(conflict_events(conn)) == 1


def test_conflict_event_payload_is_json_naming_both_beliefs(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    (_, payload), = conflict_events(conn)
    json.loads(payload)  # valid JSON
    assert a.id in payload and b.id in payload


def test_contradictions_lists_the_other_side_for_both_members(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    assert ids(B.contradictions(b)) == {a.id}
    assert ids(B.contradictions(a)) == {b.id}


def test_contradictions_never_includes_self_or_same_object(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    assert B.contradictions(a) == []
    twin = B.add_belief("sky", "color", "blue", 0.7, "other")
    assert a.id not in ids(B.contradictions(twin))
    assert twin.id not in ids(B.contradictions(a))


def test_agreeing_belief_causes_no_conflict(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "blue", 0.9, "sensor")
    assert conflict_events(conn) == []
    assert status_of(conn, a.id) == "active"
    assert status_of(conn, b.id) == "active"


@pytest.mark.parametrize("other", [("grass", "color", "green"), ("sky", "mood", "green"), ("grass", "mood", "green")])
def test_different_subject_or_predicate_is_not_a_contradiction(conn, other):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief(*other, 0.8, "user")
    assert conflict_events(conn) == []
    assert status_of(conn, a.id) == "active" and status_of(conn, b.id) == "active"
    assert B.contradictions(b) == [] and B.contradictions(a) == []


def test_third_contradicting_belief_contests_all_three(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    before = len(conflict_events(conn))
    c = B.add_belief("sky", "color", "red", 0.5, "user")
    assert all(status_of(conn, x.id) == "contested" for x in (a, b, c))
    assert ids(B.contradictions(c)) == {a.id, b.id}
    assert len(conflict_events(conn)) > before


def test_retired_belief_is_not_a_contradiction(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    B.retire(a.id, "was wrong")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    assert status_of(conn, b.id) == "active"
    assert conflict_events(conn) == []
    assert B.contradictions(b) == []


def test_retiring_one_side_removes_it_from_contradictions(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    B.retire(a.id, "sensor was broken")
    assert B.contradictions(b) == []
    assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 2


def test_conflict_with_a_different_pair_does_not_touch_unrelated_beliefs(conn):
    bystander = B.add_belief("sun", "color", "yellow", 0.9, "user")
    B.add_belief("sky", "color", "blue", 0.8, "user")
    B.add_belief("sky", "color", "green", 0.6, "user")
    assert status_of(conn, bystander.id) == "active"


def test_concurrent_contradicting_adds_still_get_caught(conn):
    """Two writers, two connections: whoever commits second must see the first."""
    errors, made = [], []

    def go(obj):
        try:
            made.append(B.add_belief("sky", "color", obj, 0.5, "t"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go, args=(o,)) for o in ("blue", "green")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(made) == 2
    assert all(status_of(conn, x.id) == "contested" for x in made)
    assert len(conflict_events(conn)) >= 1


# ---------------------------------------------------------------- retire: nothing is ever deleted


def test_retire_changes_status_and_keeps_the_row(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user", evidence_event_id=add_event(conn))
    before = rows(conn, "SELECT subject, predicate, object, confidence, source FROM beliefs WHERE id = ?", a.id)
    assert B.retire(a.id, "no longer true") is None
    assert status_of(conn, a.id) not in LIVE
    assert status_of(conn, a.id)  # a real word, not NULL or ''
    assert rows(conn, "SELECT subject, predicate, object, confidence, source FROM beliefs WHERE id = ?", a.id) == before
    assert rows(conn, "SELECT COUNT(*) FROM evidence WHERE belief_id = ?", a.id)[0][0] == 1


def test_retire_records_the_reason_somewhere(conn):
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    reason = "reason-token-7f3a91"
    B.retire(a.id, reason)
    assert reason in whole_database_text(conn)


def test_retire_does_not_delete_events(conn):
    B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.8, "user")
    n = rows(conn, "SELECT COUNT(*) FROM events")[0][0]
    B.retire(b.id, "x")
    assert rows(conn, "SELECT COUNT(*) FROM events")[0][0] >= n


def test_retire_only_touches_the_named_belief(conn):
    a = B.add_belief("a", "p", "o", 0.5, "t")
    b = B.add_belief("b", "p", "o", 0.5, "t")
    B.retire(a.id, "r")
    assert status_of(conn, b.id) == "active"


def test_retiring_twice_does_not_delete_or_resurrect(conn):
    a = B.add_belief("a", "p", "o", 0.5, "t")
    B.retire(a.id, "r1")
    first = status_of(conn, a.id)
    try:
        B.retire(a.id, "r2")
    except (ValueError, LookupError, RuntimeError):
        pass  # refusing a second retire is acceptable
    assert status_of(conn, a.id) == first


def test_no_code_path_deletes_a_belief_row():
    """The module must not contain a DELETE of beliefs or evidence (a cheap static guard on top of the behaviour tests)."""
    import re
    src = Path(B.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bDELETE\s+FROM\s+(beliefs|evidence)\b", src, re.IGNORECASE)
    assert not re.search(r"\bDROP\s+TABLE\b", src, re.IGNORECASE)


# ---------------------------------------------------------------- find


@pytest.fixture
def library(conn):
    return {
        "sky_color": B.add_belief("sky", "color", "blue", 0.9, "user"),
        "sky_mood": B.add_belief("sky", "mood", "calm", 0.5, "user"),
        "grass_color": B.add_belief("grass", "color", "green", 0.7, "user"),
        "pct": B.add_belief("claim", "text", "100% sure", 0.4, "user"),
        "under": B.add_belief("claim", "text", "a_b", 0.4, "user"),
        "plain": B.add_belief("claim", "text", "axb", 0.4, "user"),
    }


def test_find_by_subject(library):
    got = B.find(subject="sky")
    assert ids(got) == {library["sky_color"].id, library["sky_mood"].id}
    assert all(isinstance(x, type(library["sky_color"])) for x in got)


def test_find_by_predicate(library):
    assert ids(B.find(predicate="color")) == {library["sky_color"].id, library["grass_color"].id}


def test_find_by_subject_and_predicate_together(library):
    assert ids(B.find(subject="sky", predicate="color")) == {library["sky_color"].id}


def test_find_by_text_matches_object(library):
    assert library["grass_color"].id in ids(B.find(text="green"))
    assert library["sky_color"].id not in ids(B.find(text="green"))


def test_find_text_is_a_substring_search(library):
    assert library["pct"].id in ids(B.find(text="sure"))


def test_find_text_treats_like_wildcards_literally(library):
    assert ids(B.find(text="%")) == {library["pct"].id}
    assert ids(B.find(text="_")) == {library["under"].id}


def test_find_with_no_match_is_an_empty_list(library):
    assert B.find(subject="nobody") == []
    assert B.find(subject="sky", predicate="nothing") == []
    assert B.find(text="zzzzz") == []


def test_find_returns_current_status(conn, library):
    a = B.add_belief("moon", "made_of", "rock", 0.9, "user")
    b = B.add_belief("moon", "made_of", "cheese", 0.1, "user")
    got = {x.id: x.status for x in B.find(subject="moon")}
    assert got == {a.id: "contested", b.id: "contested"}


def test_find_is_not_fooled_by_sql_in_arguments(library):
    assert B.find(subject="sky' OR '1'='1") == []
    assert B.find(text="'; DROP TABLE beliefs; --") == []


# ---------------------------------------------------------------- property: random histories keep the invariants


@pytest.mark.parametrize("seed", range(8))
def test_random_history_invariants(seed):
    rnd = random.Random(seed)
    c = open_db()
    migrate(c)
    try:
        subjects, predicates, objects = ["a", "b"], ["p", "q"], ["x", "y", "z"]
        seen, retired = [], set()
        for _ in range(40):
            if seen and rnd.random() < 0.25:
                victim = rnd.choice(seen)
                B.retire(victim, f"r{rnd.random()}")
                retired.add(victim)
            else:
                b = B.add_belief(rnd.choice(subjects), rnd.choice(predicates), rnd.choice(objects),
                                 rnd.uniform(-0.5, 1.5), rnd.choice(["user", "legacy", "sensor"]))
                seen.append(b.id)
            # Nothing is ever deleted.
            assert {r[0] for r in rows(c, "SELECT id FROM beliefs")} >= set(seen)
            assert rows(c, "SELECT COUNT(*) FROM beliefs")[0][0] == len(seen)
        data = rows(c, "SELECT id, subject, predicate, object, confidence, status FROM beliefs")
        for bid, s, p, o, conf, st in data:
            assert 0 <= conf <= 1
            assert st, "status must never be empty"
            if bid in retired:
                assert st not in LIVE
            else:
                assert st in LIVE
        # Two live beliefs that disagree on (subject, predicate) must both be contested, and contradictions() must say so.
        live = [d for d in data if d[0] not in retired]
        for x in live:
            peers = {y[0] for y in live if y[1:3] == x[1:3] and y[3] != x[3]}
            if peers:
                assert x[5] == "contested"
            probe = next(bb for bb in B.find(subject=x[1], predicate=x[2]) if bb.id == x[0])
            assert ids(B.contradictions(probe)) == peers
        # Every disagreement left a trace in the event log.
        if any(d[5] == "contested" for d in data):
            assert len(conflict_events(c)) >= 1
    finally:
        c.close()


# ---------------------------------------------------------------- scripts/import_legacy_memory.py


def make_legacy(path, core=(("name", "Ada"), ("city", "Lisbon")), memories=(("m1", "likes tea"), ("m2", "owns a bike"))):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE core_memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    c.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT DEFAULT 'fact', "
              "importance REAL DEFAULT 0.5, tags TEXT DEFAULT '[]', source TEXT DEFAULT 'conversation', "
              "created_at TEXT, last_accessed TEXT, access_count INTEGER DEFAULT 0)")
    for k, v in core:
        c.execute("INSERT INTO core_memory VALUES (?, ?, '2026-01-01T00:00:00')", (k, v))
    for i, content in memories:
        c.execute("INSERT INTO memories(id, content, importance, created_at) VALUES (?, ?, 0.9, '2026-01-01T00:00:00')",
                  (i, content))
    c.commit()
    c.close()


def run_importer(*args):
    return subprocess.run([sys.executable, str(IMPORTER), *map(str, args)], capture_output=True, text=True,
                          cwd=ROOT, timeout=120, check=False)


def legacy_beliefs(conn):
    return rows(conn, "SELECT id, subject, predicate, object, confidence, source, status FROM beliefs WHERE source = 'legacy'")


def test_importer_exists():
    assert IMPORTER.is_file()


def test_importer_imports_every_row_with_source_legacy(conn, tmp_path):
    src = tmp_path / "old.sqlite"
    make_legacy(src)
    r = run_importer(src)
    assert r.returncode == 0, r.stderr
    got = legacy_beliefs(conn)
    assert len(got) == 4
    assert rows(conn, "SELECT COUNT(*) FROM beliefs WHERE source != 'legacy'")[0][0] == 0
    blob = "\n".join(" ".join(str(v) for v in row[1:4]) for row in got)
    for text in ("name", "Ada", "city", "Lisbon", "likes tea", "owns a bike"):
        assert text in blob, f"legacy text {text!r} was lost"
    assert all(0 <= row[4] <= 1 for row in got)
    assert all(row[6] in LIVE for row in got)


def test_importer_is_idempotent(conn, tmp_path):
    src = tmp_path / "old.sqlite"
    make_legacy(src)
    assert run_importer(src).returncode == 0
    first = rows(conn, "SELECT id, subject, predicate, object, confidence, source, status FROM beliefs ORDER BY id")
    events_before = rows(conn, "SELECT COUNT(*) FROM events")[0][0]
    for _ in range(2):
        assert run_importer(src).returncode == 0
    assert rows(conn, "SELECT id, subject, predicate, object, confidence, source, status FROM beliefs ORDER BY id") == first
    assert rows(conn, "SELECT COUNT(*) FROM events")[0][0] == events_before


def test_importer_second_run_adds_only_new_rows(conn, tmp_path):
    src = tmp_path / "old.sqlite"
    make_legacy(src)
    assert run_importer(src).returncode == 0
    old_ids = {r[0] for r in legacy_beliefs(conn)}
    lc = sqlite3.connect(src)
    lc.execute("INSERT INTO memories(id, content, importance) VALUES ('m3', 'speaks Portuguese', 0.5)")
    lc.execute("INSERT INTO core_memory VALUES ('pet', 'a cat', NULL)")
    lc.commit()
    lc.close()
    assert run_importer(src).returncode == 0
    new = legacy_beliefs(conn)
    assert len(new) == 6
    assert old_ids <= {r[0] for r in new}


def test_importer_never_modifies_the_legacy_database(tmp_path):
    src = tmp_path / "old.sqlite"
    make_legacy(src)
    before = src.read_bytes()
    assert run_importer(src).returncode == 0
    assert src.read_bytes() == before


def test_importer_reads_a_file_named_like_the_legacy_db(conn, tmp_path):
    src = tmp_path / "tanishi.db"  # the importer is the one place allowed to read legacy files (decision 0003)
    make_legacy(src)
    assert run_importer(src).returncode == 0
    assert len(legacy_beliefs(conn)) == 4


def test_importer_handles_empty_tables(conn, tmp_path):
    src = tmp_path / "empty.sqlite"
    make_legacy(src, core=(), memories=())
    assert run_importer(src).returncode == 0
    assert legacy_beliefs(conn) == []


def test_importer_handles_a_db_with_only_one_of_the_tables(conn, tmp_path):
    src = tmp_path / "half.sqlite"
    c = sqlite3.connect(src)
    c.execute("CREATE TABLE core_memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    c.execute("INSERT INTO core_memory VALUES ('name', 'Ada', NULL)")
    c.commit()
    c.close()
    r = run_importer(src)
    assert r.returncode == 0, r.stderr
    assert len(legacy_beliefs(conn)) == 1


def test_importer_fails_cleanly_on_a_missing_path(conn, tmp_path):
    r = run_importer(tmp_path / "nope.sqlite")
    assert r.returncode != 0
    assert not (tmp_path / "nope.sqlite").exists(), "the importer must not create the legacy file it was told to read"
    assert rows(conn, "SELECT COUNT(*) FROM beliefs")[0][0] == 0


def test_importer_without_a_path_is_a_usage_error(conn):
    assert run_importer().returncode != 0


def test_importer_keeps_unicode_and_quotes(conn, tmp_path):
    src = tmp_path / "old.sqlite"
    tricky = "it's \"quoted\"; ünï ✓ 日本語"
    make_legacy(src, core=(), memories=(("m1", tricky),))
    assert run_importer(src).returncode == 0
    (row,) = legacy_beliefs(conn)
    assert tricky in " ".join(str(v) for v in row[1:4])


def test_importer_writes_nothing_outside_the_core_state(conn, tmp_path, _temporary_tanishi_home):
    src = tmp_path / "old.sqlite"
    make_legacy(src)
    assert run_importer(src).returncode == 0
    legacy_like = [p for p in _temporary_tanishi_home.rglob("*.db") if p.name != "core_state.db"]
    assert legacy_like == []
