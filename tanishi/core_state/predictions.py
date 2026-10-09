"""The prediction ledger (node CS5): before an action she writes what she expects, after it reality scores her.

A prediction is `expected` (a dict that must hold a bool `success`, plus any other fields) and a `confidence` in
[0, 1] that `expected` holds. So the forecast probability of success is `confidence` when `expected["success"]` is
true and `1 - confidence` when it is false. resolve() scores the success field with the Brier score,
(forecast - outcome) ** 2; every other field of `expected` and `actual` is stored for later scorers (latency, ...).

Rows live in the `predictions` table of migration 0001. A prediction resolves exactly once: the read, the score and
the update share one BEGIN IMMEDIATE transaction, and the update only touches a row whose resolved_at is NULL.
Events (`prediction`, `prediction_resolved`) go through CS2's emit() after the change has committed, because emit()
takes its own write lock. A failed emit() is logged and noted as a log gap by emit() itself; the change stands, as
in the belief store (decision 0009). Stored fields and event payloads are redacted for secrets.

tool_forecast() is the day-one forecaster ToolRegistry.execute() uses: the tool's recent success rate under a prior
that tools usually work, and the median of its recent latencies (decision 0014).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import statistics
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from tanishi.core_state.db import migrate, open_db
from tanishi.core_state.events import current_task, emit, redact

logger = logging.getLogger(__name__)

ACTOR = "core_state.predictions"
PREDICT_EVENT = "prediction"
RESOLVE_EVENT = "prediction_resolved"
STALE_AFTER_HOURS = 24.0
N_BINS = 10

# tool_forecast(): the prior is PRIOR_WEIGHT pseudo-calls at PRIOR_SUCCESS, and only the last HISTORY calls count,
# so a tool that breaks (or gets fixed) moves the forecast within a few calls.
PRIOR_SUCCESS = 0.75
PRIOR_WEIGHT = 4
HISTORY = 50


class AlreadyResolvedError(ValueError):
    """resolve() was called on a prediction that has already been scored."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _open() -> sqlite3.Connection:
    conn = open_db()
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _to_json(value: dict) -> str:
    """Redacted JSON text. Raises TypeError or ValueError for anything that is not plain JSON (NaN included)."""
    return json.dumps(redact(value), ensure_ascii=False, allow_nan=False)


def _check_fields(name: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a dict, got {type(value).__name__}")
    if "success" not in value:
        raise ValueError(f"{name} must hold a 'success' field: it is the field the Brier score is taken on")
    if not isinstance(value["success"], bool):
        raise TypeError(f"{name}['success'] must be a bool")


def _forecast(expected: dict, confidence: float) -> float:
    """Probability of success implied by a prediction."""
    return confidence if expected.get("success") is True else 1.0 - confidence


def _emit(kind: str, payload: dict, session_id: str | None) -> None:
    try:
        emit(kind, payload, actor=ACTOR, session_id=session_id)
    except Exception as e:  # noqa: BLE001 - never log the payload: it may hold what we failed to redact
        logger.warning("could not record %s event: %s", kind, type(e).__name__)


def predict(about: str, expected: dict, confidence: float) -> str:
    """Write a prediction and return its id.

    `about` names what is predicted (`tool:web_search`); calibration() groups by its prefix. `expected` must hold a
    bool `success`; `confidence` in [0, 1] is the confidence that `expected` holds.
    Raises ValueError for a blank `about`, a confidence outside [0, 1] or not finite, an `expected` without a
    `success` or not plain JSON; TypeError for wrong types (a `success` that is not a bool included).
    Nothing is written when it raises.
    """
    if not isinstance(about, str):
        raise TypeError("about must be a str")
    if not about.strip():
        raise ValueError("about must not be blank")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TypeError("confidence must be a number")
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):  # also false for NaN
        raise ValueError("confidence must be in [0, 1]")
    _check_fields("expected", expected)
    expected_text = _to_json(expected)
    about = redact(about)

    pid = f"pred_{uuid.uuid4().hex}"
    ts = _now()
    conn = _open()
    try:
        with conn:
            conn.execute(
                "INSERT INTO predictions (id, ts, about, expected, confidence) VALUES (?, ?, ?, ?, ?)",
                (pid, ts, about, expected_text, confidence),
            )
    finally:
        conn.close()
    task_id, session_id = current_task()
    _emit(PREDICT_EVENT, {"id": pid, "about": about, "expected": json.loads(expected_text),
                          "confidence": confidence, "task_id": task_id}, session_id)
    return pid


