"""AR4 Sealed Vault: the agent-side client, the candidate tarball, and the vault side end to end (as the same user).

The OS-user boundary itself (another user cannot read the vault's home) is checked on the Mac by
scripts/vault/check_vault.sh, not here: CI has one user.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

from tanishi.arena import sealed
from tanishi.arena.sealed import (
    Layout,
    SealedRefused,
    SealedUnavailable,
    SealedVault,
    extract_candidate,
    pack_candidate,
)

PREFIX = "sealed_executors."


# --- fixtures ----------------------------------------------------------------------------------------------------


def _candidate(root: Path) -> Path:
    pkg = root / "tanishi"
    (pkg / "arena").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "arena" / "__init__.py").write_text("")
    (pkg / "arena" / "answer.py").write_text("def answer(req):\n    return '4'\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    return root


def _task(store: Path, tid: str, cap: str, family: str = "reasoning") -> None:
    p = store / f"{tid.replace('.', '_')}.yaml"
    p.write_text(textwrap.dedent(f"""\
        id: {tid}
        family: {family}
        tier: sealed
        prompt: What is 2 + 2?
        verifier: tanishi.arena.sealed.check
        timeout_s: 30
        tags: [sealed, "cap:{cap}"]
        """))
    p.chmod(0o600)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A vault layout owned by this user, three tasks, a checks file and a shared folder."""
    home = tmp_path / "vault-home"
    store = home / "sealed" / "tasks"
    store.mkdir(parents=True)
    home.chmod(0o700)
    (home / "sealed").chmod(0o700)
    store.chmod(0o700)
    _task(store, "sealed.one.right", "reasoning.arithmetic")
    _task(store, "sealed.two.right", "reasoning.arithmetic")
    _task(store, "sealed.three.wrong", "reasoning.dates")
    checks = home / "sealed" / "checks.py"
    marker = "VAULT" + "-ONLY-" + "MARKER"
    checks.write_text(textwrap.dedent(f"""\
        from tanishi.arena.verifiers import exact

        SECRET = "{marker}"

        def four(task, output):
            return exact(output, "4")

        def nothing_opened(task, output):
            return (1.0, "nothing read") if output == "opened:none" else (0.0, "the vault was read")

        CHECKS = {{"sealed.one.right": four, "sealed.two.right": four, "sealed.three.wrong": four}}
        """))
    checks.chmod(0o600)
    shared = tmp_path / "shared"
    (shared / "reads").mkdir(parents=True)
    monkeypatch.setenv("TANISHI_SEALED_HOME", str(home))
    monkeypatch.setenv("TANISHI_SEALED_SHARED", str(shared))
    monkeypatch.setenv("TANISHI_SEALED_EXECUTOR_PREFIXES", "sealed_executors.")
    # A vault process started by the client must import the test executors (a top-level module of this folder, like
    # the Frontier tests' fixtures): put this folder on its sys.path by name.
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent))
    monkeypatch.delenv("TANISHI_SEALED_CHECKS", raising=False)
    monkeypatch.delenv("TANISHI_SEALED_DAILY_READS", raising=False)
    monkeypatch.setattr(sealed, "_CHECKS", None)
    cand = _candidate(tmp_path / "cand")
    return Layout(home, shared), cand


def _ledger(layout: Layout) -> list[dict]:
    return [json.loads(line) for line in layout.ledger.read_text().splitlines()]


# --- the candidate tarball -------------------------------------------------------------------------------------------


def test_pack_takes_only_the_package_and_pyproject(tmp_path):
    root = _candidate(tmp_path / "c")
    (root / "tanishi" / "core_state.db").write_text("db")
    (root / "tanishi" / ".env").write_text("KEY=1")
    (root / "tanishi" / "vault").mkdir()
    (root / "tanishi" / "vault" / "x.py").write_text("")
    (root / "tanishi" / "__pycache__").mkdir()
    (root / "tanishi" / "__pycache__" / "a.pyc").write_bytes(b"x")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("")
    (root / "notes.md").write_text("personal")
    (root / "tanishi" / "link.py").symlink_to(root / "notes.md")
    with tarfile.open(fileobj=io.BytesIO(pack_candidate(root)), mode="r:gz") as tar:
        names = tar.getnames()
    assert sorted(names) == ["pyproject.toml", "tanishi/__init__.py", "tanishi/arena/__init__.py",
                             "tanishi/arena/answer.py"]


