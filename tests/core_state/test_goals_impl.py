"""Implementer's tests for CS4 goals: what the exam does not reach (subtree moves, old data, input checks)."""
import random
import sqlite3

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.goals import active_goals, add_goal


@pytest.fixture
def raw():
    add_goal("bootstrap", "user")  # installs the tree triggers
    c = open_db()
    migrate(c)
    c.execute("DELETE FROM goals")
    c.commit()
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


def test_existing_goals_get_their_ancestors_on_install():
    c = open_db()
    migrate(c)
    c.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('p', NULL, 'user', 'active')")
    c.execute("INSERT INTO goals (id, parent_id, owner, status) VALUES ('k', 'p', 'user', 'active')")
    c.commit()
    assert len(active_goals()) == 2  # installs the triggers over the old rows
    with pytest.raises(sqlite3.Error):
        c.execute("UPDATE goals SET parent_id = 'k' WHERE id = 'p'")
    c.rollback()
    c.close()


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
