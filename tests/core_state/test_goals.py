"""Exam for CS4 (goals), written from the spec alone.

Spec: add_goal(title, owner: "user" | "tanishi", parent_id=None, rank=0.5) -> Goal;
active_goals() -> list[Goal], user goals first, then by rank.
Acceptance: a tanishi-owned goal never ranks above any active user goal (property test);
goals form a tree and a cycle is rejected.

Calls go through the default Core State database (TANISHI_CORE_STATE_DB, set to a temp path
by tests/conftest.py). The spec gives no connection argument.
"""
import random
import sqlite3

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.goals import active_goals, add_goal


@pytest.fixture
def raw():
    """A second connection to the same database, for state the interface cannot reach."""
    c = open_db()
    migrate(c)
    yield c
    c.close()


def _set_status(raw, goal_id, status):
    raw.execute("UPDATE goals SET status = ? WHERE id = ?", (status, goal_id))
    raw.commit()


def _ids(goals):
    return [g.id for g in goals]


def _parent(raw, goal_id):
    return raw.execute("SELECT parent_id FROM goals WHERE id = ?", (goal_id,)).fetchone()[0]


# ---- add_goal ------------------------------------------------------------------------------

def test_add_goal_returns_goal_with_fields():
    g = add_goal("Ship CS4", "user", rank=0.3)
    assert g.id
    assert g.title == "Ship CS4"
    assert g.owner == "user"
    assert g.parent_id is None
    assert g.rank == pytest.approx(0.3)


def test_default_rank_is_half():
    assert add_goal("a", "user").rank == pytest.approx(0.5)


def test_ids_are_unique():
    ids = {add_goal(f"g{i}", "user").id for i in range(25)}
    assert len(ids) == 25


def test_new_goal_is_active_and_listed():
    g = add_goal("a", "tanishi")
    assert _ids(active_goals()) == [g.id]


def test_goal_persists_in_goals_table(raw):
    g = add_goal("persist me", "tanishi", rank=0.7)
    row = raw.execute("SELECT parent_id, owner, title, rank FROM goals WHERE id = ?", (g.id,)).fetchone()
    assert tuple(row) == (None, "tanishi", "persist me", pytest.approx(0.7))


def test_goal_lands_in_the_configured_database(tmp_path, monkeypatch):
    other = tmp_path / "other.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(other))
    g = add_goal("durable", "user")
    assert other.exists()
    assert _ids(active_goals()) == [g.id]


@pytest.mark.parametrize("owner", ["", "USER", "admin", "Tanishi", None, "user "])
def test_invalid_owner_rejected(owner):
    with pytest.raises(ValueError):
        add_goal("x", owner)
    assert active_goals() == []


def test_parent_must_exist():
    with pytest.raises((ValueError, KeyError, sqlite3.Error)):
        add_goal("orphan", "user", parent_id="no-such-goal")
    assert active_goals() == []


def test_empty_database_has_no_active_goals():
    assert active_goals() == []


# ---- tree ----------------------------------------------------------------------------------

def test_child_records_parent():
    p = add_goal("parent", "user")
    c = add_goal("child", "user", parent_id=p.id)
    assert c.parent_id == p.id
    assert {g.id: g.parent_id for g in active_goals()} == {p.id: None, c.id: p.id}


def test_deep_chain_is_allowed():
    prev = None
    for i in range(30):
        prev = add_goal(f"level {i}", "user", parent_id=prev.id if prev else None)
    assert len(active_goals()) == 30


def test_tanishi_sub_goal_under_user_goal_still_ranks_below_user_goals():
    p = add_goal("mine", "user", rank=0.9)
    c = add_goal("hers", "tanishi", parent_id=p.id, rank=0.0)
    other = add_goal("mine too", "user", rank=1.0)
    order = _ids(active_goals())
    assert order.index(c.id) > order.index(p.id)
    assert order.index(c.id) > order.index(other.id)


def test_self_parent_rejected(raw):
    g = add_goal("a", "user")
    with pytest.raises(sqlite3.Error):
        raw.execute("UPDATE goals SET parent_id = id WHERE id = ?", (g.id,))
        raw.commit()
    raw.rollback()
    assert _parent(raw, g.id) is None


def test_two_cycle_rejected(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "user", parent_id=a.id)
    with pytest.raises(sqlite3.Error):
        raw.execute("UPDATE goals SET parent_id = ? WHERE id = ?", (b.id, a.id))
        raw.commit()
    raw.rollback()
    assert _parent(raw, a.id) is None


def test_long_cycle_rejected(raw):
    chain = []
    for i in range(12):
        chain.append(add_goal(f"n{i}", "user", parent_id=chain[-1].id if chain else None))
    with pytest.raises(sqlite3.Error):
        raw.execute("UPDATE goals SET parent_id = ? WHERE id = ?", (chain[-1].id, chain[0].id))
        raw.commit()
    raw.rollback()
    assert _parent(raw, chain[0].id) is None


