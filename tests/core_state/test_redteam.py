"""Red-team attacks on CS1 (Core State schema and migrations).

Each test asserts the CORRECT behaviour. Tests for open breaks are marked
xfail(strict=True): the suite stays green today, and the moment someone fixes the
break the test XPASSes, strict mode fails the run, and the mark must be removed.
Report: build/memory/runs/redteam-CS1-20261009.md
"""
from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from tanishi.core_state import db as core_db
from tanishi.core_state import migrate, open_db

SV = "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT);"
REFUSALS = (RuntimeError, ValueError, OSError, sqlite3.Error)
BREAK = pytest.mark.xfail(strict=True, reason="red-team break, see build/memory/runs/redteam-CS1-20261009.md")


@pytest.fixture
def conn(tmp_path):
    c = open_db(str(tmp_path / "core_state.db"))
    migrate(c)
    yield c
    c.close()


def _use_migrations(monkeypatch, tmp_path: Path, files: dict[str, str]) -> None:
    d = tmp_path / "mig"
    d.mkdir()
    for name, sql in files.items():
        (d / name).write_text(sql)
    monkeypatch.setattr(core_db, "MIGRATIONS_DIR", d)


def _rejects(fn) -> bool:
    try:
        fn()
    except sqlite3.Error:
        return True
    return False


# ---------- migration runner ----------

def test_trailing_block_comment_in_migration_is_legal_sql(monkeypatch, tmp_path):
    """R1: _only_comments knows `--` but not `/* */`, so a footer comment makes migrate() raise."""
    _use_migrations(monkeypatch, tmp_path, {"0001_a.sql": "CREATE TABLE a(x);\n/* end of 0001 */\n"})
    c = sqlite3.connect(":memory:")
    assert migrate(c) == 1


@pytest.mark.parametrize(
    "body",
    [
        "CREATE TABLE a(x); -- note; with semicolon\nCREATE TABLE b(y);",
        "/* header; with semicolon */\nCREATE TABLE a(x);\nCREATE TABLE b(y);",
        "CREATE TABLE a(x DEFAULT 'a;b');\nCREATE TABLE b(y);",
        (
            "CREATE TABLE a(x);\nCREATE TRIGGER t AFTER INSERT ON a BEGIN INSERT INTO a VALUES (1); SELECT 1; END;\n"
            "CREATE TABLE b(y);"
        ),
    ],
)
def test_statement_splitter_survives_semicolons_in_comments_strings_triggers(monkeypatch, tmp_path, body):
    """Held: the splitter is not fooled by `;` inside comments, strings or trigger bodies."""
    _use_migrations(monkeypatch, tmp_path, {"0001_a.sql": SV + "\n" + body})
    c = sqlite3.connect(":memory:")
    assert migrate(c) == 1
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name='b'").fetchone()


def test_misnamed_migration_file_is_not_silently_ignored(monkeypatch, tmp_path):
    """R2: `0002_Add-B.sql` fails the name regex and is skipped; the schema change never applies, no error."""
    _use_migrations(
        monkeypatch,
        tmp_path,
        {"0001_a.sql": SV, "0002_Add-B.sql": "CREATE TABLE b(x);"},
    )
    c = sqlite3.connect(":memory:")
    try:
        version = migrate(c)
    except REFUSALS:
        return  # raising is the acceptable outcome
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" in tables and version == 2, "0002 silently skipped"


def test_migration_with_explicit_commit_cannot_break_atomicity(monkeypatch, tmp_path):
    """R3: a COMMIT; inside a migration file ends the transaction early; the later failure leaves table a behind."""
    _use_migrations(
        monkeypatch,
        tmp_path,
        {"0001_a.sql": "CREATE TABLE a(x);\nCOMMIT;\nCREATE TABLE a(y);"},
    )
    c = sqlite3.connect(":memory:")
    with pytest.raises(REFUSALS):
        migrate(c)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" not in tables and "schema_version" not in tables


def test_edited_applied_migration_is_detected(monkeypatch, tmp_path):
    """R4: no checksum of applied migrations, so editing 0001 after it ran drifts the schema silently."""
    _use_migrations(monkeypatch, tmp_path, {"0001_a.sql": "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT);"})
    c = sqlite3.connect(":memory:")
    migrate(c)
    (tmp_path / "mig" / "0001_a.sql").write_text(
        "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT);\nCREATE TABLE sneaky(x);"
    )
    with pytest.raises(REFUSALS):
        migrate(c)


