"""OBS3 exam: experiment ledger and Causal Attribution v0.

Written from the spec only (noise, is_real_gain, interleave, ablate). The spec leaves a few
shapes open, so the helpers below read them in the most natural way and nothing more:

- ``Verdict``: a boolean-ish "is it real" field named ``real`` / ``is_real`` / ``significant``,
  and a numeric effect named ``effect`` / ``gain`` / ``delta``.
- ``Run``: carries the arm, the task and the seed (attribute or dict key).
- ``run_fn`` for ``ablate`` receives the version and the collection of changes that are applied.
"""

import itertools
import random
import statistics

import pytest
from tanishi.observability import attribution as _attr
from tanishi.observability import experiments as _exp


def _pick(name):
    # The spec lists both modules for the four functions; accept either home.
    return getattr(_attr, name, None) or getattr(_exp, name)


ablate, interleave, is_real_gain, noise = (
    _pick(n) for n in ("ablate", "interleave", "is_real_gain", "noise")
)

REAL_NAMES = ("real", "is_real", "significant")
EFFECT_NAMES = ("effect", "gain", "delta")


def _get(obj, names):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AssertionError(f"{type(obj).__name__} has none of {names}")


def _real(v) -> bool:
    return bool(_get(v, REAL_NAMES))


def _effect(v) -> float:
    return float(_get(v, EFFECT_NAMES))


def _noisy(rng, mean, sigma, n):
    return [rng.gauss(mean, sigma) for _ in range(n)]


def _refused(base, cand, **kw) -> bool:
    """True when is_real_gain declines to answer (raises, or never says 'real')."""
    try:
        v = is_real_gain(base, cand, **kw)
    except (ValueError, RuntimeError, AssertionError):
        return True
    return not _real(v)


# ---------------------------------------------------------------- noise


def test_noise_of_constant_runs_is_zero():
    assert noise([0.5, 0.5, 0.5, 0.5]) == 0.0


def test_noise_is_positive_when_runs_differ():
    assert noise([0.1, 0.2, 0.3]) > 0


def test_noise_is_order_invariant():
    runs = [0.61, 0.72, 0.55, 0.9, 0.48]
    assert noise(runs) == pytest.approx(noise(list(reversed(runs))))


def test_noise_is_shift_invariant_and_scale_equivariant():
    runs = [0.61, 0.72, 0.55, 0.9, 0.48]
    base = noise(runs)
    assert noise([x + 10 for x in runs]) == pytest.approx(base)
    assert noise([x * 3 for x in runs]) == pytest.approx(base * 3)


def test_noise_grows_with_spread():
    tight = [0.50, 0.51, 0.49, 0.50]
    wide = [0.30, 0.70, 0.40, 0.60]
    assert noise(wide) > noise(tight) * 5


def test_noise_recovers_known_sigma():
    rng = random.Random(1)
    runs = _noisy(rng, 0.7, 0.1, 4000)
    assert noise(runs) == pytest.approx(0.1, rel=0.1)


def test_noise_is_a_float_and_nonnegative_property():
    rng = random.Random(2)
    for _ in range(50):
        runs = [rng.uniform(-5, 5) for _ in range(rng.randint(3, 12))]
        n = noise(runs)
        assert isinstance(n, float)
        assert n >= 0


# ---------------------------------------------------------------- is_real_gain


@pytest.mark.parametrize("k", [0, 1, 2])
def test_fewer_than_three_seeds_per_arm_refuses(k):
    big = [0.9, 0.91, 0.92, 0.93, 0.94]
    low = [0.1, 0.11, 0.12, 0.13, 0.14]
    assert _refused(low[:k], big)
    assert _refused(low, big[:k])
    assert _refused(low[:k], big[:k])


def test_refusal_is_not_a_silent_positive_even_for_huge_gain():
    # 2 seeds each, enormous gap: must still not be reported as a real gain.
    assert _refused([0.1, 0.1], [0.99, 0.99])


def test_three_seeds_per_arm_is_enough_to_answer():
    v = is_real_gain([0.10, 0.11, 0.09], [0.90, 0.91, 0.89])
    assert _real(v) is True


def test_clear_gain_over_low_noise_is_real():
    v = is_real_gain([0.50, 0.51, 0.49, 0.50], [0.70, 0.71, 0.69, 0.70])
    assert _real(v)
    assert _effect(v) == pytest.approx(0.20, abs=1e-9)


def test_legacy_case_tiny_gain_under_big_noise_is_noise():
    base = [0.60, 0.80, 0.70, 0.50, 0.90]  # mean 0.70, sd ~0.158
    cand = [x + 0.001 for x in base]  # gain 0.001, legacy kept this
    assert _refused(base, cand)


