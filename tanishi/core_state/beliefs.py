"""Belief store with evidence (node CS3).

A belief is a (subject, predicate, object) triple with a confidence in [0, 1], a required source and a status.
Two live beliefs with the same subject and predicate but a different object contradict each other: both are kept,
both are marked contested, and a `belief_conflict` event is written. Nothing is ever deleted; retire() changes status.

Triples are compared on a normalised key (trimmed, casefolded, inner whitespace collapsed); the original text is
what gets stored and returned. Events go through CS2's emit(), after the belief change has committed, because emit()
takes its own write lock. A failed emit() is logged and noted as a log gap by emit() itself; the belief change stands
(decision 0009).

Every call opens the Core State database (TANISHI_CORE_STATE_DB, decision 0003), migrates it, and closes it again,
unless the caller passes a connection from connect(). The contradiction check and the insert share one
BEGIN IMMEDIATE transaction, so two writers cannot both miss each other.
"""
from __future__ import annotations

import logging
import math
import numbers
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from tanishi.core_state.db import migrate, open_db
from tanishi.core_state.events import emit, redact

logger = logging.getLogger(__name__)

ACTIVE = "active"
CONTESTED = "contested"
RETIRED = "retired"
LIVE = (ACTIVE, CONTESTED)
CONFLICT_EVENT = "belief_conflict"
RETIRE_EVENT = "belief_retired"
REACTIVATE_EVENT = "belief_reactivated"
REASON_CODES = ("superseded", "user_correction", "contradiction_resolved", "other")
SUPPORT = "support"
RETIRE_REASON = "retire_reason"
ACTOR = "core_state.beliefs"

_COLUMNS = "id, subject, predicate, object, confidence, source, status, created_at, updated_at"


@dataclass(frozen=True)
class Belief:
    id: str
    subject: str
    predicate: str
    object: str
    confidence: float
    source: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Evidence:
    id: str
    belief_id: str
    event_id: int | None
    kind: str
    note: str | None


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """One migrated Core State connection, for callers that make many calls (the legacy importer)."""
    conn = open_db()
    try:
        migrate(conn)
        _prepare(conn)
        yield conn
    finally:
        conn.close()


def add_belief(
    subject: str,
    predicate: str,
    object: str,
    confidence: float,
    source: str,
    evidence_event_id: int | None = None,
) -> Belief:
    """Store a new belief. If a live belief disagrees with it, both become contested and a conflict event is written.

    An identical triple is stored as a second row: the exam pins one row per call (CS3-repair-conflicts.md, R5)."""
    with _using(None) as conn:
        return _add(conn, str(uuid.uuid4()), subject, predicate, object, confidence, source, evidence_event_id)


def add_belief_if_absent(
    belief_id: str, subject: str, predicate: str, object: str, confidence: float, source: str,
    *, conn: sqlite3.Connection | None = None,
) -> Belief | None:
    """Like add_belief, under a caller-chosen id; returns None and writes nothing if that id exists (for importers)."""
    if not isinstance(belief_id, str) or not belief_id:
        raise ValueError("belief_id must be a non-empty string")
    with _using(conn) as c:
        return _add(c, belief_id, subject, predicate, object, confidence, source, None, skip_existing=True)


def find(
    subject: str | None = None, predicate: str | None = None, text: str | None = None,
    *, conn: sqlite3.Connection | None = None,
) -> list[Belief]:
    """Beliefs of any status matching every given filter. `subject` and `predicate` match exactly; `text` is a literal,
    case-insensitive (ASCII) substring of subject, predicate or object. No filter returns every belief."""
    where, args = [], []
    if subject is not None:
        where.append("subject = ?")
        args.append(subject)
    if predicate is not None:
        where.append("predicate = ?")
        args.append(predicate)
    if text is not None:
        where.append("(instr(lower(subject), lower(?)) OR instr(lower(predicate), lower(?)) "
                     "OR instr(lower(object), lower(?)))")
        args += [text, text, text]
    sql = f"SELECT {_COLUMNS} FROM beliefs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _using(conn) as c:
        return [_belief(r) for r in c.execute(sql + " ORDER BY rowid", args)]


