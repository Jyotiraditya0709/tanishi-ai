"""Goals: the user's goal tree and Tanishi's own goals in one ranked table (node CS4).

The user's goals always come first. Within one owner a higher rank comes first (decision 0008).
The tree rules (no cycle, no self-parent, the parent must exist) are triggers in the database, so raw
SQL cannot break them either. They are installed here, not by a migration, because the CS1 exam pins
the schema version at 1 (open-problems/CS4.md).
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from tanishi.core_state.db import _statements, migrate, open_db

Owner = Literal["user", "tanishi"]
OWNERS: tuple[str, ...] = ("user", "tanishi")
ACTIVE = "active"

_COLUMNS = "id, title, owner, parent_id, rank, status, created_at"
# rowid breaks ties between equal ranks, so the order is the same on every call.
_ACTIVE_QUERY = (
    f"SELECT {_COLUMNS} FROM goals WHERE status = ? "
    "ORDER BY owner = 'user' DESC, rank DESC, created_at, rowid"
)

# SQLite does not allow WITH inside a trigger, so a trigger cannot walk the tree. goal_ancestors holds
# every (goal, ancestor) pair, the goal itself included, and the triggers keep it in step with parent_id.
_TREE_SQL = """
CREATE TABLE goal_ancestors (
    goal_id TEXT NOT NULL,
    ancestor_id TEXT NOT NULL,
    PRIMARY KEY (goal_id, ancestor_id)
) WITHOUT ROWID;

CREATE INDEX goal_ancestors_ancestor ON goal_ancestors(ancestor_id, goal_id);

-- Goals that exist already (UNION, not UNION ALL, so even a cycle in old data terminates).
INSERT INTO goal_ancestors(goal_id, ancestor_id)
WITH RECURSIVE up(goal_id, ancestor_id) AS (
    SELECT id, id FROM goals
    UNION
    SELECT up.goal_id, g.parent_id FROM up JOIN goals g ON g.id = up.ancestor_id WHERE g.parent_id IS NOT NULL
)
SELECT goal_id, ancestor_id FROM up;

CREATE TRIGGER goals_check_insert BEFORE INSERT ON goals
BEGIN
    SELECT RAISE(ABORT, 'goal cannot be its own parent')
        WHERE NEW.parent_id = NEW.id;
    SELECT RAISE(ABORT, 'goal parent does not exist')
        WHERE NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM goals WHERE id = NEW.parent_id);
    -- A row already pointing at this new id would close a loop the ancestor table never saw.
    SELECT RAISE(ABORT, 'goal id already has children')
        WHERE EXISTS (SELECT 1 FROM goals WHERE parent_id = NEW.id);
END;

CREATE TRIGGER goals_tree_insert AFTER INSERT ON goals
BEGIN
    INSERT INTO goal_ancestors(goal_id, ancestor_id) VALUES (NEW.id, NEW.id);
    INSERT INTO goal_ancestors(goal_id, ancestor_id)
        SELECT NEW.id, ancestor_id FROM goal_ancestors WHERE goal_id = NEW.parent_id;
END;

CREATE TRIGGER goals_id_fixed BEFORE UPDATE OF id ON goals WHEN NEW.id IS NOT OLD.id
BEGIN
    SELECT RAISE(ABORT, 'goal id cannot change');
END;

-- The (goal, goal) row makes a self-parent a cycle too.
CREATE TRIGGER goals_check_parent BEFORE UPDATE OF parent_id ON goals WHEN NEW.parent_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'goal parent does not exist')
        WHERE NOT EXISTS (SELECT 1 FROM goals WHERE id = NEW.parent_id);
    SELECT RAISE(ABORT, 'goal parent would make a cycle')
        WHERE EXISTS (SELECT 1 FROM goal_ancestors WHERE goal_id = NEW.parent_id AND ancestor_id = NEW.id);
END;

-- Move the subtree: cut it from its old ancestors, then hang it under the new parent's.
CREATE TRIGGER goals_tree_reparent AFTER UPDATE OF parent_id ON goals WHEN NEW.parent_id IS NOT OLD.parent_id
BEGIN
    DELETE FROM goal_ancestors
        WHERE goal_id IN (SELECT goal_id FROM goal_ancestors WHERE ancestor_id = NEW.id)
          AND ancestor_id IN (SELECT ancestor_id FROM goal_ancestors WHERE goal_id = NEW.id AND ancestor_id != NEW.id);
    INSERT INTO goal_ancestors(goal_id, ancestor_id)
        SELECT sub.goal_id, sup.ancestor_id
        FROM goal_ancestors sub, goal_ancestors sup
        WHERE sub.ancestor_id = NEW.id AND sup.goal_id = NEW.parent_id;
END;

CREATE TRIGGER goals_tree_delete AFTER DELETE ON goals
BEGIN
    DELETE FROM goal_ancestors WHERE goal_id = OLD.id OR ancestor_id = OLD.id;
END;
"""
_TREE_OBJECTS = (
    "goal_ancestors", "goal_ancestors_ancestor", "goals_check_insert", "goals_tree_insert",
    "goals_id_fixed", "goals_check_parent", "goals_tree_reparent", "goals_tree_delete",
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


def _tree_installed(conn: sqlite3.Connection) -> bool:
    marks = ",".join("?" * len(_TREE_OBJECTS))
    found = conn.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE name IN ({marks})", _TREE_OBJECTS).fetchone()[0]
    if 0 < found < len(_TREE_OBJECTS):
        raise RuntimeError("goal tree triggers are partly missing; refusing to trust the goals table")
    return found == len(_TREE_OBJECTS)


def _install_tree(conn: sqlite3.Connection) -> None:
    if _tree_installed(conn):
        return
    # IMMEDIATE takes the write lock first, so two processes installing at once do it once.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _tree_installed(conn):
            for stmt in _statements(_TREE_SQL):
                conn.execute(stmt)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _connect() -> sqlite3.Connection:
    conn = open_db()
    try:
        migrate(conn)
        _install_tree(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def add_goal(title: str, owner: Owner, parent_id: str | None = None, rank: float = 0.5) -> Goal:
    """Add an active goal. Raises ValueError for a bad owner, title or rank, or a parent that does not exist."""
    if not isinstance(owner, str) or owner not in OWNERS:
        raise ValueError(f"owner must be one of {OWNERS}, got {owner!r}")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if isinstance(rank, bool) or not isinstance(rank, (int, float)) or not math.isfinite(rank):
        raise ValueError(f"rank must be a finite number, got {rank!r}")
    goal = Goal(
        id=uuid.uuid4().hex,
        title=title,
        owner=owner,
        parent_id=parent_id,
        rank=float(rank),
        status=ACTIVE,
        created_at=datetime.now(UTC).isoformat(),
    )
    with closing(_connect()) as conn, conn:
        if parent_id is not None and conn.execute("SELECT 1 FROM goals WHERE id = ?", (parent_id,)).fetchone() is None:
            raise ValueError(f"parent goal {parent_id!r} does not exist")
        conn.execute(
            f"INSERT INTO goals ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (goal.id, goal.title, goal.owner, goal.parent_id, goal.rank, goal.status, goal.created_at),
        )
    return goal


def active_goals() -> list[Goal]:
    """Every active goal: all of the user's first, then Tanishi's; within each, highest rank first."""
    with closing(_connect()) as conn:
        return [Goal(*row) for row in conn.execute(_ACTIVE_QUERY, (ACTIVE,))]