def test_gain_below_twice_noise_is_noise():
    base = [0.4, 0.6, 0.5, 0.5]  # sd ~0.0816
    cand = [0.5, 0.7, 0.6, 0.6]  # gain 0.1 < 2 * noise
    assert noise(base) * 2 > 0.1
    assert _refused(base, cand)


def test_gain_above_twice_noise_is_real():
    base = [0.4, 0.6, 0.5, 0.5]  # sd ~0.0816
    cand = [x + 0.5 for x in base]  # gain 0.5 >> 2 * noise
    assert _real(is_real_gain(base, cand))


def test_no_change_is_not_a_gain():
    runs = [0.5, 0.6, 0.55]
    assert _refused(runs, list(runs))


def test_regression_is_never_a_real_gain():
    assert _refused([0.9, 0.91, 0.92], [0.1, 0.11, 0.12])


def test_zero_noise_positive_gain_is_real():
    assert _real(is_real_gain([0.5, 0.5, 0.5], [0.6, 0.6, 0.6]))


def test_zero_noise_zero_gain_is_not_real():
    assert _refused([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])


def test_min_effect_floor_blocks_small_but_noiseless_gain():
    base, cand = [0.5, 0.5, 0.5], [0.52, 0.52, 0.52]
    assert _real(is_real_gain(base, cand))
    assert _refused(base, cand, min_effect=0.05)


def test_min_effect_does_not_block_larger_gain():
    base, cand = [0.5, 0.5, 0.5], [0.7, 0.7, 0.7]
    assert _real(is_real_gain(base, cand, min_effect=0.05))


def test_inputs_are_not_mutated():
    base, cand = [0.3, 0.5, 0.4], [0.9, 0.8, 1.0]
    b0, c0 = list(base), list(cand)
    is_real_gain(base, cand)
    noise(base)
    assert base == b0 and cand == c0


# ---------------------------------------------------------------- property: known effect, known noise


@pytest.mark.parametrize("seed", range(10))
def test_property_recovers_known_effect_within_tolerance(seed):
    rng = random.Random(1000 + seed)
    true_effect = rng.choice([0.15, 0.25, 0.4])
    sigma = rng.choice([0.01, 0.02, 0.05])
    n = 30
    base = _noisy(rng, 0.5, sigma, n)
    cand = _noisy(rng, 0.5 + true_effect, sigma, n)
    v = is_real_gain(base, cand)
    assert _real(v)
    # standard error of the diff of means is sigma*sqrt(2/n); allow 4 of those.
    tol = 4 * sigma * (2 / n) ** 0.5
    assert abs(_effect(v) - true_effect) <= tol


@pytest.mark.parametrize("seed", range(10))
def test_property_null_effect_is_not_reported_real(seed):
    rng = random.Random(2000 + seed)
    sigma = 0.1
    base = _noisy(rng, 0.7, sigma, 5)
    cand = _noisy(rng, 0.7, sigma, 5)
    # With no true effect, a 2x-noise bar should (almost) never be cleared.
    gain = statistics.fmean(cand) - statistics.fmean(base)
    if gain < 2 * noise(base):
        assert _refused(base, cand)


def test_property_effect_far_below_noise_is_rejected_across_many_draws():
    rng = random.Random(7)
    false_positives = 0
    for _ in range(200):
        base = _noisy(rng, 0.7, 0.1, 4)
        cand = _noisy(rng, 0.7 + 0.001, 0.1, 4)
        if not _refused(base, cand):
            false_positives += 1
    # The legacy rule (keep if > 0.001) would accept ~50%. The 2x-noise rule is far stricter.
    assert false_positives <= 20


# ---------------------------------------------------------------- interleave


def _arm(r):
    return _get(r, ("arm", "version", "variant", "name"))


def _task(r):
    return _get(r, ("task",))


def _seed(r):
    return _get(r, ("seed",))


TASKS = ["t1", "t2", "t3", "t4"]
SEEDS = [1, 2, 3]


def test_interleave_covers_every_task_and_seed_for_both_arms():
    runs = interleave("base", "cand", TASKS, SEEDS)
    assert len(runs) == 2 * len(TASKS) * len(SEEDS)
    combos = {(_arm(r), _task(r), _seed(r)) for r in runs}
    assert len(combos) == len(runs)
    for arm in ("base", "cand"):
        for t in TASKS:
            for s in SEEDS:
                assert (arm, t, s) in combos


def test_interleave_gives_both_arms_the_same_tasks():
    runs = interleave("base", "cand", TASKS, SEEDS)
    a = sorted((_task(r), _seed(r)) for r in runs if _arm(r) == "base")
    b = sorted((_task(r), _seed(r)) for r in runs if _arm(r) == "cand")
    assert a == b


