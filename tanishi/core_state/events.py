"""The event log: every action Tanishi takes, append-only and hash-chained (node CS2).

Each row's hash is sha256(prev_hash + canonical JSON of the row without its hash), where the canonical
JSON has sorted keys, compact separators and raw UTF-8, and `payload` is the exact stored text. So any
byte changed in any column, a deleted row, or an inserted row breaks the chain at that row or the next.
The first row links to "GENESIS". The `events` triggers (decision 0007) stop honest UPDATE and DELETE;
the chain is what catches someone who drops them.

Payloads are redacted for secrets before they are hashed or written, and bounded so their content can
never make emit() fail: lone surrogates are replaced, nesting is capped at MAX_DEPTH and every string
is clipped to MAX_CHARS (decision 0008).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tanishi.core_state.db import migrate, open_db, tanishi_home

logger = logging.getLogger(__name__)

GENESIS = "GENESIS"
GAPS_FILE = "event_gaps.jsonl"  # in $TANISHI_HOME: failed writes waiting for a log_gap event
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
    ("jwt", re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]+")),
)
# Secrets inside free text where the name, not the shape, gives them away: the value goes, the name stays.
# Each pattern starts on a literal (a secret word, "authorization", "://"), so a long hostile string
# cannot make it backtrack quadratically.
_ASSIGNED_SECRETS: tuple[re.Pattern[str], ...] = (
    # `SECRET_KEY=...`, `AWS_SECRET_ACCESS_KEY=...`, `"password": "..."`, `?token=...`: any value length.
    re.compile(r"(?i)((?:api[_-]?key|secret|password|passwd|token|private[_-]?key|credential)[\w-]*"
               r"['\"]?\s*[:=]\s*['\"]?)([^\s'\",;&]+)"),
    # `Authorization: Basic ...` (or Bearer, Digest, Token): the credential after the scheme.
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?(?:(?:basic|bearer|digest|token|negotiate)\s+)?)"
               r"([^\s'\",;]+)"),
    # `scheme://user:password@host`: the password.
    re.compile(r"(://[^\s/:@]*:)([^\s/@]+)(?=@)"),
    # `?sig=...`, `&access_key=...` and other credential-named URL parameters.
    re.compile(r"(?i)([?&][\w-]{0,40}?(?:key|sig|signature|auth)[\w-]{0,40}=)([^&\s#'\"]+)"),
)
# `Basic <base64 of user:password>` without an Authorization header in front of it.
_BASIC_CREDENTIAL = re.compile(r"(?i)(?<![A-Za-z0-9])basic\s+([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])")
# Any value under one of these dict keys is a secret, whatever its type or shape.
_SECRET_KEY = re.compile(
    r"(?i)api[_-]?key|secret|password|passwd|private[_-]?key|access[_-]?token|auth[_-]?token"
    r"|(?<![a-z])(?:token|cookie|credentials?|authorization|bearer|auth)(?![a-z])"
)
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

# Bounds on what one event may hold, so payload content can never make emit() fail or bloat the log.
MAX_DEPTH = 32  # a container nested deeper than this becomes _TOO_DEEP
MAX_CHARS = 4000  # every string is clipped to this many characters, after redaction
_TOO_DEEP = "[TRUNCATED: nested too deep]"


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
    """A copy of `value` with every secret-shaped string (and dict key) replaced by a marker.

    Never raises on content: lone surrogates become U+FFFD and containers nested deeper than
    MAX_DEPTH become a marker string. Values that are not JSON (objects, NaN) pass through as they are.
    """
    return _scrub(value, 1, None)


def _scrub(value: Any, depth: int, limit: int | None) -> Any:
    if isinstance(value, str):
        return _clean_text(value, limit)
    if not isinstance(value, (dict, list, tuple)):
        return value
    if depth > MAX_DEPTH:
        return _TOO_DEEP
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            key = _clean_text(k, limit) if isinstance(k, str) else k
            while key in out:  # two redacted or clipped keys must not overwrite each other
                key = f"{key}#"
            secret_name = isinstance(k, str) and _SECRET_KEY.search(k)
            out[key] = "[REDACTED]" if secret_name and v is not None and v != "" else _scrub(v, depth + 1, limit)
        return out
    return [_scrub(v, depth + 1, limit) for v in value]


def _clean_text(text: str, limit: int | None = MAX_CHARS) -> str:
    """Lone surrogates replaced, secrets redacted, then clipped: a cut never leaves half a key."""
    text = _redact_text(_LONE_SURROGATE.sub("�", text))
    if limit is None or len(text) <= limit:
        return text
    tail = f"... [{len(text) - limit} more chars]"
    return text[:max(limit - len(tail), 0)] + tail


def _redact_text(text: str) -> str:
    for name, pattern in _SECRET_PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    for pattern in _ASSIGNED_SECRETS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return _BASIC_CREDENTIAL.sub(_redact_basic, text)


def _redact_basic(m: re.Match[str]) -> str:
    try:
        decoded = base64.b64decode(m.group(1), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):  # binascii.Error is a ValueError
        return m.group(0)
    return m.group(0).replace(m.group(1), "[REDACTED]") if ":" in decoded else m.group(0)


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
    Any failure after the header checks is noted in $TANISHI_HOME/event_gaps.jsonl (time and kind
    only), and the next successful emit() first writes a log_gap event with the count of missed writes.
    """
    if not isinstance(kind, str) or not kind:
        raise TypeError("kind must be a non-empty str")
    if not isinstance(actor, str):
        raise TypeError("actor must be a str")
    if session_id is not None and not isinstance(session_id, str):
        raise TypeError("session_id must be a str or None")
    try:
        return _append(kind, payload, actor, session_id)
    except Exception:
        _note_gap(kind)
        raise


