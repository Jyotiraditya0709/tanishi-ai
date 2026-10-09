"""Causal Attribution v0: is a gain real, and which change earned it.

Autoresearch v0 kept changes at +0.001 against about 0.1 run-to-run noise (failed-approaches/autoresearch-v0.md).
The rules here stop that:

- every arm needs at least ``MIN_SEEDS`` seeded runs, or ``is_real_gain`` refuses to answer (raises);
- a gain smaller than ``NOISE_MULTIPLE`` times the measured noise is noise;
- a gain no larger than ``MIN_REAL_GAIN`` is never real, so zero-noise (deterministic) arms cannot keep a tiny gain;
- ``ablate`` removes one change at a time and credits each change with what the score lost without it.
"""
from __future__ import annotations

import math
import numbers
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

MIN_SEEDS = 3
NOISE_MULTIPLE = 2.0
# Scores are on a 0 to 1 scale. A gain must be larger than this to be real, even when the measured noise is 0.
MIN_REAL_GAIN = 0.01


@dataclass(frozen=True)
class Verdict:
    """The answer of ``is_real_gain``. ``effect`` is mean(candidate) - mean(baseline)."""

    real: bool
    effect: float
    noise: float
    threshold: float
    n_baseline: int
    n_candidate: int
    reason: str

    def __bool__(self) -> bool:
        return self.real


def _finite(runs: Sequence[float], name: str) -> list[float]:
    if any(isinstance(x, bool) or not isinstance(x, numbers.Real) for x in runs):
        raise ValueError(f"{name} scores must be real numbers, not bool")
    values = [float(x) for x in runs]
    if not all(math.isfinite(x) for x in values):
        raise ValueError(f"{name} has a NaN or infinite score")
    return values


def noise(runs: list[float]) -> float:
    """Run-to-run noise: the sample standard deviation of repeated runs of one arm."""
    values = _finite(runs, "runs")
    if len(values) < 2:
        raise ValueError(f"noise needs at least 2 runs, got {len(values)}")
    return float(statistics.stdev(values))


def is_real_gain(baseline: list[float], candidate: list[float], min_effect: float | None = None) -> Verdict:
    """Is candidate better than baseline by more than measured noise?

    One list element is one seed, paired by position, so both lists must have the same length (use
    ``experiments.seed_scores``). Raises ValueError (refuses to answer) when they do not, or when either arm
    has fewer than ``MIN_SEEDS`` runs. Noise is the larger of the two arms' noise, so a noisy candidate cannot
    hide behind a quiet baseline. The gain is real only when it is larger than the biggest of
    ``NOISE_MULTIPLE`` x noise, ``min_effect`` (when given) and ``MIN_REAL_GAIN``.
    """
    base = _finite(baseline, "baseline")
    cand = _finite(candidate, "candidate")
    if len(base) < MIN_SEEDS or len(cand) < MIN_SEEDS:
        raise ValueError(
            f"need at least {MIN_SEEDS} seeds per arm, got baseline={len(base)} candidate={len(cand)}; refusing to answer"
        )
    if len(base) != len(cand):
        raise ValueError(f"unpaired arms: baseline has {len(base)} seeds, candidate {len(cand)}; refusing to answer")
    if min_effect is not None and (
        isinstance(min_effect, bool) or not (math.isfinite(min_effect) and min_effect >= 0)
    ):
        raise ValueError(f"min_effect must be a finite number >= 0, got {min_effect!r}")

    effect = statistics.fmean(cand) - statistics.fmean(base)
    measured = max(noise(base), noise(cand))
    noise_bar = NOISE_MULTIPLE * measured
    threshold = max(noise_bar, min_effect or 0.0, MIN_REAL_GAIN)

    if effect <= 0:
        real, reason = False, f"no gain: effect {effect:+.4g}"
    elif effect <= noise_bar:
        real, reason = False, f"noise: effect {effect:.4g} <= {NOISE_MULTIPLE:g} x noise {measured:.4g}"
    elif min_effect is not None and effect <= min_effect:
        real, reason = False, f"too small: effect {effect:.4g} <= min_effect {min_effect:.4g}"
    elif effect <= MIN_REAL_GAIN:
        real, reason = False, f"too small: effect {effect:.4g} <= smallest real gain {MIN_REAL_GAIN:g}"
    else:
        real, reason = True, f"real: effect {effect:.4g} > threshold {threshold:.4g}"
    return Verdict(real, effect, measured, threshold, len(base), len(cand), reason)


def ablate(version: Any, changes: list[str], run_fn: Callable[[Any, tuple[str, ...]], float]) -> dict[str, float]:
    """Gain attributed to each change, by removing one change at a time.

    ``run_fn(version, applied)`` scores ``version`` with only the changes in ``applied`` (a tuple, in the
    caller's order). It should average its own seeded repeats. A change's gain is
    ``score(all changes) - score(all changes except this one)``. Exceptions from ``run_fn`` propagate.
    """
    names = list(changes)
    if len(set(names)) != len(names):
        raise ValueError("changes has duplicates")
    if not names:
        return {}

    def score(applied: tuple[str, ...]) -> float:
        value = float(run_fn(version, applied))
        if not math.isfinite(value):
            raise ValueError(f"run_fn returned {value!r} for {applied!r}")
        return value

    full = score(tuple(names))
    return {c: full - score(tuple(x for x in names if x != c)) for c in names}