def test_cycle_across_owners_rejected(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "tanishi", parent_id=a.id)
    with pytest.raises(sqlite3.Error):
        raw.execute("UPDATE goals SET parent_id = ? WHERE id = ?", (b.id, a.id))
        raw.commit()
    raw.rollback()
    assert _parent(raw, a.id) is None


def test_legal_reparent_still_allowed(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "user")
    c = add_goal("c", "user", parent_id=a.id)
    raw.execute("UPDATE goals SET parent_id = ? WHERE id = ?", (b.id, c.id))
    raw.commit()
    assert _parent(raw, c.id) == b.id


# ---- ranking -------------------------------------------------------------------------------

def test_user_goals_come_before_tanishi_goals_whatever_the_rank():
    hers = add_goal("hers", "tanishi", rank=0.0)
    mine = add_goal("mine", "user", rank=1.0)
    assert _ids(active_goals()) == [mine.id, hers.id]


def test_user_goals_first_even_when_tanishi_added_first_and_extreme_rank():
    h = [add_goal(f"h{i}", "tanishi", rank=r) for i, r in enumerate([-1e9, 0.0, 1e9])]
    u = [add_goal(f"u{i}", "user", rank=r) for i, r in enumerate([-1e9, 0.5, 1e9])]
    order = _ids(active_goals())
    assert set(order[:3]) == {g.id for g in u}
    assert set(order[3:]) == {g.id for g in h}


def test_within_owner_goals_are_ordered_by_rank():
    ranks = [0.9, 0.1, 0.5, 0.3, 0.7]
    for owner in ("user", "tanishi"):
        for i, r in enumerate(ranks):
            add_goal(f"{owner}{i}", owner, rank=r)
    goals = active_goals()
    u = [g.rank for g in goals if g.owner == "user"]
    t = [g.rank for g in goals if g.owner == "tanishi"]
    assert sorted(u) == sorted(ranks)
    assert sorted(t) == sorted(ranks)
    # the spec says "by rank" without a direction: either is fine, but it must be sorted and shared
    assert u == sorted(u) or u == sorted(u, reverse=True)
    assert u == t


def test_inactive_user_goal_no_longer_outranks_tanishi(raw):
    mine = add_goal("mine", "user")
    hers = add_goal("hers", "tanishi")
    _set_status(raw, mine.id, "done")
    assert _ids(active_goals()) == [hers.id]


def test_inactive_goals_are_excluded(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "tanishi")
    c = add_goal("c", "user")
    _set_status(raw, a.id, "done")
    _set_status(raw, b.id, "dropped")
    assert _ids(active_goals()) == [c.id]


def test_active_goals_is_repeatable():
    for i in range(6):
        add_goal(f"g{i}", "user" if i % 2 else "tanishi", rank=i / 10)
    assert _ids(active_goals()) == _ids(active_goals())


def test_equal_ranks_do_not_break_owner_split():
    for i in range(4):
        add_goal(f"t{i}", "tanishi", rank=0.5)
        add_goal(f"u{i}", "user", rank=0.5)
    owners = [g.owner for g in active_goals()]
    assert owners == ["user"] * 4 + ["tanishi"] * 4


# ---- property tests (seeded random; Hypothesis is not a declared dependency) -----------------

@pytest.mark.parametrize("seed", range(20))
def test_property_tanishi_never_above_an_active_user_goal(seed, raw):
    rng = random.Random(seed)
    made = []
    for i in range(rng.randint(1, 25)):
        owner = rng.choice(["user", "tanishi"])
        parent = rng.choice(made).id if made and rng.random() < 0.4 else None
        rank = rng.choice([0.0, 0.5, 1.0, rng.random(), rng.uniform(-10, 10), rng.uniform(0, 1e6)])
        made.append(add_goal(f"g{i}", owner, parent_id=parent, rank=rank))
        if rng.random() < 0.2:
            _set_status(raw, rng.choice(made).id, rng.choice(["done", "dropped", "active"]))

    goals = active_goals()
    active_in_db = {r[0] for r in raw.execute("SELECT id FROM goals WHERE status = 'active'")}
    assert set(_ids(goals)) == active_in_db
    assert len(set(_ids(goals))) == len(goals)

    owners = [g.owner for g in goals]
    n_user = owners.count("user")
    assert owners == ["user"] * n_user + ["tanishi"] * (len(owners) - n_user)


@pytest.mark.parametrize("seed", range(10))
def test_property_every_goal_reaches_a_root(seed):
    rng = random.Random(1000 + seed)
    made = []
    for i in range(rng.randint(1, 30)):
        parent = rng.choice(made).id if made and rng.random() < 0.7 else None
        made.append(add_goal(f"g{i}", rng.choice(["user", "tanishi"]), parent_id=parent))
    parent_of = {g.id: g.parent_id for g in active_goals()}
    for gid in parent_of:
        seen, cur = set(), gid
        while cur is not None:
            assert cur not in seen
            seen.add(cur)
            cur = parent_of[cur]
