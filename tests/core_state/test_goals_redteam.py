"""Red-team proofs for CS4 goals. A test marked BREAK fails today and shows a real break."""
import sqlite3
import threading

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.goals import active_goals, add_goal


@pytest.fixture
def raw():
    add_goal("bootstrap", "user")
    c = open_db()
    migrate(c)
    c.execute("DELETE FROM goals")
    c.commit()
    yield c
    c.close()


def test_g1_huge_int_rank_is_a_value_error():  # BREAK: OverflowError escapes
    with pytest.raises(ValueError):
        add_goal("x", "user", rank=10**400)


def test_g2_tanishi_cannot_promote_itself_by_flipping_owner(raw):  # BREAK
    """Nothing stops an UPDATE owner='user' on her own goal, so she can enter the user's block."""
    g = add_goal("hers", "tanishi")
    try:
        raw.execute("UPDATE goals SET owner='user' WHERE id=?", (g.id,))
        raw.commit()
    except sqlite3.DatabaseError:
        return
    pytest.fail("owner of an existing goal can be changed from tanishi to user")


@pytest.mark.parametrize("owner", ["USER", " user", "admin", ""])
def test_g3_raw_insert_with_a_bad_owner_is_refused(raw, owner):
    """Only 'user' and 'tanishi' are owners, so no goal can ever sort above a real user goal."""
    u = add_goal("mine", "user", rank=-1e9)
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute(
            "INSERT INTO goals(id,owner,title,rank,status,created_at) VALUES('z',?,'t',1e9,'active','x')", (owner,)
        )
    raw.rollback()
    assert raw.execute("SELECT COUNT(*) FROM goals WHERE id='z'").fetchone()[0] == 0
    assert active_goals()[0].id == u.id


def test_g4_unhashable_parent_id_is_a_value_error():  # BREAK: sqlite raises ProgrammingError
    with pytest.raises(ValueError):
        add_goal("x", "user", parent_id=["a"])


def test_g5_delete_parent_with_children_refused(raw):
    p = add_goal("p", "user")
    add_goal("c", "user", parent_id=p.id)
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("DELETE FROM goals WHERE id=?", (p.id,))


def test_g6_replace_cannot_smuggle_a_cycle(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "user", parent_id=a.id)
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute(
            "INSERT OR REPLACE INTO goals(id,parent_id,owner,title,rank,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (a.id, b.id, "user", "a", 0.5, "active", "x"),
        )
    raw.commit()
    p = dict(raw.execute("SELECT id,parent_id FROM goals").fetchall())
    assert p[a.id] is None


def test_g7_reparent_to_null_keeps_ancestors_exact(raw):
    a = add_goal("a", "user")
    b = add_goal("b", "user", parent_id=a.id)
    c = add_goal("c", "user", parent_id=b.id)
    raw.execute("UPDATE goals SET parent_id=NULL WHERE id=?", (b.id,))
    raw.commit()
    got = set(raw.execute("SELECT goal_id, ancestor_id FROM goal_ancestors").fetchall())
    assert got == {(a.id, a.id), (b.id, b.id), (c.id, c.id), (c.id, b.id)}
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("UPDATE goals SET parent_id=? WHERE id=?", (c.id, b.id))


def test_g8_tree_is_capped_at_64_levels_so_the_ancestor_table_stays_small(raw):
    parent = None
    for i in range(64):
        parent = add_goal(f"g{i}", "user", parent_id=parent).id
    with pytest.raises(ValueError):
        add_goal("g64", "user", parent_id=parent)
    n = raw.execute("SELECT COUNT(*) FROM goal_ancestors").fetchone()[0]
    assert n <= 64 * 65 // 2


def test_g9_concurrent_first_use_installs_once():
    errs = []

    def go():
        try:
            add_goal("t", "user")
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert len(active_goals()) == 8


def test_g10_concurrent_reparent_cannot_make_cycle():
    a = add_goal("a", "user")
    b = add_goal("b", "user")

    def mv(x, y):
        c = open_db()
        try:
            c.execute("UPDATE goals SET parent_id=? WHERE id=?", (y, x))
            c.commit()
        except sqlite3.DatabaseError:
            pass
        finally:
            c.close()

    ts = [threading.Thread(target=mv, args=(a.id, b.id)), threading.Thread(target=mv, args=(b.id, a.id))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    c = open_db()
    rows = dict(c.execute("SELECT id,parent_id FROM goals").fetchall())
    c.close()
    assert not (rows[a.id] == b.id and rows[b.id] == a.id)


def test_g11_user_goal_under_tanishi_parent_still_sorts_by_owner():
    t = add_goal("hers", "tanishi", rank=1e9)
    u = add_goal("mine", "user", parent_id=t.id, rank=-1e9)
    assert [g.id for g in active_goals()] == [u.id, t.id]
