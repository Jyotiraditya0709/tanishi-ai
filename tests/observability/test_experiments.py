"""OBS3 implementer tests: the ledger round trip, input checks, and an evaluation against the legacy keep rule.

The spec exam is test_attribution.py (written by the testing agent); this file does not replace it.
"""
import random
import statistics

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.observability.attribution import Verdict, ablate, is_real_gain, noise
from tanishi.observability.experiments import Run, interleave, record_run, seed_scores


@pytest.fixture
def conn(tmp_path):
    c = open_db(str(tmp_path / "core_state.db"))
    migrate(c)
    yield c
    c.close()


# ---------------------------------------------------------------- ledger


def test_ledger_round_trip_feeds_is_real_gain(conn):
    rng = random.Random(3)
    plan = interleave("v1", "v2", ["t1", "t2"], [1, 2, 3, 4])
    for run in plan:
        score = rng.gauss(0.5 if run.arm == "v1" else 0.8, 0.02)
        record_run(conn, run, score, baseline="v1", candidate="v2")
    base, cand = seed_scores(conn, "v1", "v2")
    assert len(base) == len(cand) == 4
    assert is_real_gain(base, cand).real


def test_seed_scores_average_tasks_per_seed(conn):
    for task, score in (("t1", 0.2), ("t2", 0.4)):
        record_run(conn, Run("a", task, 7), score, baseline="a", candidate="b")
    record_run(conn, Run("b", "t1", 7), 0.9, baseline="a", candidate="b")
    assert seed_scores(conn, "a", "b") == ([pytest.approx(0.3)], [0.9])


def test_seed_scores_keeps_experiments_apart(conn):
    record_run(conn, Run("a", "t", 1), 0.1, baseline="a", candidate="b")
    record_run(conn, Run("a", "t", 1), 0.9, baseline="a", candidate="c")
    assert seed_scores(conn, "a", "b") == ([0.1], [])


def test_record_run_rejects_foreign_arm_and_nan(conn):
    with pytest.raises(ValueError):
        record_run(conn, Run("x", "t", 1), 0.5, baseline="a", candidate="b")
    with pytest.raises(ValueError):
        record_run(conn, Run("a", "t", 1), float("nan"), baseline="a", candidate="b")
    assert conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0


# ---------------------------------------------------------------- interleave


def test_interleave_runs_each_pair_back_to_back():
    runs = interleave("a", "b", ["t1", "t2", "t3"], [1, 2, 3])
    for first, second in zip(runs[::2], runs[1::2], strict=True):
        assert (first.task, first.seed) == (second.task, second.seed)
        assert {first.arm, second.arm} == {"a", "b"}


@pytest.mark.parametrize(("tasks", "seeds"), [(["t", "t"], [1, 2, 3]), (["t"], [1, 1, 2])])
def test_interleave_rejects_repeats(tasks, seeds):
    with pytest.raises(ValueError):
        interleave("a", "b", tasks, seeds)


def test_interleave_rejects_identical_arms():
    with pytest.raises(ValueError):
        interleave("a", "a", ["t"], [1, 2, 3])


# ---------------------------------------------------------------- attribution edges


def test_fewer_than_three_seeds_raises_not_false():
    with pytest.raises(ValueError, match="at least 3 seeds"):
        is_real_gain([0.1, 0.1], [0.9, 0.9, 0.9])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_scores_are_refused(bad):
    with pytest.raises(ValueError):
        is_real_gain([0.5, 0.5, bad], [0.9, 0.9, 0.9])
    with pytest.raises(ValueError):
        noise([0.5, bad, 0.5])


def test_noisy_candidate_cannot_hide_behind_quiet_baseline():
    # Baseline noise is 0, candidate noise is large: the gain must clear the candidate's noise too.
    v = is_real_gain([0.5, 0.5, 0.5], [0.3, 0.9, 0.6])
    assert not v.real and v.noise == pytest.approx(noise([0.3, 0.9, 0.6]))


def test_verdict_explains_itself():
    v = is_real_gain([0.5, 0.51, 0.49], [0.5, 0.52, 0.5])
    assert isinstance(v, Verdict) and not v and "noise" in v.reason


def test_zero_noise_gain_must_clear_the_floor():
    # Deterministic arms measure noise 0; the 0.01 floor still applies, and "larger than" is strict.
    assert not is_real_gain([0.5] * 3, [0.505] * 3).real
    assert not is_real_gain([0.0] * 3, [0.01] * 3).real
    v = is_real_gain([0.5] * 3, [0.52] * 3)
    assert v.real and v.threshold == pytest.approx(0.01)


def test_threshold_is_the_biggest_of_noise_min_effect_and_floor():
    assert is_real_gain([0.5] * 3, [0.6] * 3, min_effect=0.05).threshold == pytest.approx(0.05)
    assert is_real_gain([0.5] * 3, [0.6] * 3, min_effect=0.001).threshold == pytest.approx(0.01)
    assert not is_real_gain([0.5] * 3, [0.6] * 3, min_effect=0.1).real  # equal to min_effect is not larger


def test_unpaired_lists_are_refused():
    with pytest.raises(ValueError, match="unpaired"):
        is_real_gain([0.1, 0.1, 0.1], [0.9, 0.9, 0.9, 0.9])


@pytest.mark.parametrize("bad", [True, "0.5", None])
def test_non_number_scores_are_refused(bad):
    with pytest.raises(ValueError):
        is_real_gain([0.5, 0.5, bad], [0.9, 0.9, 0.9])


def test_negative_min_effect_is_refused():
    with pytest.raises(ValueError):
        is_real_gain([0.5] * 3, [0.6] * 3, min_effect=-0.1)


def test_ablate_rejects_duplicates_and_nan():
    with pytest.raises(ValueError):
        ablate("v", ["a", "a"], lambda v, applied: 0.0)
    with pytest.raises(ValueError):
        ablate("v", ["a"], lambda v, applied: float("nan"))


def test_ablate_credits_interaction_to_both_partners():
    # a and b only help together: one-at-a-time removal credits each with the full joint gain.
    def run_fn(version, applied):
        return 0.5 + (0.2 if {"a", "b"} <= set(applied) else 0.0)

    assert ablate("v", ["a", "b", "c"], run_fn) == pytest.approx({"a": 0.2, "b": 0.2, "c": 0.0})


# ---------------------------------------------------------------- evaluation: legacy rule vs OBS3


def test_evaluation_false_and_true_positive_rates():
    """Synthetic arms at the legacy noise level (sigma 0.1, 3 seeds per arm).

    The legacy rule kept any mean gain > 0.001. OBS3 must cut false keeps on null changes to a few percent
    while still finding a real 0.4 gain most of the time.
    """
    rng = random.Random(11)
    trials = 2000

    def rates(true_effect):
        legacy = obs3 = 0
        for _ in range(trials):
            base = [rng.gauss(0.7, 0.1) for _ in range(3)]
            cand = [rng.gauss(0.7 + true_effect, 0.1) for _ in range(3)]
            legacy += statistics.fmean(cand) - statistics.fmean(base) > 0.001
            obs3 += is_real_gain(base, cand).real
        return legacy / trials, obs3 / trials

    legacy_fp, obs3_fp = rates(0.0)
    _, obs3_tp = rates(0.4)
    assert legacy_fp > 0.4
    assert obs3_fp < 0.05
    assert obs3_tp > 0.7
