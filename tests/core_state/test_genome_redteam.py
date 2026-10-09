"""Red-team attacks on CS6 (genome record).

Each test asserts the CORRECT behaviour. Tests for open breaks are marked xfail(strict=True): the suite stays
green today, and the moment someone fixes the break the test XPASSes, strict mode fails the run, and the mark
must be removed. Report: build/memory/runs/redteam-CS6-20261009.md
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sqlite3
import time

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.genome import record_version

BREAK = pytest.mark.xfail(strict=True, reason="red-team break, see build/memory/runs/redteam-CS6-20261009.md")
REFUSALS = (ValueError, TypeError, sqlite3.Error)


def _rec(version="v1", parent=None, **over):
    args = {
        "version": version, "parent": parent, "genes_changed": ["g"], "compiler_version": "c1", "substrate_version": "s1",
        "arena": {"score": 1}, "attribution": {"g": 1.0}, "mirror_id": None,
    }
    args.update(over)
    record_version(**args)


def _rows():
    conn = open_db()
    try:
        migrate(conn)
        return conn.execute("SELECT version, parent, record FROM genome ORDER BY rowid").fetchall()
    finally:
        conn.close()


# G1 ---------------------------------------------------------------------------------------------------------
@BREAK
def test_g1_generator_genes_are_not_silently_dropped():
    """all() drains a generator, then list() sees nothing: the record claims no gene changed."""
    try:
        _rec(genes_changed=(g for g in ["memory", "planner"]))
    except REFUSALS:
        return  # refusing is acceptable
    assert json.loads(_rows()[0][2])["genes_changed"] == ["memory", "planner"]


# G2 ---------------------------------------------------------------------------------------------------------
@BREAK
def test_g2_self_parent_is_rejected():
    """SQLite checks the FK at end of statement, so a row may be its own parent: a cycle in the history."""
    with pytest.raises(REFUSALS):
        _rec("v1", parent="v1")
    assert _rows() == []


# G3 ---------------------------------------------------------------------------------------------------------
@BREAK
@pytest.mark.parametrize("variant", ["abc\n", " abc", "abc ", "abc\t"])
def test_g3_whitespace_variant_of_a_version_cannot_be_written_twice(variant):
    """`git rev-parse` output keeps its newline. A hook that retries with the stripped sha writes a second record."""
    try:
        _rec(variant)
    except REFUSALS:
        return  # rejecting padded versions up front is the fix
    with pytest.raises(sqlite3.IntegrityError):
        _rec("abc")


# G4 ---------------------------------------------------------------------------------------------------------
@BREAK
def test_g4_non_string_dict_keys_do_not_change_on_the_way_in():
    """json turns {1: 'x'} into {"1": 'x'}: what is read back is not what was written."""
    try:
        _rec(arena={1: "x"})
    except REFUSALS:
        return
    assert json.loads(_rows()[0][2])["arena"] == {1: "x"}


@BREAK
def test_g4b_tuples_do_not_turn_into_lists():
    try:
        _rec(attribution={"g": (1, 2)})
    except REFUSALS:
        return
    assert json.loads(_rows()[0][2])["attribution"] == {"g": (1, 2)}


# G5 ---------------------------------------------------------------------------------------------------------
@BREAK
@pytest.mark.parametrize("field,bad", [
    ("arena", [1, 2]), ("arena", "pass"), ("arena", None),
    ("attribution", ["x"]), ("attribution", 3),
    ("compiler_version", None), ("compiler_version", 7),
    ("substrate_version", {"v": 1}),
    ("mirror_id", 12),
])
def test_g5_field_types_follow_the_interface(field, bad):
    """arena/attribution are dicts, versions are strings, mirror_id is str | None. Nothing checks it."""
    with pytest.raises(REFUSALS):
        _rec(**{field: bad})
    assert _rows() == []


# G6 ---------------------------------------------------------------------------------------------------------
@BREAK
@pytest.mark.parametrize("genes", [[""], ["  "], ["a", "a"]])
def test_g6_genes_changed_has_no_blank_or_duplicate_names(genes):
    with pytest.raises(REFUSALS):
        _rec("v-genes", genes_changed=genes)


# G7 ---------------------------------------------------------------------------------------------------------
@BREAK
def test_g7_sql_cannot_rewrite_or_delete_history():
    """Known (known-bugs/cs6-genome-not-append-only-in-sql.md): genome has no triggers."""
    _rec("v1")
    conn = open_db()
    try:
        with pytest.raises(sqlite3.DatabaseError), conn:
            conn.execute("UPDATE genome SET record = '{}' WHERE version = 'v1'")
        with pytest.raises(sqlite3.DatabaseError), conn:
            conn.execute("DELETE FROM genome WHERE version = 'v1'")
    finally:
        conn.close()


@BREAK
def test_g7b_record_json_cannot_disagree_with_the_columns():
    """record.version / record.parent duplicate the columns; raw SQL can make them differ and nothing notices."""
    _rec("v1")
    _rec("v2", parent="v1")
    conn = open_db()
    try:
        with pytest.raises(sqlite3.DatabaseError), conn:
            conn.execute("UPDATE genome SET parent = NULL WHERE version = 'v2'")
    finally:
        conn.close()


# Attacks that hold (kept as regression tests) -------------------------------------------------------------------
def _worker(version):
    try:
        _rec(version)
        return "ok"
    except sqlite3.IntegrityError:
        return "dup"


def test_concurrent_writers_of_one_version_exactly_one_wins():
    open_db().close()
    with mp.get_context("spawn").Pool(6) as pool:
        results = pool.map(_worker, ["race"] * 6)
    assert sorted(results) == ["dup"] * 5 + ["ok"]
    assert len(_rows()) == 1


@pytest.mark.parametrize("value", ["\ud800", "v\x00x"])
def test_lone_surrogate_and_nul_in_version_leave_no_partial_row(value):
    try:
        _rec(value)
    except REFUSALS:
        pass
    rows = _rows()
    assert all(r[0] == value for r in rows) and len(rows) <= 1


def test_lone_surrogate_in_payload_writes_nothing():
    with pytest.raises(REFUSALS):
        _rec(arena={"note": "\ud800"})
    assert _rows() == []


def test_deep_nesting_and_cycles_write_nothing():
    deep: dict = {}
    cur = deep
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    cyc: dict = {}
    cyc["self"] = cyc
    for bad in (deep, cyc):
        with pytest.raises((ValueError, TypeError, RecursionError, sqlite3.Error)):
            _rec(arena=bad)
    assert _rows() == []


def test_mixed_type_keys_are_refused():
    with pytest.raises(REFUSALS):
        _rec(arena={1: "a", "1": "b"})
    assert _rows() == []


def test_a_rejected_parent_leaves_nothing_and_the_child_succeeds_after_the_parent():
    with pytest.raises(sqlite3.IntegrityError):
        _rec("child", parent="late")
    _rec("late")
    _rec("child", parent="late")
    assert [r[0] for r in _rows()] == ["late", "child"]


def test_sql_injection_in_strings_is_inert():
    _rec("v'); DROP TABLE genome;--", genes_changed=["x'--"], mirror_id="'; --")
    assert len(_rows()) == 1


def test_call_cost_is_bounded():
    """Every call reopens the db, re-hashes the migrations and re-checks them. Fine per merge; guard a blow-up."""
    t = time.perf_counter()
    for i in range(50):
        _rec(f"p{i}", parent=None if i == 0 else f"p{i - 1}")
    assert (time.perf_counter() - t) / 50 < 0.1