def contradictions(belief: Belief | str) -> list[Belief]:
    """Live beliefs that disagree with the stored belief of this id. Empty if it is not live or not stored.

    Only the id of `belief` is used: subject, predicate and object come from the stored row.
    """
    belief_id = belief if isinstance(belief, str) else belief.id
    with _using(None) as conn:
        b = _get(conn, belief_id)
        if b is None or b.status not in LIVE:
            return []
        return _peers(conn, b)


def retire(belief_id: str, reason: str, code: str | None = None, *, conn: sqlite3.Connection | None = None) -> None:
    """Mark a belief retired. The row and its evidence stay.

    The belief_retired event carries the id and a reason code only (one of REASON_CODES). `code` defaults to `reason`
    when that is itself a code, else "other". The free-text reason, redacted, goes in an evidence row of kind
    retire_reason that points at the event (decision 0009). Retiring an already retired belief changes nothing.
    A contested peer left with no live contradiction goes back to active. Raises LookupError for an unknown id and
    ValueError for a blank reason or an unknown code.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("retire() needs a reason")
    if code is None:
        code = reason.strip() if reason.strip() in REASON_CODES else "other"
    elif code not in REASON_CODES:
        raise ValueError(f"reason code must be one of {REASON_CODES}")
    with _using(conn) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            b = _get(c, belief_id)
            if b is None:
                raise LookupError(f"no belief {belief_id!r}")
            if b.status not in LIVE:
                c.commit()
                return
            now = _now()
            c.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?", (RETIRED, now, b.id))
            for peer in _peers(c, b):
                if peer.status == CONTESTED and not _peers(c, peer):
                    c.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?", (ACTIVE, now, peer.id))
            c.commit()
        except BaseException:
            c.rollback()
            raise
        event_id = _emit(RETIRE_EVENT, {"belief_id": b.id, "reason": code})
        # belief_id stays NULL: the evidence of a belief is what supports it, and the exam pins that retire() leaves
        # it unchanged. The row is tied to the belief through its event; evidence_for() follows that link.
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("INSERT INTO evidence(id, belief_id, event_id, kind, note) VALUES (?, NULL, ?, ?, ?)",
                      (str(uuid.uuid4()), event_id, RETIRE_REASON, redact(reason)))
            c.commit()
        except BaseException:
            c.rollback()
            raise


def reactivate(belief_id: str, *, conn: sqlite3.Connection | None = None) -> bool:
    """Bring a retired belief back to life (active, or contested if a live belief now disagrees with it).

    Returns False and changes nothing if the belief is already live. Raises LookupError for an unknown id.
    """
    with _using(conn) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            b = _get(c, belief_id)
            if b is None:
                raise LookupError(f"no belief {belief_id!r}")
            if b.status in LIVE:
                c.commit()
                return False
            peers = _peers(c, b)
            now = _now()
            c.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?",
                      (CONTESTED if peers else ACTIVE, now, b.id))
            _contest(c, peers, now)
            c.commit()
        except BaseException:
            c.rollback()
            raise
    _emit(REACTIVATE_EVENT, {"belief_id": b.id})
    if peers:
        _emit(CONFLICT_EVENT, {"belief_id": b.id, "conflicts_with": [p.id for p in peers]})
    return True


def evidence_for(belief_id: str) -> list[Evidence]:
    """Evidence rows of a belief, oldest first: what supports it, and the reasons it was retired."""
    sql = ("SELECT e.rowid, e.id, e.belief_id, e.event_id, e.kind, e.note FROM evidence e WHERE e.belief_id = ? "
           "UNION ALL "
           "SELECT e.rowid, e.id, ?, e.event_id, e.kind, e.note FROM evidence e JOIN events v ON v.id = e.event_id "
           "WHERE e.belief_id IS NULL AND e.kind = ? AND v.kind = ? AND json_extract(v.payload, '$.belief_id') = ? "
           "ORDER BY 1")
    with _using(None) as conn:
        rows = conn.execute(sql, (belief_id, belief_id, RETIRE_REASON, RETIRE_EVENT, belief_id)).fetchall()
    return [Evidence(*r[1:]) for r in rows]


# ---------------------------------------------------------------- internals


def _key(text: str | None) -> str | None:
    """The comparison key: trimmed, casefolded, inner whitespace collapsed."""
    return None if text is None else " ".join(text.split()).casefold()


def _prepare(conn: sqlite3.Connection) -> None:
    conn.create_function("belief_key", 1, _key, deterministic=True)


@contextmanager
def _using(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        _prepare(conn)
        yield conn
        return
    with connect() as c:
        yield c


def _add(
    conn: sqlite3.Connection,
    belief_id: str,
    subject: str,
    predicate: str,
    obj: str,
    confidence: float,
    source: str,
    evidence_event_id: int | None,
    skip_existing: bool = False,
) -> Belief | None:
    for name, value in (("subject", subject), ("predicate", predicate), ("object", obj)):
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if evidence_event_id is not None and (isinstance(evidence_event_id, bool) or not isinstance(evidence_event_id, int)):
        raise TypeError(f"evidence_event_id must be an int, got {type(evidence_event_id).__name__}")
    conf = _clamp(confidence)
    now = _now()
    new = Belief(belief_id, subject, predicate, obj, conf, source, ACTIVE, now, now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if skip_existing and conn.execute("SELECT 1 FROM beliefs WHERE id = ?", (belief_id,)).fetchone():
            conn.rollback()
            return None
        peers = _peers(conn, new)
        if peers:
            new = Belief(belief_id, subject, predicate, obj, conf, source, CONTESTED, now, now)
        conn.execute(
            f"INSERT INTO beliefs({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (belief_id, subject, predicate, obj, conf, source, new.status, now, now),
        )
        if evidence_event_id is not None:
            _link(conn, belief_id, evidence_event_id)
        _contest(conn, peers, now)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if peers:
        # Ids only: belief text may be personal and events are an audit log (CLAUDE.md).
        _emit(CONFLICT_EVENT, {"belief_id": belief_id, "conflicts_with": [p.id for p in peers]})
    return new


def _link(conn: sqlite3.Connection, belief_id: str, event_id: int) -> None:
    conn.execute("INSERT INTO evidence(id, belief_id, event_id, kind, note) VALUES (?, ?, ?, ?, NULL)",
                 (str(uuid.uuid4()), belief_id, event_id, SUPPORT))


def _contest(conn: sqlite3.Connection, peers: list[Belief], now: str) -> None:
    for p in peers:
        if p.status != CONTESTED:
            conn.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?", (CONTESTED, now, p.id))


def _emit(kind: str, payload: dict) -> int | None:
    """Append an event through CS2's emit(). Fails open, as CS2's own wiring does (decision 0008 item 6): the belief
    change has already committed, and emit() has noted the gap for the next successful write."""
    try:
        return emit(kind, payload, actor=ACTOR)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not write a %s event: %s", kind, type(e).__name__)
        return None


def _get(conn: sqlite3.Connection, belief_id: str) -> Belief | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
    return None if row is None else _belief(row)


def _peers(conn: sqlite3.Connection, b: Belief) -> list[Belief]:
    """Live beliefs other than `b` with the same subject and predicate and a different object, on normalised keys."""
    sql = (f"SELECT {_COLUMNS} FROM beliefs WHERE belief_key(subject) = ? AND belief_key(predicate) = ? "
           "AND belief_key(object) != ? AND id != ? AND status IN (?, ?) ORDER BY rowid")
    return [_belief(r) for r in conn.execute(sql, (_key(b.subject), _key(b.predicate), _key(b.object), b.id, *LIVE))]


def _clamp(confidence: float) -> float:
    if isinstance(confidence, bool) or not isinstance(confidence, numbers.Real):
        raise TypeError(f"confidence must be a number, got {type(confidence).__name__}")
    try:
        x = float(confidence)
    except OverflowError:  # an int too big for a float is still clearly above 1 or below 0
        return 1.0 if confidence > 0 else 0.0
    if math.isnan(x):
        raise ValueError("confidence is NaN")
    return min(1.0, max(0.0, x))


def _belief(row: tuple) -> Belief:
    return Belief(*row)


def _now() -> str:
    return datetime.now(UTC).isoformat()
