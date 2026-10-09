"""Import legacy memory rows into the Core State belief store (node CS3).

    python scripts/import_legacy_memory.py <legacy db path>

Reads `core_memory` and `memories` from the given file, opened read-only (this script is the one place allowed to
read a legacy database, decision 0003), and writes beliefs with source "legacy" to the Core State db named by
TANISHI_CORE_STATE_DB, over one connection. Each legacy row maps to a belief id derived from its content, so running
it again changes nothing:

    core_memory(key, value)              -> ("user", key, value),                  confidence 0.9
    memories(id, content, category, ...) -> ("memory:<id>", category, content),     confidence = importance

After an import, each legacy key (a core_memory key, a memories id) has exactly one live legacy belief, holding its
current value. Earlier values are retired with reason code "superseded"; a value that comes back is reactivated.
Text that is not valid UTF-8 is decoded with replacement characters; a row that still cannot be read is skipped and
counted. Row content is never printed.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tanishi.core_state import beliefs
from tanishi.core_state.db import resolve_path

SOURCE = "legacy"
CORE_CONFIDENCE = 0.9
DEFAULT_IMPORTANCE = 0.5
_NAMESPACE = uuid.UUID("6f1c2a4e-3b7d-4c55-9a0e-1d2b3c4d5e6f")


@dataclass
class Summary:
    added: int = 0
    retired: int = 0
    reactivated: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class LegacyRow:
    belief_id: str
    subject: str
    predicate: str
    object: str
    confidence: float
    same_key: bool  # True for core_memory: the key is (subject, predicate); memories own their whole subject


def legacy_rows(conn: sqlite3.Connection) -> tuple[list[LegacyRow], int]:
    """Every importable row in a stable order, and how many rows could not be read."""
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    out: list[LegacyRow] = []
    skipped = 0
    if "core_memory" in tables:
        for key, value in conn.execute("SELECT key, value FROM core_memory ORDER BY rowid"):
            if key is None or value is None:
                continue
            try:
                key, value = _text(key), _text(value)
            except (TypeError, ValueError):
                skipped += 1
                continue
            out.append(LegacyRow(_id("core_memory", key, value), "user", key, value, CORE_CONFIDENCE, True))
    if "memories" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        category = "category" if "category" in cols else "NULL"
        importance = "importance" if "importance" in cols else "NULL"
        sql = f"SELECT id, content, {category}, {importance} FROM memories ORDER BY rowid"
        for mid, content, cat, imp in conn.execute(sql):
            if mid is None or content is None:
                continue
            try:
                mid, content, cat = _text(mid), _text(content), _text(cat or "fact")
            except (TypeError, ValueError):
                skipped += 1
                continue
            conf = imp if isinstance(imp, (int, float)) and not math.isnan(imp) else DEFAULT_IMPORTANCE
            out.append(LegacyRow(_id("memories", mid, content), f"memory:{mid}", cat, content, conf, False))
    return out, skipped


def import_file(path: Path) -> Summary:
    """Import `path` into the Core State db."""
    src = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    try:
        todo, skipped = legacy_rows(src)
    finally:
        src.close()
    summary = Summary(skipped=skipped)
    with beliefs.connect() as conn:
        for row in todo:
            try:
                _import_row(conn, row, summary)
            except (TypeError, ValueError):  # bad content in this row only; sqlite errors still stop the run
                summary.skipped += 1
    return summary


def _import_row(conn: sqlite3.Connection, row: LegacyRow, summary: Summary) -> None:
    # Retire first, so the current value never contests a stale value of its own key.
    for old in beliefs.find(subject=row.subject, conn=conn):
        if (old.source == SOURCE and old.id != row.belief_id and old.status in beliefs.LIVE
                and (not row.same_key or old.predicate == row.predicate)):
            beliefs.retire(old.id, "superseded by a newer legacy value", "superseded", conn=conn)
            summary.retired += 1
    added = beliefs.add_belief_if_absent(row.belief_id, row.subject, row.predicate, row.object, row.confidence,
                                         SOURCE, conn=conn)
    if added is not None:
        summary.added += 1
    elif beliefs.reactivate(row.belief_id, conn=conn):
        summary.reactivated += 1


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
        s = import_file(src)
    except sqlite3.Error as e:  # SQLite's messages name tables and constraints, never row values
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:  # may quote content (a decode error does): print the kind only
        print(f"error: {type(e).__name__}", file=sys.stderr)
        return 1
    print(f"imported {s.added} new, retired {s.retired}, reactivated {s.reactivated}, "
          f"skipped {s.skipped} unreadable row(s) from {src}")
    return 0


def _text(value: object) -> str:
    """A legacy cell as text that SQLite can store (no lone surrogates)."""
    text = value if isinstance(value, str) else str(value)
    text.encode("utf-8")  # raises UnicodeEncodeError, a ValueError, for a lone surrogate
    return text


def _id(table: str, key: str, value: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{table}\x00{key}\x00{value}"))


if __name__ == "__main__":
    sys.exit(main())
