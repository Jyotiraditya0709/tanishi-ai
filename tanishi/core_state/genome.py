"""Genome: one machine-readable record per version of Tanishi (CS6).

Each version gets one row in the Core State `genome` table: (version, parent, created_at, record).
`record` is JSON text (decision 0006) holding every field of record_version(). Records are append-only:
a version can be written once, and a second write raises without touching the first.
"""
from __future__ import annotations

import json
import math
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

    Raises ValueError for a blank, padded or self-referencing version, a blank gene name, or a value JSON cannot hold exactly (NaN, infinity, tuples, non-str keys); TypeError
    for an argument of the wrong type; and sqlite3.IntegrityError if `version` was already
    written or `parent` was never written. A failed call writes nothing.
    """
    _check_id("version", version)
    if parent is not None:
        _check_id("parent", parent)
        if parent == version:
            raise ValueError("genome parent must differ from version")
    if isinstance(genes_changed, str):
        raise TypeError("genes_changed must be a list of gene names (strings)")
    genes = list(genes_changed)  # once, so a generator is not drained by the checks below
    if not all(isinstance(g, str) for g in genes):
        raise TypeError("genes_changed must be a list of gene names (strings)")
    if any(not g.strip() for g in genes):
        raise ValueError("gene names must not be blank")
    # Duplicates are kept: the CS6 exam writes and expects them (open-problems/CS6-repair-conflicts.md).
    for name, value in (("compiler_version", compiler_version), ("substrate_version", substrate_version)):
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
    for name, value in (("arena", arena), ("attribution", attribution)):
        if not isinstance(value, dict):
            raise TypeError(f"{name} must be a dict")
    if mirror_id is not None and not isinstance(mirror_id, str):
        raise TypeError("mirror_id must be a string or None")
    for name, value in (("arena", arena), ("attribution", attribution)):
        try:
            _check_plain_json(value)
        except RecursionError:
            raise ValueError(f"{name} is nested too deeply or contains itself") from None
    record: dict[str, Any] = {
        "version": version,
        "parent": parent,
        "genes_changed": genes,
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


def _check_id(name: str, value: object) -> None:
    """A version id is a non-empty str with no surrounding whitespace ("abc\\n" and "abc" must not both exist)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"genome {name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"genome {name} must not have leading or trailing whitespace")


def _check_plain_json(value: object) -> None:
    """Refuse anything that would not read back exactly as written: tuples, non-str keys, other types."""
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("genome values must be finite numbers")
        return
    if type(value) is list:
        for item in value:
            _check_plain_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"genome dict keys must be strings, got {type(key).__name__}")
            _check_plain_json(item)
        return
    raise ValueError(f"genome values must be plain JSON types, got {type(value).__name__}")
