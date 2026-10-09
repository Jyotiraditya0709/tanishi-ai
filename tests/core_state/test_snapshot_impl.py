"""Implementer's tests for CS7: the bundle format's own guards. The exam is test_snapshot.py (another agent)."""
import hashlib
import json
import os
import stat
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
