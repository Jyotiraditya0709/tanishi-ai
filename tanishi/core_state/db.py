"""Open the Core State database and apply its numbered migrations.

The Core State lives in its own file (decision 0003), never in the legacy databases.
Migrations are `migrations/NNNN_name.sql`; each one runs in a single transaction
together with its `schema_version` row and its checksum, so a failure leaves nothing behind.
An applied migration must never change: migrate() compares every file with the sha256
recorded when it ran (decision 0007).
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

ENV_VAR = "TANISHI_CORE_STATE_DB"
HOME_ENV_VAR = "TANISHI_HOME"
LEGACY_DB_ENV_VAR = "TANISHI_DB_PATH"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BUSY_TIMEOUT_S = 5.0

# Legacy files that only legacy code may open (decision 0003).
_LEGACY_NAMES = frozenset({"tanishi.db", "db.sqlite", "finance.db"})
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# A migration that opens or ends a transaction would break "one transaction per migration".
_TX_CONTROL = frozenset({"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"})
# migrate() makes sure its bookkeeping tables exist, whether or not a migration file declared them.
# Checksums live beside schema_version, not in it: the exam fixes schema_version's columns.
_VERSION_TABLE = "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
_CHECKSUM_TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_checksum ("
    "version INTEGER PRIMARY KEY REFERENCES schema_version(version), "
    "checksum TEXT NOT NULL)"
)


class _Migration(NamedTuple):
    number: int
    path: Path
    sql: str
    checksum: str


def tanishi_home() -> Path:
    """$TANISHI_HOME, else ~/.tanishi."""
    return Path(os.environ.get(HOME_ENV_VAR) or Path.home() / ".tanishi").expanduser()


def default_path() -> Path:
    return tanishi_home() / "core_state.db"


def _legacy_files() -> list[Path]:
    files = [tanishi_home() / "tanishi.db", Path.home() / ".tanishi" / "tanishi.db", Path("data") / "db.sqlite"]
    if os.environ.get(LEGACY_DB_ENV_VAR):
        files.append(Path(os.environ[LEGACY_DB_ENV_VAR]))
    return [f.expanduser().resolve() for f in files]


def _is_legacy(given: Path, real: Path) -> bool:
    if given.name.lower() in _LEGACY_NAMES or real.name.lower() in _LEGACY_NAMES:
        return True
    for legacy in _legacy_files():
        if real == legacy:
            return True
        if real.exists() and legacy.exists() and os.path.samefile(real, legacy):  # hard links
            return True
    return False


def resolve_path(path: str | None = None) -> str:
    """Explicit path, else $TANISHI_CORE_STATE_DB, else $TANISHI_HOME/core_state.db.

    Returns the real path (symlinks resolved), and refuses anything that is, or points at, a legacy db.
    """
    if path is None:
        path = os.environ.get(ENV_VAR) or str(default_path())
    if path == ":memory:":
        return path
    if not path:
        raise ValueError("empty Core State path")
    given = Path(path).expanduser()
    real = given.resolve()
    if _is_legacy(given, real):
        raise ValueError(f"refusing to open legacy database {given} ({real}); the Core State needs its own file")
    return str(real)


def open_db(path: str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the Core State database with WAL and foreign keys on.

    The directory is created 0700 and the db file 0600; SQLite gives -wal and -shm the db's mode.
    """
    owns_dir = path is None and not os.environ.get(ENV_VAR)
    target = resolve_path(path)
    if target != ":memory:":
        _create_private(Path(target), tighten_dir=owns_dir)
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if target != ":memory:":
            mode = _enable_wal(conn)
            if mode.lower() != "wal":
                raise sqlite3.OperationalError(f"could not enable WAL on {target} (got {mode})")
            for suffix in ("", "-wal", "-shm"):
                _make_private(Path(target + suffix))
    except BaseException:
        conn.close()
        raise
    return conn


def _create_private(target: Path, tighten_dir: bool) -> None:
    missing = []
    d = target.parent
    while not d.exists():
        missing.append(d)
        d = d.parent
    for d in reversed(missing):
        d.mkdir(mode=0o700, exist_ok=True)
    if tighten_dir:  # $TANISHI_HOME is ours; a directory the caller named is not, so leave it alone
        _make_private(target.parent, dir_mode=True)
    try:
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        pass
    _make_private(target)


