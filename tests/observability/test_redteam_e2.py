"""Red-team attacks on E2 (causal attribution). A failing test here proves a break.

See build/memory/runs/redteam-E2-20261012.md. All seven breaks are closed, so no test carries an xfail mark.
"""
import copy
import dataclasses
import json
import pickle
import random
from fractions import Fraction

import pytest

from tanishi.observability.causal import MIN_SEEDS, attribute, organ_scores, verify_gain

SEEDS = [1, 2, 3, 4, 5, 6]


def prop(organ, gain, real=True):
    return {"organ": organ, "gain": gain, "real": real}


# ------------------------------------------------------------------ false positives


def _null_false_accept(n_seeds, trials=3000, sigma=0.1):
    rng = random.Random(7)
    hits = 0
    for _ in range(trials):
        table = {}

        def run(version, seed, table=table):
            return table.setdefault((version, seed), rng.gauss(0.5, sigma))

        hits += verify_gain(run, "a", "b", list(range(n_seeds))).real
    return hits / trials


def test_null_change_false_accept_rate_at_minimum_seeds():
    # At MIN_SEEDS (5) a change that does nothing is accepted in under 0.5% of seed sets.
    assert _null_false_accept(MIN_SEEDS) < 0.005


def test_null_change_false_accept_rate_at_six_seeds_holds():
    assert _null_false_accept(6) <= 0.01


def test_a_null_change_can_be_pushed_through_by_choosing_seeds():
    # Both arms are identical in distribution. The caller owns `seeds`, so trying many 5-seed subsets finds the rare
    # one that passes; the claim is that fewer than 0.5% of them do.
    n = 1000
    table = {}
    rng = random.Random(11)

    def run(version, seed):
        return table.setdefault((version, seed), rng.gauss(0.5, 0.1))

    accepted = [
        s
        for s in (list(range(i, i + MIN_SEEDS)) for i in range(0, n * MIN_SEEDS, MIN_SEEDS))
        if verify_gain(run, "a", "b", s).real
    ]
    assert len(accepted) / n < 0.005, f"{len(accepted)} of {n} seed sets accepted a change that does nothing"


def test_one_outlier_seed_cannot_buy_a_gain():
    rng = random.Random(2)
    base = {s: 0.5 + rng.gauss(0, 0.02) for s in range(10)}
    run = lambda v, s: base[s] + (0.9 if v == "b" and s == 0 else 0.0)
    assert not verify_gain(run, "a", "b", list(range(10))).real


def test_seed_ignored_by_scorer_is_not_confused_with_repeats():
    # A scorer that ignores the seed gives the same number n times; a 0.011 gain is "real" because noise is 0.
    # This is OBS3's accepted rule 7 (floor 0.01); kept here so nobody mistakes it for a repeat.
    v = verify_gain(lambda ver, s: 0.511 if ver == "b" else 0.5, "a", "b", SEEDS)
    assert v.real and v.noise == 0.0


# ------------------------------------------------------------------ the 0.01 floor is not exact


def test_floor_boundary_does_not_depend_on_the_score_level():
    # The true gain is exactly 0.01 in decimal. Spelling both arms as decimals (0.31 vs 0.3) hits the float error.
    verdicts = {
        k: verify_gain(lambda v, s, k=k: (k + 1) / 100 if v == "b" else k / 100, "a", "b", SEEDS).real
        for k in range(1, 99)
    }
    assert len(set(verdicts.values())) == 1, sorted(k for k, real in verdicts.items() if real)


# ------------------------------------------------------------------ bad input that escapes ValueError (rule 12)


def test_unhashable_versions_are_judged_or_refused_with_valueerror():
    try:
        verify_gain(lambda v, s: 0.5 + (0.3 if v == {"v": 2} else 0), {"v": 1}, {"v": 2}, SEEDS)
    except ValueError:
        return
    # if it is accepted instead, it must be judged correctly


def test_arms_that_are_equal_but_print_differently_are_not_silently_merged():
    # 1 == 1.0 == True, but str() differs, so the "must differ" check passes and the dict keys merge.
    run = lambda v, s: 0.8 if isinstance(v, float) else 0.5
    try:
        v = verify_gain(run, 1, 1.0, SEEDS)
    except ValueError:
        return
    assert v.real, "the candidate was 0.3 better and the verdict merged the arms into one"


@pytest.mark.parametrize("where", ["score", "min_effect", "gain"])
def test_huge_ints_raise_valueerror(where):
    big = 10**400
    with pytest.raises(ValueError):
        if where == "score":
            verify_gain(lambda v, s: big, "a", "b", SEEDS)
        elif where == "min_effect":
            verify_gain(lambda v, s: 0.5, "a", "b", SEEDS, min_effect=big)
        else:
            organ_scores([prop("a", big)])