def _append(kind: str, payload: Any, actor: str, session_id: str | None) -> int:
    text = json.dumps(_scrub(payload, 1, MAX_CHARS), ensure_ascii=False, allow_nan=False)
    kind, actor = _clean_text(kind), _clean_text(actor)
    session_id = None if session_id is None else _clean_text(session_id)

    conn = _open()
    claim: tuple[Path, list[str]] | None = None
    try:
        # IMMEDIATE takes the write lock before we read the chain head, so two writers cannot fork it.
        conn.execute("BEGIN IMMEDIATE")
        try:
            head = conn.execute("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
            # The id is part of the hashed row, so we pick it ourselves, as AUTOINCREMENT would.
            id_ = max(head[0] if head else 0, seq[0] if seq else 0) + 1
            prev_hash = head[1] if head else GENESIS
            claim = _claim_gaps()
            if claim:  # earlier writes failed: say so before this event, in the same transaction
                gap = json.dumps(_scrub(_gap_summary(claim[1]), 1, MAX_CHARS), ensure_ascii=False)
                prev_hash = _insert(conn, id_, "log_gap", "event_log", None, gap, prev_hash)
                id_ += 1
            _insert(conn, id_, kind, actor, session_id, text, prev_hash)
            conn.commit()
        except BaseException:
            conn.rollback()
            if claim:
                _write_gap_lines(claim[1])  # the log_gap was not written, so the gaps stay pending
            raise
    finally:
        conn.close()
    if claim:
        claim[0].unlink(missing_ok=True)
    return id_


def _insert(conn: sqlite3.Connection, id_: int, kind: str, actor: str, session_id: str | None, text: str,
            prev_hash: str) -> str:
    ts = datetime.now(UTC).isoformat(timespec="microseconds")
    digest = _hash(prev_hash, _canonical(id_, ts, kind, actor, session_id, text, prev_hash))
    conn.execute(
        f"INSERT INTO events ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (id_, ts, kind, actor, session_id, text, prev_hash, digest),
    )
    return digest


def _gaps_path() -> Path:
    return tanishi_home() / GAPS_FILE


def _note_gap(kind: str) -> None:
    """Record that a write of `kind` failed. Never the payload, and never raises."""
    line = json.dumps({"ts": datetime.now(UTC).isoformat(timespec="microseconds"), "kind": _clean_text(kind, 200)})
    _write_gap_lines([line])


def _write_gap_lines(lines: list[str]) -> None:
    path = _gaps_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))
    except OSError as e:  # nowhere left to say it; the caller still gets the original error
        logger.warning("could not record an event log gap: %s", type(e).__name__)


