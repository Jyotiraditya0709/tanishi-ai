"""Exam for CS1 (Core State schema and migrations), written from the spec alone.

Covers every acceptance line:
  * migrate() on an empty file creates every table, records version 1, and is idempotent
  * a failing migration is rolled back completely
  * WAL on, foreign keys on
  * the legacy databases are never opened
plus open_db path resolution and a seeded round-trip property test over every table.
"""
import hashlib
import json
import random
import sqlite3

import pytest

from tanishi.core_state import migrate, open_db

SPEC_COLUMNS = {
    "schema_version": ["version", "applied_at"],
    "events": ["id", "ts", "kind", "actor", "session_id", "payload", "prev_hash", "hash"],
    "beliefs": ["id", "subject", "predicate", "object", "confidence", "source",
                "created_at", "updated_at", "status"],
    "evidence": ["id", "belief_id", "event_id", "kind", "note"],
    "goals": ["id", "parent_id", "owner", "title", "rank", "status", "created_at"],
    "predictions": ["id", "ts", "about", "expected", "confidence", "actual",
                    "resolved_at", "score"],
    "rules": ["id", "domain", "statement", "support", "confidence"],
    "capabilities": ["id", "name", "family", "state", "ability", "calibration_error",
                     "updated_at"],
    "experiments": ["id", "ts", "candidate", "baseline", "task_set", "seed", "score",
                    "cost", "meta"],
    "portfolio": ["id", "item", "sleeve", "cost_estimate", "expected_gain", "status"],
    "genome": ["version", "parent", "created_at", "record"],
    "genes": ["id", "description", "introduced_in"],
    "substrate_state": ["task_id", "working", "plan", "goal", "hypotheses", "updated_at"],
}
SPEC_PK = {
    "schema_version": ["version"],
    "events": ["id"],
    "genome": ["version"],
    "substrate_state": ["task_id"],
}  # every other table: ["id"]

# column kinds for generated rows (after the key column):
# t=text, r=real, c=confidence in [0, 1], i=int, j=json object, n=NULL (reference columns: targets are not in the spec)
KINDS = {
    "events": "ttttjtt",
    "beliefs": "tttctttt",
    "evidence": "nntt",
    "goals": "nttrtt",
    "predictions": "ttjcjtr",
    "rules": "ttic",
    "capabilities": "tttrrt",
    "experiments": "ttttirrj",
    "portfolio": "ttrrt",
    "genome": "ntj",
    "genes": "tt",
    "substrate_state": "jjjjt",
}
TABLES = sorted(SPEC_COLUMNS)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("TANISHI_CORE_STATE_DB", raising=False)
    return h


@pytest.fixture
def conn(tmp_path):
    c = open_db(str(tmp_path / "core.db"))
    yield c
    c.close()


def tables(c):
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def schema_dump(c):
    rows = c.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    return [tuple(r) for r in rows]


def versions(c):
    return [tuple(r) for r in c.execute(
        "SELECT version, applied_at FROM schema_version ORDER BY version")]


# ---------------------------------------------------------------- open_db


def test_open_db_returns_sqlite_connection(tmp_path):
    c = open_db(str(tmp_path / "a.db"))
    try:
        assert isinstance(c, sqlite3.Connection)
        assert (tmp_path / "a.db").exists()
    finally:
        c.close()


def test_open_db_uses_env_var_when_no_path(tmp_path, monkeypatch, home):
    target = tmp_path / "from_env.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(target))
    c = open_db()
    try:
        migrate(c)
    finally:
        c.close()
    assert target.exists()
    assert not (home / ".tanishi" / "core_state.db").exists()


def test_open_db_explicit_path_beats_env(tmp_path, monkeypatch, home):
    env_target = tmp_path / "env.db"
    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(env_target))
    c = open_db(str(explicit))
    c.close()
    assert explicit.exists()
    assert not env_target.exists()


def test_open_db_default_is_home_tanishi_core_state_db(home):
    c = open_db()
    try:
        migrate(c)
    finally:
        c.close()
    assert (home / ".tanishi" / "core_state.db").exists()


def test_open_db_twice_same_file_both_usable(tmp_path):
    p = str(tmp_path / "two.db")
    a, b = open_db(p), open_db(p)
    try:
        migrate(a)
        assert migrate(b) == 1
        a.execute("INSERT INTO genes(id, description, introduced_in) VALUES ('g','d','v')")
        a.commit()
        # WAL: a second connection sees committed data
        assert b.execute("SELECT count(*) FROM genes").fetchone()[0] == 1
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------- pragmas


