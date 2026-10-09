"""Encrypted, signed snapshots of the Core State and restore into an empty home (node CS7).

A bundle holds `core_state.db`, `identity.yaml` and the `skills/` folder of $TANISHI_HOME. Absent optional parts stay
absent. Legacy databases are never read or bundled (decision 0003).

Bundle file layout (decision 0015):

    MAGIC (8 bytes) | HMAC-SHA256(signing key, MAGIC + token) (32 bytes) | Fernet token

The token encrypts a payload: an 8-byte big-endian header length, a JSON manifest, then every file's bytes in manifest
order. The manifest lists each file's relative path, size and sha256. restore() checks the signature first, decrypts,
checks every path against a whitelist and every checksum, and writes into a staging directory beside the target. Only
when everything is in place does it rename the staging directory to the target home, so a failure leaves nothing.

Keys come from the environment only: TANISHI_SNAPSHOT_KEY (a Fernet key) and TANISHI_SNAPSHOT_SIGNING_KEY (the HMAC
secret). Neither key nor any file content appears in an error message.

The db is captured byte for byte: snapshot() checkpoints the WAL into the main file, then holds the write lock while it
reads that file, so committed rows still in the `-wal` file are included and no writer can change it mid-read. Both
snapshot() and restore() run `PRAGMA quick_check`, so a damaged db is never bundled or restored as if it were fine.

Staging names carry the writer's pid (`.tanishi-snapshot-<pid>-*.part` in the destination, `.tanishi-restore-<pid>-*`
beside the restore target). A process killed hard leaves them behind; the next snapshot() into that destination or
restore() beside that target removes those whose process is gone.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import struct
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken

from tanishi.core_state.db import BUSY_TIMEOUT_S, open_db, resolve_path, tanishi_home

KEY_ENV_VAR = "TANISHI_SNAPSHOT_KEY"
SIGNING_KEY_ENV_VAR = "TANISHI_SNAPSHOT_SIGNING_KEY"
MAGIC = b"TNSNAP01"
SUFFIX = ".tanishi-snapshot"
FORMAT = 1
DB_NAME = "core_state.db"
IDENTITY_NAME = "identity.yaml"
SKILLS_DIR = "skills"
_MAC_LEN = 32
_LEN = struct.Struct(">Q")
# Path separators of this OS other than "/". On POSIX "\" is an ordinary filename character; on Windows it is not.
_FOREIGN_SEPS = tuple({os.sep, os.altsep} - {None, "/"})
_PART_PREFIX = ".tanishi-snapshot-"
_RESTORE_PREFIX = ".tanishi-restore-"
_STAGING_PID = re.compile(r"^\.tanishi-(?:snapshot|restore)-(\d+)-")
_STALE_AFTER_S = 24 * 3600  # where liveness of a pid cannot be checked (Windows), sweep by age instead


class SnapshotError(Exception):
    """A snapshot could not be written, or a bundle was refused."""


# ---------------------------------------------------------------- keys


def _fernet() -> Fernet:
    raw = os.environ.get(KEY_ENV_VAR)
    if not raw:
        raise SnapshotError(f"{KEY_ENV_VAR} is not set")
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        raise SnapshotError(f"{KEY_ENV_VAR} is not a valid Fernet key") from None


def _signing_key() -> bytes:
    raw = os.environ.get(SIGNING_KEY_ENV_VAR)
    if not raw:
        raise SnapshotError(f"{SIGNING_KEY_ENV_VAR} is not set")
    return raw.encode()


def _mac(key: bytes, token: bytes) -> bytes:
    return hmac.new(key, MAGIC + token, hashlib.sha256).digest()


# ---------------------------------------------------------------- snapshot


def snapshot(dest_dir: str | os.PathLike[str], home: str | os.PathLike[str] | None = None) -> Path:
    """Write one encrypted, signed bundle of `home` (default $TANISHI_HOME) into `dest_dir` and return its path.

    Call it once per destination to keep copies in two places. A destination inside the home's skills/ folder is left
    out of the bundle, so bundles never nest. Every path is checked with the rule restore() applies, so a bundle this
    writes can always be restored on this OS; a skill name that cannot be stored safely is refused here, loudly.
    """
    fernet, signing_key = _fernet(), _signing_key()  # fail before touching anything
    dest = Path(dest_dir)
    if not dest.is_dir():
        raise SnapshotError(f"snapshot destination {dest} is not an existing directory")
    _sweep_stale_staging(dest)
    src = Path(home) if home is not None else tanishi_home()
    db_path = Path(resolve_path(str(src / DB_NAME) if home is not None else None))
    if not db_path.is_file():
        raise SnapshotError(f"no Core State database at {db_path}")

    files: list[tuple[str, bytes]] = [(DB_NAME, _read_db_consistently(db_path))]
    dirs: list[str] = []
    identity = src / IDENTITY_NAME
    if identity.is_symlink():
        raise SnapshotError(f"{IDENTITY_NAME} is a symlink; refusing to follow it")
    if identity.exists():
        if not identity.is_file():
            raise SnapshotError(f"{IDENTITY_NAME} is not a regular file")
        files.append((IDENTITY_NAME, _read_source(identity, IDENTITY_NAME)))
    skills = src / SKILLS_DIR
    if skills.is_symlink():
        raise SnapshotError(f"{SKILLS_DIR}/ is a symlink; refusing to follow it")
    if skills.is_dir():
        skip = _dest_inside(skills, dest)
        dirs.append(SKILLS_DIR)
        for p in sorted(skills.rglob("*")):
            if skip is not None and (p == skip or skip in p.parents):
                continue  # our own destination: bundling it would nest every earlier bundle in this one
            rel = p.relative_to(src).as_posix()
            if p.is_symlink():
                raise SnapshotError(f"{rel} is a symlink; refusing to follow it")
            if p.is_dir():
                _check_capture_path(rel, is_dir=True)
                dirs.append(rel)
            elif p.is_file():
                _check_capture_path(rel, is_dir=False)
                files.append((rel, _read_source(p, rel)))
            else:
                raise SnapshotError(f"{rel} is not a regular file")

    manifest = {
        "format": FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "dirs": dirs,
        "files": [{"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for rel, data in files],
    }
    header = json.dumps(manifest, sort_keys=True).encode()
    payload = b"".join([_LEN.pack(len(header)), header, *(data for _, data in files)])
    token = fernet.encrypt(payload)
    blob = MAGIC + _mac(signing_key, token) + token

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    final = dest / f"core_state-{stamp}-{secrets.token_hex(4)}{SUFFIX}"
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=f"{_PART_PREFIX}{os.getpid()}-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return final


def _read_source(path: Path, rel: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise SnapshotError(f"could not read {rel}: {e.strerror}") from None


def _dest_inside(skills: Path, dest: Path) -> Path | None:
    """If `dest` lies inside `skills`, return it as a path under `skills` (the subtree to leave out), else None."""
    skills_real, dest_real = skills.resolve(), dest.resolve()
    if dest_real == skills_real:
        raise SnapshotError(f"snapshot destination is the {SKILLS_DIR}/ folder itself; pick a folder outside it")
    if skills_real in dest_real.parents:
        return skills / dest_real.relative_to(skills_real)
    return None


def _check_capture_path(rel: str, is_dir: bool) -> None:
    """Refuse at capture time any path restore() would refuse, so no bundle we write is unrestorable."""
    try:
        rel.encode("utf-8")  # a name that is not valid UTF-8 cannot round-trip through the JSON manifest safely
        _check_path(rel, is_dir)
    except (UnicodeEncodeError, ValueError):
        raise SnapshotError(f"{rel!r} cannot be stored safely in a snapshot bundle; rename it") from None


def _read_db_consistently(db_path: Path) -> bytes:
    """Checkpoint the WAL into the main file, then check and read that file while holding the write lock.

    With the WAL empty and writers locked out, the main file alone is the whole committed state.
    """
    wal = Path(str(db_path) + "-wal")
    try:
        conn = open_db(str(db_path))
    except sqlite3.Error as e:
        raise SnapshotError(f"could not open the Core State database: {e}") from None
    conn.isolation_level = None
    try:
        deadline = time.monotonic() + BUSY_TIMEOUT_S
        while True:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            conn.execute("BEGIN IMMEDIATE")
            try:
                if not wal.exists() or wal.stat().st_size == 0:
                    _require_intact(conn)
                    return db_path.read_bytes()
            finally:
                conn.execute("ROLLBACK")
            if time.monotonic() >= deadline:
                raise SnapshotError("could not checkpoint the Core State WAL; a reader kept it busy")
            time.sleep(0.05)
    except sqlite3.Error as e:
        raise SnapshotError(f"could not read the Core State database consistently: {e}") from e
    finally:
        conn.close()


def _require_intact(conn: sqlite3.Connection) -> None:
    """Refuse a database that fails PRAGMA quick_check (damaged pages, broken b-trees)."""
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError:
        rows = []
    if rows != [("ok",)]:
        raise SnapshotError("the Core State database failed its integrity check (PRAGMA quick_check)")


# ---------------------------------------------------------------- staging debris


def _sweep_stale_staging(directory: Path) -> None:
    """Remove snapshot/restore staging left in `directory` by a process that was killed before it could clean up."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for p in entries:
        m = _STAGING_PID.match(p.name)
        if not m or not _staging_is_stale(p, int(m.group(1))):
            continue
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            pass  # someone else swept it, or we may not; never fail a snapshot over debris


