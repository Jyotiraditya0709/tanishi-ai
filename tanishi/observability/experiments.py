"""Experiment ledger: plan seeded, interleaved runs and record their scores in the Core State.

``interleave`` gives both arms exactly the same (task, seed) pairs. Each pair runs back to back in a random
arm order, and the pairs themselves are shuffled, so drift over time (warm caches, rate limits, a model
update mid-run) lands on both arms equally. The shuffle is seeded from the inputs: the same plan always
comes out the same, and different plans come out differently.

Each run is one row of the ``experiments`` table (CS1). ``arm`` and ``task`` go in ``meta``, since the
table has no columns for them. One (arm, task, seed) gets one row: a rerun goes under a new arm label.
``seed_scores`` reads the rows back as one score per seed per arm (the mean over tasks), keeping only seeds
that both arms ran on the same tasks, which is what ``attribution.is_real_gain`` takes.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import random
import sqlite3
import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self


@dataclass(frozen=True)
class Run:
    """One planned run: ``arm`` (the baseline or the candidate) on ``task`` with ``seed``."""

    arm: Any
    task: Any
    seed: Any


class SeedScores(tuple):
    """``(baseline, candidate)`` per-seed scores, paired by seed. ``dropped`` lists the seeds left out."""

    dropped: list[int]

    def __new__(cls, baseline: list[float], candidate: list[float], dropped: list[int]) -> Self:
        self = super().__new__(cls, (baseline, candidate))
        self.dropped = dropped
        return self


def _check_seed(seed: Any) -> int:
    # bool is an int, and int(1.9) == 1 would merge two seeds, so only a plain int is a seed.
    # ValueError, not TypeError: every bad ledger input raises ValueError (decision 0011).
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"seed must be a plain int, got {seed!r}")  # noqa: TRY004
    return seed


def _plan_seed(baseline: Any, candidate: Any, tasks: Sequence[Any], seeds: Sequence[Any]) -> int:
    # Not hash(): str hashing is salted per process, and the plan must be reproducible.
    digest = hashlib.sha256(repr((baseline, candidate, list(tasks), list(seeds))).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def interleave(baseline: Any, candidate: Any, tasks: Sequence[Any], seeds: Sequence[Any]) -> list[Run]:
    """Every (task, seed) for both arms, in a random but reproducible interleaved order."""
    if isinstance(tasks, (str, bytes)) or isinstance(seeds, (str, bytes)):
        raise ValueError("tasks and seeds must be lists, not a string")  # noqa: TRY004
    tasks, seeds = list(tasks), list(seeds)
    for seed in seeds:
        _check_seed(seed)
    # The ledger stores arms as text, so arms that print the same cannot be told apart.
    if str(baseline) == str(candidate):
        raise ValueError("baseline and candidate must differ, or their runs cannot be told apart")
    if len(set(map(repr, tasks))) != len(tasks) or len(set(seeds)) != len(seeds):
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
    """Write one finished run to the ledger and return its id. A crashed run should be recorded as 0.

    Raises ValueError for a second row with the same arm, task and seed in this experiment: record a rerun
    under a new arm label instead, so no score is ever overwritten or averaged away.
    """
    arm, task = str(run.arm), str(run.task)
    if arm not in (baseline, candidate):
        raise ValueError(f"run arm {run.arm!r} is neither {baseline!r} nor {candidate!r}")
    seed = _check_seed(run.seed)
    if isinstance(score, bool) or not isinstance(score, numbers.Real) or not math.isfinite(score):
        raise ValueError(f"score must be a finite real number, got {score!r}")
    if cost is not None and (
        isinstance(cost, bool) or not isinstance(cost, numbers.Real) or not math.isfinite(cost) or cost < 0
    ):
        raise ValueError(f"cost must be a finite number >= 0, got {cost!r}")
    row_id = uuid.uuid4().hex
    extra = {**(meta or {}), "arm": arm, "task": task}
    with conn:
        taken = conn.execute(
            "SELECT 1 FROM experiments WHERE baseline = ? AND candidate = ? AND task_set = ? AND seed = ? "
            "AND json_extract(meta, '$.arm') = ? LIMIT 1",
            (baseline, candidate, task, seed, arm),
        ).fetchone()
        if taken:
            raise ValueError(f"arm {arm!r} already has a row for task {task!r} seed {seed}; use a new arm label")
        conn.execute(
            "INSERT INTO experiments (id, ts, candidate, baseline, task_set, seed, score, cost, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                datetime.now(UTC).isoformat(),
                candidate,
                baseline,
                task,
                seed,
                float(score),
                None if cost is None else float(cost),
                json.dumps(extra, sort_keys=True),
            ),
        )
    return row_id


def seed_scores(conn: sqlite3.Connection, baseline: str, candidate: str) -> SeedScores:
    """Per-seed scores (mean over tasks) for (baseline arm, candidate arm) of one experiment, ordered by seed.

    Only seeds where both arms have rows for the same set of tasks are kept, so the two lists are paired.
    The other seeds are in ``.dropped``. Unpacks as ``base, cand = seed_scores(...)``.
    """
    rows = conn.execute(
        "SELECT json_extract(meta, '$.arm'), task_set, seed, score FROM experiments "
        "WHERE baseline = ? AND candidate = ?",
        (baseline, candidate),
    ).fetchall()
    by_arm: dict[str, dict[int, dict[str, float]]] = {baseline: {}, candidate: {}}
    for arm, task, seed, score in rows:
        if arm not in by_arm:
            continue
        tasks = by_arm[arm].setdefault(seed, {})
        if task in tasks:
            raise ValueError(f"ledger has two rows for arm {arm!r} task {task!r} seed {seed}")
        tasks[task] = score

    base_rows, cand_rows = by_arm[baseline], by_arm[candidate]
    kept, dropped = [], []
    for seed in sorted(base_rows.keys() | cand_rows.keys()):
        b, c = base_rows.get(seed), cand_rows.get(seed)
        (kept if b and c and b.keys() == c.keys() else dropped).append(seed)
    return SeedScores(
        [statistics.fmean(base_rows[s].values()) for s in kept],
        [statistics.fmean(cand_rows[s].values()) for s in kept],
        dropped,
    )