def test_pack_refuses_a_folder_without_the_package(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(ValueError):
        pack_candidate(tmp_path / "x")


def test_extract_round_trips_and_the_hash_follows_content(tmp_path):
    root = _candidate(tmp_path / "c")
    h1 = extract_candidate(pack_candidate(root), _empty(tmp_path / "d1"))
    h2 = extract_candidate(pack_candidate(root), _empty(tmp_path / "d2"))
    assert h1 == h2 and len(h1) == 64
    assert (tmp_path / "d1" / "tanishi" / "arena" / "answer.py").read_text() == "def answer(req):\n    return '4'\n"
    (root / "tanishi" / "arena" / "answer.py").write_text("def answer(req):\n    return '5'\n")
    assert extract_candidate(pack_candidate(root), _empty(tmp_path / "d3")) != h1


def _empty(p: Path) -> Path:
    p.mkdir()
    return p


def _tarball(entries) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _file(name: str, data: bytes = b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


@pytest.mark.parametrize("entry", [
    _file("/etc/passwd"),
    _file("tanishi/../../escape.py"),
    _file("other/x.py"),
    _file("tanishi/vault/x.py"),
    _file("tanishi/state.sqlite3"),
    _file("tanishi/.env"),
])
def test_extract_refuses_names_outside_the_package(tmp_path, entry):
    data = _tarball([_file("tanishi/__init__.py"), entry])
    with pytest.raises(ValueError):
        extract_candidate(data, _empty(tmp_path / "d"))
    assert not (tmp_path / "escape.py").exists()


def test_extract_refuses_a_name_given_twice(tmp_path):
    data = _tarball([_file("tanishi/__init__.py"), _file("tanishi/__init__.py", b"y")])
    with pytest.raises(ValueError):
        extract_candidate(data, _empty(tmp_path / "d"))


def test_extract_refuses_links_and_devices(tmp_path):
    link = tarfile.TarInfo("tanishi/link.py")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    with pytest.raises(ValueError):
        extract_candidate(_tarball([_file("tanishi/__init__.py"), (link, None)]), _empty(tmp_path / "d"))


def test_extract_refuses_garbage_and_oversize(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        extract_candidate(b"not a tarball", _empty(tmp_path / "d"))
    monkeypatch.setattr(sealed, "MAX_CANDIDATE_BYTES", 10)
    with pytest.raises(ValueError):
        extract_candidate(_tarball([_file("tanishi/__init__.py", b"x" * 11)]), _empty(tmp_path / "e"))


# --- the vault side, in process ----------------------------------------------------------------------------------


def test_evaluate_scores_charges_logs_and_shares(vault):
    layout, cand = vault
    result = sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    scores = sorted(s["score"] for s in result["scores"])
    assert scores == [0.0, 1.0, 1.0]
    assert all(set(s) == {"task_id_hash", "score", "cost"} for s in result["scores"])
    assert len(result["receipt"]) == 64 and len(result["candidate_hash"]) == 64
    outcomes = [e["outcome"] for e in _ledger(layout)]
    assert outcomes == ["ran", "scored"]
    shared = json.loads((layout.reads_out / f"{result['read_id']}.json").read_text())
    assert shared == result
    text = json.dumps(result)
    assert "sealed.one" not in text and "2 + 2" not in text  # no id, no prompt crosses back


def test_the_same_candidate_is_read_once(vault):
    layout, cand = vault
    sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    with pytest.raises(sealed._Stop) as stop:
        sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    assert stop.value.reply == {"refused": "already_read"}
    assert _ledger(layout)[-1]["why"] == "already_read"


def test_the_daily_budget_counts_every_candidate(vault, monkeypatch):
    layout, cand = vault
    monkeypatch.setenv("TANISHI_SEALED_DAILY_READS", "1")
    sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    (cand / "tanishi" / "new.py").write_text("x = 1\n")  # a new candidate, same day
    with pytest.raises(sealed._Stop) as stop:
        sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    assert stop.value.reply == {"refused": "daily_cap"}


def test_a_flagged_leak_locks_every_read(vault):
    layout, cand = vault
    layout.leak_marker.write_text("{}")
    with pytest.raises(sealed._Stop) as stop:
        sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    assert stop.value.reply == {"refused": "leak"}
    assert not layout.ledger.exists()  # refused before anything was charged


def test_the_executor_cannot_read_the_checks_or_the_store(vault, monkeypatch):
    layout, cand = vault
    # Control: run unconfined, the same executor does open both, so a 1.0 below is the sandbox, not a crash.
    monkeypatch.setenv("TANISHI_SEALED_CHECKS", str(layout.checks))
    import sealed_executors

    assert sealed_executors.try_to_read_the_vault(None, "c", 0) == "opened:checks,store"
    monkeypatch.delenv("TANISHI_SEALED_CHECKS")
    checks = layout.checks.read_text().replace(
        'CHECKS = {"sealed.one.right": four,', 'CHECKS = {"sealed.one.right": nothing_opened,')
    layout.checks.write_text(checks)
    result = sealed.serve_evaluate(pack_candidate(cand), PREFIX + "try_to_read_the_vault", 1, layout)
    assert sorted(s["score"] for s in result["scores"]) == [0.0, 0.0, 1.0]  # ran, opened nothing


def test_executors_outside_the_allowed_prefixes_are_refused(vault, monkeypatch):
    layout, cand = vault
    monkeypatch.delenv("TANISHI_SEALED_EXECUTOR_PREFIXES")
    with pytest.raises(sealed._Stop) as stop:
        sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    assert stop.value.code == sealed.EXIT_ERROR
    assert not layout.ledger.exists()


def test_a_task_without_a_check_breaks_the_store(vault):
    layout, cand = vault
    _task(layout.store, "sealed.four.right", "reasoning.dates")
    with pytest.raises(sealed._Stop) as stop:
        sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    assert stop.value.code == sealed.EXIT_ERROR and not layout.ledger.exists()


def test_a_checks_file_others_can_read_is_refused(vault):
    layout, cand = vault
    layout.checks.chmod(0o644)
    with pytest.raises(sealed._Stop):
        sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)


def test_a_missing_vault_is_unavailable(tmp_path):
    layout = Layout(tmp_path / "nobody", tmp_path / "shared")
    with pytest.raises(sealed._Stop) as stop:
        sealed.serve_manifest(layout)
    assert stop.value.reply == {"unavailable": "not_installed"}


def test_manifest_maps_hashes_to_capabilities(vault):
    layout, _ = vault
    manifest = sealed.serve_manifest(layout)
    assert sorted(manifest.values()) == ["reasoning.arithmetic", "reasoning.arithmetic", "reasoning.dates"]
    assert all(len(h) == 32 for h in manifest)


def test_receipts_verify_and_a_changed_score_does_not(vault):
    layout, cand = vault
    result = sealed.serve_evaluate(pack_candidate(cand), PREFIX + "answer_by_id", 1, layout)
    assert sealed.serve_verify(json.dumps(result).encode(), layout) == {"valid": True}
    result["scores"][0]["score"] = 1.0 - result["scores"][0]["score"]
    assert sealed.serve_verify(json.dumps(result).encode(), layout) == {"valid": False}


# --- the agent side, through a real process ----------------------------------------------------------------------


def _client() -> SealedVault:
    return SealedVault([sys.executable, "-m", "tanishi.arena.sealed"], timeout_s=600)


def test_client_end_to_end(vault):
    _, cand = vault
    client = _client()
    result = client.evaluate(cand, executor=PREFIX + "answer_by_id")
    assert len(result.scores) == 3 and abs(result.mean - 2 / 3) < 1e-9
    assert client.verify(result)
    assert set(client.manifest().values()) == {"reasoning.arithmetic", "reasoning.dates"}
    status = client.status()
    assert status["installed"] and status["tasks"] == 3 and status["reads_today"] == 1 and not status["leak"]
    with pytest.raises(SealedRefused) as refused:
        client.evaluate(cand, executor=PREFIX + "answer_by_id")
    assert refused.value.reason == "already_read"


def test_client_maps_a_missing_vault_and_a_failing_command(tmp_path, monkeypatch):
    monkeypatch.setenv("TANISHI_SEALED_HOME", str(tmp_path / "nobody"))
    with pytest.raises(SealedUnavailable):
        _client().manifest()
    with pytest.raises(SealedUnavailable):
        SealedVault(["false"]).status()
    with pytest.raises(SealedUnavailable):
        SealedVault([str(tmp_path / "no-such-program")]).status()


@pytest.mark.parametrize("reply", [
    {"read_id": "x"},
    {"scores": []},
    "not a dict",
])
def test_client_refuses_a_malformed_result(tmp_path, reply):
    script = tmp_path / "fake.py"
    script.write_text(f"import sys, json; sys.stdin.buffer.read(); print(json.dumps({reply!r}))")
    cand = _candidate(tmp_path / "c")
    with pytest.raises(SealedUnavailable):
        SealedVault([sys.executable, str(script)]).evaluate(cand, executor="tanishi.x.y")


def test_a_result_with_no_scores_never_reads_as_a_pass():
    result = sealed.SealedResult("0" * 32, "t", "0" * 64, "tanishi.x.y", 1, (), "0" * 64)
    with pytest.raises(ValueError):
        _ = result.mean


def test_the_cli_prints_one_json_object_and_no_task_text(vault):
    _, cand = vault
    done = subprocess.run([sys.executable, "-m", "tanishi.arena.sealed", "evaluate", "--executor",
                           PREFIX + "answer_by_id"], input=pack_candidate(cand), capture_output=True, check=False,
                          env=os.environ.copy())
    assert done.returncode == 0, done.stdout
    assert len(done.stdout.decode().strip().splitlines()) == 1
    assert b"2 + 2" not in done.stdout + done.stderr and b"sealed.one" not in done.stdout + done.stderr
