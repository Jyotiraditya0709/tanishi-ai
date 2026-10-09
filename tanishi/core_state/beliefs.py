"""Belief store with evidence (node CS3).

A belief is a (subject, predicate, object) triple with a confidence in [0, 1], a required source and a status.
Two live beliefs with the same subject and predicate but a different object contradict each other: both are kept,
both are marked contested, and a `belief_conflict` event is written. Nothing is ever deleted; retire() changes status.

Every call opens the Core State database (TANISHI_CORE_STATE_DB, decision 0003), migrates it, and closes it again.
The contradiction check and the insert share one BEGIN IMMEDIATE transaction, so two writers cannot both miss each other.
"""
from __future__ import annotations

import json
import math
import numbers
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from tanishi.core_state.db import migrate, open_db

ACTIVE = "active"
CONTESTED = "contested"
RETIRED = "retired"
LIVE = (ACTIVE, CONTESTED)
CONFLICT_EVENT = "belief_conflict"
RETIRE_EVENT = "belief_retired"

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


def add_belief(
    subject: str,
    predicate: str,
    object: str,
    confidence: float,
    source: str,
    evidence_event_id: int | None = None,
) -> Belief:
    """Store a new belief. If a live belief disagrees with it, both become contested and a conflict event is written."""
    with _connect() as conn:
        return _add(conn, str(uuid.uuid4()), subject, predicate, object, confidence, source, evidence_event_id)


def add_belief_if_absent(
    belief_id: str, subject: str, predicate: str, object: str, confidence: float, source: str
) -> Belief | None:
    """Like add_belief, under a caller-chosen id; returns None and writes nothing if that id exists (for importers)."""
    if not isinstance(belief_id, str) or not belief_id:
        raise ValueError("belief_id must be a non-empty string")
    with _connect() as conn:
        return _add(conn, belief_id, subject, predicate, object, confidence, source, None, skip_existing=True)


def find(subject: str | None = None, predicate: str | None = None, text: str | None = None) -> list[Belief]:
    """Beliefs of any status matching every given filter. `text` is a literal, case-insensitive (ASCII) substring
    of subject, predicate or object. No filter returns every belief."""
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
    with _connect() as conn:
        return [_belief(r) for r in conn.execute(sql + " ORDER BY rowid", args)]


def contradictions(belief: Belief) -> list[Belief]:
    """Live beliefs with the same subject and predicate and a different object. Empty if `belief` itself is not live."""
    with _connect() as conn:
        row = conn.execute("SELECT status FROM beliefs WHERE id = ?", (belief.id,)).fetchone()
        status = row[0] if row else belief.status
        if status not in LIVE:
            return []
        return _peers(conn, belief.id, belief.subject, belief.predicate, belief.object)


def retire(belief_id: str, reason: str) -> None:
    """Mark a belief retired and record why in a belief_retired event. The row and its evidence stay.

    Retiring an already retired belief changes nothing. A contested peer left with no live contradiction goes back
    to active. Raises LookupError for an unknown id and ValueError for a blank reason.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("retire() needs a reason")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(f"SELECT {_COLUMNS} FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
            if row is None:
                raise LookupError(f"no belief {belief_id!r}")
            b = _belief(row)
            if b.status in LIVE:
                now = _now()
                conn.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?", (RETIRED, now, b.id))
                _emit(conn, now, RETIRE_EVENT, {"belief_id": b.id, "reason": reason})
                for peer in _peers(conn, b.id, b.subject, b.predicate, b.object):
                    if peer.status == CONTESTED and not _peers(conn, peer.id, peer.subject, peer.predicate, peer.object):
                        conn.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?", (ACTIVE, now, peer.id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


# ---------------------------------------------------------------- internals


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = open_db()
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


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
    conf = _clamp(confidence)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if skip_existing and conn.execute("SELECT 1 FROM beliefs WHERE id = ?", (belief_id,)).fetchone():
            conn.rollback()
            return None
        peers = _peers(conn, belief_id, subject, predicate, obj)
        status = CONTESTED if peers else ACTIVE
        now = _now()
        conn.execute(
            f"INSERT INTO beliefs({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (belief_id, subject, predicate, obj, conf, source, status, now, now),
        )
        if evidence_event_id is not None:
            conn.execute(
                "INSERT INTO evidence(id, belief_id, event_id, kind, note) VALUES (?, ?, ?, 'support', NULL)",
                (str(uuid.uuid4()), belief_id, evidence_event_id),
            )
        if peers:
            for p in peers:
                if p.status != CONTESTED:
                    conn.execute("UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?", (CONTESTED, now, p.id))
            # Ids only: belief text may be personal and events are an audit log (CLAUDE.md).
            _emit(conn, now, CONFLICT_EVENT, {"belief_id": belief_id, "conflicts_with": [p.id for p in peers]})
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return Belief(belief_id, subject, predicate, obj, conf, source, status, now, now)


def _emit(conn: sqlite3.Connection, ts: str, kind: str, payload: dict) -> None:
    """Write an event row inside the caller's transaction. CS2's emit() will own the hash chain; until then it is unhashed."""
    conn.execute(
        "INSERT INTO events(ts, kind, actor, session_id, payload) VALUES (?, ?, 'core_state.beliefs', NULL, ?)",
        (ts, kind, json.dumps(payload)),
    )


def _peers(conn: sqlite3.Connection, belief_id: str, subject: str, predicate: str, obj: str) -> list[Belief]:
    sql = (f"SELECT {_COLUMNS} FROM beliefs WHERE subject = ? AND predicate = ? AND object != ? AND id != ? "
           "AND status IN (?, ?) ORDER BY rowid")
    return [_belief(r) for r in conn.execute(sql, (subject, predicate, obj, belief_id, *LIVE))]


def _clamp(confidence: float) -> float:
    if isinstance(confidence, bool) or not isinstance(confidence, numbers.Real):
        raise TypeError(f"confidence must be a number, got {type(confidence).__name__}")
    x = float(confidence)
    if math.isnan(x):
        raise ValueError("confidence is NaN")
    return min(1.0, max(0.0, x))


def _belief(row: tuple) -> Belief:
    return Belief(*row)


def _now() -> str:
    return datetime.now(UTC).isoformat()
