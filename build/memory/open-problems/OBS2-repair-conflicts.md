# OBS2 repair · where the card and a test disagreed

## cei() on a product that overflows

- **Card (fix 3):** `cei()` and `rcr()` raise ValueError if the result is not finite, and do not return inf.
- **Test:** `tests/observability/test_redteam_obs2.py::test_cei_overflow_to_inf_is_refused_or_finite` calls
  `north_star.cei(1e200, 1e200, 1, 1, 1, 1)` with no `pytest.raises` and no `try`, then asserts the result is not inf.
  A ValueError fails it. (Its name says "refused or finite", but only the rcr twin of this test accepts a ValueError.)
- **What I built (the test wins):** `cei()` returns `None` when its product is not finite. `None` is how OBS2 already
  shows a number that cannot be measured, and it is strict JSON. Nothing in the exam pins the overflow case.
  `rcr()` follows the card and raises ValueError, which its test accepts.
- **What I would do instead:** make both raise ValueError, as the card says, and change the cei test to
  `pytest.raises(ValueError)` or to accept either outcome. That is an edit to a red-team test, so it needs the human.
