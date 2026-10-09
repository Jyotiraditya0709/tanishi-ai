"""The event log: every action Tanishi takes, append-only and hash-chained (node CS2).

Each row's hash is sha256(prev_hash + canonical JSON of the row without its hash), where the canonical
JSON has sorted keys, compact separators and raw UTF-8, and `payload` is the exact stored text. So any
byte changed in any column, a deleted row, or an inserted row breaks the chain at that row or the next.
The first row links to "GENESIS". The `events` triggers (decision 0007) stop honest UPDATE and DELETE;
the chain is what catches someone who drops them.

Payloads are redacted for API-key patterns before they are hashed or written.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tanishi.core_state.db import migrate, open_db

GENESIS = "GENESIS"
_COLUMNS = "id, ts, kind, actor, session_id, payload, prev_hash, hash"

# Key shapes, each kept from matching inside an ordinary word ("risk-assessment" is not an OpenAI key).
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic", re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}")),
    ("stripe", re.compile(r"(?<![A-Za-z0-9])[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("aws", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])")),
    ("github", re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("slack", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("google", re.compile(r"(?<![A-Za-z0-9])AIza[A-Za-z0-9_\-]{35}")),
    ("huggingface", re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("bearer", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/\-]{20,}=*")),
)
# `api_key=...`, `"password": "..."` and the like inside free text: the value goes, the name stays.
_ASSIGNED_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token)['\"]?\s*[:=]\s*['\"]?)"
    r"([^\s'\",;]{8,})"
)
# A string value under one of these dict keys is a secret, whatever it looks like.
_SECRET_KEY = re.compile(r"(?i)api[_-]?key|secret|password|passwd|private[_-]?key|access[_-]?token|auth[_-]?token")


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    kind: str
    actor: str
    session_id: str | None
    payload: Any
    prev_hash: str
    hash: str


def redact(value: Any) -> Any:
    """A copy of `value` with every API-key-shaped string (and dict key) replaced by a marker."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            key = _redact_text(k) if isinstance(k, str) else k
            while key in out:  # two redacted keys must not overwrite each other
                key = f"{key}#"
            secret_name = isinstance(k, str) and _SECRET_KEY.search(k)
            out[key] = "[REDACTED]" if secret_name and isinstance(v, str) and v else redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _redact_text(text: str) -> str:
    for name, pattern in _SECRET_PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return _ASSIGNED_SECRET.sub(lambda m: m.group(1) + "[REDACTED]", text)


def _canonical(id_: int, ts: str, kind: str, actor: str, session_id: str | None, payload: str | None,
               prev_hash: str) -> str:
    row = {"id": id_, "ts": ts, "kind": kind, "actor": actor, "session_id": session_id,
           "payload": payload, "prev_hash": prev_hash}
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _open() -> sqlite3.Connection:
    conn = open_db()
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def emit(kind: str, payload: dict, actor: str = "tanishi", session_id: str | None = None) -> int:
    """Append one event to the Core State log and return its id.

    Raises TypeError or ValueError, writing nothing, if the payload is not plain JSON (NaN included).
    """
    if not isinstance(kind, str) or not kind:
        raise TypeError("kind must be a non-empty str")
    if not isinstance(actor, str):
        raise TypeError("actor must be a str")
    if session_id is not None and not isinstance(session_id, str):
        raise TypeError("session_id must be a str or None")
    text = json.dumps(redact(payload), ensure_ascii=False, allow_nan=False)
    kind, actor = _redact_text(kind), _redact_text(actor)
    session_id = None if session_id is None else _redact_text(session_id)

    conn = _open()
    try:
        # IMMEDIATE takes the write lock before we read the chain head, so two writers cannot fork it.
        conn.execute("BEGIN IMMEDIATE")
        try:
            head = conn.execute("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
            # The id is part of the hashed row, so we pick it ourselves, as AUTOINCREMENT would.
            id_ = max(head[0] if head else 0, seq[0] if seq else 0) + 1
            prev_hash = head[1] if head else GENESIS
            ts = datetime.now(UTC).isoformat(timespec="microseconds")
            digest = _hash(prev_hash, _canonical(id_, ts, kind, actor, session_id, text, prev_hash))
            conn.execute(
                f"INSERT INTO events ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (id_, ts, kind, actor, session_id, text, prev_hash, digest),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()
    return id_


def verify_chain(conn: sqlite3.Connection) -> tuple[bool, int | None]:
    """Walk the log in id order. (True, None) if intact, else (False, id of the first broken row).

    A deleted row shows up at the row after it. A deleted tail shows up as the first missing id,
    because sqlite_sequence still remembers it. Read only.
    """
    cur = conn.cursor()
    cur.row_factory = None
    expected_prev = GENESIS
    last_id = 0
    for id_, ts, kind, actor, session_id, payload, prev_hash, digest in cur.execute(
        f"SELECT {_COLUMNS} FROM events ORDER BY id"
    ):
        if prev_hash != expected_prev or not isinstance(prev_hash, str):
            return False, id_
        try:
            ok = digest == _hash(prev_hash, _canonical(id_, ts, kind, actor, session_id, payload, prev_hash))
        except (TypeError, ValueError):  # a column rewritten to a blob or a broken string
            ok = False
        if not ok:
            return False, id_
        expected_prev, last_id = digest, id_
    seq = cur.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
    if seq and seq[0] > last_id:
        return False, last_id + 1
    return True, None


def iter_events(kind: str | None = None, since: str | None = None) -> Iterator[Event]:
    """Events in id order, read lazily. `kind` matches exactly; `since` keeps rows with ts >= since."""
    sql = f"SELECT {_COLUMNS} FROM events"
    where, args = [], []
    if kind is not None:
        where.append("kind = ?")
        args.append(kind)
    if since is not None:
        where.append("ts >= ?")
        args.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    conn = _open()
    try:
        for row in conn.execute(sql, args):
            yield Event(row[0], row[1], row[2], row[3], row[4],
                        None if row[5] is None else json.loads(row[5]), row[6], row[7])
    finally:
        conn.close()
