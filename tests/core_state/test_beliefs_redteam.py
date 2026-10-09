"""Red-team attacks on CS3 (belief store and legacy importer).

Each test asserts the CORRECT behaviour. Tests for open breaks are marked xfail(strict=True): the suite stays
green today, and the moment someone fixes a break the test XPASSes, strict mode fails the run, and the mark must go.
Report: build/memory/runs/redteam-CS3-20261009.md
"""
from __future__ import annotations

import json
import random
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tanishi.core_state import beliefs as B
from tanishi.core_state import open_db

ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / "scripts" / "import_legacy_memory.py"
BREAK = pytest.mark.xfail(strict=True, reason="red-team break, see build/memory/runs/redteam-CS3-20261009.md")


def _q(sql, *args):
    c = open_db()
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def _run(*args):
    return subprocess.run([sys.executable, str(IMPORTER), *map(str, args)], capture_output=True, text=True,
                          cwd=ROOT, timeout=120, check=False)


def _legacy(path, core=(), memories=()):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS core_memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, "
              "category TEXT DEFAULT 'fact', importance REAL DEFAULT 0.5)")
    c.executemany("INSERT OR REPLACE INTO core_memory(key, value) VALUES (?, ?)", core)
    c.executemany("INSERT OR REPLACE INTO memories(id, content, category, importance) VALUES (?, ?, ?, ?)", memories)
    c.commit()
    c.close()


# ---------- state machine ----------

def test_status_invariant_holds_under_random_add_and_retire():
    """Held: after any sequence, a live belief is contested iff a live belief disagrees with it."""
    rng = random.Random(7)
    ids = []
    for _ in range(150):
        if ids and rng.random() < 0.3:
            B.retire(rng.choice(ids), "r")
        else:
            ids.append(B.add_belief(rng.choice("ab"), rng.choice("pq"), rng.choice("xyz"), 0.5, "t").id)
    for b in B.find():
        if b.status == "retired":
            continue
        has_peer = bool(B.contradictions(b))
        assert (b.status == "contested") == has_peer, b


def test_concurrent_conflicting_adds_all_end_up_contested():
    """Held: the check and insert share one IMMEDIATE transaction, so no writer misses another."""
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: B.add_belief("x", "p", f"v{i}", 0.5, "t"), range(16)))
    assert {b.status for b in B.find(subject="x")} == {"contested"}
    assert len(_q("SELECT 1 FROM events WHERE kind = 'belief_conflict'")) == 15


def test_failed_add_leaves_no_row_event_or_status_change():
    """Held: a bad evidence id rolls back the whole add, including the peer's contested flip."""
    a = B.add_belief("s", "p", "o1", 0.5, "t")
    with pytest.raises(sqlite3.IntegrityError):
        B.add_belief("s", "p", "o2", 0.5, "t", evidence_event_id=9999)
    assert [(b.id, b.status) for b in B.find()] == [(a.id, "active")]
    assert _q("SELECT COUNT(*) FROM events") == [(0,)]


def test_surrogate_text_is_refused_without_a_partial_write():
    """Held: lone surrogates cannot be encoded; the add raises and writes nothing."""
    with pytest.raises(UnicodeEncodeError):
        B.add_belief("\ud800", "p", "o", 0.5, "t")
    assert B.find() == []


# ---------- breaks ----------

def test_retire_reason_is_not_copied_verbatim_into_the_event_log():
    """R1 (medium): reason is free text from the caller and is stored whole in events.payload. CLAUDE.md: no secrets
    or personal data in events; decision 0008 point 2 protects belief text but not the reason. A secret pasted into
    a reason lands in an append-only log. Smallest fix: store only a reason code or a length/hash, keep text elsewhere."""
    b = B.add_belief("s", "p", "o", 0.5, "t")
    B.retire(b.id, "leaked key sk-ant-api03-SECRETSECRET")
    payloads = [r[0] for r in _q("SELECT payload FROM events WHERE kind = 'belief_retired'")]
    assert all("SECRETSECRET" not in p for p in payloads)


def test_evidence_event_id_must_be_an_int():
    """R2 (low): True is accepted as event id 1 and silently links evidence to the wrong event (bool is an int).
    Smallest fix: reject bool and non-int in _add, as _clamp already does for confidence."""
    B.find()  # migrates the db
    c = open_db()
    c.execute("INSERT INTO events(ts, kind, actor) VALUES ('t', 'first', 'x')")
    c.commit()
    c.close()
    with pytest.raises(TypeError):
        B.add_belief("s", "p", "o", 0.5, "t", evidence_event_id=True)


def test_huge_int_confidence_is_clamped_not_overflowed():
    """R3 (low): the spec says clamp. float(10**400) raises OverflowError, which is neither of the documented errors."""
    assert B.add_belief("s", "p", "o", 10**400, "t").confidence == 1.0


def test_case_and_whitespace_variants_still_contradict():
    """R4 (medium, already in known-bugs as 'matching is exact'): ("User","name","Al") vs ("user","name","Bob") are
    kept as two active, uncontested beliefs, so the contradiction check is evaded by spelling. This is the common
    case for LLM-written beliefs. Smallest fix: compare subject and predicate with a normalising key (strip, casefold)."""
    a = B.add_belief("User", "name", "Al", 0.8, "t")
    b = B.add_belief("user ", "Name", "Bob", 0.8, "t")
    assert b.status == "contested" and B.contradictions(a)


