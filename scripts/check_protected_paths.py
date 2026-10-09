#!/usr/bin/env python3
"""Fail when an agent branch changes a protected path.

    python scripts/check_protected_paths.py --base origin/main --branch agent/core/cs1

Protected paths are read from build/graph.yaml. Any branch whose name starts with agent/
may not add, edit, move or delete a file under them. Other branches pass here and are
held for the owner's review by CODEOWNERS instead.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FALLBACK = ["constitution/", "warden/", "vault/", "reality/", "builder_vault/", "tanishi/guard/",
            ".github/", "CLAUDE.md", "build/graph.yaml", "build/agents/", "scripts/check_protected_paths.py"]


def protected_paths() -> list[str]:
    try:
        graph = yaml.safe_load((ROOT / "build" / "graph.yaml").read_text())
        return list(graph.get("protected_paths") or FALLBACK)
    except (OSError, yaml.YAMLError, AttributeError, TypeError):  # a broken graph must not open the gate
        return FALLBACK


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-status", "-M", f"{base}...HEAD"],
                         cwd=ROOT, text=True, capture_output=True, check=True).stdout
    files: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        files.extend(parts[1:])  # renames list both old and new paths
    return files


def violations(files: list[str], protected: list[str]) -> list[str]:
    bad = []
    for f in files:
        for p in protected:
            if f == p.rstrip("/") or (p.endswith("/") and f.startswith(p)):
                bad.append(f)
                break
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--branch", required=True)
    args = ap.parse_args()
    if not args.branch.startswith("agent/"):
        print(f"{args.branch}: not an agent branch; CODEOWNERS review applies.")
        return 0
    bad = violations(changed_files(args.base), protected_paths())
    if bad:
        print("Agent branch touches protected paths:")
        for f in bad:
            print("  ", f)
        return 1
    print("No protected paths touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
