"""Implementer's tests for CS4 goals: what the exam does not reach (subtree moves, old data, input checks)."""
import random
import shutil
import sqlite3

import pytest

from tanishi.core_state import db, migrate, open_db
from tanishi.core_state.goals import MAX_LEVELS, active_goals, add_goal


@pytest.fixture
def raw():
    c = open_db()
    migrate(c)
    yield c
    c.close()


def _reparent(raw, goal_id, parent_id):
    raw.execute("UPDATE goals SET parent_id = ? WHERE id = ?", (parent_id, goal_id))
    raw.commit()


def test_higher_rank_comes_first_within_an_owner():
    low = add_goal("low", "user", rank=0.1)
    high = add_goal("high", "user", rank=0.9)
    assert [g.id for g in active_goals()] == [high.id, low.id]


@pytest.mark.parametrize("rank", [float("nan"), float("inf"), "0.5", None, True])
def test_bad_rank_rejected(rank):
    with pytest.raises(ValueError):
        add_goal("x", "user", rank=rank)


@pytest.mark.parametrize("title", ["", "   ", None, 3])
def test_bad_title_rejected(title):
    with pytest.raises(ValueError):
        add_goal(title, "user")


def test_cycle_through_a_moved_subtree_rejected(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "user", parent_id=a.id)
    c = add_goal("c", "user")
    d = add_goal("d", "user", parent_id=c.id)
    _reparent(raw, c.id, b.id)  # a > b > c > d
    with pytest.raises(sqlite3.Error):
        _reparent(raw, a.id, d.id)
    raw.rollback()
    _reparent(raw, c.id, None)  # cut c off: a > b, c > d
    _reparent(raw, a.id, d.id)  # now legal: c > d > a > b


def test_raw_insert_cannot_self_parent_or_dangle(raw):
    with pytest.raises(sqlite3.Error):
        raw.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('x', 'x', 'user', 'active')")
    raw.rollback()
    raw.execute("PRAGMA foreign_keys = OFF")
    with pytest.raises(sqlite3.Error):
        raw.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('y', 'nope', 'user', 'active')")
    raw.rollback()


def test_goal_id_cannot_change(raw):
    g = add_goal("a", "user")
    with pytest.raises(sqlite3.Error):
        raw.execute("UPDATE goals SET id = 'new' WHERE id = ?", (g.id,))
    raw.rollback()


def _at_version_1(tmp_path, monkeypatch):
    """A db migrated with 0001 only, as before CS4. The patch is undone, so the next migrate() applies 0002."""
    only_first = tmp_path / "migrations"
    only_first.mkdir()
    shutil.copy(db.MIGRATIONS_DIR / "0001_init.sql", only_first)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", only_first)
    c = open_db()
    assert migrate(c) == 1
    monkeypatch.undo()
    return c


def test_migration_fills_ancestors_for_existing_goals(tmp_path, monkeypatch):
    c = _at_version_1(tmp_path, monkeypatch)
    c.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('p', NULL, 'user', 'active')")
    c.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('k', 'p', 'user', 'active')")
    c.commit()
    assert migrate(c) == 2
    assert set(c.execute("SELECT goal_id, ancestor_id FROM goal_ancestors")) == {("p", "p"), ("k", "k"), ("k", "p")}
    with pytest.raises(sqlite3.Error):
        c.execute("UPDATE goals SET parent_id = 'k' WHERE id = 'p'")
    c.rollback()
    c.close()


def test_migration_replaces_objects_the_old_goals_module_installed(tmp_path, monkeypatch):
    """Before 0002, goals.py created goal_ancestors itself. A stale copy must not survive the migration."""
    c = _at_version_1(tmp_path, monkeypatch)
    c.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('p', NULL, 'user', 'active')")
    c.execute("CREATE TABLE goal_ancestors (goal_id TEXT NOT NULL, ancestor_id TEXT NOT NULL)")
    c.execute("INSERT INTO goal_ancestors VALUES ('ghost', 'ghost')")
    c.execute("CREATE TRIGGER goals_tree_delete AFTER DELETE ON goals BEGIN SELECT 1; END")
    c.commit()
    assert migrate(c) == 2
    assert list(c.execute("SELECT goal_id, ancestor_id FROM goal_ancestors")) == [("p", "p")]
    c.close()


