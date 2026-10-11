"""Causal Attribution, full: is a gain real, which gene earned it, and which organ proposed it.

Builds on OBS3 (``attribution.is_real_gain``, decision 0011) and CS6 (the Genome record's ``attribution`` field).

- ``verify_gain`` runs both arms on every seed itself, so the caller cannot hand-pick runs. Each seed's runs go back
  to back in a seeded random arm order, and the seeds are shuffled, so drift over time lands on both arms equally.
  The verdict uses the OBS3 rules unchanged: at least 3 seeds, a gain must beat 2 x the measured noise and the 0.01
  floor, and a crashed run must be scored 0 by ``run_fn``.
- ``attribute`` runs nothing, everything, and everything minus one change for each change, all on the same seeds.
  A gene's effect is the drop its removal causes, and it gets its own verdict, so a passenger riding with a winner
  earns nothing. Interacting changes still get overlapping credit (decision 0011, documented limit).
- ``organ_scores`` sums, per organ, the attributed gains of its proposals that were judged real.

Every bad input raises ``ValueError`` (decision 0011, rule 12). Exceptions from ``run_fn`` propagate.
"""
from __future__ import annotations

import hashlib
import math
import numbers
import random
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

from tanishi.observability.attribution import MIN_SEEDS, Verdict, is_real_gain


@dataclass(frozen=True)
class Attribution:
    """``total`` is nothing vs everything. ``genes`` maps each change, in the caller's order, to the verdict of
    (everything minus that change) vs (everything): its ``effect`` is the drop that removing it causes."""

    total: Verdict
    genes: Mapping[str, Verdict]

    def as_dict(self) -> dict[str, Any]:
        """Plain JSON, ready for ``genome.record_version(attribution=...)``."""
        return {"total": _verdict_dict(self.total), "genes": {g: _verdict_dict(v) for g, v in self.genes.items()}}


def _verdict_dict(v: Verdict) -> dict[str, Any]:
    d = asdict(v)
    d["effect"], d["noise"], d["threshold"] = float(v.effect), float(v.noise), float(v.threshold)
    return d


def _check_seeds(seeds: Sequence[int]) -> list[int]:
    if isinstance(seeds, (str, bytes)):
        raise ValueError("seeds must be a list of ints, not a string")  # noqa: TRY004
    seeds = list(seeds)
    for s in seeds:
        # bool is an int and int(2.0) would pass as a seed: only a plain int is a seed (decision 0011, rule 10).
        if isinstance(s, bool) or not isinstance(s, int):
            raise ValueError(f"seed must be a plain int, got {s!r}")  # noqa: TRY004
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must not repeat")
    if len(seeds) < MIN_SEEDS:
        raise ValueError(f"need at least {MIN_SEEDS} seeds, got {len(seeds)}; refusing to answer")
    return seeds


def _check_min_effect(min_effect: float | None) -> None:
    if min_effect is None:
        return
    if isinstance(min_effect, bool) or not isinstance(min_effect, numbers.Real) or not (
        math.isfinite(min_effect) and min_effect >= 0
    ):
        raise ValueError(f"min_effect must be a finite number >= 0, got {min_effect!r}")


def _score(value: Any, arm: Any, seed: int) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError(f"run_fn returned {value!r} for {arm!r} seed {seed}; a score is a finite number")
    return float(value)


