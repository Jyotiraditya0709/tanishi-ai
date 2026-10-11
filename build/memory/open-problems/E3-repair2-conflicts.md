# E3 · repair round 2: where a card fix and a test conflict (for the human)

Date: 2026-10-12. The card's rule: the test wins. Two fixes cannot be made without failing exam tests in
`tests/frontier/test_graph.py`. The red-team proofs for them stay `xfail(strict=True)`; no assertion was changed.

## RT-2: `add_node` accepts only `unexplored`, `missing`, `correlated`

**Conflict.** `test_every_listed_state_can_be_stored` is parametrised over all five states and asserts
`add_node(..., state="mastered")` and `state="weak"` store and read back that state. The red-team test
`test_a_node_cannot_be_created_mastered_without_evidence` asserts the same call raises ValueError. Both
cannot hold.

**What holds now.** `add_node` takes every state; its docstring says only the three are honest starting points.
`test_a_node_cannot_be_created_mastered_without_evidence` stays xfail.

**What I would do.** Change the exam's parametrisation to `("unexplored", "missing", "correlated")`, add an exam
test that `mastered` and `weak` raise, and add `START_STATES` to `graph.py` with the `_state` check in `add_node`.
That is about four lines. My own `test_clustering_eval.py::test_nightly_on_the_corpus_proposes_one_candidate_per_unexplained_mode`
creates a `weak` node with `add_node(state="weak")`. Rewrite it to record failing outcomes instead.

## RT-3: a forecast counts only from the CS5 ledger, and only when older than the first outcome

**Conflict.** The exam's `_fill` records every outcome first, then calls `set_predicted_rate`, then expects
`mastered` in six tests: `test_mastered_at_ninety_percent_with_calibrated_prediction`,
`test_prediction_exactly_ten_points_off_still_counts_as_within`, `test_eighty_nine_percent_is_not_mastered_even_if_calibrated`
(second half), `test_mastery_is_lost_when_failures_arrive` (first assert), `test_mastered_requires_both_clauses_property`
and `test_nightly_dense_cluster_inside_a_mastered_nodes_family_becomes_a_missing_candidate`. The red-team test
`test_a_forecast_made_after_the_outcomes_does_not_earn_mastery` makes the same sequence (all pass, then
`set_predicted_rate(1.0)`) and asserts *not* mastered. These are the same sequence with opposite assertions.

**What holds now (built, tested in `test_capability_graph_impl.py`):**
- `forecast_from_ledger(node_id, prediction_id)` reads the `predictions` row from the graph's own db, which is the
  Core State db in production. The prediction must be about `capability:<node_id>`. The forecast is its
  P(success) (decision 0014 rule 1).
- `Node.forecast_trusted` is true only for a ledger forecast whose row `ts` is earlier than the node's
  `first_outcome_at`, which is set once by the first `record_outcome` and never moves when outcomes are re-run.
- An untrusted ledger forecast counts as no forecast, so the node cannot be mastered.
- **A direct `set_predicted_rate` still counts for mastery** (never `forecast_trusted`), only because the exam
  pins it. Until this is resolved, a promotion gate must read `state == "mastered" and forecast_trusted`.

**What I would do.** Rewrite the exam's `_fill` to write a CS5 prediction before the outcomes and call
`forecast_from_ledger`. Then in `CapabilityGraph._recompute` change
`predicted = n.predicted_rate if n.forecast_id is None or n.forecast_trusted else None` to
`... if n.forecast_trusted else None`. Then either remove `set_predicted_rate` or keep it as a "what-if" that
never counts. The `state_for` table tests are pure and need no change.

A consequence of "older than the first outcome": once a node has any outcome, no new forecast can be trusted, so
a node scored before its forecast can never be mastered. If that is too strict, the next rule would be "older than
the first outcome recorded after the forecast", which needs per-outcome history (the outcomes table keeps only the
latest result per instance).
