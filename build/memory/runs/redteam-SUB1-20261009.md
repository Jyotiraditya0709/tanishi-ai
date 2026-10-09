# Red team · SUB1 state · 2026-10-09

Proofs: `tests/substrate/test_state_redteam.py` (12 tests; 7 fail on purpose and show a break, 5 pass and show what held).

## Breaks

| # | Sev | Break | Test | Smallest fix |
|---|-----|-------|------|--------------|
| B1 | **Medium** | `save()` writes `working`/`hypotheses` in plaintext. A key pasted into working state lands in `core_state.db`. Events redact (`events.redact`); substrate state does not. | `test_secret_in_working_state_*` | In `save()`, run `redact()` over working/hypotheses/step text before `_dumps`, or refuse with `ValueError` if `redact(x) != x`. Refusing keeps the round-trip rule honest. |
| B2 | Low | Duplicate `Step.id` accepted, so `result_ref` and "which step is done" are ambiguous. | `test_duplicate_step_ids_*` | In `_validate_shape`, `ValueError` on repeated ids. |
| B3 | Low | Empty step id accepted (task id is checked, step id is not). | `test_empty_step_id_*` | Same place: `ValueError` on `""`. |
| B4 | Low | `"DONE"`, `"Done"`, `" done"` are opaque text, so `next_step()` silently re-runs a finished step (a repeat tool call). | `test_status_case_variant_*` | Refuse a status that is not lowercase-stripped, or restrict to the four exported words. Decision 0014 allows opaque text, so this needs a human call. |
| B5 | Low | A row whose plan lacks a step key makes `load()` leak `KeyError` (decision 0014 says `ValueError` for a bad row). | `test_corrupt_plan_row_*` | Wrap the rebuild in `try/except (KeyError, TypeError, ValueError, json.JSONDecodeError)` and re-raise `ValueError`. |
| B6 | Low | Plan `'null'` (valid JSON, wrong shape) leaks `TypeError`. | `test_plan_json_null_*` | Same wrap as B5. |
| B8 | Low | Whitespace-only `task_id` accepted: an invisible task. | `test_whitespace_only_task_id_*` | `if not state.task_id.strip()`. |

## Held (tried, no break)

Sixteen processes saving into a brand-new db at once all succeed (B7, migrations race is safe). A lone surrogate
round-trips. `10**5000` is refused cleanly and leaves no row. A RUNNING step is replayed on resume (by design, noted in
the known limits: a non-idempotent step can run twice, so tools behind it must be idempotent or check `result_ref`).
Also read and found fine: save is one `INSERT OR REPLACE` (kill leaves whole old or whole new row), snapshots are deep
(`json.dumps`), IntEnum/tuple/NaN/cycles are refused.

## Not fixed, already in known-bugs

`known-bugs/sub1-state-limits.md` (no history, last writer wins, whole-row rewrite plus `migrate()` on every call,
unchecked goal ids, power loss untested). I did not measure the per-call `migrate()` cost; it hashes every migration
file each save, which will matter once the loop saves per step.

## Evaluation leakage

None: SUB1 has no path to Frontier tasks or the Sealed Vault. I did not open `vault/` or `builder_vault/`.
