"""Open the Core State database and apply its numbered migrations.

The Core State lives in its own file (decision 0003), never in the legacy databases.
Migrations are `migrations/NNNN_name.sql`; each one runs in a single transaction
together with its `schema_version` row, so a failure leaves nothing behind.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

ENV_VAR = "TANISHI_CORE_STATE_DB"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BUSY_TIMEOUT_S = 5.0

# Legacy files that only legacy code may open (decision 0003).
_LEGACY_NAMES = frozenset({"tanishi.db", "db.sqlite", "finance.db"})
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def default_path() -> Path:
    return Path.home() / ".tanishi" / "core_state.db"


def resolve_path(path: str | None = None) -> str:
    """Explicit path, else $TANISHI_CORE_STATE_DB, else ~/.tanishi/core_state.db."""
    if path is None:
        path = os.environ.get(ENV_VAR) or str(default_path())
    if path == ":memory:":
        return path
    resolved = Path(path).expanduser()
    if resolved.name.lower() in _LEGACY_NAMES:
        raise ValueError(f"refusing to open legacy database {resolved}; the Core State needs its own file")
    return str(resolved)


def open_db(path: str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the Core State database with WAL and foreign keys on."""
    target = resolve_path(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if target != ":memory:":
            mode = _enable_wal(conn)
            if mode.lower() != "wal":
                raise sqlite3.OperationalError(f"could not enable WAL on {target} (got {mode})")
    except BaseException:
        conn.close()
        raise
    return conn


def _enable_wal(conn: sqlite3.Connection) -> str:
    """Switch to WAL, retrying while busy.

    When several processes open a fresh file at once, the journal-mode switch can
    return SQLITE_BUSY at once without consulting the busy timeout, so we retry here.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_S
    delay = 0.005
    while True:
        try:
            return conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def _migrations() -> list[tuple[int, Path]]:
    found = []
    for f in MIGRATIONS_DIR.iterdir():
        m = _MIGRATION_NAME.match(f.name)
        if m:
            found.append((int(m.group(1)), f))
    found.sort()
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"migrations must be numbered 1..N with no gaps or duplicates, got {numbers}")
    return found


def _statements(sql: str) -> list[str]:
    """Split a script into statements. executescript() would COMMIT first, breaking atomicity."""
    out, buf = [], ""
    pieces = sql.split(";")
    for i, piece in enumerate(pieces):
        buf += piece if i == len(pieces) - 1 else piece + ";"
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt.strip(";").strip() and not _only_comments(stmt):
                out.append(stmt)
            buf = ""
    if buf.strip(";").strip() and not _only_comments(buf):
        raise ValueError("migration ends with an incomplete statement")
    return out


def _only_comments(stmt: str) -> bool:
    lines = (ln.strip() for ln in stmt.rstrip(";").splitlines())
    return all(not ln or ln.startswith("--") for ln in lines)


def _current_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not exists:
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations, one transaction each. Returns the schema version."""
    if conn.in_transaction:
        raise RuntimeError("migrate() needs a connection with no open transaction; commit or roll back first")
    migrations = _migrations()
    latest = migrations[-1][0] if migrations else 0
    for number, file in migrations:
        statements = _statements(file.read_text(encoding="utf-8"))
        # IMMEDIATE takes the write lock up front, so two processes migrating at once
        # serialise here and the second one sees the first one's version row.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _current_version(conn) >= number:
                conn.rollback()
                continue
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (number, datetime.now(UTC).isoformat()),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    version = _current_version(conn)
    if version > latest:
        raise RuntimeError(f"database is at schema version {version}, newer than this code knows ({latest})")
    return version
