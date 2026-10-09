"""Implementer's tests for CS3: choices the spec leaves open, pinned so later nodes can rely on them.

The exam (test_beliefs.py) is the other agent's; these only cover what it marks as unsettled.
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tanishi.core_state import beliefs as B
from tanishi.core_state import open_db
from tanishi.core_state.events import emit, verify_chain

ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / "scripts" / "import_legacy_memory.py"


def _q(sql, *args):
    c = open_db()
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def test_add_belief_migrates_a_fresh_database_itself():
    b = B.add_belief("s", "p", "o", 0.5, "t")
    assert _q("SELECT id FROM beliefs") == [(b.id,)]


def test_retiring_the_last_contradiction_makes_the_survivor_active_again():
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    b = B.add_belief("sky", "color", "green", 0.6, "user")
    c = B.add_belief("sky", "color", "red", 0.6, "user")
    B.retire(a.id, "wrong")
    assert {x.id: x.status for x in B.find(subject="sky")}[b.id] == "contested"  # still disagrees with c
    B.retire(c.id, "wrong")
    assert {x.id: x.status for x in B.find(subject="sky")} == {a.id: "retired", b.id: "active", c.id: "retired"}


def test_retire_event_carries_id_and_code_and_the_text_goes_to_evidence():
    a = B.add_belief("s", "p", "o", 0.5, "t")
    B.retire(a.id, "user said so; api_key=abc123")
    (payload,) = _q("SELECT payload FROM events WHERE kind = 'belief_retired'")[0]
    assert json.loads(payload) == {"belief_id": a.id, "reason": "other"}
    (ev,) = B.evidence_for(a.id)
    assert (ev.belief_id, ev.kind) == (a.id, "retire_reason")
    assert "user said so" in ev.note and "abc123" not in ev.note
    assert _q("SELECT COUNT(*) FROM evidence WHERE belief_id = ?", a.id) == [(0,)]


@pytest.mark.parametrize("reason, code, expected", [("superseded", None, "superseded"),
                                                     ("fixed by the user", "user_correction", "user_correction")])
def test_retire_reason_code(reason, code, expected):
    a = B.add_belief("s", "p", "o", 0.5, "t")
    B.retire(a.id, reason, code)
    (payload,) = _q("SELECT payload FROM events WHERE kind = 'belief_retired'")[0]
    assert json.loads(payload)["reason"] == expected


def test_retire_unknown_code_raises_and_changes_nothing():
    a = B.add_belief("s", "p", "o", 0.5, "t")
    with pytest.raises(ValueError):
        B.retire(a.id, "x", "because")
    assert B.find(subject="s")[0].status == "active"


def test_belief_events_keep_the_hash_chain_intact():
    a = B.add_belief("sky", "color", "blue", 0.8, "user")
    B.add_belief("sky", "color", "green", 0.6, "user")
    B.retire(a.id, "sensor was broken")
    kinds = [k for (k,) in _q("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["belief_conflict", "belief_retired"]
    c = open_db()
    try:
        assert verify_chain(c) == (True, None)
    finally:
        c.close()


def test_no_raw_insert_into_events_in_cs3_code():
    for path in (Path(B.__file__), IMPORTER):
        assert not re.search(r"INSERT\s+INTO\s+events\b", path.read_text(encoding="utf-8"), re.IGNORECASE), path


def test_matching_is_normalised_but_original_text_is_stored():
    a = B.add_belief("  The  Sky ", "Color", "Blue", 0.8, "user")
    b = B.add_belief("the sky", "color ", " blue ", 0.8, "user")  # same fact, other spelling: no conflict
    c = B.add_belief("THE SKY", "COLOR", "Green", 0.8, "user")
    assert (a.subject, a.object) == ("  The  Sky ", "Blue")
    assert b.status == "active"
    assert c.status == "contested" and {x.id for x in B.contradictions(c)} == {a.id, b.id}


def test_contradictions_uses_the_stored_row_and_accepts_an_id():
    a = B.add_belief("s", "p", "o1", 0.5, "t")
    b = B.add_belief("s", "p", "o2", 0.5, "t")
    forged = B.Belief(a.id, "other", "fields", "entirely", 0.5, "t", "active", "", "")
    assert [x.id for x in B.contradictions(forged)] == [b.id]
    assert [x.id for x in B.contradictions(b.id)] == [a.id]
    assert B.contradictions("no-such-id") == []


def test_evidence_for_lists_support_rows_oldest_first():
    first = emit("observation", {})
    second = emit("observation", {})
    a = B.add_belief("s", "p", "o", 0.5, "t", evidence_event_id=first)
    assert [(e.event_id, e.kind) for e in B.evidence_for(a.id)] == [(first, "support")]
    B.add_belief("s2", "p", "o", 0.5, "t", evidence_event_id=second)
    assert B.evidence_for(a.id)[0].event_id == first and len(B.evidence_for(a.id)) == 1
    assert B.evidence_for("no-such-id") == []


@pytest.mark.parametrize("bad", [True, False, 1.0, "1"])
def test_evidence_event_id_must_be_a_plain_int(bad):
    with pytest.raises(TypeError):
        B.add_belief("s", "p", "o", 0.5, "t", evidence_event_id=bad)
    assert _q("SELECT COUNT(*) FROM beliefs") == [(0,)]


@pytest.mark.parametrize("given, expected", [(10**400, 1.0), (-(10**400), 0.0)])
def test_huge_int_confidence_clamps(given, expected):
    assert B.add_belief("s", "p", "o", given, "t").confidence == expected


def test_reactivate_brings_back_a_retired_belief_and_checks_conflicts():
    a = B.add_belief("s", "p", "o1", 0.5, "t")
    B.retire(a.id, "superseded")
    b = B.add_belief("s", "p", "o2", 0.5, "t")
    assert B.reactivate(a.id) is True
    assert {x.id: x.status for x in B.find(subject="s")} == {a.id: "contested", b.id: "contested"}
    assert B.reactivate(a.id) is False
    kinds = [k for (k,) in _q("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["belief_retired", "belief_reactivated", "belief_conflict"]
    with pytest.raises(LookupError):
        B.reactivate("no-such-id")


def test_a_broken_event_log_does_not_undo_the_belief_change():
    B.find()  # migrate
    c = open_db()
    c.execute("INSERT INTO events(ts, kind, actor) VALUES ('t', 'unhashed', 'x')")  # a row emit() cannot chain onto
    c.commit()
    c.close()
    a = B.add_belief("s", "p", "o1", 0.5, "t")
    B.add_belief("s", "p", "o2", 0.5, "t")
    B.retire(a.id, "r")
    assert B.find(subject="s")[0].status == "retired"
    assert _q("SELECT COUNT(*) FROM events") == [(1,)]


def test_retire_twice_is_a_no_op():
    a = B.add_belief("s", "p", "o", 0.5, "t")
    B.retire(a.id, "r1")
    B.retire(a.id, "r2")
    assert len(_q("SELECT 1 FROM events WHERE kind = 'belief_retired'")) == 1


def test_retire_unknown_id_or_blank_reason_raises():
    with pytest.raises(LookupError):
        B.retire("no-such-id", "r")
    a = B.add_belief("s", "p", "o", 0.5, "t")
    with pytest.raises(ValueError):
        B.retire(a.id, "  ")
    assert B.find(subject="s")[0].status == "active"


def test_conflict_event_carries_ids_only():
    a = B.add_belief("secret-subject", "p", "secret-one", 0.5, "t")
    b = B.add_belief("secret-subject", "p", "secret-two", 0.5, "t")
    (payload,) = _q("SELECT payload FROM events WHERE kind = 'belief_conflict'")[0]
    assert json.loads(payload) == {"belief_id": b.id, "conflicts_with": [a.id]}


def test_unknown_evidence_event_stores_nothing():
    with pytest.raises(sqlite3.IntegrityError):
        B.add_belief("s", "p", "o", 0.5, "t", evidence_event_id=999999)
    assert _q("SELECT COUNT(*) FROM beliefs") == [(0,)]


@pytest.mark.parametrize("bad", ["0.5", None, True])
def test_non_numeric_confidence_is_refused(bad):
    with pytest.raises(TypeError):
        B.add_belief("s", "p", "o", bad, "t")


def test_add_belief_if_absent_writes_once():
    assert B.add_belief_if_absent("fixed-id", "s", "p", "o", 0.5, "legacy") is not None
    assert B.add_belief_if_absent("fixed-id", "s", "p", "other", 0.5, "legacy") is None
    assert _q("SELECT object FROM beliefs") == [("o",)]


def _legacy(path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS core_memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    c.execute("INSERT OR REPLACE INTO core_memory VALUES (?, ?, NULL)", rows)
    c.commit()
    c.close()


def _run(*args):
    return subprocess.run([sys.executable, str(IMPORTER), *map(str, args)], capture_output=True, text=True,
                          cwd=ROOT, timeout=120, check=False)


def test_importer_changed_core_value_supersedes_the_old_one(tmp_path):
    src = tmp_path / "old.sqlite"
    _legacy(src, ("city", "Lisbon"))
    assert _run(src).returncode == 0
    _legacy(src, ("city", "Porto"))
    r = _run(src)
    assert r.returncode == 0 and "retired 1" in r.stdout
    got = {x.object: x.status for x in B.find(subject="user", predicate="city")}
    assert got == {"Lisbon": "retired", "Porto": "active"}
    _legacy(src, ("city", "Lisbon"))
    r = _run(src)
    assert "reactivated 1" in r.stdout
    got = {x.object: x.status for x in B.find(subject="user", predicate="city")}
    assert got == {"Lisbon": "active", "Porto": "retired"}
    codes = {json.loads(p)["reason"] for (p,) in _q("SELECT payload FROM events WHERE kind = 'belief_retired'")}
    assert codes == {"superseded"}


def test_importer_changed_memory_content_supersedes_the_old_one(tmp_path):
    src = tmp_path / "old.sqlite"
    for content in ("likes tea", "likes green tea"):
        c = sqlite3.connect(src)
        c.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, content TEXT NOT NULL)")
        c.execute("INSERT OR REPLACE INTO memories VALUES ('m1', ?)", (content,))
        c.commit()
        c.close()
        assert _run(src).returncode == 0
    got = {x.object: x.status for x in B.find(subject="memory:m1")}
    assert got == {"likes tea": "retired", "likes green tea": "active"}


def test_importer_does_not_retire_beliefs_from_other_sources(tmp_path):
    mine = B.add_belief("user", "city", "Faro", 0.5, "user")
    src = tmp_path / "old.sqlite"
    _legacy(src, ("city", "Lisbon"))
    assert _run(src).returncode == 0
    assert {x.object: x.status for x in B.find(subject="user")} == {"Faro": "contested", "Lisbon": "contested"}
    assert mine.id in {x.id for x in B.find(subject="user")}


def test_importer_counts_unreadable_rows_and_never_prints_them(tmp_path):
    src = tmp_path / "old.sqlite"
    _legacy(src, ("good", "fine"))
    c = sqlite3.connect(src)
    c.execute("INSERT INTO core_memory(key, value) VALUES ('bad', CAST(X'fffe5345435245544b4559' AS TEXT))")
    c.commit()
    c.close()
    r = _run(src)
    assert r.returncode == 0, r.stderr
    assert "SECRETKEY" not in r.stdout + r.stderr
    assert "skipped 0" in r.stdout  # decoded with replacement characters, so nothing had to be skipped
    assert {x.predicate for x in B.find()} == {"good", "bad"}


def test_importer_uses_one_core_state_connection(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("import_legacy_memory", IMPORTER)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)  # dataclasses look their module up while it loads
    spec.loader.exec_module(mod)
    opened = []
    real = B.connect

    def counting():
        opened.append(1)
        return real()

    monkeypatch.setattr(B, "connect", counting)
    src = tmp_path / "old.sqlite"
    for i in range(5):
        _legacy(src, (f"k{i}", f"v{i}"))
    assert mod.import_file(src).added == 5
    assert opened == [1]


def test_importer_refuses_the_core_state_db_as_its_source():
    B.add_belief("s", "p", "o", 0.5, "t")
    r = _run(_q("PRAGMA database_list")[0][2])
    assert r.returncode != 0
    assert _q("SELECT COUNT(*) FROM beliefs") == [(1,)]
