"""Implementer's tests for CS3: choices the spec leaves open, pinned so later nodes can rely on them.

The exam (test_beliefs.py) is the other agent's; these only cover what it marks as unsettled.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tanishi.core_state import beliefs as B
from tanishi.core_state import open_db

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


def test_retire_writes_a_belief_retired_event_with_the_reason():
    a = B.add_belief("s", "p", "o", 0.5, "t")
    B.retire(a.id, "superseded")
    (payload,) = _q("SELECT payload FROM events WHERE kind = 'belief_retired'")[0]
    assert json.loads(payload) == {"belief_id": a.id, "reason": "superseded"}


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


def test_importer_changed_core_value_is_a_contested_new_belief(tmp_path):
    src = tmp_path / "old.sqlite"
    _legacy(src, ("city", "Lisbon"))
    assert _run(src).returncode == 0
    _legacy(src, ("city", "Porto"))
    assert _run(src).returncode == 0
    got = {x.object: x.status for x in B.find(subject="user", predicate="city")}
    assert got == {"Lisbon": "contested", "Porto": "contested"}


def test_importer_refuses_the_core_state_db_as_its_source():
    B.add_belief("s", "p", "o", 0.5, "t")
    r = _run(_q("PRAGMA database_list")[0][2])
    assert r.returncode != 0
    assert _q("SELECT COUNT(*) FROM beliefs") == [(1,)]
