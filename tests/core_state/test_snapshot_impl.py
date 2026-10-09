"""Implementer's tests for CS7: the bundle format's own guards. The exam is test_snapshot.py (another agent)."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tanishi.core_state import beliefs
from tanishi.core_state import snapshot as snap


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("TANISHI_SNAPSHOT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TANISHI_SNAPSHOT_SIGNING_KEY", "impl-signing-secret")
    monkeypatch.setenv("TANISHI_HOME", str(h))
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(h / "core_state.db"))
    beliefs.add_belief("s", "p", "o", 0.5, "test")
    return h


def forge(files: dict[str, bytes], dirs=()) -> bytes:
    """A correctly signed and encrypted bundle with whatever manifest an attacker who holds the keys wants."""
    manifest = {
        "format": snap.FORMAT,
        "created_at": "x",
        "dirs": list(dirs),
        "files": [{"path": k, "size": len(v), "sha256": hashlib.sha256(v).hexdigest()} for k, v in files.items()],
    }
    header = json.dumps(manifest).encode()
    payload = len(header).to_bytes(8, "big") + header + b"".join(files.values())
    token = Fernet(os.environ[snap.KEY_ENV_VAR].encode()).encrypt(payload)
    return snap.MAGIC + snap._mac(os.environ[snap.SIGNING_KEY_ENV_VAR].encode(), token) + token


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "skills/../../escape", "tanishi.db", "skills", "./core_state.db",
                                 "other/file", "skills\\..\\x", ""])
def test_signed_bundle_with_a_bad_path_is_refused(home, tmp_path, bad):
    evil = tmp_path / "evil.bundle"
    evil.write_bytes(forge({"core_state.db": b"x", bad: b"payload"}))
    target = tmp_path / "t"
    with pytest.raises(snap.SnapshotError):
        snap.restore(evil, target)
    assert not target.exists()
    assert not (tmp_path / "escape").exists()


def test_signed_bundle_with_a_bad_dir_is_refused(home, tmp_path):
    evil = tmp_path / "evil.bundle"
    evil.write_bytes(forge({"core_state.db": b"x"}, dirs=["../outside"]))
    with pytest.raises(snap.SnapshotError):
        snap.restore(evil, tmp_path / "t")
    assert not (tmp_path / "outside").exists()


def test_bundle_without_a_db_is_refused(home, tmp_path):
    evil = tmp_path / "evil.bundle"
    evil.write_bytes(forge({"identity.yaml": b"name: x\n"}))
    with pytest.raises(snap.SnapshotError):
        snap.restore(evil, tmp_path / "t")


def test_symlink_in_skills_is_refused(home, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("not hers")
    (home / "skills").mkdir()
    (home / "skills" / "link").symlink_to(secret)
    d = tmp_path / "d"
    d.mkdir()
    with pytest.raises(snap.SnapshotError):
        snap.snapshot(d)
    assert list(d.iterdir()) == []


def test_missing_db_is_refused_and_not_created(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("TANISHI_SNAPSHOT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TANISHI_SNAPSHOT_SIGNING_KEY", "k")
    monkeypatch.setenv("TANISHI_HOME", str(h))
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(h / "core_state.db"))
    d = tmp_path / "d"
    d.mkdir()
    with pytest.raises(snap.SnapshotError):
        snap.snapshot(d)
    assert not (h / "core_state.db").exists()


def test_missing_destination_is_refused(home, tmp_path):
    with pytest.raises(snap.SnapshotError):
        snap.snapshot(tmp_path / "nowhere")


def test_restore_target_that_is_a_file_is_refused(home, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    bundle = snap.snapshot(d)
    f = tmp_path / "file"
    f.write_text("keep")
    with pytest.raises(snap.SnapshotError):
        snap.restore(bundle, f)
    assert f.read_text() == "keep"


def test_bundle_and_restored_files_are_private(home, tmp_path):
    (home / "identity.yaml").write_text("name: x\n")
    d = tmp_path / "d"
    d.mkdir()
    bundle = snap.snapshot(d)
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
    target = tmp_path / "t"
    snap.restore(bundle, target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    for name in ("core_state.db", "identity.yaml"):
        assert stat.S_IMODE((target / name).stat().st_mode) == 0o600


def test_failure_at_the_last_step_leaves_no_target_and_no_staging(home, tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    bundle = snap.snapshot(d)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(snap.os, "rename", boom)
    with pytest.raises(OSError):
        snap.restore(bundle, tmp_path / "t")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d", "home"]


def test_errors_never_carry_the_keys(home, tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    bundle = snap.snapshot(d)
    monkeypatch.setenv("TANISHI_SNAPSHOT_SIGNING_KEY", "other-secret-value")
    with pytest.raises(snap.SnapshotError) as e:
        snap.restore(bundle, tmp_path / "t")
    assert "other-secret-value" not in str(e.value) and "impl-signing-secret" not in str(e.value)
    monkeypatch.setenv("TANISHI_SNAPSHOT_KEY", "short-bad-key-value")
    with pytest.raises(snap.SnapshotError) as e:
        snap.snapshot(d)
    assert "short-bad-key-value" not in str(e.value)


def test_empty_skills_dir_round_trips(home, tmp_path):
    (home / "skills" / "empty").mkdir(parents=True)
    d = tmp_path / "d"
    d.mkdir()
    target = tmp_path / "t"
    snap.restore(snap.snapshot(d), target)
    assert (target / "skills" / "empty").is_dir()


def test_explicit_home_argument(home, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    bundle = snap.snapshot(d, home=home)
    target = tmp_path / "t"
    snap.restore(bundle, target)
    assert (target / "core_state.db").read_bytes() == Path(home / "core_state.db").read_bytes()


def _corrupt_db_bytes() -> bytes:
    for i in range(300):
        beliefs.add_belief(f"s{i}", "p", f"filler-{i}" * 20, 0.5, "test")
    raw = bytearray(snap._read_db_consistently(Path(os.environ["TANISHI_CORE_STATE_DB"])))
    for off in range(8192, len(raw) - 4096, 4096):  # scribble inside data pages, leave the header alone
        raw[off + 100 : off + 140] = b"\xff" * 40
    return bytes(raw)


def test_corrupt_database_is_refused_with_snapshot_error(home, tmp_path):
    (home / "core_state.db").write_bytes(_corrupt_db_bytes())
    d = tmp_path / "d"
    d.mkdir()
    with pytest.raises(snap.SnapshotError, match="integrity"):
        snap.snapshot(d)
    assert list(d.iterdir()) == []


def test_signed_bundle_holding_a_corrupt_database_is_refused(home, tmp_path):
    evil = tmp_path / "evil.bundle"
    evil.write_bytes(forge({"core_state.db": _corrupt_db_bytes()}))
    with pytest.raises(snap.SnapshotError, match="integrity"):
        snap.restore(evil, tmp_path / "t")
    assert not (tmp_path / "t").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tanishi-restore-")] == []


def test_successful_restore_leaves_only_the_home(home, tmp_path):
    (home / "skills").mkdir()
    (home / "skills" / "a.py").write_text("x = 1\n")
    d = tmp_path / "d"
    d.mkdir()
    bundle = snap.snapshot(d)
    parent = tmp_path / "restores"
    snap.restore(bundle, parent / "t")
    assert [p.name for p in parent.iterdir()] == ["t"]  # no staging dir, no plaintext db copy beside it
    assert sorted(p.name for p in (parent / "t").iterdir()) == ["core_state.db", "skills"]  # no -wal / -shm


def test_destination_that_is_the_skills_folder_is_refused(home):
    (home / "skills").mkdir()
    with pytest.raises(snap.SnapshotError):
        snap.snapshot(home / "skills")
    assert list((home / "skills").iterdir()) == []


def test_skill_name_that_is_not_utf8_is_refused_at_snapshot(home, tmp_path):
    (home / "skills").mkdir()
    try:
        fd = os.open(os.fsencode(home / "skills") + b"/bad\xff.py", os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        pytest.skip("this filesystem refuses non-UTF-8 names, so it cannot hold one")
    os.close(fd)
    d = tmp_path / "d"
    d.mkdir()
    with pytest.raises(snap.SnapshotError, match="rename it"):
        snap.snapshot(d)
    assert list(d.iterdir()) == []


@pytest.mark.parametrize("rel", ["skills/bad\udcff.py", "skills/nul\x00.py"])
def test_capture_rule_refuses_names_restore_could_not_take(rel):
    with pytest.raises(snap.SnapshotError, match="rename it"):
        snap._check_capture_path(rel, is_dir=False)


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.mark.skipif(os.name != "posix", reason="pid liveness is checked on POSIX only")
def test_staging_left_by_a_killed_process_is_swept(home, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    dead = _dead_pid()
    part = d / f".tanishi-snapshot-{dead}-abc.part"
    part.write_bytes(b"half a bundle")
    ours = d / f".tanishi-snapshot-{os.getpid()}-abc.part"  # this process: maybe another thread's, so kept
    ours.write_bytes(b"in use")
    bundle = snap.snapshot(d)
    assert not part.exists() and ours.exists()

    parent = tmp_path / "restores"
    stale = parent / f".tanishi-restore-{dead}-xyz"
    stale.mkdir(parents=True)
    (stale / "core_state.db").write_bytes(b"plaintext copy")
    unrelated = parent / ".notes.restore-1-x"
    unrelated.mkdir()
    snap.restore(bundle, parent / "t")
    assert not stale.exists()
    assert unrelated.exists()
