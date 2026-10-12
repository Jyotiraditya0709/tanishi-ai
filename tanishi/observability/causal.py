"""Causal Attribution, full: is a gain real, which gene earned it, and which organ proposed it.

Builds on OBS3 (``attribution.is_real_gain``, decision 0011) and CS6 (the Genome record's ``attribution`` field).

- ``verify_gain`` runs both arms on every seed itself, so the caller cannot hand-pick runs. Each seed's runs go back
  to back in a seeded random arm order, and the seeds are shuffled, so drift over time lands on both arms equally.
  The verdict uses the OBS3 rules unchanged (a gain must beat 2 x the measured noise and the 0.01 floor, and a
  crashed run must be scored 0 by ``run_fn``), but E2 needs ``MIN_SEEDS`` (5) seeds where OBS3 needs 3: at 3 seeds
  a change that does nothing is accepted about 2% of the time (decision 0028).
- ``attribute`` runs nothing, everything, and everything minus one change for each change, all on the same seeds.
  A gene's effect is the drop its removal causes, and it gets its own verdict, so a passenger riding with a winner
  earns nothing. Interacting changes still get overlapping credit (decision 0011, documented limit), and redundant
  changes get none: ``Attribution.unattributed`` shows that missing credit.
- ``organ_scores`` sums, per organ, the attributed gains of its proposals that were judged real, so real harm
  lowers the score.

Every bad input raises ``ValueError`` (decision 0011, rule 12). Exceptions from ``run_fn`` propagate.
"""
from __future__ import annotations

import hashlib
import math
import numbers
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from tanishi.observability.attribution import (
    FLOOR_TOLERANCE,
    MIN_REAL_GAIN,
    Verdict,
    is_real_gain,
)

# Stricter than OBS3's 3 on purpose. Null-change false-accept rates (red team, 6000 trials): 3 seeds 2.0%,
# 4 seeds 0.7%, 5 seeds 0.37%, 6 seeds 0.07% (decision 0028).
MIN_SEEDS = 5


@dataclass(frozen=True)
class Attribution:
    """``total`` is nothing vs everything. ``genes`` maps each change, in the caller's order, to the verdict of
    (everything minus that change) vs (everything): its ``effect`` is the drop that removing it causes.

    ``genes`` is a plain dict so the result can be pickled, deep-copied and passed to ``dataclasses.asdict``.
    """

    total: Verdict
    genes: dict[str, Verdict]

    @property
    def credited(self) -> float:
        """The sum of the effects of the genes judged real."""
        return math.fsum(v.effect for v in self.genes.values() if v.real)

    @property
    def unattributed(self) -> float:
        """The real total gain (0 when the total is not real) minus the gain credited to genes.

        Positive when a real gain landed on no gene, for example two redundant genes where either alone gives the
        whole gain, so removing one costs nothing. Negative when interacting genes are over-credited.
        """
        return (self.total.effect if self.total.real else 0.0) - self.credited

    @property
    def complete(self) -> bool:
        """False when more gain is missing credit than the total's threshold, so the missing part would be real."""
        return self.unattributed <= self.total.threshold

    def as_dict(self) -> dict[str, Any]:
        """Plain JSON, ready for ``genome.record_version(attribution=...)``."""
        return {
            "total": _verdict_dict(self.total),
            "genes": {g: _verdict_dict(v) for g, v in self.genes.items()},
            "credited": self.credited,
            "unattributed": self.unattributed,
            "complete": self.complete,
        }

    def proposals(self, organ_of: Mapping[str, str]) -> list[dict[str, Any]]:
        """One ``organ_scores`` proposal per gene, with the gene id. ``organ_of`` maps every gene to its organ.

        ``real`` is True when the gene's gain is real or its harm is (removing it is a real gain by the same OBS3
        rules), so a gene that really hurts lowers its organ's score.
        """
        if not isinstance(organ_of, Mapping):
            raise ValueError("organ_of must map each gene to its organ")  # noqa: TRY004
        missing = [g for g in self.genes if g not in organ_of]
        if missing:
            raise ValueError(f"no organ for genes {missing}")
        return [
            {"organ": organ_of[g], "gene": g, "gain": v.effect, "real": v.real or _harm_is_real(v)}
            for g, v in self.genes.items()
        ]