def _make_private(p: Path, dir_mode: bool = False) -> None:
    """Drop group and other permission bits, if the file exists."""
    try:
        mode = stat.S_IMODE(os.stat(p).st_mode)
    except FileNotFoundError:
        return
    if mode & 0o077:
        os.chmod(p, (mode & 0o700) | (0o700 if dir_mode else 0o600))


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


def _migrations() -> list[_Migration]:
    found = []
    for f in MIGRATIONS_DIR.iterdir():
        if f.suffix.lower() != ".sql":
            continue
        m = _MIGRATION_NAME.match(f.name)
        if not m:
            raise RuntimeError(f"migration file {f.name} must be named NNNN_lowercase_name.sql")
        raw = f.read_bytes().replace(b"\r\n", b"\n")  # a CRLF checkout is the same migration
        found.append(_Migration(int(m.group(1)), f, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()))
    found.sort()
    numbers = [m.number for m in found]
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
            code = _without_comments(buf).strip(" \t\r\n;")
            if code:
                keyword = code.split(None, 1)[0].upper()
                if keyword in _TX_CONTROL:
                    raise ValueError(f"migration must not control the transaction: {keyword} statement found")
                out.append(buf.strip())
            buf = ""
    if _without_comments(buf).strip(" \t\r\n;"):
        raise ValueError("migration ends with an incomplete statement")
    return out


def _without_comments(sql: str) -> str:
    """`sql` with `--` and `/* */` comments replaced by a space; quoted strings and names kept."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`[":
            j = sql.find("]" if ch == "[" else ch, i + 1)
            j = n if j < 0 else j + 1
            out.append(sql[i:j])  # a doubled quote reads as two adjacent literals, which is fine here
            i = j
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _current_version(conn: sqlite3.Connection) -> int:
    if not _has_table(conn, "schema_version"):
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def _check_applied(conn: sqlite3.Connection, migrations: list[_Migration]) -> None:
    """Every recorded version must be 1..N, have its file, and match that file's checksum."""
    if not _has_table(conn, "schema_version"):
        return
    applied = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    if not applied:
        return
    known = {m.number: m for m in migrations}
    for v in applied:
        if v not in known:
            raise RuntimeError(
                f"database records schema version {v}, which has no migration file here "
                f"(this code knows {len(migrations)}); is the database newer than the code?"
            )
    if applied != list(range(1, len(applied) + 1)):
        raise RuntimeError(f"schema_version has gaps or junk: {applied}")
    if not _has_table(conn, "schema_checksum"):
        raise RuntimeError("schema_version rows have no recorded checksums; refusing to trust them")
    recorded = dict(conn.execute("SELECT version, checksum FROM schema_checksum"))
    for v in applied:
        if not recorded.get(v):
            raise RuntimeError(f"schema version {v} has no recorded checksum; refusing to trust it")
        if recorded[v] != known[v].checksum:
            raise RuntimeError(f"migration {known[v].path.name} changed after it was applied; add a new migration instead")


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations, one transaction each. Returns the schema version."""
    if conn.in_transaction:
        raise RuntimeError("migrate() needs a connection with no open transaction; commit or roll back first")
    migrations = _migrations()
    plans = [(m, _statements(m.sql)) for m in migrations]  # every file is checked before the db is touched
    _check_applied(conn, migrations)
    for m, statements in plans:
        # IMMEDIATE takes the write lock up front, so two processes migrating at once
        # serialise here and the second one sees the first one's version row.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _current_version(conn) >= m.number:
                conn.rollback()
                continue
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(_VERSION_TABLE)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (m.number, datetime.now(UTC).isoformat()),
            )
            conn.execute(_CHECKSUM_TABLE)
            conn.execute("INSERT INTO schema_checksum(version, checksum) VALUES (?, ?)", (m.number, m.checksum))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    _check_applied(conn, migrations)
    return _current_version(conn)
