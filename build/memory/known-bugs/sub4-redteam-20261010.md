# SUB4 · red-team breaks (2026-10-10, open)

Details and proofs: `runs/redteam-SUB4-20261010.md`, `tests/substrate/test_uncertainty_redteam.py`.

- [ ] B1 High: a prediction resolved through CS5 `resolve()` (which is what `ToolRegistry.execute()` uses) never
  updates `capabilities`. Only `uncertainty.resolve_prediction()` does.
- [ ] B1b Medium: `get_capability()` reads a stored row that can be stale against the ledger.
- [ ] B3 Low: a lone-surrogate capability name raises `UnicodeEncodeError` from `get_capability` / `update_calibration`.
- [ ] B4 Low: distinct secret-shaped tags merge into one `[REDACTED:...]` capability.
- [ ] B6 Medium (design): no evidence count, so n=1 reads as ability 1.0, error 0.0.
- [ ] B7 Medium (design): predictions are self-graded; do not use `ability` as an Arena score or promotion input.