def _staging_is_stale(p: Path, pid: int) -> bool:
    if pid == os.getpid():
        return False  # another thread of this process may be using it
    if os.name != "posix":
        try:
            return time.time() - p.lstat().st_mtime > _STALE_AFTER_S
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False  # alive, owned by someone else
    return False


# ---------------------------------------------------------------- restore


def restore(bundle: str | os.PathLike[str], home: str | os.PathLike[str]) -> None:
    """Restore `bundle` into `home`, which must be missing or an empty directory.

    Everything is checked before anything is written. Any failure raises and leaves `home` as it was.
    """
    target = Path(home)
    _require_empty(target)
    fernet, signing_key = _fernet(), _signing_key()
    bundle_path = Path(bundle)
    if not bundle_path.is_file():
        raise SnapshotError(f"snapshot bundle {bundle_path} is not a file")
    try:
        blob = bundle_path.read_bytes()
    except OSError as e:
        raise SnapshotError(f"could not read snapshot bundle {bundle_path}: {e.strerror}") from None
    dirs, files = _open_bundle(blob, fernet, signing_key)

    target.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_staging(target.parent)
    # The staging dir holds a plaintext db until the rename; its pid-tagged name lets a later restore() sweep it
    # if this process is killed before the `except` below can.
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f"{_RESTORE_PREFIX}{os.getpid()}-"))
    try:
        for rel in dirs:
            (staging / rel).mkdir(mode=0o700, parents=True, exist_ok=True)
        for rel, data, digest in files:
            out = staging / rel
            try:
                out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except OSError as e:
                raise SnapshotError(f"{rel} cannot be written on this system: {e.strerror}") from None
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if hashlib.sha256(out.read_bytes()).hexdigest() != digest:  # checksum compare on what landed on disk
                raise SnapshotError(f"{rel} did not restore byte for byte")
        _check_staged_db(staging / DB_NAME)
        os.chmod(staging, 0o700)
        _require_empty(target)  # someone may have written there while we worked
        if target.exists():
            target.rmdir()
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _check_staged_db(db_file: Path) -> None:
    """quick_check the staged db without changing it: read-only and immutable, so no -wal or -shm is created."""
    try:
        conn = sqlite3.connect(f"{db_file.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        raise SnapshotError("the restored Core State database cannot be opened") from None
    try:
        _require_intact(conn)
    finally:
        conn.close()


def _require_empty(target: Path) -> None:
    if target.is_symlink():
        raise SnapshotError(f"restore target {target} is a symlink")
    if target.exists():
        if not target.is_dir():
            raise SnapshotError(f"restore target {target} is not a directory")
        if any(target.iterdir()):
            raise SnapshotError(f"restore target {target} is not empty; refusing to overwrite it")


def _open_bundle(blob: bytes, fernet: Fernet, signing_key: bytes) -> tuple[list[str], list[tuple[str, bytes, str]]]:
    """Verify, decrypt and parse a bundle. Returns (dirs, [(path, data, sha256)])."""
    if len(blob) < len(MAGIC) + _MAC_LEN + 1 or not blob.startswith(MAGIC):
        raise SnapshotError("not a Tanishi snapshot bundle")
    mac, token = blob[len(MAGIC):len(MAGIC) + _MAC_LEN], blob[len(MAGIC) + _MAC_LEN:]
    if not hmac.compare_digest(mac, _mac(signing_key, token)):
        raise SnapshotError("bundle signature does not match; wrong signing key or a damaged bundle")
    try:
        payload = fernet.decrypt(token)
    except InvalidToken:
        raise SnapshotError("bundle does not decrypt; wrong snapshot key or a damaged bundle") from None

    try:
        if len(payload) < _LEN.size:
            raise ValueError("payload too short")
        (n,) = _LEN.unpack_from(payload)
        manifest = json.loads(payload[_LEN.size:_LEN.size + n].decode())
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ValueError("unknown bundle format")
        dirs = manifest["dirs"]
        entries = manifest["files"]
        if not isinstance(dirs, list) or not isinstance(entries, list):
            raise TypeError("bad manifest")
        offset = _LEN.size + n
        files: list[tuple[str, bytes, str]] = []
        seen: set[str] = set()
        for d in dirs:
            _check_path(d, is_dir=True)
        for e in entries:
            rel, size, digest = e["path"], e["size"], e["sha256"]
            _check_path(rel, is_dir=False)
            if rel in seen or not isinstance(size, int) or size < 0 or not isinstance(digest, str):
                raise ValueError("bad manifest entry")
            seen.add(rel)
            data = payload[offset:offset + size]
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("file checksum mismatch")
            files.append((rel, data, digest))
            offset += size
        if offset != len(payload):
            raise ValueError("trailing bytes after the last file")
        if DB_NAME not in seen:
            raise ValueError(f"bundle has no {DB_NAME}")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, struct.error) as e:
        raise SnapshotError(f"bundle contents are malformed: {e}") from None
    return dirs, files


def _check_path(rel: object, is_dir: bool) -> None:
    """Only core_state.db, identity.yaml and things under skills/ may be restored; nothing may escape the home."""
    if not isinstance(rel, str) or not rel or "\x00" in rel or any(s in rel for s in _FOREIGN_SEPS):
        raise ValueError("bad path in manifest")
    p = PurePosixPath(rel)
    if p.is_absolute() or any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError("path escapes the home")
    if is_dir:
        ok = p.parts[0] == SKILLS_DIR
    else:
        ok = rel in (DB_NAME, IDENTITY_NAME) or (p.parts[0] == SKILLS_DIR and len(p.parts) > 1)
    if not ok:
        raise ValueError("path is not part of a Core State bundle")
