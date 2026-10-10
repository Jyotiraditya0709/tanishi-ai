# SUB4 · exam: where the spec is silent

The spec for SUB4 has two acceptance lines and no interface. The exam (`tests/substrate/test_uncertainty.py`) was
written from the spec alone and pins the readings below. If the implementer disagrees, say so here and let the human
decide; "the test wins" unless the human rules otherwise.

1. **The tag.** "The capability tagged on the prediction" is `expected["capability"]`, a non-blank str, written with
   CS5's `predict()` (the `predictions` table has no capability column, and CS5 stores extra `expected` fields as given).
   A prediction without the field is untagged: it resolves normally and touches no capability row.
2. **The row.** A capability's id in the `capabilities` table is the tag text. The row is created on first update
   (`id` = `name` = tag). `calibration_error`, `ability` and `updated_at` are written there; the exam reads
   `capabilities.calibration_error` by SQL, as the acceptance line words it.
3. **Public names** in `tanishi.substrate.uncertainty`:
   - `resolve_prediction(prediction_id, actual) -> float`: CS5 `resolve()` plus the capability update; returns the
     Brier score. CS5 errors (`LookupError`, `AlreadyResolvedError`) pass through and change nothing.
   - `update_calibration(capability) -> float | None`: recompute the capability's row from every resolved prediction
     tagged with it; return its calibration_error (None with no resolved evidence). Idempotent.
   - `get_capability(capability) -> obj` with attributes `ability` and `calibration_error`: what the router reads.
     Unknown capability: both are None, no exception. Reads the row; never cached.
4. **calibration_error** is the expected calibration error over the same 10 equal-width forecast bins CS5
   `calibration()` uses: `sum_k (n_k / N) * |mean_forecast_k - success_rate_k|`, over the resolved predictions tagged
   with the capability. Forecast = `confidence` if `expected["success"]` else `1 - confidence` (decision 0014).
   0.0 is perfect, values lie in [0, 1]. Hand-computed cases use forecasts well inside a bin (0.85, 0.25, 0.65).
5. **ability** is a float in [0, 1] or None before any resolved evidence. Its formula is not pinned; the exam only
   requires that a capability that succeeds more often has the higher ability and that it is in [0, 1]. (Suggested:
   success rate of the resolved predictions.)
6. **Not covered:** "every state item carries a confidence" (goal text) has no acceptance line and no interface, so it
   has no test. The router wiring itself is SUB3. Whether `ToolRegistry.execute()` tags tool predictions with a
   capability is not in this node's files.

What I would do instead: put the interface and the definition of calibration_error in the spec.
