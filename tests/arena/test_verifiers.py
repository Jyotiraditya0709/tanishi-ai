"""AR1 implementer tests: verifier resolution, normalising and the building blocks."""
import math

import pytest

from tanishi.arena.verifiers import Verdict, contains, exact, normalize, number, resolve


def test_resolve_finds_a_function():
    assert resolve("math.sqrt") is math.sqrt


@pytest.mark.parametrize("path", ["nosuchmod_xyz.f", "math.no_such_fn", "nodot"])
def test_resolve_raises_for_missing(path):
    with pytest.raises((ImportError, AttributeError)):
        resolve(path)


def test_resolve_refuses_non_callable():
    with pytest.raises(TypeError):
        resolve("math.pi")


@pytest.mark.parametrize(
    "raw,score",
    [
        ((1.0, "ok"), 1.0),
        ([0.25, "list is fine"], 0.25),
        ((1, "int"), 1.0),
        ((0, "zero"), 0.0),
        ((1.5, "high"), 0.0),
        ((-0.1, "neg"), 0.0),
        ((float("nan"), "nan"), 0.0),
        ((float("inf"), "inf"), 0.0),
        ((True, "bool is not a score"), 0.0),
        (("1.0", "string"), 0.0),
        ((None, "none"), 0.0),
        (1.0, 0.0),
        ("great", 0.0),
        ((1.0,), 0.0),
        ((1.0, "a", "b"), 0.0),
        (None, 0.0),
    ],
)
def test_normalize_never_gives_credit_to_malformed_output(raw, score):
    v = normalize(raw)
    assert isinstance(v, Verdict)
    assert v.score == score
    assert isinstance(v.reason, str) and v.reason


def test_exact():
    assert exact(" Hello ", "hello").score == 1.0
    assert exact("Hello", "hello", case=True).score == 0.0
    assert exact("", "x").score == 0.0
    assert exact(None, "x").score == 0.0


def test_contains_is_fractional():
    assert contains("red and blue", "red", "blue").score == 1.0
    assert contains("red only", "red", "blue").score == 0.5
    assert contains("   ", "red").score == 0.0
    with pytest.raises(ValueError):
        contains("x")


def test_number_takes_the_last_number():
    assert number("2 + 2 = 4", 4).score == 1.0
    assert number("the answer is 1,000.5", 1000.5).score == 1.0
    assert number("3", 4).score == 0.0
    assert number("no digits", 4).score == 0.0
    assert number(None, 4).score == 0.0
    assert number("4.0001", 4, tol=0.001).score == 1.0