def test_goals_module_needs_only_migrate():
    add_goal("a", "user")
    c = open_db()
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    c.close()


def test_owner_cannot_change(raw):
    g = add_goal("hers", "tanishi")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE goals SET owner = 'user' WHERE id = ?", (g.id,))
    raw.rollback()
    raw.execute("UPDATE goals SET owner = 'tanishi', title = 'same owner' WHERE id = ?", (g.id,))
    raw.commit()


def test_replace_cannot_change_owner(raw):
    """REPLACE deletes and re-inserts, so no UPDATE trigger fires. The leftover ancestor row still stops it."""
    g = add_goal("hers", "tanishi")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT OR REPLACE INTO goals (id, owner, title, rank, status) VALUES (?, 'user', 'x', 1, 'active')",
            (g.id,),
        )
    raw.rollback()
    assert [x.owner for x in active_goals()] == ["tanishi"]


@pytest.mark.parametrize("owner", ["USER", "Tanishi", "user ", "admin", "", None])
def test_replace_with_a_bad_owner_is_refused(raw, owner):
    g = add_goal("mine", "user")
    for gid in (g.id, "fresh"):  # an existing id and a new one
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT OR REPLACE INTO goals (id, owner, title, rank, status) VALUES (?, ?, 'x', 1, 'active')",
                (gid, owner),
            )
        raw.rollback()
    assert [(x.id, x.owner) for x in active_goals()] == [(g.id, "user")]


def _chain(n):
    ids, parent = [], None
    for i in range(n):
        parent = add_goal(f"level {i}", "user", parent_id=parent).id
        ids.append(parent)
    return ids


def test_tree_stops_at_max_levels(raw):
    chain = _chain(MAX_LEVELS)
    with pytest.raises(ValueError):
        add_goal("too deep", "user", parent_id=chain[-1])
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('x', ?, 'user', 'active')", (chain[-1],))
    raw.rollback()
    assert len(active_goals()) == MAX_LEVELS


def test_moving_a_subtree_respects_max_levels(raw):
    host = _chain(40)
    moved = _chain(30)  # moved[0] is a root with 29 levels below it
    with pytest.raises(sqlite3.IntegrityError):
        _reparent(raw, moved[0], host[34])  # host level 35: the deepest would land at 65
    raw.rollback()
    _reparent(raw, moved[0], host[33])  # host level 34: the deepest lands at exactly 64
    level = raw.execute("SELECT COUNT(*) FROM goal_ancestors WHERE goal_id = ?", (moved[-1],)).fetchone()[0]
    assert level == MAX_LEVELS


@pytest.mark.parametrize("seed", range(10))
def test_property_random_moves_never_make_a_cycle(seed, raw):
    rng = random.Random(seed)
    ids = [add_goal(f"g{i}", rng.choice(["user", "tanishi"])).id for i in range(12)]
    for _ in range(80):
        child, parent = rng.choice(ids), rng.choice(ids + [None])
        try:
            _reparent(raw, child, parent)
        except sqlite3.Error:
            raw.rollback()
    parent_of = dict(raw.execute("SELECT id, parent_id FROM goals"))
    for gid in parent_of:
        seen, cur = set(), gid
        while cur is not None:
            assert cur not in seen
            seen.add(cur)
            cur = parent_of[cur]
        # the ancestor table matches the real tree
        stored = {r[0] for r in raw.execute("SELECT ancestor_id FROM goal_ancestors WHERE goal_id = ?", (gid,))}
        assert stored == seen
