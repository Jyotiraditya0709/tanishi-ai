"""Red-team proofs for CS7 (snapshot, restore, Continuity Test v0).

Breaks are `xfail(strict=True)`: the suite stays green now; when a fix lands the test XPASSes, strict mode fails the
run, and the fixer removes the mark. Tests without a mark pin behaviour that held under attack.
"""
# ruff: noqa: F811 - fixtures imported from the exam shadow their own arguments
import json
import sqlite3
import threading

import pytest

from tanishi.core_state import beliefs
from tanishi.core_state import continuity as ct
from tanishi.core_state import snapshot as snap
from tests.core_state.test_snapshot import (  # noqa: F401
    MARKER,
    fill,
    home,
    keys,
    questions,
    seed_history,
)


def _dest(tmp_path, name="dest"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


def _raise(exc):
    def f(*_a, **_k):
        raise exc

    return f


def test_r1_every_snapshot_can_be_restored_even_with_odd_skill_names(home, tmp_path):
    fill(home)
    (home / "skills" / "a\\b.py").write_text("x = 1\n")
    bundle = snap.snapshot(_dest(tmp_path))
    snap.restore(bundle, tmp_path / "back")  # raises SnapshotError: "bad path in manifest"
    assert (tmp_path / "back" / "skills" / "a\\b.py").read_text() == "x = 1\n"


def test_r2_a_corrupt_database_is_not_snapshotted_as_if_fine(home, tmp_path):
    for i in range(400):
        beliefs.add_belief(f"s{i}", "p", f"{MARKER}-{i}" * 20, 0.8, "test")
    db_file = home / "core_state.db"
    c = sqlite3.connect(db_file)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    c.close()
    raw = bytearray(db_file.read_bytes())
    for off in range(8192, len(raw) - 4096, 4096):  # scribble inside data pages, leave the header alone
        raw[off + 100 : off + 140] = b"\xff" * 40
    db_file.write_bytes(bytes(raw))
    with pytest.raises(Exception):  # noqa: B017 - any refusal counts
        snap.snapshot(_dest(tmp_path))  # today it writes a signed, encrypted copy of the damage


def test_r3_destination_inside_the_home_does_not_feed_the_next_bundle(home, tmp_path):
    fill(home)
    d = home / "skills" / "backups"
    d.mkdir()
    sizes = [snap.snapshot(d).stat().st_size for _ in range(3)]
    assert max(sizes) < 2 * min(sizes)  # today each bundle holds all the earlier ones


@pytest.mark.xfail(strict=True, reason="R4: an answerer that dumps all memory for every question passes")
def test_r4_dumping_all_memory_does_not_pass_continuity(home, tmp_path):
    seed_history(home)
    qf = tmp_path / "q.json"
    qf.write_text(json.dumps(questions()))
    conn = sqlite3.connect(home / "core_state.db")
    rows = [row for t in ("beliefs", "goals") for row in conn.execute(f"SELECT * FROM {t}")]
    conn.close()
    dump = " | ".join(str(c) for row in rows for c in row)
    assert not ct.run(qf, home, lambda q, h: dump).passed


@pytest.mark.xfail(strict=True, reason="R5: ten copies of one question pass by knowing one fact")
def test_r5_duplicate_questions_are_refused(tmp_path):
    qf = tmp_path / "q.json"
    qf.write_text(json.dumps([{"question": "What is my name?", "expected": "Tanishi"}] * 10))
    with pytest.raises(ValueError):
        ct.load_questions(qf)


@pytest.mark.xfail(strict=True, reason="R6: an expected answer that appears in its own question lets an echo pass")
def test_r6_expected_inside_the_question_is_refused(tmp_path):
    qs = [{"question": f"Is the code word {MARKER}-{i} still yours?", "expected": f"{MARKER}-{i}"} for i in range(10)]
    qf = tmp_path / "q.json"
    qf.write_text(json.dumps(qs))
    assert not ct.run(qf, tmp_path, lambda q, h: q).passed  # empty home, echo answerer


@pytest.mark.xfail(strict=True, reason="R7: short expected answers match inside unrelated words ('no' in 'know')")
def test_r7_expected_matches_whole_words_only(tmp_path):
    qs = [{"question": f"Question {i}?", "expected": "no"} for i in range(10)]
    qf = tmp_path / "q.json"
    qf.write_text(json.dumps(qs))
    assert not ct.run(qf, tmp_path, lambda q, h: "I don't know").passed


def test_r8_odd_inputs_raise_snapshot_error_not_oserror(home, tmp_path):
    with pytest.raises(snap.SnapshotError):
        snap.restore(tmp_path, tmp_path / "back")
    fill(home)
    (home / "identity.yaml").unlink()
    (home / "identity.yaml").mkdir()
    with pytest.raises(snap.SnapshotError):
        snap.snapshot(_dest(tmp_path))


# ---- held under attack


def test_old_bundle_restores_old_state_by_design(home, tmp_path):
    """Rollback is not detected: restore() never compares the manifest timestamp. Pinned so a fix is deliberate."""
    fill(home)
    old = snap.snapshot(_dest(tmp_path))
    beliefs.add_belief("later", "pred", "newer fact", 0.9, "test")
    snap.snapshot(_dest(tmp_path))
    snap.restore(old, tmp_path / "back")
    c = sqlite3.connect(tmp_path / "back" / "core_state.db")
    assert c.execute("SELECT count(*) FROM beliefs WHERE subject='later'").fetchone()[0] == 0
    c.close()


def test_snapshots_under_concurrent_writes_all_restore_clean(home, tmp_path):
    fill(home)
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            try:
                beliefs.add_belief(f"w{i}", "pred", f"v{i}", 0.5, "test")
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        bundles = [snap.snapshot(_dest(tmp_path)) for _ in range(8)]
    finally:
        stop.set()
        t.join()
    assert not errors
    for n, b in enumerate(bundles):
        out = tmp_path / f"r{n}"
        snap.restore(b, out)
        c = sqlite3.connect(out / "core_state.db")
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        c.close()


def test_failed_restore_and_failed_write_leave_no_debris(home, tmp_path, monkeypatch):
    fill(home)
    d = _dest(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(snap.os, "replace", _raise(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            snap.snapshot(d)
    assert list(d.iterdir()) == []
    b = snap.snapshot(d)
    with monkeypatch.context() as m:
        m.setattr(snap.os, "rename", _raise(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            snap.restore(b, tmp_path / "back")
    assert [p.name for p in tmp_path.iterdir() if "restore" in p.name] == []


def test_symlinks_are_refused(home, tmp_path):
    fill(home)
    outside = tmp_path / "secret.txt"
    outside.write_text("topsecret")
    (home / "skills" / "link").symlink_to(outside)
    with pytest.raises(snap.SnapshotError):
        snap.snapshot(_dest(tmp_path))
