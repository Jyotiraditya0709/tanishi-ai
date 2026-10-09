"""Import legacy memory rows into the Core State belief store (node CS3).

    python scripts/import_legacy_memory.py <legacy db path>

Reads `core_memory` and `memories` from the given file, opened read-only (this script is the one place allowed to
read a legacy database, decision 0003), and writes beliefs with source "legacy" to the Core State db named by
TANISHI_CORE_STATE_DB. Each legacy row maps to a belief id derived from its content, so running it again adds only
rows it has not seen:

    core_memory(key, value)              -> ("user", key, value),                  confidence 0.9
    memories(id, content, category, ...) -> ("memory:<id>", category, content),     confidence = importance

A changed core_memory value is a new belief, which contradicts the old one and so is contested, not overwritten.
Each memory gets its own subject, because free-text memories are not one-value-per-predicate facts.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tanishi.core_state import beliefs
from tanishi.core_state.db import resolve_path

SOURCE = "legacy"
CORE_CONFIDENCE = 0.9
DEFAULT_IMPORTANCE = 0.5
_NAMESPACE = uuid.UUID("6f1c2a4e-3b7d-4c55-9a0e-1d2b3c4d5e6f")


def legacy_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str, str, float]]:
    """(belief_id, subject, predicate, object, confidence) for every importable row, in a stable order."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    out = []
    if "core_memory" in tables:
        for key, value in conn.execute("SELECT key, value FROM core_memory ORDER BY rowid"):
            if key is None or value is None:
                continue
            key, value = str(key), str(value)
            out.append((_id("core_memory", key, value), "user", key, value, CORE_CONFIDENCE))
    if "memories" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        category = "category" if "category" in cols else "NULL"
        importance = "importance" if "importance" in cols else "NULL"
        sql = f"SELECT id, content, {category}, {importance} FROM memories ORDER BY rowid"
        for mid, content, cat, imp in conn.execute(sql):
            if mid is None or content is None:
                continue
            mid, content = str(mid), str(content)
            conf = imp if isinstance(imp, (int, float)) and not math.isnan(imp) else DEFAULT_IMPORTANCE
            out.append((_id("memories", mid, content), f"memory:{mid}", str(cat or "fact"), content, conf))
    return out


def import_file(path: Path) -> int:
    """Import `path`; returns how many new beliefs were written."""
    conn = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    try:
        todo = legacy_rows(conn)
    finally:
        conn.close()
    added = 0
    for belief_id, subject, predicate, obj, conf in todo:
        if beliefs.add_belief_if_absent(belief_id, subject, predicate, obj, conf, SOURCE) is not None:
            added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="legacy SQLite file holding core_memory and/or memories")
    args = parser.parse_args(argv)
    src: Path = args.path.expanduser()
    if not src.is_file():
        print(f"error: {src} is not a file", file=sys.stderr)
        return 2
    try:
        if src.resolve() == Path(resolve_path()):
            print("error: that is the Core State database itself, not a legacy one", file=sys.stderr)
            return 2
        added = import_file(src)
    except (sqlite3.Error, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"imported {added} new belief(s) from {src}")
    return 0


def _id(table: str, key: str, value: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{table}\x00{key}\x00{value}"))


if __name__ == "__main__":
    sys.exit(main())
