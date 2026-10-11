"""Exam for E2 (Causal Attribution, full), written from the spec alone.

Spec: a claimed gain is accepted only if it survives seeded repeats and beats measured noise; ablation switches one
change off at a time and reads the drop, so the gain lands on the right gene; every organ is scored on the gain its
proposals produce. The interface is not named by the card: see build/memory/open-problems/E2-exam-assumptions.md.
"""
import json
import math
import random

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state.genome import record_version
from tanishi.observability.causal import attribute, organ_scores, verify_gain

SEEDS = [1, 2, 3, 4, 5, 6]


def jitter(seed, tag, scale):
    """Deterministic per-(seed, tag) noise."""
    return random.Random(f"{seed}:{tag}").uniform(-scale, scale)


def scorer(effects, base=0.5, noise=0.0):
    """run_fn(version, seed) for verify_gain: `effects` maps version to its true lift."""
    def run(version, seed):
        return base + effects.get(version, 0.0) + jitter(seed, "shared", noise) + jitter(seed, version, noise / 4)
    return run


def recorder(score=0.5):
    calls = []

    def run(version, seed):
        calls.append((version, seed))
        return score

    return run, calls


# ------------------------------------------------- accepted only if it survives seeded repeats and beats noise


def test_clear_gain_is_accepted():
    v = verify_gain(scorer({"v2": 0.2}, noise=0.02), "v1", "v2", SEEDS)
    assert v.real and v.effect == pytest.approx(0.2, abs=0.03)


def test_no_change_is_rejected():
    assert not verify_gain(scorer({}, noise=0.02), "v1", "v2", SEEDS).real


def test_regression_is_rejected():
    v = verify_gain(scorer({"v2": -0.2}), "v1", "v2", SEEDS)
    assert not v.real and v.effect < 0


def test_one_lucky_seed_does_not_survive_repeats():
    def run(version, seed):
        return 0.5 + (0.6 if version == "v2" and seed == 1 else 0.0)

    v = verify_gain(run, "v1", "v2", SEEDS)
    assert v.effect > 0.05  # the mean looks like a gain...
    assert not v.real  # ...but it did not repeat


def test_gain_inside_noise_is_rejected():
    rng = random.Random(0)
    table = {(v, s): 0.5 + (0.03 if v == "v2" else 0) + rng.gauss(0, 0.15) for s in SEEDS for v in ("v1", "v2")}
    v = verify_gain(lambda ver, s: table[(ver, s)], "v1", "v2", SEEDS)
    assert v.noise > 0.05 and not v.real


def test_noise_is_measured_from_repeats():
    v = verify_gain(scorer({"v2": 0.3}, noise=0.1), "v1", "v2", SEEDS)
    assert v.noise > 0
    assert v.threshold >= 2 * v.noise


def test_deterministic_tiny_gain_is_rejected():
    assert not verify_gain(scorer({"v2": 0.001}), "v1", "v2", SEEDS).real


def test_gain_equal_to_floor_is_not_real():
    assert not verify_gain(lambda v, s: 0.01 if v == "v2" else 0.0, "v1", "v2", SEEDS).real


def test_min_effect_raises_the_bar():
    run = scorer({"v2": 0.05})
    assert verify_gain(run, "v1", "v2", SEEDS).real
    assert not verify_gain(run, "v1", "v2", SEEDS, min_effect=0.1).real


def test_crashed_candidate_scored_zero_is_rejected():
    assert not verify_gain(lambda v, s: 0.5 if v == "v1" else 0.0, "v1", "v2", SEEDS).real


# ------------------------------------------------- seeded, interleaved runs


def test_every_arm_runs_every_seed_once():
    run, calls = recorder()
    verify_gain(run, "v1", "v2", SEEDS)
    assert sorted(calls) == sorted((v, s) for v in ("v1", "v2") for s in SEEDS)


def test_the_run_function_is_given_the_seed_as_an_int():
    run, calls = recorder()
    verify_gain(run, "v1", "v2", SEEDS)
    assert {s for _, s in calls} == set(SEEDS) and all(type(s) is int for _, s in calls)


def test_arms_are_interleaved_per_seed():
    run, calls = recorder()
    verify_gain(run, "v1", "v2", SEEDS)
    for i in range(0, len(calls), 2):
        assert calls[i][1] == calls[i + 1][1], "the two arms of one seed must run back to back"
        assert {calls[i][0], calls[i + 1][0]} == {"v1", "v2"}


def test_order_is_randomised_not_always_baseline_first():
    seeds = list(range(1, 41))
    run, calls = recorder()
    verify_gain(run, "v1", "v2", seeds)
    assert {calls[i][0] for i in range(0, len(calls), 2)} == {"v1", "v2"}
    assert [s for _, s in calls[::2]] != seeds  # seeds are shuffled too


def test_order_is_reproducible():
    def order():
        run, calls = recorder()
        verify_gain(run, "v1", "v2", SEEDS)
        return calls

    assert order() == order()


