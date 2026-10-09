# CS5 exam · where the spec is silent

The exam (`tests/core_state/test_predictions.py`) was written from the spec alone. It pins these readings. If the
implementer disagrees, say so here and let the human decide; "the test wins" unless the human rules otherwise.

1. `confidence` is the confidence that `expected` holds. Forecast P(success) = `confidence` if `expected["success"]`
   is true, else `1 - confidence`. Score = (forecast - outcome)^2.
2. `confidence` outside [0,1] or NaN, a blank `about`: `ValueError`. Unknown id on `resolve`: `LookupError`.
   Non-dict `actual`/`expected`: `TypeError` or `ValueError`. A rejected resolve must not burn the prediction.
3. `calibration(prefix)` returns `{"brier": float|None, "bins": [dict, ...]}`; each bin has a count under `"n"` or
   `"count"`; counts add up to the number of scored predictions. Prefix is literal (no LIKE wildcards). No data: brier None.
4. The stale helper has no name in the spec. The exam finds the one public function in `predictions.py` whose name
   contains `unresolved`, `stale` or `overdue`; it takes no required argument and returns ids or records containing them.
5. predict and resolve each write at least one CS2 event (kind contains "predict"); the prediction event of a tool call
   comes before its `tool_call` event; secrets are redacted (use `emit()`).
6. The exam ages a prediction by scanning tables named `*predict*` in the Core State db and moving ISO/epoch timestamps
   back 25 h. If the implementation stores time some other way (e.g. integer ms), that helper will need adjusting.
7. Not covered: the exact stored `expected latency` value (only that "latency" is stored), and failure of the ledger
   inside `execute()` (fail open vs closed is not in the spec).