def test_interleave_is_not_blocked_by_arm():
    runs = interleave("base", "cand", TASKS, SEEDS)
    arms = [_arm(r) for r in runs]
    assert arms != sorted(arms, key=lambda a: a != "base")  # not all baseline first
    assert arms != sorted(arms, key=lambda a: a != "cand")  # not all candidate first
    # a real interleave switches arm more than once
    switches = sum(1 for x, y in itertools.pairwise(arms) if x != y)
    assert switches >= 2


def test_interleave_order_is_not_a_fixed_pattern_across_inputs():
    orders = set()
    for seeds in ([1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12], [13, 14, 15]):
        runs = interleave("base", "cand", TASKS, seeds)
        orders.add(tuple(_arm(r) for r in runs))
    assert len(orders) > 1


def test_interleave_is_reproducible_for_same_inputs():
    a = interleave("base", "cand", TASKS, SEEDS)
    b = interleave("base", "cand", TASKS, SEEDS)
    assert [(_arm(r), _task(r), _seed(r)) for r in a] == [(_arm(r), _task(r), _seed(r)) for r in b]


def test_interleave_does_not_mutate_inputs():
    tasks, seeds = list(TASKS), list(SEEDS)
    interleave("base", "cand", tasks, seeds)
    assert tasks == TASKS and seeds == SEEDS


def test_interleave_empty_tasks_gives_no_runs():
    assert list(interleave("base", "cand", [], SEEDS)) == []


def test_interleave_block_effect_drift_cancels():
    """A linear drift over run order must not systematically favour one arm."""
    biases = []
    for k in range(40):
        runs = interleave("base", "cand", TASKS, [k * 10 + i for i in range(3)])
        pos = {"base": [], "cand": []}
        for i, r in enumerate(runs):
            pos[_arm(r)].append(i)
        biases.append(statistics.fmean(pos["cand"]) - statistics.fmean(pos["base"]))
    # Average position difference stays small relative to the run count (24).
    assert abs(statistics.fmean(biases)) < 3


# ---------------------------------------------------------------- ablate


def _changes_in(args, kwargs):
    for v in list(args) + list(kwargs.values()):
        if isinstance(v, (list, tuple, set, frozenset)) and all(isinstance(x, str) for x in v):
            return frozenset(v)
    raise AssertionError("run_fn was not handed the applied changes")


def _additive_run_fn(effects, base=0.5, calls=None):
    def run_fn(*args, **kwargs):
        applied = _changes_in(args, kwargs)
        if calls is not None:
            calls.append(applied)
        return base + sum(effects[c] for c in applied)

    return run_fn


def test_ablate_attributes_each_change_in_additive_world():
    effects = {"a": 0.10, "b": 0.30, "c": -0.05}
    out = ablate("v1", list(effects), _additive_run_fn(effects))
    assert set(out) == set(effects)
    for c, e in effects.items():
        assert out[c] == pytest.approx(e, abs=1e-9)


def test_ablate_neutral_change_gets_zero():
    effects = {"useful": 0.2, "dead": 0.0}
    out = ablate("v1", list(effects), _additive_run_fn(effects))
    assert out["dead"] == pytest.approx(0.0, abs=1e-9)
    assert out["useful"] == pytest.approx(0.2, abs=1e-9)


def test_ablate_is_one_at_a_time_never_removes_two_changes_together():
    effects = {"a": 0.1, "b": 0.2, "c": 0.3}
    calls = []
    ablate("v1", list(effects), _additive_run_fn(effects, calls=calls))
    full = frozenset(effects)
    assert calls, "run_fn was never called"
    for applied in calls:
        assert len(full - applied) <= 1 or len(applied) <= 1
    # every change is isolated by some call
    for c in effects:
        assert any(applied == full - {c} or applied == {c} for applied in calls)


def test_ablate_calls_run_fn_for_each_change():
    effects = {"a": 0.1, "b": 0.2}
    calls = []
    ablate("v1", list(effects), _additive_run_fn(effects, calls=calls))
    assert len(calls) >= len(effects)


def test_ablate_with_no_changes_is_empty():
    assert ablate("v1", [], _additive_run_fn({})) == {}


def test_ablate_does_not_mutate_changes_list():
    effects = {"a": 0.1, "b": 0.2}
    changes = list(effects)
    ablate("v1", changes, _additive_run_fn(effects))
    assert changes == ["a", "b"]


def test_ablate_property_random_additive_worlds():
    rng = random.Random(42)
    for _ in range(25):
        k = rng.randint(1, 6)
        effects = {f"c{i}": rng.uniform(-0.3, 0.5) for i in range(k)}
        out = ablate("v", list(effects), _additive_run_fn(effects, base=rng.random()))
        assert set(out) == set(effects)
        for c, e in effects.items():
            assert out[c] == pytest.approx(e, abs=1e-9)


def test_ablate_propagates_run_fn_failure():
    def boom(*a, **k):
        raise RuntimeError("run crashed")

    with pytest.raises(RuntimeError):
        ablate("v1", ["a"], boom)