def test_run_function_errors_propagate():
    def boom(version, seed):
        raise RuntimeError("arena down")

    with pytest.raises(RuntimeError):
        verify_gain(boom, "v1", "v2", SEEDS)


# ------------------------------------------------- refusals


@pytest.mark.parametrize(
    "seeds", [[], [1], [1, 2], [1, 1, 2, 3], [1, 2, 3.0], [True, 2, 3], [1.5, 2, 3], "123", [1, 2, "3"]]
)
def test_bad_seeds_are_refused(seeds):
    with pytest.raises(ValueError):
        verify_gain(lambda v, s: 0.5, "v1", "v2", seeds)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "0.5", None])
def test_bad_scores_are_refused(bad):
    with pytest.raises(ValueError):
        verify_gain(lambda v, s: bad if v == "v2" else 0.5, "v1", "v2", SEEDS)


@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf, True])
def test_bad_min_effect_is_refused(bad):
    with pytest.raises(ValueError):
        verify_gain(lambda v, s: 0.5, "v1", "v2", SEEDS, min_effect=bad)


def test_same_arm_twice_is_refused():
    with pytest.raises(ValueError):
        verify_gain(lambda v, s: 0.5, "v1", "v1", SEEDS)


# ------------------------------------------------- properties of the verdict


@pytest.mark.parametrize("trial", range(10))
def test_seed_order_does_not_change_the_verdict(trial):
    rng = random.Random(trial)
    seeds = rng.sample(range(100), 7)
    run = scorer({"v2": rng.uniform(-0.1, 0.3)}, noise=rng.uniform(0, 0.1))
    a = verify_gain(run, "v1", "v2", seeds)
    b = verify_gain(run, "v1", "v2", rng.sample(seeds, len(seeds)))
    assert a.real == b.real
    assert a.effect == pytest.approx(b.effect) and a.noise == pytest.approx(b.noise)


@pytest.mark.parametrize("trial", range(10))
def test_verdict_is_idempotent_and_shift_invariant(trial):
    rng = random.Random(100 + trial)
    run = scorer({"v2": rng.uniform(-0.1, 0.3)}, noise=rng.uniform(0, 0.1))
    first = verify_gain(run, "v1", "v2", SEEDS)
    assert verify_gain(run, "v1", "v2", SEEDS) == first
    shifted = verify_gain(lambda v, s: run(v, s) + 0.2, "v1", "v2", SEEDS)
    assert shifted.real == first.real and shifted.effect == pytest.approx(first.effect)


# ------------------------------------------------- ablation: the gain lands on the right gene

GENES = {"retrieval": 0.20, "planner": 0.0, "critic": 0.10}


def system(effects, noise=0.0):
    def run(applied, seed):
        base = 0.4 + sum(effects[g] for g in applied)
        return base + jitter(seed, "shared", noise) + jitter(seed, applied, noise / 4)
    return run


def test_gain_lands_on_the_right_genes():
    a = attribute(list(GENES), system(GENES, noise=0.01), SEEDS)
    assert a.total.real and a.total.effect == pytest.approx(0.3, abs=0.03)
    assert a.genes["retrieval"].real and a.genes["retrieval"].effect == pytest.approx(0.2, abs=0.03)
    assert a.genes["critic"].real and a.genes["critic"].effect == pytest.approx(0.1, abs=0.03)
    assert not a.genes["planner"].real  # a passenger gene earns nothing
    assert abs(a.genes["planner"].effect) < 0.03


def test_ranking_follows_the_true_effects():
    a = attribute(list(GENES), system(GENES, noise=0.01), SEEDS)
    ranked = sorted(a.genes, key=lambda g: a.genes[g].effect, reverse=True)
    assert ranked == ["retrieval", "critic", "planner"]


def test_a_passenger_riding_with_a_winner_is_not_credited():
    a = attribute(["big", "rider"], system({"big": 0.3, "rider": 0.0}), SEEDS)
    assert a.total.real and a.genes["big"].real and not a.genes["rider"].real


def test_a_harmful_gene_shows_a_negative_effect():
    a = attribute(["good", "bad"], system({"good": 0.3, "bad": -0.1}), SEEDS)
    assert a.genes["bad"].effect < 0 and not a.genes["bad"].real
    assert a.genes["good"].real


def test_gene_gain_inside_noise_is_rejected():
    effects = {"small": 0.02, "big": 0.3}
    rng = random.Random(5)
    table = {}

    def run(applied, seed):
        key = (applied, seed)
        if key not in table:
            table[key] = 0.4 + sum(effects[g] for g in applied) + rng.gauss(0, 0.1)
        return table[key]

    a = attribute(["small", "big"], run, SEEDS)
    assert a.genes["big"].real and not a.genes["small"].real


def test_ablation_switches_one_change_off_at_a_time():
    seen = set()
    names = ["a", "b", "c", "d"]

    def run(applied, seed):
        seen.add(applied)
        return 0.5

    attribute(names, run, SEEDS)
    expected = {(), tuple(names)} | {tuple(x for x in names if x != c) for c in names}
    assert seen == expected


