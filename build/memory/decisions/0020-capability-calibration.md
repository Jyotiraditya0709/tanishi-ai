# 0020 · Capability calibration and the router read (SUB4)

Date: 2026-10-10. Decided by: the SUB4 implementer, adopting every reading the exam pinned
(`open-problems/SUB4-exam-assumptions.md`). Revisit when SUB3 wires the router, or the Self Model grows past v0.

1. **The tag** is `expected["capability"]` on a CS5 prediction, a non-blank str, matched as exact text (no strip, no
   case folding). Anything else (number, list, blank, missing) is untagged and touches no capability row.
2. **The row** in `capabilities` has `id` = tag. Created on first resolved evidence with `name` = tag; later updates
   write only `ability`, `calibration_error`, `updated_at`, so `name`, `family`, `state` set by others survive.
3. **Recompute, never increment.** `update_calibration(cap)` rebuilds the row from every resolved prediction tagged
   `cap`, inside one `BEGIN IMMEDIATE`. Idempotent, order independent, and it picks up predictions resolved through
   CS5's `resolve()` directly. No resolved evidence: returns None and writes nothing.
4. **calibration_error** = ECE over CS5's 10 equal-width forecast bins (same binning as `predictions.calibration()`).
   **ability** = plain success rate of the tagged resolved predictions. Both in [0, 1].
5. **Fail open after the ledger commits.** `resolve_prediction()` lets CS5 errors through untouched. Once CS5 has
   scored the prediction, a failed capability update is logged (exception type only) and the Brier score returned;
   the next update of that capability heals the row.
6. **The router reads** `get_capability(cap) -> CapabilityReading(capability, ability, calibration_error,
   updated_at)`, frozen, never cached. Unknown capability: all None, no exception. Bad name: TypeError / ValueError.
