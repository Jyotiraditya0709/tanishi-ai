# CS5 repair · where the card and a test disagree (2026-10-09)

## R7: migration 0003 breaks three CS4 builder tests that pin the schema version at 2

The card assigns migration 0003 to CS5 for the predictions index, and the R7 red-team test only passes once a fresh
`open_db()` + `migrate()` has that index (an index created at runtime by `predictions.py` would not be there).
These CS4 builder tests (not mine, not the exam, not red team) assert the version is **exactly** 2, so any 0003
from any node fails them:

| Test (`tests/core_state/test_goals_impl.py`) | Assertion |
|---|---|
| `test_migration_fills_ancestors_for_existing_goals` | `assert migrate(c) == 2` |
| `test_migration_replaces_objects_the_old_goals_module_installed` | `assert migrate(c) == 2` |
| `test_goals_module_needs_only_migrate` | `SELECT MAX(version) ... == 2` |

I did not edit them. The migration is its own commit (`CS5: migration 0003 ...`), so it can be dropped alone if the
human prefers R7 open over these three. **What would settle it:** in each, assert that 0002 was applied, not that it
is the last one, e.g. `assert migrate(c) >= 2` and
`assert c.execute("SELECT 1 FROM schema_version WHERE version = 2").fetchone()`. What they check (0002 fills the
ancestors, replaces stale objects, `add_goal` needs only `migrate()`) stays checked. Same lesson as the CS2/CS5 event
tests: never pin a global that later nodes must grow.

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
