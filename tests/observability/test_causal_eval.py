"""E2 evaluation: how often causal attribution credits the wrong gene, and how often it finds the right one.

Synthetic systems with two real genes and two passenger genes, independent Gaussian run noise (sigma) on every run,
6 seeds. Results are written up in build/memory/benchmarks/E2-causal-eval.md. Run with ``-s`` to see the table.
"""
import random

import pytest

from tanishi.observability.causal import attribute, verify_gain

SEEDS = [1, 2, 3, 4, 5, 6]
TRIALS = 200


def evaluate(sigma, effect, trials=TRIALS):
    """(false-accept rate on passengers, recall on real genes) over ``trials`` random systems."""
    false_accepts = misses = 0
    for t in range(trials):
        rng = random.Random(t)
        effects = {"r0": effect, "r1": effect, "p0": 0.0, "p1": 0.0}
        names = list(effects)
        rng.shuffle(names)

        def run(applied, seed, effects=effects, rng=rng):
            return 0.4 + sum(effects[g] for g in applied) + rng.gauss(0, sigma)

        genes = attribute(names, run, SEEDS).genes
        false_accepts += genes["p0"].real + genes["p1"].real
        misses += (not genes["r0"].real) + (not genes["r1"].real)
    return false_accepts / (2 * trials), 1 - misses / (2 * trials)


@pytest.mark.parametrize("sigma", [0.02, 0.1])
def test_passengers_are_almost_never_credited(sigma):
    false_accept, _ = evaluate(sigma, 4 * sigma)
    print(f"\nsigma={sigma} effect=4 sigma: false-accept {false_accept:.3f}")
    assert false_accept <= 0.02


@pytest.mark.parametrize("sigma", [0.02, 0.1])
def test_real_genes_well_above_noise_are_found(sigma):
    _, recall = evaluate(sigma, 6 * sigma)
    print(f"\nsigma={sigma} effect=6 sigma: recall {recall:.3f}")
    assert recall >= 0.9


def test_autoresearch_v0_keep_is_rejected():
    """The v0 failure: a +0.001 change kept against about 0.1 run-to-run noise."""
    accepted = 0
    for t in range(TRIALS):
        rng = random.Random(t)
        def run(version, seed, rng=rng):
            return 0.7 + (0.001 if version == "b" else 0.0) + rng.gauss(0, 0.1)

        verdict = verify_gain(run, "a", "b", SEEDS)
        accepted += verdict.real
    print(f"\nautoresearch-v0 accept rate {accepted / TRIALS:.3f}")
    assert accepted / TRIALS <= 0.02


def test_print_curve():
    """Not a gate: the recall curve against effect size, for the benchmark note."""
    for k in (1, 2, 3, 4, 6):
        fa, rc = evaluate(0.05, k * 0.05, trials=100)
        print(f"\neffect={k} sigma: false-accept {fa:.3f} recall {rc:.3f}", end="")
