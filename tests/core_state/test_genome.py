"""Exam for CS6 (genome record), written from the spec alone.

Spec: record_version(version, parent, genes_changed, compiler_version, substrate_version,
arena, attribution, mirror_id) -> None writes one machine-readable record per version into the
Core State `genome` table (version, parent, created_at, record JSON text). Records are
append-only; a version can be written once.

The interface takes no connection, so the function finds the database the way the rest of the
Core State does: TANISHI_CORE_STATE_DB (tests/conftest.py points it at a temp file).
Assumption (the spec names the fields but not the JSON layout): `record` is a JSON object whose
keys are the parameter names. The "merge hook writes one record" line lives in OBS3/CI and is
not testable from this node's files.
"""
import json
import random
import sqlite3

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.genome import record_version

# The spec does not name the error; any of these means "rejected".
REJECTED = (sqlite3.Error, ValueError, TypeError, KeyError)
ARENA = {"pass_rate": 0.75, "tasks": 12, "slices": ["cs", "obs"]}
ATTRIBUTION = {"agent": "builder-1", "node": "CS6"}


def write(version="v1", parent=None, **over):
    args = {
        "version": version,
        "parent": parent,
        "genes_changed": ["gene.a", "gene.b"],
        "compiler_version": "c1",
        "substrate_version": "s1",
        "arena": ARENA,
        "attribution": ATTRIBUTION,
        "mirror_id": "mirror-1",
    }
    args.update(over)
    return record_version(**args)


def rows():
    """Read through a fresh connection, so uncommitted writes are invisible."""
    c = open_db()
    try:
        migrate(c)
        return c.execute("SELECT version, parent, created_at, record FROM genome ORDER BY rowid").fetchall()
    finally:
        c.close()


def record_of(version):
    for v, _p, _t, rec in rows():
        if v == version:
            return json.loads(rec)
    raise AssertionError(f"no row for {version}")


def test_returns_none():
    assert write() is None


def test_writes_one_committed_row():
    write("v1")
    r = rows()
    assert len(r) == 1
    version, parent, created_at, record = r[0]
    assert version == "v1"
    assert parent is None
    assert created_at  # non-empty timestamp
    assert isinstance(json.loads(record), dict)


def test_record_holds_every_field():
    write("v1")
    rec = record_of("v1")
    assert rec["genes_changed"] == ["gene.a", "gene.b"]
    assert rec["compiler_version"] == "c1"
    assert rec["substrate_version"] == "s1"
    assert rec["arena"] == ARENA
    assert rec["attribution"] == ATTRIBUTION
    assert rec["mirror_id"] == "mirror-1"


def test_mirror_id_none_is_kept_as_null():
    write("v1", mirror_id=None)
    assert record_of("v1")["mirror_id"] is None


def test_empty_genes_changed_is_allowed():
    write("v1", genes_changed=[])
    assert record_of("v1")["genes_changed"] == []


def test_genes_order_preserved():
    genes = ["z", "a", "m", "b"]
    write("v1", genes_changed=genes)
    assert record_of("v1")["genes_changed"] == genes


def test_parent_chain():
    write("v1")
    write("v2", parent="v1")
    write("v3", parent="v2")
    assert [(v, p) for v, p, _, _ in rows()] == [("v1", None), ("v2", "v1"), ("v3", "v2")]


def test_branching_children_share_a_parent():
    write("v1")
    write("v2a", parent="v1")
    write("v2b", parent="v1")
    assert {v for v, p, _, _ in rows() if p == "v1"} == {"v2a", "v2b"}


def test_unknown_parent_is_rejected_and_writes_nothing():
    with pytest.raises(REJECTED):
        write("v2", parent="never-written")
    assert rows() == []


# --- append-only: a version can be written once -------------------------------------------

def test_second_write_of_same_version_raises():
    write("v1")
    with pytest.raises(REJECTED):
        write("v1")
    assert len(rows()) == 1


def test_rewrite_with_different_data_does_not_change_the_original():
    write("v1")
    before = rows()
    with pytest.raises(REJECTED):
        write("v1", genes_changed=["other"], compiler_version="c2", arena={"x": 1}, mirror_id=None)
    assert rows() == before


def test_rewrite_with_identical_data_still_raises():
    write("v1")
    with pytest.raises(REJECTED):
        write("v1")