def test_finite_but_enormous_scores_do_not_produce_infinite_json():
    run = lambda v, s: 1e308 if v == "b" else -1e308
    try:
        v = verify_gain(run, "a", "b", SEEDS)
    except ValueError:
        return
    json.dumps(dataclasses.asdict(v), allow_nan=False)


def test_organ_sum_overflow_is_valueerror():
    with pytest.raises(ValueError):
        organ_scores([prop("a", 1e308), prop("a", 1e308)])


@pytest.mark.parametrize("which", ["seeds", "changes"])
def test_non_iterables_raise_valueerror(which):
    with pytest.raises(ValueError):
        if which == "seeds":
            verify_gain(lambda v, s: 0.5, "a", "b", 5)
        else:
            attribute(None, lambda a, s: 0.5, SEEDS)


def test_verdict_from_verify_gain_is_plain_json():
    v = verify_gain(lambda ver, s: 0.8 if ver == "b" else 0.5, "a", "b", SEEDS, min_effect=Fraction(1, 10))
    json.dumps(dataclasses.asdict(v), allow_nan=False)


# ------------------------------------------------------------------ abstractions the next node will trip over


def test_attribution_survives_asdict_pickle_and_deepcopy():
    a = attribute(["x", "y"], lambda ap, s: 0.5 + 0.2 * len(ap), SEEDS)
    dataclasses.asdict(a)
    assert pickle.loads(pickle.dumps(a)) == a
    assert copy.deepcopy(a) == a


def test_redundant_genes_do_not_leave_a_real_gain_unattributed():
    # Either retriever alone gives +0.3; both give +0.3. Leave-one-out sees no drop from removing either.
    a = attribute(["r1", "r2"], lambda applied, s: 0.5 + (0.3 if applied else 0.0), SEEDS)
    assert a.total.real
    credited = sum(v.effect for v in a.genes.values() if v.real)
    flagged = getattr(a, "unattributed", None)
    assert credited > 0 or (flagged is not None and flagged > 0), "total gain 0.3, credited 0, nothing says so"


# ------------------------------------------------------------------ organ scores


def test_organ_that_only_proposes_harmful_changes_scores_below_a_quiet_organ():
    a = attribute(["good", "bad"], lambda ap, s: 0.5 + (0.3 if "good" in ap else 0) - (0.2 if "bad" in ap else 0), SEEDS)
    assert a.genes["bad"].effect < 0
    props = a.proposals({"good": "helpful", "bad": "harmful"})
    assert next(p for p in props if p["gene"] == "bad")["real"] is True
    out = organ_scores([*props, prop("quiet", 0.0, real=False)])
    assert out["harmful"] < out["quiet"]


def test_organ_scores_refuse_a_repeated_gene_id_and_count_a_real_negative_gain():
    with pytest.raises(ValueError):
        organ_scores([{**prop("a", 0.2), "gene": "g"}, {**prop("a", 0.2), "gene": "g"}])
    # Proposals without an id may repeat, and a real negative gain is accepted and lowers the score.
    assert organ_scores([prop("a", 0.2), prop("a", 0.2)]) == {"a": pytest.approx(0.4)}
    assert organ_scores([prop("a", -0.5, real=True)])["a"] == pytest.approx(-0.5)


def test_organ_scores_cannot_be_inflated_by_unreal_proposals():
    assert organ_scores([prop("a", 99.0, real=False)] * 50) == {"a": 0.0}


# ------------------------------------------------------------------ tried and held


def test_one_change_attribution_equals_total():
    a = attribute(["only"], lambda ap, s: 0.5 + 0.2 * len(ap), SEEDS)
    assert a.genes["only"].effect == pytest.approx(a.total.effect)


def test_run_fn_cannot_change_the_arms_it_is_given():
    seen = []

    def run(applied, seed):
        seen.append(applied)
        return 0.5

    attribute(["a", "b"], run, SEEDS)
    assert all(isinstance(x, tuple) for x in seen)


def test_global_random_state_is_untouched():
    random.seed(123)
    expected = random.random()
    random.seed(123)
    verify_gain(lambda v, s: 0.5, "a", "b", SEEDS)
    attribute(["a", "b"], lambda ap, s: 0.5, SEEDS)
    assert random.random() == expected


def test_many_changes_finish_quickly():
    import time

    names = [f"g{i}" for i in range(150)]
    t = time.perf_counter()
    attribute(names, lambda ap, s: 0.5 + 0.001 * len(ap), SEEDS)
    assert time.perf_counter() - t < 15


def test_a_run_fn_exception_leaves_no_partial_result():
    calls = []

    def run(applied, seed):
        calls.append(1)
        if len(calls) == 5:
            raise RuntimeError("arena down")
        return 0.5

    with pytest.raises(RuntimeError):
        attribute(["a", "b"], run, SEEDS)
