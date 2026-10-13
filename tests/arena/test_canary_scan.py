"""AR4 canary scan: a sealed canary anywhere outside the vault is found, reported, locks the vault and alerts.

No test here holds a whole canary as text: each one is built at run time, so the repo itself scans clean.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tanishi.arena import canary_scan as cs

REPO = Path(__file__).resolve().parents[2]


def canary(n: int = 0) -> str:
    return cs.PREFIX + f"{n:02x}" * 16


def test_finds_a_canary_and_ignores_near_misses():
    good = canary(171)
    data = f"x {good} y {cs.PREFIX}{'AB' * 16} {cs.PREFIX}{'ab' * 15}z".encode()
    assert [c for _, c in cs.scan_bytes(data)] == [good]


def test_finds_a_canary_split_across_chunks(monkeypatch):
    monkeypatch.setattr(cs, "CHUNK", 7)
    data = ("." * 50 + canary(1) + "." * 13 + canary(2)).encode()
    hits = cs.scan_bytes(data)
    assert [c for _, c in hits] == [canary(1), canary(2)]
    assert hits[0][0] == 50


def test_a_planted_canary_in_a_skill_file_is_caught(tmp_path):
    """The card's acceptance line: plant a canary in a skill, run the scan, see it caught."""
    skills = tmp_path / ".tanishi" / "skills"
    skills.mkdir(parents=True)
    (skills / "clean.md").write_text("how to add numbers\n")
    (skills / "learned.md").write_text(f"remember this task: ...\n[task ref: {canary(9)}]\n")
    report, leak = tmp_path / "last.json", tmp_path / "LEAK"
    assert cs.main(["--target", str(tmp_path / ".tanishi"), "--report", str(report), "--leak-file", str(leak)]) == 1
    data = json.loads(report.read_text())
    assert data["clean"] is False
    assert [h["path"] for h in data["hits"]] == [str(skills / "learned.md")]
    assert json.loads(leak.read_text())["hits"] == 1


def test_a_clean_tree_reports_clean_and_leaves_no_lock(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here " + cs.PREFIX + "x")
    leak = tmp_path / "LEAK"
    assert cs.main(["--target", str(tmp_path), "--report", str(tmp_path / "r.json"), "--leak-file", str(leak)]) == 0
    assert not leak.exists()
    assert json.loads((tmp_path / "r.json").read_text())["clean"] is True


def test_excluded_folders_and_links_are_not_entered(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text(canary(3))
    root = tmp_path / "root"
    (root / ".venv").mkdir(parents=True)
    (root / ".venv" / "y.txt").write_text(canary(4))
    (root / "link").symlink_to(outside)
    (root / "file-link.txt").symlink_to(outside / "x.txt")
    hits, stats = cs.scan_tree(str(root))
    assert hits == [] and stats["files"] == 0


def test_binary_and_database_files_are_scanned(tmp_path):
    import sqlite3

    db = tmp_path / "core_state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE beliefs (text TEXT)")
    conn.execute("INSERT INTO beliefs VALUES (?)", (f"user said {canary(5)}",))
    conn.commit()
    conn.close()
    hits, _ = cs.scan_tree(str(tmp_path))
    assert {h["canary"] for h in hits} == {canary(5)}


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_canary_deleted_from_the_tree_is_still_found_in_git_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True,
                       capture_output=True)

    git("init", "-q")
    (repo / "notes.md").write_text(f"oops {canary(6)}\n")
    git("add", "notes.md")
    git("commit", "-qm", "add")
    (repo / "notes.md").unlink()
    git("commit", "-qam", "remove")
    tree_hits, _ = cs.scan_tree(str(repo))  # .git is excluded from the tree walk
    assert tree_hits == []
    hits, stats = cs.scan_git(str(repo))
    assert {h["canary"] for h in hits} == {canary(6)} and stats["objects"] >= 4
    if os.geteuid() != 0:  # as root the CLI insists on --git-as (never run git as root on someone else's repo)
        assert cs.main(["--git", str(repo)]) == 1
    else:
        with pytest.raises(SystemExit):
            cs.main(["--git", str(repo)])


def test_a_missing_target_is_reported_not_fatal(tmp_path):
    report = cs.run_scan([str(tmp_path / "nope")])
    assert report["clean"] and report["targets"][0]["missing"]


def test_alert_needs_settings_and_never_raises(tmp_path):
    env = tmp_path / "telegram.env"
    env.write_text("SOMETHING_ELSE=1\n")
    assert cs.send_telegram(str(env), "hi") == "MissingTelegramSettings"
    assert cs.send_telegram(str(tmp_path / "missing.env"), "hi") == "FileNotFoundError"


def test_env_file_parsing_accepts_export_and_quotes(tmp_path):
    env = tmp_path / "t.env"
    env.write_text('# c\nexport TELEGRAM_BOT_TOKEN="abc"\nTELEGRAM_OWNER_ID=\'42\'\n')
    assert cs.read_env_file(str(env)) == {"TELEGRAM_BOT_TOKEN": "abc", "TELEGRAM_OWNER_ID": "42"}


def test_the_scanner_runs_on_the_python_root_has(tmp_path):
    """Installed as root and run by /usr/bin/python3 on macOS (3.9): no newer syntax, no tanishi import."""
    source = Path(cs.__file__).read_text()
    ast.parse(source, feature_version=(3, 9))
    tree = ast.parse(source)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "tanishi" not in imported


def test_the_repo_holds_no_canary():
    """Nothing tracked in this repo may hold a canary: not the scanner, not a test, not a note."""
    hits = []
    for folder in ("tanishi", "tests", "build", "scripts", "docs"):
        if (REPO / folder).exists():
            hits += cs.scan_tree(str(REPO / folder))[0]
    assert hits == []