def _harm_is_real(v: Verdict) -> bool:
    # The reverse comparison has the same noise and threshold (noise is the larger arm's), and its effect is -effect.
    return -v.effect > v.threshold and -v.effect > MIN_REAL_GAIN + FLOOR_TOLERANCE


def _verdict_dict(v: Verdict) -> dict[str, Any]:
    d = asdict(v)
    d["effect"], d["noise"], d["threshold"] = float(v.effect), float(v.noise), float(v.threshold)
    return d


def _as_list(items: Any, what: str) -> list[Any]:
    if isinstance(items, (str, bytes)):
        raise ValueError(f"{what} must be a list, not a string")  # noqa: TRY004
    try:
        return list(items)
    except TypeError:
        raise ValueError(f"{what} must be a list, got {items!r}") from None


def _real_number(value: Any, what: str) -> float:
    """``value`` as a finite float; ``bool``, non-numbers, NaN, infinities and ints too big for a float raise."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{what} must be a finite number, got {value!r}")  # noqa: TRY004
    try:
        f = float(value)
    except (OverflowError, TypeError):
        raise ValueError(f"{what} must be a finite number, got {value!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    return f


def _check_seeds(seeds: Sequence[int]) -> list[int]:
    seeds = _as_list(seeds, "seeds")
    for s in seeds:
        # bool is an int and int(2.0) would pass as a seed: only a plain int is a seed (decision 0011, rule 10).
        if isinstance(s, bool) or not isinstance(s, int):
            raise ValueError(f"seed must be a plain int, got {s!r}")  # noqa: TRY004
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must not repeat")
    if len(seeds) < MIN_SEEDS:
        raise ValueError(f"need at least {MIN_SEEDS} seeds, got {len(seeds)}; refusing to answer")
    return seeds


def _check_min_effect(min_effect: float | None) -> float | None:
    if min_effect is None:
        return None
    value = _real_number(min_effect, "min_effect")
    if value < 0:
        raise ValueError(f"min_effect must be a finite number >= 0, got {min_effect!r}")
    return value


def _judge(base: list[float], cand: list[float], min_effect: float | None) -> Verdict:
    """OBS3's verdict with plain float numbers. A mean or noise that overflows a float raises ``ValueError``."""
    try:
        v = is_real_gain(base, cand, min_effect)
    except OverflowError:
        raise ValueError("scores too large: their mean or noise overflows a float") from None
    v = replace(v, effect=float(v.effect), noise=float(v.noise), threshold=float(v.threshold))
    if not all(math.isfinite(x) for x in (v.effect, v.noise, v.threshold)):
        raise ValueError("scores too large: their difference overflows a float")
    return v


