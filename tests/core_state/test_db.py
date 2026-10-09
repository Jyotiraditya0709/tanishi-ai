"""Implementer's tests for tanishi.core_state.db: the guards and edges the exam does not cover."""
import sqlite3
import threading

import pytest

from tanishi.core_state import db, migrate, open_db


@pytest.fixture
def conn(tmp_path):
    c = open_db(str(tmp_path / "core.db"))
    yield c
    c.close()


@pytest.mark.parametrize("name", ["tanishi.db", "db.sqlite", "finance.db", "TANISHI.DB"])
def test_refuses_legacy_file_names(tmp_path, name):
    with pytest.raises(ValueError):
        open_db(str(tmp_path / name))
    assert not (tmp_path / name).exists()


def test_refuses_legacy_name_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv(db.ENV_VAR, str(tmp_path / "data" / "db.sqlite"))
    with pytest.raises(ValueError):
        open_db()
    assert not (tmp_path / "data").exists()


def test_creates_missing_parent_dirs(tmp_path):
    c = open_db(str(tmp_path / "a" / "b" / "core.db"))
    c.close()
    assert (tmp_path / "a" / "b" / "core.db").exists()


def test_memory_database_migrates():
    c = open_db(":memory:")
    try:
        assert migrate(c) == 1
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c.close()


def test_migrate_refuses_open_transaction(conn):
    migrate(conn)
    conn.execute("INSERT INTO genes(id, description, introduced_in) VALUES ('g','d','v')")
    assert conn.in_transaction
    with pytest.raises(RuntimeError):
        migrate(conn)
    conn.rollback()


def test_newer_database_than_code_is_refused(conn):
    migrate(conn)
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (99, 'future')")
    conn.commit()
    with pytest.raises(RuntimeError):
        migrate(conn)


@pytest.mark.parametrize("round_", range(20))
def test_concurrent_migrate_applies_version_once(tmp_path, round_):
    path = str(tmp_path / "race.db")
    results, errors = [], []
    start = threading.Barrier(4, timeout=10)

    def worker():
        try:
            start.wait()  # open and migrate a fresh file at the same moment
            c = open_db(path)
            try:
                results.append(migrate(c))
            finally:
                c.close()
        except (sqlite3.Error, threading.BrokenBarrierError) as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a worker hung"
    assert errors == []
    assert results == [1, 1, 1, 1]
    c = open_db(path)
    try:
        assert c.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
    finally:
        c.close()


def test_statement_splitter_respects_strings_and_comments():
    sql = (
        "-- header; with a semicolon\n"
        "CREATE TABLE a (x TEXT DEFAULT 'one;two');\n"
        "\n"
        "INSERT INTO a VALUES ('three;four');\n"
        "-- trailing comment\n"
    )
    assert db._statements(sql) == [
        "-- header; with a semicolon\nCREATE TABLE a (x TEXT DEFAULT 'one;two');",
        "INSERT INTO a VALUES ('three;four');",
    ]


def test_statement_splitter_rejects_incomplete_tail():
    with pytest.raises(ValueError):
        db._statements("CREATE TABLE a (x TEXT);\nCREATE TABLE b (y TEXT")


def test_migration_numbering_gap_is_refused(tmp_path, monkeypatch):
    (tmp_path / "0001_init.sql").write_text("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);")
    (tmp_path / "0003_skip.sql").write_text("CREATE TABLE x (id TEXT);")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    c = open_db(str(tmp_path / "gap.db"))
    try:
        with pytest.raises(RuntimeError):
            migrate(c)
    finally:
        c.close()


def test_second_migration_applies_and_failed_one_rolls_back_alone(tmp_path, monkeypatch):
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "0001_init.sql").write_text(db.MIGRATIONS_DIR.joinpath("0001_init.sql").read_text())
    (mig / "0002_bad.sql").write_text("CREATE TABLE extra (id TEXT);\nCREATE TABLE genes (id TEXT);")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    c = open_db(str(tmp_path / "two.db"))
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(c)
        # 0001 committed on its own; 0002 left nothing behind.
        assert c.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name = 'extra'").fetchone() is None
        (mig / "0002_bad.sql").write_text("CREATE TABLE extra (id TEXT);")
        assert migrate(c) == 2
    finally:
        c.close()


def test_json_columns_keep_json_text(tmp_path):
    """Decision 0006: JSON lives in TEXT columns, so a JSON scalar like '1.0' stays text."""
    import json

    from tanishi.core_state import migrate, open_db

    conn = open_db(str(tmp_path / "cs.db"))
    migrate(conn)
    conn.execute(
        "INSERT INTO genome (version, parent, created_at, record) VALUES (?, ?, ?, ?)",
        ("v1", None, "2026-10-09T00:00:00", "1.0"),
    )
    value, kind = conn.execute("SELECT record, typeof(record) FROM genome WHERE version = 'v1'").fetchone()
    assert kind == "text"
    assert value == "1.0"
    assert json.loads(value) == 1.0