def test_wal_and_foreign_keys_on_after_open(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_wal_and_foreign_keys_on_after_migrate(conn):
    migrate(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_wal_persists_for_a_fresh_plain_connection(tmp_path):
    p = tmp_path / "w.db"
    c = open_db(str(p))
    migrate(c)
    c.close()
    raw = sqlite3.connect(str(p))
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        raw.close()


def test_foreign_keys_on_for_reopened_connection(tmp_path):
    p = str(tmp_path / "r.db")
    c = open_db(p)
    migrate(c)
    c.close()
    c2 = open_db(p)
    try:
        assert c2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c2.close()


# ---------------------------------------------------------------- migrate: first run


def test_migrate_empty_file_returns_one(conn):
    assert migrate(conn) == 1


def test_migrate_creates_every_table(conn):
    migrate(conn)
    assert set(SPEC_COLUMNS) <= tables(conn)


def test_empty_file_has_no_tables_before_migrate(conn):
    assert not (set(SPEC_COLUMNS) & tables(conn))


@pytest.mark.parametrize("table", TABLES)
def test_table_has_spec_columns(conn, table):
    migrate(conn)
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    assert cols == SPEC_COLUMNS[table]


@pytest.mark.parametrize("table", TABLES)
def test_table_primary_key(conn, table):
    migrate(conn)
    pk = [r[1] for r in sorted(
        (r for r in conn.execute(f"PRAGMA table_info({table})") if r[5]),
        key=lambda r: r[5])]
    assert pk == SPEC_PK.get(table, ["id"])


def test_records_version_one_in_schema_version(conn):
    migrate(conn)
    rows = versions(conn)
    assert [r[0] for r in rows] == [1]
    assert rows[0][1]  # applied_at is filled in


def test_events_id_autoassigns_increasing(conn):
    migrate(conn)
    for i in range(3):
        conn.execute(
            "INSERT INTO events(ts, kind, actor, session_id, payload, prev_hash, hash) "
            "VALUES ('t','k','a','s','{}',?,?)", (f"p{i}", f"h{i}"))
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM events ORDER BY id")]
    assert ids == [1, 2, 3]


def test_primary_keys_are_enforced(conn):
    migrate(conn)
    conn.execute("INSERT INTO genes(id, description, introduced_in) VALUES ('g','d','v')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO genes(id, description, introduced_in) VALUES ('g','x','y')")


# ---------------------------------------------------------------- migrate: idempotence


def test_migrate_twice_returns_one_both_times(conn):
    assert migrate(conn) == 1
    assert migrate(conn) == 1


def test_migrate_twice_changes_nothing(conn):
    migrate(conn)
    before_schema, before_versions = schema_dump(conn), versions(conn)
    migrate(conn)
    assert schema_dump(conn) == before_schema
    assert versions(conn) == before_versions


def test_migrate_again_keeps_existing_data(conn):
    migrate(conn)
    conn.execute("INSERT INTO genes(id, description, introduced_in) VALUES ('g','d','v')")
    conn.execute("INSERT INTO rules(id, domain, statement, support, confidence) "
                 "VALUES ('r','dom','s',3,0.5)")
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT id, description FROM genes").fetchall() == [("g", "d")]
    assert conn.execute("SELECT support, confidence FROM rules").fetchall() == [(3, 0.5)]


def test_migrate_on_reopened_file_is_noop(tmp_path):
    p = str(tmp_path / "re.db")
    c = open_db(p)
    migrate(c)
    snap = (schema_dump(c), versions(c))
    c.close()
    c2 = open_db(p)
    try:
        assert migrate(c2) == 1
        assert (schema_dump(c2), versions(c2)) == snap
    finally:
        c2.close()


@pytest.mark.parametrize("seed", range(5))
def test_property_repeated_migrate_is_idempotent(tmp_path, seed):
    rng = random.Random(seed)
    p = str(tmp_path / f"p{seed}.db")
    snap = None
    for _ in range(rng.randint(2, 6)):
        c = open_db(p)
        try:
            assert migrate(c) == 1
            if rng.random() < 0.5:
                assert migrate(c) == 1
            cur = (schema_dump(c), versions(c))
            snap = snap or cur
            assert cur == snap
        finally:
            c.close()


# ---------------------------------------------------------------- atomicity


def _deny(action, table):
    """Authorizer that fails the statement performing `action` on `table`."""
    def auth(code, a1, a2, db, src):
        if code == action and a1 == table:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return auth


@pytest.mark.parametrize("action,target", [
    (sqlite3.SQLITE_CREATE_TABLE, "substrate_state"),
    (sqlite3.SQLITE_CREATE_TABLE, "genes"),
    (sqlite3.SQLITE_INSERT, "schema_version"),
])
def test_failed_migration_rolls_back_completely(conn, action, target):
    conn.set_authorizer(_deny(action, target))
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    conn.set_authorizer(None)
    assert not conn.in_transaction
    assert not (set(SPEC_COLUMNS) & tables(conn)), "partial schema left behind"


@pytest.mark.parametrize("action,target", [
    (sqlite3.SQLITE_CREATE_TABLE, "substrate_state"),
    (sqlite3.SQLITE_INSERT, "schema_version"),
])
def test_migrate_succeeds_after_a_rolled_back_attempt(conn, action, target):
    conn.set_authorizer(_deny(action, target))
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    conn.set_authorizer(None)
    assert migrate(conn) == 1
    assert set(SPEC_COLUMNS) <= tables(conn)
    assert [r[0] for r in versions(conn)] == [1]


def test_failed_migration_leaves_no_version_row_on_disk(tmp_path):
    p = str(tmp_path / "f.db")
    c = open_db(p)
    c.set_authorizer(_deny(sqlite3.SQLITE_CREATE_TABLE, "substrate_state"))
    with pytest.raises(sqlite3.Error):
        migrate(c)
    c.close()
    raw = sqlite3.connect(p)
    try:
        assert not (set(SPEC_COLUMNS) & tables(raw))
    finally:
        raw.close()


# ---------------------------------------------------------------- legacy databases


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_databases_are_never_opened(tmp_path, monkeypatch, home):
    legacy_home = home / ".tanishi"
    legacy_home.mkdir()
    legacy_home_db = legacy_home / "tanishi.db"
    legacy_home_db.write_bytes(b"LEGACY-HOME-SENTINEL")
    cwd = tmp_path / "cwd"
    (cwd / "data").mkdir(parents=True)
    legacy_repo_db = cwd / "data" / "db.sqlite"
    legacy_repo_db.write_bytes(b"LEGACY-REPO-SENTINEL")
    monkeypatch.chdir(cwd)

    sentinels = {legacy_home_db: _sha(legacy_home_db), legacy_repo_db: _sha(legacy_repo_db)}
    opened = []
    real_connect = sqlite3.connect

    def spy(database, *a, **k):
        opened.append(str(database))
        return real_connect(database, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", spy)

    for path in (None, str(tmp_path / "explicit.db")):
        c = open_db(path)
        try:
            migrate(c)
            migrate(c)
        finally:
            c.close()

    for name in opened:
        assert not name.endswith("tanishi.db"), name
        assert not name.endswith("db.sqlite"), name
    for p, digest in sentinels.items():
        assert _sha(p) == digest, f"{p} was modified"
    leftovers = [x.name for d in (legacy_home, cwd / "data") for x in d.iterdir()]
    assert not any(n.endswith(("-wal", "-shm")) for n in leftovers)


def test_default_path_does_not_collide_with_legacy_name(home):
    c = open_db()
    try:
        migrate(c)
    finally:
        c.close()
    assert not (home / ".tanishi" / "tanishi.db").exists()


# ---------------------------------------------------------------- round-trip property


def _value(rng, kind, n):
    if kind == "t":
        return f"{rng.choice('abcxyz')}-{n}-{rng.randint(0, 10**6)}"
    if kind == "n":
        return None
    if kind == "r":
        return rng.random() * rng.choice([1, 100, 1e6])
    if kind == "c":
        return rng.random()
    if kind == "i":
        return rng.randint(-1000, 10**9)
    return json.dumps({"k": rng.randint(0, 99), "l": [rng.random(), "s", None],
                       "n": {"x": True}}, sort_keys=True)


@pytest.mark.parametrize("table", [t for t in TABLES if t != "schema_version"])
@pytest.mark.parametrize("seed", range(3))
def test_property_rows_round_trip(conn, table, seed):
    migrate(conn)
    rng = random.Random(seed * 1000 + len(table))
    cols = SPEC_COLUMNS[table]
    cols[0]
    kinds = KINDS[table]
    data_cols = cols[1:]
    assert len(kinds) == len(data_cols), "test table drifted from spec"
    expected = {}
    for n in range(20):
        row = [_value(rng, k, n) for k in kinds]
        k = f"{table}-{n}" if table != "events" else None
        if table == "events":
            cur = conn.execute(
                f"INSERT INTO events({','.join(data_cols)}) VALUES ({','.join('?' * len(row))})",
                row)
            k = cur.lastrowid
        else:
            conn.execute(
                f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [k, *row])
        expected[k] = row
    conn.commit()
    got = {r[0]: list(r[1:]) for r in conn.execute(f"SELECT {','.join(cols)} FROM {table}")}
    assert got == expected
    for k, row in expected.items():
        for kind, val, back in zip(kinds, row, got[k]):
            if kind == "j":
                assert json.loads(back) == json.loads(val)


@pytest.mark.parametrize("table, cols, row", [
    ("beliefs", "id, subject, predicate, object, confidence, source, created_at, updated_at, status",
     ["b", "s", "p", "o", None, "src", "t", "t", "active"]),
    ("predictions", "id, ts, about, expected, confidence, actual, resolved_at, score",
     ["p", "t", "a", "{}", None, "{}", "t", 0.5]),
    ("rules", "id, domain, statement, support, confidence", ["r", "d", "st", 1, None]),
])
@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan")])
def test_confidence_outside_unit_interval_is_refused(conn, table, cols, row, bad):
    migrate(conn)
    row = [bad if v is None else v for v in row]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO {table}({cols}) VALUES ({','.join('?' * len(row))})", row)


def test_data_survives_close_and_reopen(tmp_path):
    p = str(tmp_path / "d.db")
    c = open_db(p)
    migrate(c)
    c.execute("INSERT INTO genome(version, parent, created_at, record) "
              "VALUES ('v1', NULL, 't', '{\"a\": 1}')")
    c.commit()
    c.close()
    c2 = open_db(p)
    try:
        assert migrate(c2) == 1
        row = c2.execute("SELECT version, parent, record FROM genome").fetchone()
        assert (row[0], row[1], json.loads(row[2])) == ("v1", None, {"a": 1})
    finally:
        c2.close()