def _run_interleaved(run_fn: Callable[[Any, int], float], arms: Sequence[Any], seeds: list[int]) -> list[list[float]]:
    """Score every arm once per seed. Returns each arm's scores (by arm index) ordered by seed, so arms pair by
    position. Arms are kept apart by index, not value, so unhashable arms work and ``1`` and ``1.0`` stay separate.

    Seeds are shuffled and, per seed, all arms run back to back in a shuffled order. The shuffle is seeded from the
    inputs (not ``hash()``, which is salted per process), so the same call always runs in the same order.
    """
    digest = hashlib.sha256(repr((list(arms), seeds)).encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    order = list(seeds)
    rng.shuffle(order)
    by_arm: list[dict[int, float]] = [{} for _ in arms]
    for seed in order:
        shuffled = list(range(len(arms)))
        rng.shuffle(shuffled)
        for i in shuffled:
            by_arm[i][seed] = _real_number(run_fn(arms[i], seed), f"run_fn score for {arms[i]!r} seed {seed}")
    return [[scores[s] for s in sorted(seeds)] for scores in by_arm]


def verify_gain(
    run_fn: Callable[[Any, int], float],
    baseline: Any,
    candidate: Any,
    seeds: Sequence[int],
    *,
    min_effect: float | None = None,
) -> Verdict:
    """Run ``run_fn(version, seed)`` once per arm per seed, interleaved, and judge candidate vs baseline (OBS3).

    Needs at least ``MIN_SEEDS`` distinct seeds; ``n_baseline`` and ``n_candidate`` on the verdict record how many.
    """
    seeds = _check_seeds(seeds)
    min_effect = _check_min_effect(min_effect)
    # The ledger stores arms as text, so arms that print the same cannot be told apart (decision 0011, rule 11).
    if str(baseline) == str(candidate):
        raise ValueError("baseline and candidate must differ")
    base, cand = _run_interleaved(run_fn, [baseline, candidate], seeds)
    return _judge(base, cand, min_effect)


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
    names = _as_list(changes, "changes")
    if not names:
        raise ValueError("no changes to attribute")
    if any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("every change must be a non-blank str")
    if len(set(names)) != len(names):
        raise ValueError("changes has duplicates")
    seeds = _check_seeds(seeds)
    min_effect = _check_min_effect(min_effect)

    full = tuple(names)
    without = {c: tuple(x for x in names if x != c) for c in names}
    arms = list(dict.fromkeys([(), full, *without.values()]))  # one change: "all minus it" is already ()
    runs = _run_interleaved(run_fn, arms, seeds)
    scores = {arm: runs[i] for i, arm in enumerate(arms)}
    total = _judge(scores[()], scores[full], min_effect)
    genes = {c: _judge(scores[without[c]], scores[full], min_effect) for c in names}
    return Attribution(total, genes)


def organ_scores(proposals: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Score each organ on the gain its proposals produced: the sum of ``gain`` over its proposals with ``real``.

    Each proposal is ``{"organ": str, "gain": float, "real": bool}``, with ``gain`` and ``real`` taken from an
    ``Attribution`` gene (``Attribution.proposals`` builds them), plus an optional ``"gene": str`` id. The same gene
    id twice in one call is refused, so one gene cannot be counted twice. A real negative gain (real harm) lowers
    the score, so scores can be negative. An organ whose proposals were all unreal scores 0.0. The whole input is
    checked before anything is returned.
    """
    gains: dict[str, list[float]] = {}
    genes: set[str] = set()
    for p in _as_list(proposals, "proposals"):
        if not isinstance(p, Mapping):
            raise ValueError(f"a proposal must be a mapping, got {p!r}")  # noqa: TRY004
        missing = {"organ", "gain", "real"} - p.keys()
        if missing:
            raise ValueError(f"proposal is missing {sorted(missing)}")
        organ, real = p["organ"], p["real"]
        if not isinstance(organ, str) or not organ.strip() or organ != organ.strip():
            raise ValueError(f"organ must be a non-blank str with no surrounding space, got {organ!r}")
        gain = _real_number(p["gain"], "gain")
        if not isinstance(real, bool):
            raise ValueError(f"real must be a bool, got {real!r}")  # noqa: TRY004
        if "gene" in p:
            gene = p["gene"]
            if not isinstance(gene, str) or not gene.strip():
                raise ValueError(f"gene must be a non-blank str, got {gene!r}")
            if gene in genes:
                raise ValueError(f"gene {gene!r} is proposed twice")
            genes.add(gene)
        bucket = gains.setdefault(organ, [])
        if real:
            bucket.append(gain)
    # fsum is exact, so the score does not depend on proposal order.
    try:
        return {organ: math.fsum(values) for organ, values in gains.items()}
    except OverflowError:
        raise ValueError("organ score overflows a float") from None
