"""Exam for CS7 (snapshots, restore, Continuity Test v0), written from the spec alone.

Spec (build/graph.yaml, node CS7):
  * snapshot() writes an encrypted (cryptography Fernet, key from env) and signed bundle of core_state.db,
    identity.yaml and the skills folder.
  * restore() into an empty home reproduces the database byte for byte (checksum compare).
  * The Continuity Test v0 asks 10 questions about her history and goals and passes only if the restored instance
    answers from memory. The question file lives in vault/, not here.

The spec gives no signatures, so this exam pins the smallest interface that makes the lines testable. Every item is
listed in build/memory/open-problems/CS7-exam-assumptions.md; if the implementer disagrees, say so there. Do not edit the tests.

  S1  tanishi.core_state.snapshot.snapshot(dest_dir) -> Path : writes ONE bundle file into dest_dir, returns its path.
      It snapshots the home at $TANISHI_HOME: core_state.db, identity.yaml (optional), skills/ (optional).
  S2  tanishi.core_state.snapshot.restore(bundle, home) -> None : home must be missing or empty. Anything wrong
      (key, signature, damage, non-empty home) raises, and then nothing is left in home.
  S3  Keys come from the environment: TANISHI_SNAPSHOT_KEY (a Fernet key) and TANISHI_SNAPSHOT_SIGNING_KEY
      (the signing secret). A missing key raises.
  S4  tanishi.core_state.continuity.run(questions_file, home, answerer) -> result with .passed (bool), .correct (int),
      .total (int). questions_file is JSON: a list of {"question": str, "expected": str}. answerer(question, home) -> str
      is the "restored instance". An answer is right if it contains `expected`, ignoring case. Exactly 10 questions
      (anything else is a ValueError). It passes only if all 10 are right.
"""
import hashlib
import json
import random
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tanishi.core_state import beliefs, db, goals
from tanishi.core_state import continuity as ct
from tanishi.core_state import snapshot as snap

REFUSED = Exception  # the spec names no error type: any refusal counts
MARKER = "zq-marker-7731-unique"


@pytest.fixture
def keys(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setenv("TANISHI_SNAPSHOT_KEY", k)
    monkeypatch.setenv("TANISHI_SNAPSHOT_SIGNING_KEY", "signing-secret-one")
    return k


@pytest.fixture
def home(tmp_path, monkeypatch, keys):
    h = tmp_path / "home_a"
    h.mkdir()
    monkeypatch.setenv("TANISHI_HOME", str(h))
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(h / "core_state.db"))
    return h


def fill(home: Path, n_beliefs=3):
    for i in range(n_beliefs):
        beliefs.add_belief(f"subj{i}", "pred", f"{MARKER}-{i}", 0.8, "test")
    goals.add_goal(f"goal {MARKER}", "user")
    (home / "identity.yaml").write_text(f"name: Test\nmotto: {MARKER}\n")
    (home / "skills").mkdir(exist_ok=True)
    (home / "skills" / "one.py").write_text(f"# {MARKER}\nprint(1)\n")
    (home / "skills" / "sub").mkdir(exist_ok=True)
    (home / "skills" / "sub" / "two.bin").write_bytes(bytes(range(256)))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def dest(tmp_path, name="dest"):
    d = tmp_path / name
    d.mkdir()
    return d


def test_snapshot_writes_one_file_and_returns_it(home, tmp_path):
    fill(home)
    d = dest(tmp_path)
    bundle = snap.snapshot(d)
    assert Path(bundle).is_file()
    assert Path(bundle).parent == d
    assert [p for p in d.iterdir()] == [Path(bundle)]
    assert Path(bundle).stat().st_size > 0


def test_bundle_is_encrypted_no_plaintext_anywhere(home, tmp_path, keys):
    fill(home)
    d = dest(tmp_path)
    snap.snapshot(d)
    for p in d.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            assert MARKER.encode() not in data
            assert b"SQLite format 3" not in data
            assert b"print(1)" not in data
            assert keys.encode() not in data
            assert b"signing-secret-one" not in data
        assert MARKER not in p.name


def test_restore_reproduces_db_byte_for_byte(home, tmp_path):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "home_b"
    snap.restore(bundle, target)
    # compare after snapshot(): snapshot may checkpoint the WAL, the file on disk is what was captured
    assert sha(target / "core_state.db") == sha(home / "core_state.db")


def test_restore_reproduces_identity_and_skills(home, tmp_path):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "home_b"
    snap.restore(bundle, target)
    assert (target / "identity.yaml").read_bytes() == (home / "identity.yaml").read_bytes()
    assert tree(target / "skills") == tree(home / "skills")


def test_restore_into_existing_empty_dir(home, tmp_path):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "empty"
    target.mkdir()
    snap.restore(bundle, target)
    assert sha(target / "core_state.db") == sha(home / "core_state.db")