def test_ablation_keeps_the_callers_order_and_every_seed():
    calls = []
    names = ["z", "a", "m"]
    attribute(names, lambda applied, seed: calls.append((applied, seed)) or 0.5, SEEDS)
    assert all(applied == tuple(x for x in names if x in applied) for applied, _ in calls)
    for applied in {a for a, _ in calls}:
        assert sorted(s for a, s in calls if a == applied) == SEEDS


def test_ablation_refuses_bad_input():
    with pytest.raises(ValueError):
        attribute(["a", "a"], lambda ap, s: 0.5, SEEDS)
    with pytest.raises(ValueError):
        attribute(["a", "b"], lambda ap, s: 0.5, [1, 2])
    with pytest.raises(ValueError):
        attribute(["a", "b"], lambda ap, s: math.nan, SEEDS)


def test_empty_change_list_gives_no_genes_or_refuses():
    try:
        a = attribute([], lambda ap, s: 0.5, SEEDS)
    except ValueError:
        return
    assert dict(a.genes) == {}


@pytest.mark.parametrize("trial", range(8))
def test_additive_gene_effects_sum_to_the_total(trial):
    rng = random.Random(trial)
    effects = {f"g{i}": rng.uniform(0.02, 0.2) for i in range(rng.randint(2, 5))}
    a = attribute(list(effects), system(effects), SEEDS)
    assert sum(g.effect for g in a.genes.values()) == pytest.approx(a.total.effect)
    for name, true in effects.items():
        assert a.genes[name].effect == pytest.approx(true)


def test_attribution_is_plain_json_and_fits_a_genome_record():
    a = attribute(list(GENES), system(GENES, noise=0.01), SEEDS)
    data = a.as_dict()
    assert json.loads(json.dumps(data, allow_nan=False)) == data
    assert set(data["genes"]) == set(GENES)
    assert all({"effect", "real"} <= set(v) for v in data["genes"].values())
    assert data["genes"]["retrieval"]["real"] is True and data["genes"]["planner"]["real"] is False

    conn = open_db()
    migrate(conn)
    conn.close()
    record_version("v-e2", None, list(GENES), "c1", "s1", {"pass_rate": 0.8}, data, None)
    conn = open_db()
    (rec,) = conn.execute("SELECT record FROM genome WHERE version = 'v-e2'").fetchone()
    conn.close()
    assert json.loads(rec)["attribution"] == data


# ------------------------------------------------- every organ is scored on the gain its proposals produce


def prop(organ, gain, real=True):
    return {"organ": organ, "gain": gain, "real": real}


def test_organ_score_is_the_gain_of_its_real_proposals():
    out = organ_scores([prop("research", 0.1), prop("research", 0.05), prop("coding", 0.2)])
    assert out == {"research": pytest.approx(0.15), "coding": pytest.approx(0.2)}


def test_unreal_proposals_earn_nothing():
    out = organ_scores([prop("research", 0.5, real=False), prop("research", 0.05), prop("memory", 0.3, real=False)])
    assert out == {"research": pytest.approx(0.05), "memory": 0.0}


def test_organ_with_only_unreal_proposals_is_still_scored():
    assert organ_scores([prop("critic", 0.2, real=False)]) == {"critic": 0.0}


def test_no_proposals_no_scores():
    assert organ_scores([]) == {}


def test_a_generator_is_read_in_full():
    assert organ_scores(prop("a", 0.1) for _ in range(3)) == {"a": pytest.approx(0.3)}


@pytest.mark.parametrize("trial", range(8))
def test_organ_scores_ignore_proposal_order(trial):
    rng = random.Random(trial)
    items = [prop(rng.choice("abc"), round(rng.uniform(-0.05, 0.3), 3), rng.random() < 0.6) for _ in range(20)]
    a, b = organ_scores(items), organ_scores(rng.sample(items, len(items)))
    assert a.keys() == b.keys() == {i["organ"] for i in items}
    for organ in a:
        assert a[organ] == pytest.approx(b[organ])
        assert a[organ] == pytest.approx(sum(i["gain"] for i in items if i["organ"] == organ and i["real"]))


@pytest.mark.parametrize(
    "bad",
    [
        prop("a", math.nan), prop("a", math.inf), prop("a", True), prop("a", "0.1"), prop("", 0.1), prop("  ", 0.1),
        {"organ": "a", "gain": 0.1}, {"gain": 0.1, "real": True},
    ],
)
def test_bad_proposals_are_refused(bad):
    with pytest.raises(ValueError):
        organ_scores([prop("ok", 0.1), bad])


def test_organ_score_follows_attribution_end_to_end():
    a = attribute(list(GENES), system(GENES, noise=0.01), SEEDS)
    organ_of = {"retrieval": "research", "planner": "research", "critic": "coding"}
    out = organ_scores({"organ": organ_of[g], "gain": v.effect, "real": v.real} for g, v in a.genes.items())
    assert out["research"] == pytest.approx(0.2, abs=0.03)  # planner added nothing
    assert out["coding"] == pytest.approx(0.1, abs=0.03)
