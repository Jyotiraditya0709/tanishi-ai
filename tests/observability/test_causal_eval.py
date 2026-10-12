"""E2 evaluation: how often causal attribution credits the wrong gene, and how often it finds the right one.

Synthetic systems with two real genes and two passenger genes, independent Gaussian run noise (sigma) on every run,
6 seeds. Results are written up in build/memory/benchmarks/E2-causal-eval.md. Run with ``-s`` to see the table.
"""
import random

import pytest

from tanishi.observability.causal import MIN_SEEDS, attribute, organ_scores, verify_gain

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


def test_null_change_false_accept_at_the_minimum_seeds():
    """E2-RT-1: at MIN_SEEDS (5) a change that does nothing is rarely accepted (red team measured 0.37%)."""
    rng = random.Random(7)
    trials, accepted = 2000, 0
    for _ in range(trials):
        table = {}

        def run(version, seed, table=table):
            return table.setdefault((version, seed), rng.gauss(0.5, 0.1))

        accepted += verify_gain(run, "a", "b", list(range(MIN_SEEDS))).real
    print(f"\nnull change at {MIN_SEEDS} seeds: accept rate {accepted / trials:.4f}")
    assert accepted / trials <= 0.01


def test_fewer_than_five_seeds_are_refused_and_the_verdict_counts_seeds():
    with pytest.raises(ValueError):
        verify_gain(lambda v, s: 0.5, "a", "b", [1, 2, 3, 4])
    v = verify_gain(lambda v, s: 0.5, "a", "b", SEEDS)
    assert v.n_baseline == v.n_candidate == len(SEEDS)


def test_redundant_genes_mark_the_attribution_incomplete():
    a = attribute(["r1", "r2"], lambda applied, s: 0.5 + (0.3 if applied else 0.0), SEEDS)
    assert a.total.real and not a.complete and a.unattributed == pytest.approx(0.3)
    d = a.as_dict()
    assert d["complete"] is False and d["unattributed"] == pytest.approx(0.3) and d["credited"] == 0.0


def test_additive_genes_leave_nothing_unattributed():
    a = attribute(["x", "y"], lambda applied, s: 0.5 + 0.2 * len(applied), SEEDS)
    assert a.complete and a.unattributed == pytest.approx(0.0)


def test_real_harm_lowers_the_organ_score_through_proposals():
    a = attribute(["good", "bad"], lambda ap, s: 0.5 + (0.3 if "good" in ap else 0) - (0.2 if "bad" in ap else 0), SEEDS)
    quiet = {"organ": "quiet", "gain": 0.0, "real": False}
    out = organ_scores([*a.proposals({"good": "helpful", "bad": "harmful"}), quiet])
    assert out["harmful"] == pytest.approx(-0.2) and out["harmful"] < out["quiet"] < out["helpful"]


def test_a_gene_id_proposed_twice_is_refused():
    p = {"organ": "a", "gene": "g1", "gain": 0.2, "real": True}
    with pytest.raises(ValueError):
        organ_scores([p, dict(p)])


def test_print_curve():
    """Not a gate: the recall curve against effect size, for the benchmark note."""
    for k in (1, 2, 3, 4, 6):
        fa, rc = evaluate(0.05, k * 0.05, trials=100)
        print(f"\neffect={k} sigma: false-accept {fa:.3f} recall {rc:.3f}", end="")