def test_restored_db_opens_and_holds_the_data(home, tmp_path, latest_version):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "home_b"
    snap.restore(bundle, target)
    conn = db.open_db(str(target / "core_state.db"))
    try:
        assert db.migrate(conn) == latest_version
        objs = {r[0] for r in conn.execute("SELECT object FROM beliefs")}
        assert objs == {f"{MARKER}-{i}" for i in range(3)}
        assert conn.execute("SELECT count(*) FROM goals").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_snapshot_captures_committed_rows_still_in_the_wal(home, tmp_path):
    fill(home)
    reader = db.open_db(str(home / "core_state.db"))  # an open connection keeps the -wal file alive
    try:
        reader.execute("SELECT count(*) FROM beliefs").fetchone()
        beliefs.add_belief("late", "pred", "late-object", 0.5, "test")
        bundle = snap.snapshot(dest(tmp_path))
    finally:
        reader.close()
    target = tmp_path / "home_b"
    snap.restore(bundle, target)
    conn = sqlite3.connect(str(target / "core_state.db"))
    try:
        assert conn.execute("SELECT count(*) FROM beliefs WHERE object='late-object'").fetchone()[0] == 1
    finally:
        conn.close()


def test_snapshot_does_not_change_live_data(home, tmp_path):
    fill(home)
    before = sorted(b.id for b in beliefs.find())
    snap.snapshot(dest(tmp_path))
    assert sorted(b.id for b in beliefs.find()) == before
    assert (home / "identity.yaml").read_text().endswith(f"{MARKER}\n")


def test_missing_identity_and_skills_round_trip(home, tmp_path):
    beliefs.add_belief("a", "b", "c", 0.5, "test")
    assert not (home / "identity.yaml").exists()
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "home_b"
    snap.restore(bundle, target)
    assert sha(target / "core_state.db") == sha(home / "core_state.db")
    assert not (target / "identity.yaml").exists()


def test_two_destinations_both_restore(home, tmp_path):
    fill(home)
    b1 = snap.snapshot(dest(tmp_path, "place1"))
    b2 = snap.snapshot(dest(tmp_path, "place2"))
    assert Path(b1).parent != Path(b2).parent
    for i, b in enumerate((b1, b2)):
        t = tmp_path / f"r{i}"
        snap.restore(b, t)
        assert sha(t / "core_state.db") == sha(home / "core_state.db")


def test_snapshots_are_point_in_time(home, tmp_path):
    fill(home)
    first = snap.snapshot(dest(tmp_path, "d1"))
    beliefs.add_belief("later", "pred", "after-first", 0.5, "test")
    second = snap.snapshot(dest(tmp_path, "d2"))
    t1, t2 = tmp_path / "r1", tmp_path / "r2"
    snap.restore(first, t1)
    snap.restore(second, t2)
    def count(t):
        c = sqlite3.connect(str(t / "core_state.db"))
        try:
            return c.execute("SELECT count(*) FROM beliefs").fetchone()[0]
        finally:
            c.close()
    assert count(t2) == count(t1) + 1


def test_missing_key_raises_on_snapshot_and_restore(home, tmp_path, monkeypatch):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    monkeypatch.delenv("TANISHI_SNAPSHOT_KEY")
    with pytest.raises(REFUSED):
        snap.snapshot(dest(tmp_path, "d2"))
    with pytest.raises(REFUSED):
        snap.restore(bundle, tmp_path / "t")
    assert not (tmp_path / "t" / "core_state.db").exists()


def test_missing_signing_key_raises(home, tmp_path, monkeypatch):
    fill(home)
    monkeypatch.delenv("TANISHI_SNAPSHOT_SIGNING_KEY")
    with pytest.raises(REFUSED):
        snap.snapshot(dest(tmp_path))


def test_invalid_fernet_key_raises(home, tmp_path, monkeypatch):
    fill(home)
    monkeypatch.setenv("TANISHI_SNAPSHOT_KEY", "not-a-fernet-key")
    with pytest.raises(REFUSED):
        snap.snapshot(dest(tmp_path))


def test_wrong_encryption_key_refuses_and_leaves_nothing(home, tmp_path, monkeypatch):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    monkeypatch.setenv("TANISHI_SNAPSHOT_KEY", Fernet.generate_key().decode())
    target = tmp_path / "t"
    with pytest.raises(REFUSED):
        snap.restore(bundle, target)
    assert not target.exists() or not any(target.iterdir())


def test_wrong_signing_key_refuses_and_leaves_nothing(home, tmp_path, monkeypatch):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    monkeypatch.setenv("TANISHI_SNAPSHOT_SIGNING_KEY", "a-different-secret")
    target = tmp_path / "t"
    with pytest.raises(REFUSED):
        snap.restore(bundle, target)
    assert not target.exists() or not any(target.iterdir())