def _claim_gaps() -> tuple[Path, list[str]] | None:
    """Move the pending gaps file aside (atomic, so gaps noted meanwhile start a new file) and read it."""
    path = _gaps_path()
    if not path.exists():
        return None
    claimed = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.claim")
    try:
        os.replace(path, claimed)
    except OSError:  # gone already (another writer claimed it), or not ours to move
        return None
    try:
        lines = [line for line in claimed.read_text(encoding="utf-8", errors="replace").splitlines() if line]
    except OSError:
        return None
    if not lines:
        claimed.unlink(missing_ok=True)
        return None
    return claimed, lines


def _gap_summary(lines: list[str]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    times = []
    for line in lines:
        try:
            item = json.loads(line)
            kind, ts = str(item["kind"]), str(item["ts"])
        except (ValueError, TypeError, KeyError):
            kind, ts = "unknown", ""
        kinds[kind] = kinds.get(kind, 0) + 1
        if ts:
            times.append(ts)
    return {"count": len(lines), "kinds": kinds, "first_ts": min(times, default=None),
            "last_ts": max(times, default=None)}


def verify_chain(conn: sqlite3.Connection) -> tuple[bool, int | None]:
    """Walk the log in id order. (True, None) if intact, else (False, id of the first missing or broken row).

    Ids must run 1, 2, 3, ... with no gap, and the last id must equal sqlite_sequence, so a deleted
    row, a deleted tail or a wiped log shows up as the first missing id, even after later emits.
    The rows and sqlite_sequence are read in one read transaction, so a concurrent emit() cannot
    make a healthy log look truncated. Read only.
    """
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")
    try:
        return _verify(conn)
    finally:
        if began:
            conn.rollback()


def _verify(conn: sqlite3.Connection) -> tuple[bool, int | None]:
    cur = conn.cursor()
    cur.row_factory = None
    expected_prev = GENESIS
    last_id = 0
    for id_, ts, kind, actor, session_id, payload, prev_hash, digest in cur.execute(
        f"SELECT {_COLUMNS} FROM events ORDER BY id"
    ):
        if id_ != last_id + 1:
            return False, last_id + 1
        if prev_hash != expected_prev or not isinstance(prev_hash, str):
            return False, id_
        try:
            ok = digest == _hash(prev_hash, _canonical(id_, ts, kind, actor, session_id, payload, prev_hash))
        except (TypeError, ValueError):  # a column rewritten to a blob or a broken string
            ok = False
        if not ok:
            return False, id_
        expected_prev, last_id = digest, id_
    row = cur.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
    seq = row[0] if row else 0
    if seq != last_id:  # seq ahead: the tail was deleted; seq behind: sqlite_sequence was rewritten
        return False, min(seq, last_id) + 1
    return True, None


def iter_events(kind: str | None = None, since: str | None = None) -> Iterator[Event]:
    """Events in id order, read lazily. `kind` matches exactly; `since` keeps rows with ts >= since.

    `since` is any ISO 8601 time ("Z", an offset, or none, which means UTC). It is compared with each
    row's ts as a UTC datetime, not as a string. Raises ValueError for a `since` that is not ISO 8601.
    """
    since_dt = None if since is None else _utc(since)
    sql = f"SELECT {_COLUMNS} FROM events"
    args = []
    if kind is not None:
        sql += " WHERE kind = ?"
        args.append(kind)
    sql += " ORDER BY id"
    conn = _open()
    try:
        for row in conn.execute(sql, args):
            if since_dt is not None and not _at_or_after(row[1], since_dt):
                continue
            yield Event(row[0], row[1], row[2], row[3], row[4],
                        None if row[5] is None else json.loads(row[5]), row[6], row[7])
    finally:
        conn.close()


def _utc(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _at_or_after(ts: Any, since: datetime) -> bool:
    try:
        return _utc(ts) >= since
    except (TypeError, ValueError):  # a ts that is not a time cannot be shown to be after `since`
        return False
