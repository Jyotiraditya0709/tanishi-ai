# CS5 repair · where the card and a test disagree (2026-10-09)

## R6: the card says ValueError, the red-team test says "return a list"

The repair card: "`unresolved_older_than` rejects a non-finite or absurd `hours` with ValueError."
The red-team test `test_predictions_redteam.py::test_r6_stale_helper_survives_odd_hours` asserts
`isinstance(P.unresolved_older_than(hours), list)` for `nan`, `inf`, `1e12` and `-1.0`. A ValueError fails it.

The test wins (round rule). What I built instead:

- `nan`: returns `[]` (no age compares as older than NaN).
- `inf`, or a positive age past the calendar (`1e12` overflows `timedelta`): returns `[]` (nothing is that old).
- `-inf`, or a hugely negative age: lists every unresolved prediction (same as `-1`).
- A non-number still raises TypeError, as before.

If the human wants the ValueError, the red-team test's assertion for those three cases has to change first
(e.g. `pytest.raises(ValueError)` for nan/inf/1e12, a list for -1). The code change is then two lines.
