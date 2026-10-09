"""Red-team attacks on OBS3. A failing test here proves a break; see build/memory/runs/redteam-OBS3-*.md."""
import math
import random

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.observability.attribution import ablate, is_real_gain
from tanishi.observability.experiments import Run, interleave, record_run, seed_scores


@pytest.fixture
def conn(tmp_path):
    c = open_db(str(tmp_path / "core_state.db"))
    migrate(c)
    yield c
    c.close()


def test_zero_noise_arms_accept_a_tiny_gain():
    # Deterministic arms (e.g. the seed is ignored) give noise 0, so threshold 0: any gain is "real".
    assert not is_real_gain([0.5, 0.5, 0.5], [0.5001, 0.5001, 0.5001]).real


def test_false_positive_rate_under_null_is_low():
    rng = random.Random(0)
    n = 20000
    hits = sum(
        is_real_gain([rng.gauss(0.5, 0.1) for _ in range(3)], [rng.gauss(0.5, 0.1) for _ in range(3)]).real
        for _ in range(n)
    )
    assert hits / n <= 0.05, hits / n


def test_rerun_of_same_seed_is_one_sample_not_three(conn):
    record_run(conn, Run("a", "t", 1), 0.1, baseline="a", candidate="b")
    record_run(conn, Run("b", "t", 1), 0.4, baseline="a", candidate="b")
    # a repeat of the same arm, task and seed is refused at once, not averaged away
    with pytest.raises(ValueError):
        record_run(conn, Run("a", "t", 1), 0.9, baseline="a", candidate="b")
    base, cand = seed_scores(conn, "a", "b")
    assert len(base) == 1 and len(cand) == 1
    assert base[0] == pytest.approx(0.1) and cand[0] == pytest.approx(0.4)


def test_seed_missing_in_one_arm_is_not_misaligned(conn):
    for seed in (1, 2, 3):
        record_run(conn, Run("a", "t", seed), 0.5, baseline="a", candidate="b")
    for seed in (1, 3):
        record_run(conn, Run("b", "t", seed), 0.9, baseline="a", candidate="b")
    base, cand = seed_scores(conn, "a", "b")
    assert len(base) == len(cand)


def test_partial_tasks_for_a_seed_bias_the_mean(conn):
    record_run(conn, Run("a", "easy", 1), 1.0, baseline="a", candidate="b")
    record_run(conn, Run("a", "hard", 1), 0.0, baseline="a", candidate="b")
    record_run(conn, Run("b", "easy", 1), 1.0, baseline="a", candidate="b")
    base, cand = seed_scores(conn, "a", "b")
    assert base == cand


def test_nan_cost_not_silently_nulled(conn):
    with pytest.raises(ValueError):
        record_run(conn, Run("a", "t", 1), 0.5, baseline="a", candidate="b", cost=float("nan"))


def test_fractional_seeds_do_not_collide(conn):
    with pytest.raises(ValueError):
        record_run(conn, Run("a", "t", 1.9), 0.9, baseline="a", candidate="b")


def test_bool_seed_rejected(conn):
    with pytest.raises((ValueError, TypeError)):
        record_run(conn, Run("a", "t", True), 0.5, baseline="a", candidate="b")


def test_string_tasks_not_split_into_characters():
    runs = None
    with pytest.raises(ValueError):
        runs = interleave("a", "b", "abc", [1, 2, 3])
    assert runs is None


def test_arm_names_that_stringify_equal_are_rejected():
    with pytest.raises(ValueError):
        interleave(1, "1", ["t"], [1, 2, 3])


def test_bad_meta_writes_nothing(conn):
    with pytest.raises(TypeError):
        record_run(conn, Run("a", "t", 1), 0.5, baseline="a", candidate="b", meta={"x": {1, 2}})
    assert conn.execute("select count(*) from experiments").fetchone()[0] == 0


def test_ablate_nan_raises():
    with pytest.raises(ValueError):
        ablate("v", ["a"], lambda v, applied: math.nan)


def test_is_real_gain_rejects_bool_scores():
    with pytest.raises((ValueError, TypeError)):
        is_real_gain([True, False, True], [1, 1, 1])
