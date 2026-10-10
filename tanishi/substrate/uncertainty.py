"""Uncertainty and the Self Model v0 (node SUB4): resolved predictions become a calibration table per capability.

A prediction is tagged with a capability by an `expected["capability"]` field, a non-blank str, written with CS5's
`predict()`. The tag text is the capability's id in the `capabilities` table (migration 0001); the row is created
on its first update with `name` = the tag. A prediction without a valid tag is untagged: it resolves as usual and
touches no capability.

`resolve_prediction()` is CS5's `resolve()` followed by `update_calibration()` for the tagged capability. The
update recomputes the row from every resolved prediction carrying the tag, so it is idempotent, independent of
resolution order, and also picks up predictions resolved through CS5 directly.

- `calibration_error` is the expected calibration error over CS5's N_BINS equal-width forecast bins:
  sum_k (n_k / N) * |mean_forecast_k - success_rate_k|. 0 is perfect; it lies in [0, 1]. The forecast of success
  is `confidence` if `expected["success"]` else `1 - confidence` (decision 0014), binned as `calibration()` does.
- `ability` is the success rate of those predictions, in [0, 1].

Both stay NULL until there is resolved evidence. `get_capability()` is what the router (SUB3) reads.
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

logger = logging.getLogger(__name__)

TAG = "capability"
N_BINS = predictions.N_BINS


@dataclass(frozen=True)
class CapabilityReading:
    """One capability as the router sees it. `ability` and `calibration_error` are None with no evidence."""

    capability: str
    ability: float | None
    calibration_error: float | None
    updated_at: str | None


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
    return capability


def tag_of(expected: object) -> str | None:
    """The capability a prediction's `expected` is tagged with, or None when it carries no usable tag."""
    if not isinstance(expected, dict):
        return None
    tag = expected.get(TAG)
    return tag if isinstance(tag, str) and tag.strip() else None


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


def update_calibration(capability: str) -> float | None:
    """Recompute `capability`'s ability and calibration_error from its resolved predictions and store them.

    Returns the calibration_error, or None when no resolved prediction carries the tag (nothing is written then).
    Read and write share one BEGIN IMMEDIATE transaction, so two concurrent updates cannot store a stale result
    over a fresher one.
    """
    capability = _check_capability(capability)
    with closing(_open()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            pairs = _evidence(conn, capability)
            if not pairs:
                conn.rollback()
                return None
            error = _ece(pairs)
            ability = math.fsum(o for _, o in pairs) / len(pairs)
            conn.execute(
                "INSERT INTO capabilities (id, name, ability, calibration_error, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET ability = excluded.ability, "
                "calibration_error = excluded.calibration_error, updated_at = excluded.updated_at",
                (capability, capability, ability, error, datetime.now(UTC).isoformat(timespec="microseconds")),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return error


def resolve_prediction(prediction_id: str, actual: dict) -> float:
    """Resolve a prediction with CS5 and update the capability it is tagged with. Returns the Brier score.

    CS5's errors pass through unchanged and nothing is written: LookupError for an unknown id,
    AlreadyResolvedError (a ValueError) for a second resolve, TypeError or ValueError for a bad `actual`.
    Once the ledger has scored the prediction, the resolution stands: if the capability update then fails, it is
    logged and the score still returned (fail open, as decision 0009). The next update of that capability
    recomputes from all evidence, so the row heals.
    """
    score = predictions.resolve(prediction_id, actual)
    try:
        with closing(_open()) as conn:
            row = conn.execute("SELECT expected FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
        capability = tag_of(predictions._object(row[0]) if row else None)
        if capability is not None:
            update_calibration(capability)
    except Exception as e:  # noqa: BLE001 - the ledger row is committed; a failed update must not hide that
        logger.warning("prediction resolved but capability update failed: %s", type(e).__name__)
    return score


def get_capability(capability: str) -> CapabilityReading:
    """What the router reads: the stored ability and calibration_error of `capability`. Never cached.

    An unknown capability reads as unknown (both None), not as an error.
    """
    capability = _check_capability(capability)
    with closing(_open()) as conn:
        row = conn.execute(
            "SELECT ability, calibration_error, updated_at FROM capabilities WHERE id = ?", (capability,)
        ).fetchone()
    if row is None:
        return CapabilityReading(capability, None, None, None)
    return CapabilityReading(capability, row[0], row[1], row[2])