def test_rewrite_of_a_parent_does_not_disturb_children():
    write("v1")
    write("v2", parent="v1")
    before = rows()
    with pytest.raises(REJECTED):
        write("v1", parent=None, genes_changed=["x"])
    assert rows() == before


def test_version_is_case_sensitive():
    write("v1")
    write("V1")
    assert len(rows()) == 2


# --- bad input leaves no partial row --------------------------------------------------------

@pytest.mark.parametrize("over", [
    {"arena": {"bad": object()}},
    {"attribution": {"bad": {1, 2}}},
    {"arena": {"nan": float("nan")}},
])
def test_unserialisable_or_non_json_values_are_rejected(over):
    with pytest.raises(REJECTED):
        write("v1", **over)
    assert rows() == []


@pytest.mark.parametrize("version", ["", None])
def test_blank_version_is_rejected(version):
    with pytest.raises(REJECTED):
        write(version)
    assert rows() == []


def test_failed_write_does_not_burn_the_version():
    with pytest.raises(REJECTED):
        write("v1", arena={"bad": object()})
    write("v1")
    assert len(rows()) == 1


# --- fidelity -------------------------------------------------------------------------------

def test_float_and_unicode_survive_round_trip():
    arena = {"score": 1.0, "name": "naïve – 日本語", "nested": {"k": [1, 2.5, None, True]}}
    write("v1", arena=arena, attribution={"who": "агент"})
    rec = record_of("v1")
    assert rec["arena"] == arena
    assert isinstance(rec["arena"]["score"], float)  # decision 0006: JSON in TEXT keeps 1.0
    assert rec["attribution"] == {"who": "агент"}


def test_record_column_is_text():
    write("v1")
    c = open_db()
    try:
        assert c.execute("SELECT typeof(record) FROM genome").fetchone()[0] == "text"
    finally:
        c.close()


def test_caller_mutation_after_write_does_not_change_the_record():
    arena = {"n": 1}
    genes = ["a"]
    write("v1", arena=arena, genes_changed=genes)
    arena["n"] = 2
    genes.append("b")
    rec = record_of("v1")
    assert rec["arena"] == {"n": 1}
    assert rec["genes_changed"] == ["a"]


def test_works_on_a_fresh_database_without_prior_migrate(tmp_path, monkeypatch):
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "fresh" / "core.db"))
    write("v1")  # must create/migrate the database itself
    assert [v for v, *_ in rows()] == ["v1"]


def test_uses_only_the_configured_database(tmp_path, monkeypatch):
    target = tmp_path / "chosen.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(target))
    write("v1")
    c = sqlite3.connect(str(target))
    try:
        assert c.execute("SELECT version FROM genome").fetchall() == [("v1",)]
    finally:
        c.close()


def test_writes_do_not_touch_other_tables():
    write("v1")
    c = open_db()
    try:
        for t in ("events", "beliefs", "evidence", "goals", "genes", "substrate_state"):
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    finally:
        c.close()


# --- property: a random history round-trips and stays append-only --------------------------

@pytest.mark.parametrize("seed", range(8))
def test_random_history_round_trips(seed):
    rng = random.Random(seed)
    written = {}
    order = []
    for i in range(rng.randint(1, 15)):
        version = f"v{i}-{rng.randrange(10**6)}"
        parent = rng.choice(order) if order and rng.random() < 0.9 else None
        genes = [f"g{rng.randrange(50)}" for _ in range(rng.randint(0, 5))]
        arena = {"score": rng.random(), "n": rng.randint(0, 100), "tags": [rng.choice("abc") for _ in range(3)]}
        mirror = rng.choice([None, f"m{rng.randrange(99)}"])
        write(version, parent=parent, genes_changed=genes, arena=arena, mirror_id=mirror)
        written[version] = (parent, genes, arena, mirror)
        order.append(version)

    snapshot = rows()
    assert [v for v, *_ in snapshot] == order
    for v, p, _t, rec in snapshot:
        parent, genes, arena, mirror = written[v]
        rec = json.loads(rec)
        assert p == parent
        assert rec["genes_changed"] == genes
        assert rec["arena"] == arena
        assert rec["mirror_id"] == mirror

    # idempotence of failure: every re-write raises and the table is unchanged
    for v in rng.sample(order, min(3, len(order))):
        with pytest.raises(REJECTED):
            write(v, genes_changed=["changed"])
    assert rows() == snapshot
