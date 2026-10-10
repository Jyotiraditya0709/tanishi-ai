# SUB4 · implementer run

1. **What did we learn?** The exam's pinned interface (`resolve_prediction`, `update_calibration`, `get_capability`,
   ECE over CS5's 10 bins, tag in `expected["capability"]`) was sound and buildable without a migration. Recomputing
   the row from all evidence under `BEGIN IMMEDIATE`, not incrementing it, makes idempotence, order independence and
   "resolved via CS5 directly" fall out for free. 76/76 exam tests and the full suite (1941) pass.
2. **What failed, and why?** My own fail-open test first used `monkeypatch.undo()`, which also undid the suite
   conftest's temp `TANISHI_CORE_STATE_DB` (same fixture instance), so the follow-up read hit a fresh db. Use
   `monkeypatch.context()` for a scoped patch in this repo.
3. **Which assumption was wrong?** That the goal line ("every state item carries a confidence") was part of the
   work. No acceptance line, no interface; left out and written up in `known-bugs/sub4-calibration-limits.md`.
4. **What should the next agent know?** SUB3: read `get_capability(cap)`; it has no evidence count and ability is
   an unsmoothed rate. Nothing in production tags predictions with a capability yet, so the table is empty until a
   caller passes `expected["capability"]` to `predict()` and resolves through `resolve_prediction()` (or calls
   `update_calibration()` after CS5's `resolve()`). Decision 0020 has the rules.