def test_schema_version_row_without_tables_is_not_trusted(conn, tmp_path):
    """R5: a hand-made schema_version row for 1 makes migrate() report 1 with no tables at all."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)")
    c.execute("INSERT INTO schema_version VALUES (1, 'x')")
    c.commit()
    try:
        migrate(c)
    except REFUSALS:
        return  # refusing is acceptable
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone(), "claimed v1 with no tables"


# ---------- open_db ----------

def test_db_and_dir_are_private(tmp_path):
    """R6: the file holding conversations/beliefs is created 0644 in a 0755 dir; other local users can read it."""
    p = tmp_path / "newdir" / "core_state.db"
    c = open_db(str(p))
    migrate(c)
    c.close()
    assert stat.S_IMODE(os.stat(p).st_mode) & 0o077 == 0
    assert stat.S_IMODE(os.stat(p.parent).st_mode) & 0o077 == 0


def test_symlink_to_legacy_db_is_refused(tmp_path):
    """R7: the legacy-db guard checks the file NAME only; a symlink with another name opens tanishi.db."""
    legacy = tmp_path / "tanishi.db"
    sqlite3.connect(legacy).close()
    link = tmp_path / "alias.db"
    link.symlink_to(legacy)
    with pytest.raises(ValueError):
        open_db(str(link))
    # and migrate() must not have touched the legacy file
    assert sqlite3.connect(legacy).execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


@BREAK
def test_default_path_is_not_the_real_home_when_tests_forget_the_env(monkeypatch, tmp_path):
    """R8: open_db() with no arg and no env writes ~/.tanishi/core_state.db. CLAUDE.md forbids tests doing that,
    and nothing stops a forgetful test. Expect a guard (e.g. refuse the default path under pytest)."""
    monkeypatch.delenv(core_db.ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(REFUSALS):
        open_db()


# ---------- schema: integrity ----------

def test_events_are_append_only(conn):
    """R9: the hash chain is only as good as the table. UPDATE/DELETE on events succeed, so history can be rewritten."""
    conn.execute("INSERT INTO events(ts,kind,prev_hash,hash) VALUES ('t','a','GENESIS','h1')")
    conn.commit()
    assert _rejects(lambda: conn.execute("UPDATE events SET kind='forged' WHERE id=1"))
    assert _rejects(lambda: conn.execute("DELETE FROM events WHERE id=1"))


def test_event_chain_cannot_fork(conn):
    """R10: two events with the same prev_hash (two writers that both read the tail) are accepted."""
    conn.execute("INSERT INTO events(ts,kind,prev_hash,hash) VALUES ('t','a','GENESIS','h1')")
    assert _rejects(lambda: conn.execute("INSERT INTO events(ts,kind,prev_hash,hash) VALUES ('t','b','GENESIS','h2')"))


def test_event_ids_are_never_reused(conn):
    """R11: INTEGER PRIMARY KEY without AUTOINCREMENT reuses the top id after a delete, so evidence.event_id can
    silently point at a different event."""
    conn.execute("INSERT INTO events(ts,kind) VALUES ('t','a')")
    conn.execute("INSERT INTO events(ts,kind) VALUES ('t','b')")
    conn.execute("DELETE FROM events WHERE id=2")
    conn.execute("INSERT INTO events(ts,kind) VALUES ('t','c')")
    assert conn.execute("SELECT max(id) FROM events").fetchone()[0] == 3


def test_evidence_cannot_dangle(conn):
    """R12: foreign_keys=ON is vacuous, no FK is declared. Evidence for a belief/event that does not exist is accepted,
    and deleting a belief leaves its evidence behind (a wrong belief's support survives)."""
    assert _rejects(lambda: conn.execute("INSERT INTO evidence(id,belief_id,event_id) VALUES ('e','nope',999)"))


def test_text_primary_keys_reject_null(conn):
    """R13: SQLite allows NULL in a non-INTEGER PRIMARY KEY. Unlimited un-addressable rows with id NULL."""
    assert _rejects(lambda: conn.execute("INSERT INTO beliefs(id,subject) VALUES (NULL,'a')"))


def test_confidence_must_be_a_number_between_0_and_1(conn):
    """R14: REAL affinity is advisory. 'high', 5 and -3 all land in beliefs.confidence."""
    for i, bad in enumerate(("high", 5, -3, float("nan"))):
        assert _rejects(
            lambda i=i, bad=bad: conn.execute("INSERT INTO beliefs(id,confidence) VALUES (?, ?)", (f"b{i}", bad))
        ), f"accepted confidence {bad!r}"


def test_json_columns_must_be_valid_json(conn):
    """R15: decision 0006 made JSON columns TEXT but dropped the CHECK(json_valid()) that open-problems/CS1.md suggested.
    Garbage payloads are stored, and json.loads blows up later at read time."""
    assert _rejects(lambda: conn.execute("INSERT INTO events(ts,kind,payload) VALUES ('t','a','not json{')"))


# ---------- performance ----------

def test_hot_lookups_use_an_index(conn):
    """R16: no secondary index anywhere. Every lookup the DAL will make is a full scan."""
    queries = [
        "SELECT * FROM evidence WHERE belief_id='x'",
        "SELECT * FROM beliefs WHERE subject='a' AND predicate='b'",
        "SELECT * FROM events WHERE session_id='s'",
        "SELECT * FROM goals WHERE parent_id='x'",
    ]
    for q in queries:
        plan = " ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + q))
        assert "USING" in plan and "SCAN" not in plan.replace("USING", "USING ").split("USING")[0], f"{q}: {plan}"
