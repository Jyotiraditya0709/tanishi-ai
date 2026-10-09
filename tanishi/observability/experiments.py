"""Experiment ledger: plan seeded, interleaved runs and record their scores in the Core State.

``interleave`` gives both arms exactly the same (task, seed) pairs. Each pair runs back to back in a random
arm order, and the pairs themselves are shuffled, so drift over time (warm caches, rate limits, a model
update mid-run) lands on both arms equally. The shuffle is seeded from the inputs: the same plan always
comes out the same, and different plans come out differently.

Each run is one row of the ``experiments`` table (CS1). ``arm`` and ``task`` go in ``meta``, since the
table has no columns for them. ``seed_scores`` reads them back as one score per seed per arm (the mean over
tasks), which is what ``attribution.is_real_gain`` takes.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Run:
    """One planned run: ``arm`` (the baseline or the candidate) on ``task`` with ``seed``."""

    arm: Any
    task: Any
    seed: Any


def _plan_seed(baseline: Any, candidate: Any, tasks: Sequence[Any], seeds: Sequence[Any]) -> int:
    # Not hash(): str hashing is salted per process, and the plan must be reproducible.
    digest = hashlib.sha256(repr((baseline, candidate, list(tasks), list(seeds))).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def interleave(baseline: Any, candidate: Any, tasks: Sequence[Any], seeds: Sequence[Any]) -> list[Run]:
    """Every (task, seed) for both arms, in a random but reproducible interleaved order."""
    tasks, seeds = list(tasks), list(seeds)
    if baseline == candidate:
        raise ValueError("baseline and candidate must differ, or their runs cannot be told apart")
    if len(set(map(repr, tasks))) != len(tasks) or len(set(map(repr, seeds))) != len(seeds):
        raise ValueError("tasks and seeds must not repeat")
    rng = random.Random(_plan_seed(baseline, candidate, tasks, seeds))
    pairs = [(t, s) for t in tasks for s in seeds]
    rng.shuffle(pairs)
    runs: list[Run] = []
    for task, seed in pairs:
        arms = [baseline, candidate]
        rng.shuffle(arms)
        runs.extend(Run(arm, task, seed) for arm in arms)
    return runs


def record_run(
    conn: sqlite3.Connection,
    run: Run,
    score: float,
    *,
    baseline: str,
    candidate: str,
    cost: float | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Write one finished run to the ledger and return its id. A crashed run should be recorded as 0."""
    if str(run.arm) not in (baseline, candidate):
        raise ValueError(f"run arm {run.arm!r} is neither {baseline!r} nor {candidate!r}")
    if not math.isfinite(score):
        raise ValueError(f"score must be finite, got {score!r}")
    row_id = uuid.uuid4().hex
    extra = {**(meta or {}), "arm": str(run.arm), "task": str(run.task)}
    with conn:
        conn.execute(
            "INSERT INTO experiments (id, ts, candidate, baseline, task_set, seed, score, cost, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                datetime.now(UTC).isoformat(),
                candidate,
                baseline,
                str(run.task),
                int(run.seed),
                float(score),
                cost,
                json.dumps(extra, sort_keys=True),
            ),
        )
    return row_id


def seed_scores(conn: sqlite3.Connection, baseline: str, candidate: str) -> tuple[list[float], list[float]]:
    """Per-seed scores (mean over tasks) for (baseline arm, candidate arm) of one experiment, ordered by seed."""
    rows = conn.execute(
        "SELECT json_extract(meta, '$.arm'), seed, score FROM experiments WHERE baseline = ? AND candidate = ?",
        (baseline, candidate),
    ).fetchall()
    by_arm: dict[str, dict[int, list[float]]] = {baseline: {}, candidate: {}}
    for arm, seed, score in rows:
        if arm in by_arm:
            by_arm[arm].setdefault(seed, []).append(score)

    def per_seed(arm: str) -> list[float]:
        return [statistics.fmean(v) for _, v in sorted(by_arm[arm].items())]

    return per_seed(baseline), per_seed(candidate)