@BREAK
def test_identical_triple_is_not_stored_twice():
    """R5 (low): the same fact added twice makes two active rows with independent confidence and no link; find()
    returns duplicates and every later join on (subject, predicate) double counts. Smallest fix: on an exact live
    duplicate, return the existing belief (or raise confidence) and attach the new evidence to it."""
    a = B.add_belief("d", "p", "o", 0.5, "t")
    B.add_belief("d", "p", "o", 0.9, "t")
    assert len(B.find(subject="d")) == 1 and a.id


def test_contradictions_ignores_a_forged_belief_object():
    """R6 (low): contradictions() trusts the caller's subject/predicate/object, so a Belief with an unknown id
    reports 'conflicts' with real rows. Smallest fix: look the row up by id and use the stored fields; raise on unknown."""
    B.add_belief("s", "p", "o", 0.5, "t")
    forged = B.Belief("no-such-id", "s", "p", "other", 0.5, "t", "active", "", "")
    assert B.contradictions(forged) == []


def test_evidence_can_be_read_back():
    """R7 (design): no function lists a belief's evidence, so the 'every belief has evidence' goal is write-only and
    the next node (retrieval, M1) has to open the table by hand. Passes once an accessor exists."""
    assert hasattr(B, "evidence_for") or hasattr(B, "evidence")


# ---------- importer ----------

def test_importer_one_undecodable_row_does_not_block_every_other_row(tmp_path):
    """R8 (medium): a single legacy TEXT value that is not valid UTF-8 raises inside legacy_rows(); nothing at all is
    imported and the script exits 1. Legacy rows are free text, so this will happen. Smallest fix: read with
    conn.text_factory = lambda b: b.decode('utf-8', 'replace') or skip and count the bad row."""
    src = tmp_path / "old.sqlite"
    _legacy(src, core=[("good", "fine")])
    c = sqlite3.connect(src)
    c.execute("INSERT INTO core_memory(key, value) VALUES ('bad', CAST(X'fffe41' AS TEXT))")
    c.commit()
    c.close()
    r = _run(src)
    assert r.returncode == 0, r.stderr
    assert "good" in {b.predicate for b in B.find()}


def test_importer_reverted_legacy_value_does_not_leave_the_stale_value_live(tmp_path):
    """R9 (medium): legacy city Lisbon -> Porto -> Lisbon. The importer never retires, and Lisbon's id already exists,
    so the final state is Lisbon + Porto both live and contested while legacy says only Lisbon. Every legacy edit
    forever needs a manual retire(). Smallest fix: when a (user, key) the importer owns changes, retire the previous
    legacy belief for that key (source == 'legacy') instead of contesting it."""
    src = tmp_path / "old.sqlite"
    for city in ("Lisbon", "Porto", "Lisbon"):
        _legacy(src, core=[("city", city)])
        assert _run(src).returncode == 0
    live = {b.object for b in B.find(subject="user", predicate="city") if b.status != "retired"}
    assert live == {"Lisbon"}


def test_importer_concurrent_runs_do_not_duplicate(tmp_path):
    """Held: deterministic ids plus the check-and-insert transaction make parallel imports idempotent."""
    src = tmp_path / "old.sqlite"
    _legacy(src, core=[(f"k{i}", f"v{i}") for i in range(20)],
            memories=[(f"m{i}", f"text {i}", "fact", 0.5) for i in range(20)])
    procs = [subprocess.Popen([sys.executable, str(IMPORTER), str(src)], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(4)]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
    assert len(B.find()) == 40


def test_importer_clamps_odd_importance_and_skips_null_rows(tmp_path):
    """Held: inf, negative and NaN-ish importance never reach the CHECK constraint."""
    src = tmp_path / "old.sqlite"
    _legacy(src, memories=[("a", "x", "fact", float("inf")), ("b", "y", "fact", -3.0), ("c", "z", None, None)])
    assert _run(src).returncode == 0
    assert sorted(b.confidence for b in B.find()) == [0.0, 0.5, 1.0]


def test_importer_does_not_touch_the_legacy_file(tmp_path):
    """Held: the legacy db is opened read-only."""
    src = tmp_path / "old.sqlite"
    _legacy(src, core=[("a", "b")])
    before = src.read_bytes()
    assert _run(src).returncode == 0
    assert src.read_bytes() == before


def test_importer_garbage_file_fails_cleanly(tmp_path):
    """Held: a non-SQLite file exits 1 with a message, no traceback, no writes."""
    src = tmp_path / "junk.sqlite"
    src.write_bytes(b"not a database" * 100)
    r = _run(src)
    assert r.returncode != 0 and "Traceback" not in r.stderr
    assert B.find() == []


# ---------- leakage ----------

def test_events_written_by_beliefs_carry_no_belief_text():
    """Held: conflict payloads are ids only."""
    B.add_belief("secretsubj", "p", "secretobj1", 0.5, "t")
    B.add_belief("secretsubj", "p", "secretobj2", 0.5, "t")
    blob = json.dumps(_q("SELECT payload FROM events"))
    assert "secret" not in blob
