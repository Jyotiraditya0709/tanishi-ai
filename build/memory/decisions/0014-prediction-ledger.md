# 0014 · Prediction ledger semantics (CS5)

Date: 2026-10-09. Decided by: the CS5 builder, within the spec and the readings the CS5 exam pinned
(`open-problems/CS5-exam-assumptions.md`). Revisit before the Self Model or World Model reads the ledger.

1. **What confidence means.** `confidence` is the confidence that `expected` holds. Forecast P(success) is
   `confidence` if `expected["success"]` is true, else `1 - confidence`. Score = (forecast - outcome)², stored in
   `predictions.score`. `calibration()` recomputes forecasts the same way.
2. **`success` is required and must be a bool**, in `expected` and in `actual`. Missing: ValueError. Not a bool:
   TypeError. A refused call writes nothing, so the prediction stays resolvable. Every other field is stored as given
   (redacted JSON in TEXT, decision 0006); non-JSON values (NaN, objects) are refused.
3. **Exactly once.** `resolve()` reads, scores and updates in one `BEGIN IMMEDIATE` transaction, updating only
   `WHERE resolved_at IS NULL`. Unknown id: `LookupError`. Second resolve: `AlreadyResolvedError` (a ValueError).
4. **Events after commit, fail open.** `prediction {id, about, expected, confidence, task_id}` and
   `prediction_resolved {id, about, actual, score, task_id}`, actor `core_state.predictions`, session from
   `task_scope`. A failed emit() warns and is noted as a log gap by emit(); the ledger row stands (as decision 0009).
5. **Ids** are `pred_<uuid4 hex>`. Times are UTC ISO with microseconds, like the event log.
6. **Calibration** returns `{"brier", "n", "bins"}`, 10 equal-width bins on the forecast, each
   `{"lo", "hi", "n", "mean_forecast", "success_rate"}`. Prefix match is literal (`substr`, not LIKE).
7. **The stale helper** is `unresolved_older_than(hours=24.0)`. The exam finds it by name: keep it the only public
   function in `predictions.py` whose name contains `unresolved`, `stale` or `overdue`.
8. **Tool predictions.** `about = "tool:<name>"`, `expected = {"success": True, "latency_ms": <median of the last 50
   latencies, or None>}`, `confidence = (successes + 0.75·4) / (n + 4)` over the last 50 resolved calls of that tool.
   Resolved with `{"success", "latency_ms"}` only when the handler ran to an outcome (success, error, timeout).
   *Changed in the CS5 repair (red team R1-R3):* a call that never reached the tool is not evidence about the tool.
   An unknown name, or a tool that needs approval with no approval callback, is not predicted at all. A user denial,
   a raising approval callback, a missing `tool_call` event or a cancellation leaves the prediction unscored
   (unresolved; the stale helper lists it after 24 h). The ledger **fails open** in `execute()`: it only learns,
   it is not a gate.
