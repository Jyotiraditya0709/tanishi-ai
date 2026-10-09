# CS1 · the exam contradicts the red-team tests (for the tester agent)

Found in the CS1 repair round on 2026-10-09. No code can satisfy both sides, and the implementer may not change an assertion.
The human ruled on each case. The suite has **10 known failures** in `tests/core_state/test_migrations.py` until the exam's
owner amends it.

| Conflict | Ruling | Effect | What the exam should change |
|---|---|---|---|
| `test_property_rows_round_trip` writes `confidence = random() * choice([1, 100, 1e6])` into beliefs, predictions and rules. R14 asserts 5 and -3 are rejected. | Red team wins | 9 cases fail (`[0-2]-beliefs/predictions/rules`) with `CHECK constraint failed: confidence BETWEEN 0 AND 1` | Generate confidence in [0, 1] (a new kind, e.g. `c`, in `KINDS`) |
| `test_events_id_autoassigns_increasing` inserts three events that all have `prev_hash='p'`. Decision 0007 / R10 make `prev_hash` UNIQUE. | Decision wins | 1 case fails with `UNIQUE constraint failed: events.prev_hash` | Use distinct `prev_hash` values (e.g. chain them: `prev_hash = h{i-1}`) |
| Three exam tests call `open_db()` under pytest with `TANISHI_CORE_STATE_DB` unset and expect `$HOME/.tanishi/core_state.db`. R8 asserts that call must raise. | Exam wins | `test_default_path_is_not_the_real_home_when_tests_forget_the_env` stays `xfail(strict)` | None. The red-team test could instead assert that the autouse fixture keeps the real home untouched |

Settled without a ruling, because the test wins over a decision and nothing fails:
- Decision 4 wanted a `schema_version.checksum` column. The exam asserts `schema_version` columns are exactly
  `(version, applied_at)`, so checksums live in `schema_checksum`.
- Decision 2 wanted DELETE on `events` to always abort. R11 deletes an unhashed event, so the trigger only guards hashed rows.
- Decision 8 wanted the autouse fixture to set `TANISHI_HOME`. That would move the default path away from `$HOME/.tanishi`
  and fail the exam, so the fixture unsets `TANISHI_HOME` and sets a temporary `HOME`.

## Status
Resolved on 2026-10-09 in f0399d3: the exam data follows decision 0007.
