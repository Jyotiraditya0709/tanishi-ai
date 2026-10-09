"""Encrypted, signed snapshots of the Core State and restore into an empty home (node CS7).

A bundle holds `core_state.db`, `identity.yaml` and the `skills/` folder of $TANISHI_HOME. Absent optional parts stay
absent. Legacy databases are never read or bundled (decision 0003).

Bundle file layout (decision 0014):

    MAGIC (8 bytes) | HMAC-SHA256(signing key, MAGIC + token) (32 bytes) | Fernet token

The token encrypts a payload: an 8-byte big-endian header length, a JSON manifest, then every file's bytes in manifest
order. The manifest lists each file's relative path, size and sha256. restore() checks the signature first, decrypts,
checks every path against a whitelist and every checksum, and writes into a staging directory beside the target. Only
when everything is in place does it rename the staging directory to the target home, so a failure leaves nothing.

Keys come from the environment only: TANISHI_SNAPSHOT_KEY (a Fernet key) and TANISHI_SNAPSHOT_SIGNING_KEY (the HMAC
secret). Neither key nor any file content appears in an error message.

The db is captured byte for byte: snapshot() checkpoints the WAL into the main file, then holds the write lock while it
reads that file, so committed rows still in the `-wal` file are included and no writer can change it mid-read.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
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

    Call it once per destination to keep copies in two places.
    """
    fernet, signing_key = _fernet(), _signing_key()  # fail before touching anything
    dest = Path(dest_dir)
    if not dest.is_dir():
        raise SnapshotError(f"snapshot destination {dest} is not an existing directory")
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
        files.append((IDENTITY_NAME, identity.read_bytes()))
    skills = src / SKILLS_DIR
    if skills.is_symlink():
        raise SnapshotError(f"{SKILLS_DIR}/ is a symlink; refusing to follow it")
    if skills.is_dir():
        dirs.append(SKILLS_DIR)
        for p in sorted(skills.rglob("*")):
            rel = p.relative_to(src).as_posix()
            if p.is_symlink():
                raise SnapshotError(f"{rel} is a symlink; refusing to follow it")
            if p.is_dir():
                dirs.append(rel)
            elif p.is_file():
                files.append((rel, p.read_bytes()))
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
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=".snapshot-", suffix=".part")
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


def _read_db_consistently(db_path: Path) -> bytes:
    """Checkpoint the WAL into the main file, then read that file while holding the write lock.

    With the WAL empty and writers locked out, the main file alone is the whole committed state.
    """
    wal = Path(str(db_path) + "-wal")
    conn = open_db(str(db_path))
    conn.isolation_level = None
    try:
        deadline = time.monotonic() + BUSY_TIMEOUT_S
        while True:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            conn.execute("BEGIN IMMEDIATE")
            try:
                if not wal.exists() or wal.stat().st_size == 0:
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


# ---------------------------------------------------------------- restore


def restore(bundle: str | os.PathLike[str], home: str | os.PathLike[str]) -> None:
    """Restore `bundle` into `home`, which must be missing or an empty directory.

    Everything is checked before anything is written. Any failure raises and leaves `home` as it was.
    """
    target = Path(home)
    _require_empty(target)
    fernet, signing_key = _fernet(), _signing_key()
    blob = Path(bundle).read_bytes()
    dirs, files = _open_bundle(blob, fernet, signing_key)

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.restore-"))
    try:
        for rel in dirs:
            (staging / rel).mkdir(mode=0o700, parents=True, exist_ok=True)
        for rel, data, digest in files:
            out = staging / rel
            out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if hashlib.sha256(out.read_bytes()).hexdigest() != digest:  # checksum compare on what landed on disk
                raise SnapshotError(f"{rel} did not restore byte for byte")
        os.chmod(staging, 0o700)
        _require_empty(target)  # someone may have written there while we worked
        if target.exists():
            target.rmdir()
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


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
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
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