def test_any_single_byte_flip_is_detected(home, tmp_path):
    fill(home)
    bundle = Path(snap.snapshot(dest(tmp_path)))
    good = bundle.read_bytes()
    rng = random.Random(7)
    positions = {0, len(good) - 1, len(good) // 2} | {rng.randrange(len(good)) for _ in range(40)}
    for i, pos in enumerate(sorted(positions)):
        bad = bytearray(good)
        bad[pos] ^= 0x01
        evil = bundle.with_name(f"evil{i}.bundle")
        evil.write_bytes(bytes(bad))
        target = tmp_path / f"t{i}"
        with pytest.raises(REFUSED):
            snap.restore(evil, target)
        assert not target.exists() or not any(target.iterdir()), f"flip at {pos} left files behind"


def test_truncated_and_empty_bundles_are_refused(home, tmp_path):
    fill(home)
    bundle = Path(snap.snapshot(dest(tmp_path)))
    good = bundle.read_bytes()
    for i, data in enumerate((b"", good[:10], good[: len(good) // 2], good[:-1], good + b"x")):
        bad = bundle.with_name(f"cut{i}.bundle")
        bad.write_bytes(data)
        target = tmp_path / f"t{i}"
        with pytest.raises(REFUSED):
            snap.restore(bad, target)
        assert not target.exists() or not any(target.iterdir())


def test_missing_bundle_raises(home, tmp_path):
    with pytest.raises(REFUSED):
        snap.restore(tmp_path / "nope.bundle", tmp_path / "t")


def test_restore_refuses_a_non_empty_home_and_changes_nothing(home, tmp_path):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "busy"
    target.mkdir()
    (target / "core_state.db").write_bytes(b"precious")
    (target / "notes.txt").write_text("keep me")
    with pytest.raises(REFUSED):
        snap.restore(bundle, target)
    assert (target / "core_state.db").read_bytes() == b"precious"
    assert (target / "notes.txt").read_text() == "keep me"


def test_restore_is_repeatable_to_fresh_homes(home, tmp_path):
    fill(home)
    bundle = snap.snapshot(dest(tmp_path))
    a, b = tmp_path / "ra", tmp_path / "rb"
    snap.restore(bundle, a)
    snap.restore(bundle, b)
    assert tree(a) == tree(b)


def test_snapshot_never_opens_or_bundles_legacy_dbs(home, tmp_path):
    fill(home)
    (home / "tanishi.db").write_bytes(b"LEGACY-" + MARKER.encode())
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "t"
    snap.restore(bundle, target)
    assert not (target / "tanishi.db").exists()
    assert (home / "tanishi.db").read_bytes() == b"LEGACY-" + MARKER.encode()


def test_property_random_trees_round_trip_exactly(home, tmp_path):
    rng = random.Random(20261009)
    names = ["a.py", "b.txt", "dir/c.bin", "dir/deep/d.md", "ünï.txt", "e e.txt", "empty.dat"]
    for trial in range(12):
        skills = home / "skills"
        if skills.exists():
            for p in sorted(skills.rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            skills.rmdir()
        skills.mkdir()
        for n in rng.sample(names, rng.randint(0, len(names))):
            f = skills / n
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(rng.randbytes(rng.choice([0, 1, 17, 4096, 70000])))
        (home / "identity.yaml").write_bytes(rng.randbytes(rng.randint(0, 500)))
        beliefs.add_belief(f"s{trial}", "p", rng.randbytes(8).hex(), rng.random(), "prop")
        bundle = snap.snapshot(dest(tmp_path, f"d{trial}"))
        target = tmp_path / f"r{trial}"
        snap.restore(bundle, target)
        assert sha(target / "core_state.db") == sha(home / "core_state.db")
        assert (target / "identity.yaml").read_bytes() == (home / "identity.yaml").read_bytes()
        assert tree(target / "skills") == tree(skills)


def test_ciphertext_differs_between_snapshots_of_same_state(home, tmp_path):
    fill(home)
    b1 = Path(snap.snapshot(dest(tmp_path, "d1"))).read_bytes()
    b2 = Path(snap.snapshot(dest(tmp_path, "d2"))).read_bytes()
    assert b1 != b2  # fresh IV each time; equal bundles would leak that nothing changed


# ---------------------------------------------------------------- Continuity Test v0

HISTORY = [(f"hist{i}", "happened", f"{MARKER}-event-{i}") for i in range(6)]
GOALS = [(f"{MARKER}-goal-{i}", 0.1 * (i + 1)) for i in range(4)]


def seed_history(home: Path):
    for s, p, o in HISTORY:
        beliefs.add_belief(s, p, o, 0.9, "test")
    for title, rank in GOALS:
        goals.add_goal(title, "user", rank=rank)


def questions() -> list[dict]:
    qs = [{"question": f"What happened in {s}?", "expected": o} for s, _p, o in HISTORY]
    qs += [{"question": f"What is your user goal at rank {r:.1f}?", "expected": t} for t, r in GOALS]
    return qs


def memory_answerer(question: str, home: Path) -> str:
    conn = sqlite3.connect(str(Path(home) / "core_state.db"))
    try:
        for s, _p, _o in HISTORY:
            if question == f"What happened in {s}?":
                row = conn.execute("SELECT object FROM beliefs WHERE subject=? AND status!='retired'", (s,)).fetchone()
                return row[0] if row else "I don't remember"
        for _t, r in GOALS:
            if question == f"What is your user goal at rank {r:.1f}?":
                row = conn.execute("SELECT title FROM goals WHERE owner='user' AND abs(rank-?)<1e-9", (r,)).fetchone()
                return row[0] if row else "I don't remember"
    finally:
        conn.close()
    return "I don't remember"


@pytest.fixture
def qfile(tmp_path):
    p = tmp_path / "questions.json"
    p.write_text(json.dumps(questions()))
    return p


def test_restored_instance_passes_continuity(home, tmp_path, qfile):
    seed_history(home)
    bundle = snap.snapshot(dest(tmp_path))
    target = tmp_path / "restored"
    snap.restore(bundle, target)
    r = ct.run(qfile, target, memory_answerer)
    assert r.total == 10
    assert r.correct == 10
    assert r.passed is True


def test_empty_instance_fails_continuity(home, tmp_path, qfile):
    conn = db.open_db(str(home / "core_state.db"))
    db.migrate(conn)
    conn.close()
    r = ct.run(qfile, home, memory_answerer)
    assert r.total == 10
    assert r.correct == 0
    assert r.passed is False


def test_snapshot_taken_before_history_fails_continuity(home, tmp_path, qfile):
    beliefs.add_belief("hist0", "happened", f"{MARKER}-event-0", 0.9, "test")
    bundle = snap.snapshot(dest(tmp_path))
    seed_history(home)  # later history is not in the snapshot
    target = tmp_path / "restored"
    snap.restore(bundle, target)
    r = ct.run(qfile, target, memory_answerer)
    assert r.passed is False
    assert r.correct < 10


def test_nine_of_ten_is_not_a_pass(home, tmp_path, qfile):
    seed_history(home)
    def forgetful(q, h):
        return "no idea" if "hist5" in q else memory_answerer(q, h)
    r = ct.run(qfile, home, forgetful)
    assert (r.correct, r.total, r.passed) == (9, 10, False)


def test_answers_not_from_memory_fail(home, tmp_path, qfile):
    seed_history(home)
    r = ct.run(qfile, home, lambda q, h: "I am an assistant with no history")
    assert r.passed is False
    assert r.correct == 0


def test_matching_ignores_case(home, tmp_path, qfile):
    seed_history(home)
    r = ct.run(qfile, home, lambda q, h: memory_answerer(q, h).upper())
    assert r.passed is True


def test_answerer_receives_each_question_and_the_home(home, tmp_path, qfile):
    seed_history(home)
    seen = []
    def spy(q, h):
        seen.append((q, Path(h)))
        return memory_answerer(q, h)
    ct.run(qfile, home, spy)
    assert [q for q, _ in seen] == [d["question"] for d in questions()]
    assert {h for _, h in seen} == {home}


@pytest.mark.parametrize("n", [0, 1, 9, 11, 20])
def test_question_count_must_be_exactly_ten(home, tmp_path, n):
    p = tmp_path / "q.json"
    p.write_text(json.dumps([{"question": f"q{i}", "expected": "x"} for i in range(n)]))
    with pytest.raises(ValueError):
        ct.run(p, home, lambda q, h: "x")


def test_bad_question_files_raise(home, tmp_path):
    with pytest.raises(OSError):
        ct.run(tmp_path / "missing.json", home, lambda q, h: "x")
    p = tmp_path / "q.json"
    p.write_text("not json {")
    with pytest.raises(ValueError):
        ct.run(p, home, lambda q, h: "x")
    p.write_text(json.dumps([{"question": f"q{i}"} for i in range(10)]))  # no "expected"
    with pytest.raises((ValueError, KeyError, TypeError)):
        ct.run(p, home, lambda q, h: "x")


def test_blank_expected_does_not_pass_for_free(home, tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps([{"question": f"q{i}", "expected": ""} for i in range(10)]))
    try:
        r = ct.run(p, home, lambda q, h: "anything")
    except ValueError:
        return
    assert r.passed is False


def test_question_file_is_not_shipped_in_the_repo():
    pkg = Path(snap.__file__).parent
    leaked = [p.name for p in pkg.rglob("*") if p.suffix in {".json", ".yaml", ".yml"} and "question" in p.name.lower()]
    assert leaked == []
