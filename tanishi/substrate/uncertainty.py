"""Uncertainty and the Self Model v0 (node SUB4): resolved predictions become a calibration table per capability.

A prediction is tagged with a capability by an `expected["capability"]` field, a non-blank str, written with CS5's
`predict()`. The tag text is the capability's id in the `capabilities` table (migration 0001); the row is created
on its first update with `name` = the tag. A prediction without a valid tag is untagged: it resolves as usual and
touches no capability. A tag that CS5's redaction would change (a secret-shaped or unencodable text) is not valid:
stored redacted, two different tags would merge into one capability (red team B4).

The row follows every resolution, whoever resolves (decision 0021):
- CS5's `resolve()` calls this module's hook after it commits, so the row is rewritten by `update_calibration()`.
- `get_capability()` reads through: when the ledger holds a tagged resolution at or after the row's `updated_at`
  (resolved in a process that never imported this module, or a failed hook), it recomputes the row first.

The update recomputes the row from every resolved prediction carrying the tag, so it is idempotent, independent of
resolution order, and heals after a failure.

- `calibration_error` is the expected calibration error over CS5's N_BINS equal-width forecast bins:
  sum_k (n_k / N) * |mean_forecast_k - success_rate_k|. 0 is perfect; it lies in [0, 1]. The forecast of success
  is `confidence` if `expected["success"]` else `1 - confidence` (decision 0014), binned as `calibration()` does.
- `ability` is the success rate of those predictions, in [0, 1]. Below MIN_EVIDENCE resolved predictions it is
  Laplace's rule of succession, (successes + 1) / (n + 2), so a handful of lucky calls never reads as 0 or 1.
- `evidence` (on the reading, not stored) is the number of resolved predictions behind the row. A reading with
  `low_evidence` (fewer than MIN_EVIDENCE) is not a known ability: the router (SUB3) must not trust it as one.

Both stored values stay NULL until there is resolved evidence. `get_capability()` is what the router reads.
Readings are self-graded until predictions carry a call_id: never an Arena score or promotion input (decision 0021).
"""
from __future__ import annotations

import logging
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

from tanishi.core_state import predictions
from tanishi.core_state.db import migrate, open_db
from tanishi.core_state.events import redact

logger = logging.getLogger(__name__)

TAG = "capability"
N_BINS = predictions.N_BINS
MIN_EVIDENCE = 5  # fewer resolved predictions than this: the reading is low_evidence
# What CS5's redaction writes into a string: secret markers start with "[REDACTED", a lone surrogate becomes U+FFFD
_REDACTION_MARKS = ("[REDACTED", "�")
_MAX_COUNTS = 4096

# Evidence count behind a stored row, keyed by (db file, capability, updated_at). Every write of a row sets a new
# updated_at, so an entry can never describe a different row; it only saves re-reading the ledger on each read.
_counts: dict[tuple[str, str, str], int] = {}


@dataclass(frozen=True)
class CapabilityReading:
    """One capability as the router sees it. `ability` and `calibration_error` are None with no evidence.

    `evidence` is the number of resolved predictions behind the reading. With `low_evidence` the ability is a
    shrunken guess, not a known ability.
    """

    capability: str
    ability: float | None
    calibration_error: float | None
    updated_at: str | None
    evidence: int = 0

    @property
    def low_evidence(self) -> bool:
        return self.evidence < MIN_EVIDENCE


def _open() -> sqlite3.Connection:
    conn = open_db()
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _check_capability(capability: object) -> str:
    if not isinstance(capability, str):
        raise TypeError("capability must be a str")
    if not capability.strip():
        raise ValueError("capability must not be blank")
    try:
        capability.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("capability must be valid UTF-8 text (no lone surrogates)") from None
    return capability


def _taggable(text: str) -> bool:
    """Whether `text` can be a tag: not blank, and stored by CS5 exactly as written (redaction leaves it alone)."""
    return bool(text.strip()) and not any(m in text for m in _REDACTION_MARKS) and redact(text) == text


def tag_of(expected: object) -> str | None:
    """The capability a prediction's `expected` is tagged with, or None when it carries no usable tag."""
    if not isinstance(expected, dict):
        return None
    tag = expected.get(TAG)
    return tag if isinstance(tag, str) and _taggable(tag) else None


def _forecast(expected: dict, confidence: float) -> float:
    return confidence if expected.get("success") is True else 1.0 - confidence


def _ece(pairs: list[tuple[float, float]]) -> float:
    """Expected calibration error of (forecast, outcome) pairs, binned as CS5 calibration() bins them."""
    bins: dict[int, list[tuple[float, float]]] = {}
    for p, outcome in pairs:
        bins.setdefault(min(int(p * N_BINS), N_BINS - 1), []).append((p, outcome))
    total = len(pairs)
    err = math.fsum(
        len(members) / total * abs(math.fsum(p for p, _ in members) / len(members)
                                   - math.fsum(o for _, o in members) / len(members))
        for members in bins.values()
    )
    return min(max(err, 0.0), 1.0)


def _ability(pairs: list[tuple[float, float]]) -> float:
    successes, n = math.fsum(o for _, o in pairs), len(pairs)
    return successes / n if n >= MIN_EVIDENCE else (successes + 1.0) / (n + 2.0)


