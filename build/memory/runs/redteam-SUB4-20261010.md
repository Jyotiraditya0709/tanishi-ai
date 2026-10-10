# SUB4 · red team (2026-10-10)

Target: `tanishi/substrate/uncertainty.py` at 1dc16cd. Proofs: `tests/substrate/test_uncertainty_redteam.py`
(5 fail on purpose, 2 xfail strict for design points, 6 pass). Nothing was fixed here: the red team does not build.

## Breaks

| # | Sev | Break | Proof | Smallest fix |
|---|-----|-------|-------|--------------|
| B1 | **High** | The acceptance line "resolving predictions updates `capabilities.calibration_error`" holds only for callers of `uncertainty.resolve_prediction()`. `ToolRegistry.execute()` and any other CS5 caller use `predictions.resolve()`, which never touches `capabilities`. The row stays absent until someone happens to call `update_calibration()`. A test that resolves through CS5 and reads the table by SQL fails. | `test_b1_*` | Make the read self-healing: in `get_capability()`, call `update_calibration()` when the ledger holds a resolved tagged prediction newer than `updated_at`. Or one line in CS5 `resolve()` (not this node's file) calling a registered hook after commit. |
| B1b | Medium | Same root, router side: `get_capability()` returns stale ability after a CS5-side resolve (reads 1.0, truth 0.5). The docstring says "never cached", but the stored row is a cache. | `test_b1b_*` | Same as B1. |
| B3 | Low | A name with a lone surrogate makes `get_capability()` and `update_calibration()` raise `UnicodeEncodeError`, outside the documented TypeError / ValueError / unknown-reading contract. The router would crash on it. | `test_b3_*` | In `_check_capability`, `capability.encode("utf-8")` in a try and raise ValueError. |
| B4 | Low | Tags go through CS5's redaction, so every secret-shaped tag becomes the same `[REDACTED:openai]` text. Two different tags merge into one capability, which is readable only by its marker name. Known limit says "will not match"; the merge is worse and unlisted. | `test_b4_*` | In `predictions.predict`-side or `tag_of`: refuse (treat as untagged) a tag that `redact()` would change. SUB1's `save()` does this for `task_id`. |
| B6 | Medium (design) | One resolved prediction at confidence 1.0 reads `ability=1.0, calibration_error=0.0`: a perfect score from n=1. No count in the read. The benchmark "calibration table" passes for the wrong reason on tiny samples. Already listed as a limit; the red team rates it Medium because SUB3 will route on it. | `test_b6_*` (xfail) | Add `n` to the reading (count it in `update_calibration`, return it from `get_capability` without a column by recomputing), and let SUB3 shrink toward a prior. |
| B7 | Medium (design) | Nothing ties a tagged prediction to reality: a caller can write and resolve its own predictions and set any capability's ability. Same root as CS5's open design point (no `call_id`). Evaluation leakage if a candidate can reach `predict()` and `resolve()`. | `test_b7_*` (xfail) | Do not feed `ability` into any Arena or promotion decision until predictions carry a `call_id` matching a `tool_call` event. |

## Tried, held

- Tag edge cases (blank, number, bool, list, dict, padded, case): untagged or exact, as decided.
- NUL byte in a tag: SQLite's `json_extract` returns it intact on this build (3.x here); the Python re-check also guards it.
- Ordinary names that mention a secret word (`token counting`, `api_key rotation`): survive redaction.
- Raw rows with non-object `expected` / `actual`, confidence outside [0, 1] (the schema CHECK refuses it): skipped or clamped.
- Concurrency: `BEGIN IMMEDIATE` around read and write; CS5 resolve-once guards double scoring.
- Failure after the ledger commits: score is still returned, warning carries only the exception type, no secret in logs.
- Overwriting other columns (`name`, `family`, `state`): the upsert leaves them.
- Perf: 60k ledger rows with 20k of one capability: one resolve 0.18 s (linear, about 3 µs a row). Fine today; the
  scan is already a listed limit, and an expression index would need a migration.

## Not tested

The goal's "every state item carries a confidence" has no interface and no tests (listed in known-bugs). Not a break
of the acceptance list, but the node does not deliver its own goal sentence.
