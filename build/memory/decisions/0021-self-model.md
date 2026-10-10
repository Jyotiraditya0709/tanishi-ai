# 0021 · Self Model v0: capability calibration and the router read (SUB4)

Date: 2026-10-10. Decided by: the SUB4 implementer, adopting every reading the exam pinned
(`open-problems/SUB4-exam-assumptions.md`); points 3, 6, 7, 8 and 9 changed in the repair round after the red team
(`runs/redteam-SUB4-20261010.md`), on the human's decisions. Was 0020. Revisit when SUB3 wires the router, when
predictions carry a `call_id`, or when the Self Model grows past v0.

1. **The tag** is `expected["capability"]` on a CS5 prediction, a non-blank str, matched as exact text (no strip, no
   case folding). Anything else (number, list, blank, missing) is untagged and touches no capability row.
   *Repair (B4):* a tag that CS5's redaction would change is untagged too, and so is a stored tag holding a
   redaction mark (`[REDACTED`, or U+FFFD from a lone surrogate). Two different secret-shaped tags can no longer
   merge into one capability. Asking for such a name reads as unknown / returns None.
2. **The row** in `capabilities` has `id` = tag. Created on first resolved evidence with `name` = tag; later updates
   write only `ability`, `calibration_error`, `updated_at`, so `name`, `family`, `state` set by others survive.
3. **Recompute, never increment.** `update_calibration(cap)` rebuilds the row from every resolved prediction tagged
   `cap`, inside one `BEGIN IMMEDIATE`. Idempotent and order independent. No resolved evidence: returns None and
   writes nothing.
4. **calibration_error** = ECE over CS5's 10 equal-width forecast bins (same binning as `predictions.calibration()`).
   **ability** = plain success rate of the tagged resolved predictions. Both in [0, 1].
   *Repair (B6):* with fewer than 5 (`MIN_EVIDENCE`) resolved predictions, ability is Laplace's rule of succession,
   `(successes + 1) / (n + 2)`, so one lucky call never reads as 1.0. From 5 on it is the plain rate (so B7's
   20-of-20 still reads 1.0; an always-on prior would have hidden B7, which stays a known limit).
5. **Fail open after the ledger commits.** CS5 errors pass through untouched. Once CS5 has scored the prediction, a
   failed capability update is logged (exception type only) and the Brier score returned; the next read or update
   heals the row.
6. **The router reads** `get_capability(cap) -> CapabilityReading(capability, ability, calibration_error,
   updated_at, evidence)` plus the property `low_evidence` (`evidence < 5`). Frozen. Unknown capability: all None,
   evidence 0. Bad name: TypeError / ValueError; a name that is not valid UTF-8 (lone surrogate) is a ValueError
   (repair, B3).
7. **Every resolution reaches the row, whoever resolves (repair, B1/B1b).** Two layers:
   - *Read-through* (the human's decision): `get_capability()` recomputes the row first when the ledger holds a tagged
     resolution with `resolved_at >= updated_at` (ties count as newer; a row with no ability, made by someone else,
     counts as never computed). The time test runs before `json_extract`, so a fresh read costs about 6 ms on a
     60k-row ledger.
   - *CS5 hook*: `predictions.on_resolve(hook)` runs after each `resolve()` commits; this module registers one that
     calls `update_calibration()` for a tagged prediction. Needed because the B1 proof reads the table by SQL right
     after `predictions.resolve()`, with no SUB4 call between (`open-problems/SUB4-repair-conflicts.md`). The hook
     only exists in a process that imported `tanishi.substrate.uncertainty`; the read-through covers the rest.
   `resolve_prediction()` is now just CS5's `resolve()`; the hook does the update.
8. **The evidence count is not stored** (no migration). A fresh read counts once per row version and keeps the count
   in memory keyed by (db file, capability, updated_at); every write sets a new `updated_at`, so a kept count can never
   describe another row.
9. **Not a score (B7).** Predictions are self-graded: a caller that writes and resolves its own tagged predictions
   sets the ability. Until predictions carry a `call_id` that matches a `tool_call` event, ability and
   calibration_error readings **must not be used for Arena scoring, promotion, or any other selection between
   candidates**. They may inform routing (SUB3), with `low_evidence` readings treated as unknown.