def resolve(prediction_id: str, actual: dict) -> float:
    """Score a prediction against what happened and return its Brier score for the success field.

    Raises LookupError for an unknown id, AlreadyResolvedError (a ValueError) if it was resolved before, and
    TypeError or ValueError for an `actual` that is not a dict with a bool `success`. A refused call changes nothing,
    so the prediction can still be resolved.
    """
    if not isinstance(prediction_id, str):
        raise TypeError("prediction_id must be a str")
    _check_fields("actual", actual)
    actual_text = _to_json(actual)
    outcome = 1.0 if actual["success"] else 0.0

    conn = _open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT about, expected, confidence, resolved_at FROM predictions WHERE id = ?",
                               (prediction_id,)).fetchone()
            if row is None:
                raise LookupError(f"no prediction with id {prediction_id!r}")
            about, expected_text, confidence, resolved_at = row
            if resolved_at is not None:
                raise AlreadyResolvedError(f"prediction {prediction_id!r} was already resolved at {resolved_at}")
            expected = json.loads(expected_text) if expected_text else {}
            score = (_forecast(expected, confidence) - outcome) ** 2
            cur = conn.execute(
                "UPDATE predictions SET actual = ?, resolved_at = ?, score = ? WHERE id = ? AND resolved_at IS NULL",
                (actual_text, _now(), score, prediction_id),
            )
            if cur.rowcount != 1:  # cannot happen under the write lock; refuse rather than score twice
                raise AlreadyResolvedError(f"prediction {prediction_id!r} was already resolved")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()
    task_id, session_id = current_task()
    _emit(RESOLVE_EVENT, {"id": prediction_id, "about": about, "actual": json.loads(actual_text),
                          "score": score, "task_id": task_id}, session_id)
    return score


def calibration(about_prefix: str) -> dict:
    """Brier score and reliability bins over the resolved predictions whose `about` starts with `about_prefix`.

    The prefix is literal (no wildcards); "" covers everything. Returns
    `{"brier": mean score or None, "n": count, "bins": [...]}` with N_BINS equal-width bins on the forecast
    probability of success, each `{"lo", "hi", "n", "mean_forecast", "success_rate"}` (the last two None when
    empty). A calibrated forecaster has mean_forecast close to success_rate in every bin. Read only.
    """
    if not isinstance(about_prefix, str):
        raise TypeError("about_prefix must be a str")
    conn = _open()
    try:
        rows = conn.execute(
            "SELECT expected, confidence, actual, score FROM predictions "
            "WHERE resolved_at IS NOT NULL AND score IS NOT NULL AND substr(about, 1, length(?)) = ?",
            (about_prefix, about_prefix),
        ).fetchall()
    finally:
        conn.close()

    forecasts: list[list[float]] = [[] for _ in range(N_BINS)]
    outcomes: list[list[float]] = [[] for _ in range(N_BINS)]
    scores = []
    for expected_text, confidence, actual_text, score in rows:
        p = _forecast(json.loads(expected_text) if expected_text else {}, confidence)
        k = min(int(p * N_BINS), N_BINS - 1)
        forecasts[k].append(p)
        outcomes[k].append(1.0 if actual_text and json.loads(actual_text).get("success") is True else 0.0)
        scores.append(score)
    bins = []
    for k in range(N_BINS):
        n = len(forecasts[k])
        bins.append({"lo": k / N_BINS, "hi": (k + 1) / N_BINS, "n": n,
                     "mean_forecast": statistics.fmean(forecasts[k]) if n else None,
                     "success_rate": statistics.fmean(outcomes[k]) if n else None})
    return {"brier": statistics.fmean(scores) if scores else None, "n": len(scores), "bins": bins}


def unresolved_older_than(hours: float = STALE_AFTER_HOURS) -> list[dict]:
    """Unresolved predictions made more than `hours` ago, oldest first, as `{"id", "about", "ts", "confidence"}`.

    A prediction whose ts cannot be read as a time is listed too: nobody can show it is fresh. Read only.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    conn = _open()
    try:
        rows = conn.execute(
            "SELECT id, about, ts, confidence FROM predictions WHERE resolved_at IS NULL ORDER BY ts, id"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for pid, about, ts, confidence in rows:
        try:
            made = datetime.fromisoformat(ts)
            made = made.replace(tzinfo=UTC) if made.tzinfo is None else made
            stale = made < cutoff
        except (TypeError, ValueError):
            stale = True
        if stale:
            out.append({"id": pid, "about": about, "ts": ts, "confidence": confidence})
    return out


def tool_forecast(tool_name: str) -> tuple[float, float | None]:
    """(probability of success, expected latency in ms or None) for the next call of `tool_name`.

    The success rate of its last HISTORY resolved calls, pulled toward PRIOR_SUCCESS by PRIOR_WEIGHT pseudo-calls,
    so it is never exactly 0 or 1. Latency is the median `latency_ms` of those calls, None with no history.
    """
    about = f"tool:{tool_name}"
    conn = _open()
    try:
        rows = conn.execute(
            "SELECT actual FROM predictions WHERE about = ? AND resolved_at IS NOT NULL "
            "ORDER BY resolved_at DESC LIMIT ?",
            (redact(about), HISTORY),
        ).fetchall()
    finally:
        conn.close()
    successes, latencies = 0, []
    for (actual_text,) in rows:
        actual = json.loads(actual_text) if actual_text else {}
        successes += actual.get("success") is True
        ms = actual.get("latency_ms")
        if isinstance(ms, (int, float)) and not isinstance(ms, bool) and math.isfinite(ms):
            latencies.append(float(ms))
    p = (successes + PRIOR_SUCCESS * PRIOR_WEIGHT) / (len(rows) + PRIOR_WEIGHT)
    return p, (statistics.median(latencies) if latencies else None)
