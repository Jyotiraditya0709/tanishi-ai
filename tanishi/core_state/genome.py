"""Genome: one machine-readable record per version of Tanishi (CS6).

Each version gets one row in the Core State `genome` table: (version, parent, created_at, record).
`record` is JSON text (decision 0006) holding every field of record_version(). Records are append-only:
a version can be written once, and a second write raises without touching the first.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from tanishi.core_state.db import migrate, open_db


def record_version(
    version: str,
    parent: str | None,
    genes_changed: list[str],
    compiler_version: str,
    substrate_version: str,
    arena: dict,
    attribution: dict,
    mirror_id: str | None,
) -> None:
    """Append the genome record for `version` to the Core State database.

    Raises ValueError for a blank version or a value JSON cannot hold exactly (NaN, infinity),
    TypeError for a value JSON cannot hold at all, and sqlite3.IntegrityError if `version`
    was already written or `parent` was never written. A failed call writes nothing.
    """
    if not isinstance(version, str) or not version.strip():
        raise ValueError("genome version must be a non-empty string")
    if parent is not None and (not isinstance(parent, str) or not parent.strip()):
        raise ValueError("genome parent must be None or a non-empty string")
    if isinstance(genes_changed, str):
        raise TypeError("genes_changed must be a list of gene names, not a string")
    record: dict[str, Any] = {
        "version": version,
        "parent": parent,
        "genes_changed": list(genes_changed),
        "compiler_version": compiler_version,
        "substrate_version": substrate_version,
        "arena": arena,
        "attribution": attribution,
        "mirror_id": mirror_id,
    }
    # Serialised before the db is opened, so a bad value never reaches it and later caller edits cannot leak in.
    text = json.dumps(record, allow_nan=False, ensure_ascii=False, sort_keys=True)

    conn = open_db()
    try:
        migrate(conn)
        with conn:  # one transaction: commit on success, roll back on any error
            conn.execute(
                "INSERT INTO genome(version, parent, created_at, record) VALUES (?, ?, ?, ?)",
                (version, parent, datetime.now(UTC).isoformat(), text),
            )
    finally:
        conn.close()