def _evidence(conn: sqlite3.Connection, capability: str) -> list[tuple[float, float]]:
    """(forecast, outcome) for every resolved prediction tagged with `capability`. Malformed rows are skipped."""
    rows = conn.execute(
        "SELECT expected, confidence, actual FROM predictions "
        "WHERE resolved_at IS NOT NULL AND score IS NOT NULL AND json_extract(expected, '$.capability') = ?",
        (capability,),
    ).fetchall()
    pairs = []
    for expected_text, confidence, actual_text in rows:
        expected, actual = predictions._object(expected_text), predictions._object(actual_text)
        if expected is None or actual is None or tag_of(expected) != capability:
            continue
        pairs.append((_forecast(expected, confidence), 1.0 if actual.get("success") is True else 0.0))
    return pairs


def _has_newer_evidence(conn: sqlite3.Connection, capability: str, since: str | None) -> bool:
    """Whether a tagged resolution is at or after `since` (any at all when None). The cheap time test runs first."""
    return conn.execute(
        "SELECT 1 FROM predictions WHERE resolved_at IS NOT NULL AND (?1 IS NULL OR resolved_at >= ?1) "
        "AND score IS NOT NULL AND json_extract(expected, '$.capability') = ?2 LIMIT 1",
        (since, capability),
    ).fetchone() is not None


def _db_file(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def _remember(key: tuple[str, str, str], n: int) -> None:
    if len(_counts) >= _MAX_COUNTS:
        _counts.clear()
    _counts[key] = n


def _refresh(conn: sqlite3.Connection, capability: str) -> CapabilityReading | None:
    """Recompute and store `capability`'s row from the ledger. None (and nothing written) without evidence.

    Read and write share one BEGIN IMMEDIATE transaction, so two concurrent refreshes cannot store a stale result
    over a fresher one.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        pairs = _evidence(conn, capability)
        if not pairs:
            conn.rollback()
            return None
        error, ability = _ece(pairs), _ability(pairs)
        updated_at = datetime.now(UTC).isoformat(timespec="microseconds")
        conn.execute(
            "INSERT INTO capabilities (id, name, ability, calibration_error, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET ability = excluded.ability, "
            "calibration_error = excluded.calibration_error, updated_at = excluded.updated_at",
            (capability, capability, ability, error, updated_at),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _remember((_db_file(conn), capability, updated_at), len(pairs))
    return CapabilityReading(capability, ability, error, updated_at, len(pairs))


def update_calibration(capability: str) -> float | None:
    """Recompute `capability`'s ability and calibration_error from its resolved predictions and store them.

    Returns the calibration_error, or None when no resolved prediction carries the tag (nothing is written then).
    A name that can never be a tag (one CS5's redaction would change) has no evidence: None.
    """
    capability = _check_capability(capability)
    if not _taggable(capability):
        return None
    with closing(_open()) as conn:
        reading = _refresh(conn, capability)
    return None if reading is None else reading.calibration_error


def resolve_prediction(prediction_id: str, actual: dict) -> float:
    """Resolve a prediction with CS5 and update the capability it is tagged with. Returns the Brier score.

    CS5's errors pass through unchanged and nothing is written: LookupError for an unknown id,
    AlreadyResolvedError (a ValueError) for a second resolve, TypeError or ValueError for a bad `actual`.
    The capability update is this module's CS5 resolve hook, so it is the same whether the caller uses this or
    `predictions.resolve()`. Once the ledger has scored the prediction, the resolution stands: if the update then
    fails, it is logged and the score still returned (fail open, as decision 0009); the next read or update heals.
    """
    return predictions.resolve(prediction_id, actual)


def _on_resolved(prediction_id: str, expected: object) -> None:
    """CS5 resolve hook: rewrite the tagged capability's row. Never raises."""
    try:
        capability = tag_of(expected)
        if capability is not None:
            update_calibration(capability)
    except Exception as e:  # noqa: BLE001 - the ledger row is committed; a failed update must not hide that
        logger.warning("prediction resolved but capability update failed: %s", type(e).__name__)


predictions.on_resolve(_on_resolved)


def get_capability(capability: str) -> CapabilityReading:
    """What the router reads: ability, calibration_error and the evidence count of `capability`.

    Reads through to the ledger: a row older than the newest tagged resolution is recomputed before it is read.
    An unknown capability reads as unknown (both None, evidence 0), not as an error. Bad name: TypeError or
    ValueError. A reading with `low_evidence` is not a known ability.
    """
    capability = _check_capability(capability)
    if not _taggable(capability):
        return CapabilityReading(capability, None, None, None)
    with closing(_open()) as conn:
        conn.execute("BEGIN")  # one snapshot for the row, the freshness test and the count
        try:
            row = conn.execute(
                "SELECT ability, calibration_error, updated_at FROM capabilities WHERE id = ?", (capability,)
            ).fetchone()
            # a row someone else made (name, family) without evidence says nothing about what came before it
            since = row[2] if row is not None and row[0] is not None else None
            stale = _has_newer_evidence(conn, capability, since)
            n = 0
            if not stale and since is not None:
                key = (_db_file(conn), capability, since)
                n = _counts.get(key)
                if n is None:
                    n = len(_evidence(conn, capability))
                    _remember(key, n)
        finally:
            conn.rollback()
        if stale:
            reading = _refresh(conn, capability)
            if reading is not None:
                return reading
    if row is None:
        return CapabilityReading(capability, None, None, None)
    return CapabilityReading(capability, row[0], row[1], row[2], n)
