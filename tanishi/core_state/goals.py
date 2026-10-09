"""Goals: the user's goal tree and Tanishi's own goals in one ranked table (node CS4).

The user's goals always come first. Within one owner a higher rank comes first (decision 0010).
The tree rules (no cycle, no self-parent, the parent must exist, at most 64 levels) and the fixed owner
are triggers in migrations/0002_goal_tree.sql, so raw SQL cannot break them either. This module
installs nothing itself: migrate() does.
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from tanishi.core_state.db import migrate, open_db

Owner = Literal["user", "tanishi"]
OWNERS: tuple[str, ...] = ("user", "tanishi")
ACTIVE = "active"
MAX_LEVELS = 64  # a root is level 1; the 0002 triggers enforce the same number

_COLUMNS = "id, title, owner, parent_id, rank, status, created_at"
# rowid breaks ties between equal ranks, so the order is the same on every call.
_ACTIVE_QUERY = (
    f"SELECT {_COLUMNS} FROM goals WHERE status = ? "
    "ORDER BY owner = 'user' DESC, rank DESC, created_at, rowid"
)


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    owner: Owner
    parent_id: str | None
    rank: float
    status: str
    created_at: str


def _connect() -> sqlite3.Connection:
    conn = open_db()
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _finite_rank(rank: object) -> float:
    if isinstance(rank, bool) or not isinstance(rank, (int, float)):
        raise ValueError(f"rank must be a finite number, got {rank!r}")  # noqa: TRY004 - every bad rank is a ValueError (decision 0010)
    try:
        value = float(rank)
    except OverflowError:  # an int too big for a float, such as 10**400
        raise ValueError(f"rank must be a finite number, got an int of {rank.bit_length()} bits") from None
    if not math.isfinite(value):
        raise ValueError(f"rank must be a finite number, got {rank!r}")
    return value


def add_goal(title: str, owner: Owner, parent_id: str | None = None, rank: float = 0.5) -> Goal:
    """Add an active goal.

    Raises ValueError for a bad owner, title, rank or parent_id, a parent that does not exist, or a parent
    already at the deepest level (MAX_LEVELS).
    """
    if not isinstance(owner, str) or owner not in OWNERS:
        raise ValueError(f"owner must be one of {OWNERS}, got {owner!r}")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if parent_id is not None and not isinstance(parent_id, str):
        raise ValueError(f"parent_id must be a goal id string or None, got {type(parent_id).__name__}")
    goal = Goal(
        id=uuid.uuid4().hex,
        title=title,
        owner=owner,
        parent_id=parent_id,
        rank=_finite_rank(rank),
        status=ACTIVE,
        created_at=datetime.now(UTC).isoformat(),
    )
    with closing(_connect()) as conn, conn:
        if parent_id is not None:
            if conn.execute("SELECT 1 FROM goals WHERE id = ?", (parent_id,)).fetchone() is None:
                raise ValueError(f"parent goal {parent_id!r} does not exist")
            level = conn.execute("SELECT COUNT(*) FROM goal_ancestors WHERE goal_id = ?", (parent_id,)).fetchone()[0]
            if level >= MAX_LEVELS:
                raise ValueError(f"goal tree is limited to {MAX_LEVELS} levels; parent {parent_id!r} is at {level}")
        conn.execute(
            f"INSERT INTO goals ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (goal.id, goal.title, goal.owner, goal.parent_id, goal.rank, goal.status, goal.created_at),
        )
    return goal


def active_goals() -> list[Goal]:
    """Every active goal: all of the user's first, then Tanishi's; within each, highest rank first."""
    with closing(_connect()) as conn:
        return [Goal(*row) for row in conn.execute(_ACTIVE_QUERY, (ACTIVE,))]