def _run_interleaved(
    run_fn: Callable[[Any, int], float], arms: Sequence[Hashable], seeds: list[int]
) -> dict[Hashable, list[float]]:
    """Score every arm once per seed. Returns each arm's scores ordered by seed, so arms pair by position.

    Seeds are shuffled and, per seed, all arms run back to back in a shuffled order. The shuffle is seeded from the
    inputs (not ``hash()``, which is salted per process), so the same call always runs in the same order.
    """
    digest = hashlib.sha256(repr((list(arms), seeds)).encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    order = list(seeds)
    rng.shuffle(order)
    by_arm: dict[Hashable, dict[int, float]] = {arm: {} for arm in arms}
    for seed in order:
        shuffled = list(arms)
        rng.shuffle(shuffled)
        for arm in shuffled:
            by_arm[arm][seed] = _score(run_fn(arm, seed), arm, seed)
    return {arm: [scores[s] for s in sorted(seeds)] for arm, scores in by_arm.items()}


def verify_gain(
    run_fn: Callable[[Any, int], float],
    baseline: Any,
    candidate: Any,
    seeds: Sequence[int],
    *,
    min_effect: float | None = None,
) -> Verdict:
    """Run ``run_fn(version, seed)`` once per arm per seed, interleaved, and judge candidate vs baseline (OBS3)."""
    seeds = _check_seeds(seeds)
    _check_min_effect(min_effect)
    # The ledger stores arms as text, so arms that print the same cannot be told apart (decision 0011, rule 11).
    if str(baseline) == str(candidate):
        raise ValueError("baseline and candidate must differ")
    scores = _run_interleaved(run_fn, [baseline, candidate], seeds)
    return is_real_gain(scores[baseline], scores[candidate], min_effect)


def attribute(
    changes: Sequence[str],
    run_fn: Callable[[tuple[str, ...], int], float],
    seeds: Sequence[int],
    *,
    min_effect: float | None = None,
) -> Attribution:
    """One-at-a-time ablation with seeded repeats.

    ``run_fn(applied, seed)`` scores the system with only the changes in ``applied`` switched on (a tuple, in the
    caller's order). Arms: ``()``, all changes, and all changes minus one, for each change. Every arm runs on every
    seed, interleaved as in ``verify_gain``. ``min_effect`` applies to the total and to every gene.
    """
    if isinstance(changes, (str, bytes)):
        raise ValueError("changes must be a list of names, not a string")  # noqa: TRY004
    names = list(changes)
    if not names:
        raise ValueError("no changes to attribute")
    if any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("every change must be a non-blank str")
    if len(set(names)) != len(names):
        raise ValueError("changes has duplicates")
    seeds = _check_seeds(seeds)
    _check_min_effect(min_effect)

    full = tuple(names)
    without = {c: tuple(x for x in names if x != c) for c in names}
    arms = list(dict.fromkeys([(), full, *without.values()]))  # one change: "all minus it" is already ()
    scores = _run_interleaved(run_fn, arms, seeds)
    total = is_real_gain(scores[()], scores[full], min_effect)
    genes = {c: is_real_gain(scores[without[c]], scores[full], min_effect) for c in names}
    return Attribution(total, MappingProxyType(genes))


def organ_scores(proposals: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Score each organ on the gain its proposals produced: the sum of ``gain`` over its proposals with ``real``.

    Each proposal is ``{"organ": str, "gain": float, "real": bool}``, with ``gain`` and ``real`` taken from an
    ``Attribution`` gene. An organ whose proposals were all unreal scores 0.0. The whole input is checked before
    anything is returned.
    """
    gains: dict[str, list[float]] = {}
    for p in proposals:
        if not isinstance(p, Mapping):
            raise ValueError(f"a proposal must be a mapping, got {p!r}")  # noqa: TRY004
        missing = {"organ", "gain", "real"} - p.keys()
        if missing:
            raise ValueError(f"proposal is missing {sorted(missing)}")
        organ, gain, real = p["organ"], p["gain"], p["real"]
        if not isinstance(organ, str) or not organ.strip() or organ != organ.strip():
            raise ValueError(f"organ must be a non-blank str with no surrounding space, got {organ!r}")
        if isinstance(gain, bool) or not isinstance(gain, numbers.Real) or not math.isfinite(gain):
            raise ValueError(f"gain must be a finite number, got {gain!r}")
        if not isinstance(real, bool):
            raise ValueError(f"real must be a bool, got {real!r}")  # noqa: TRY004
        bucket = gains.setdefault(organ, [])
        if real:
            bucket.append(float(gain))
    # fsum is exact, so the score does not depend on proposal order.
    return {organ: math.fsum(values) for organ, values in gains.items()}
