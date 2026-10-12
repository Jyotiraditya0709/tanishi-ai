# 0027 · The Capability Graph and nightly clustering (E3)

Date: 2026-10-11. Decided by: the E3 implementer, adopting every name the exam pinned
(`open-problems/E3-exam-assumptions.md`, A1..A16; A13 and A14 are human decisions of 2026-10-12). R1 (Hypothesis Engine), AR4 and M1 read this graph.

1. **Store.** `CapabilityGraph(db_path=None)` opens the Core State db (`core_state.db.open_db`, so legacy dbs are
   refused) and creates `capability_nodes`, `capability_outcomes`, `capability_edges` with `IF NOT EXISTS`. No
   migration file: the exam opens a fresh path without `migrate`. These tables are separate from CS1's `capabilities`
   table, which SUB4 fills from self-graded predictions (0021). The two are not synced yet.
2. **Node ids, state strings and edge kinds are the contract.** States: `mastered weak missing correlated unexplored`.
   Edges: `requires` (directed, no cycles), `fails_together` (undirected, stored with src < dst), `composes_into`.
3. **Estimates.** ability = (passes + 1) / (n + 2), confidence = n / (n + 10), over the node's own instances only.
   n = instances with an outcome; a re-run replaces that instance's outcome, so n never falls.
   *Changed in repair round 2 (RT-1):* the observed pass rate is passes / **number of sealed instances**, not / n.
4. **States.** `record_outcome`, `set_predicted_rate`, `forecast_from_ledger` and `refresh` recompute in the same
   transaction. With evidence: `mastered` only when **every sealed instance has a scored outcome** (n ==
   len(instances), RT-1), observed >= 0.9 and |predicted - observed| <= 0.10 (1e-9 slack for float error); else
   `weak`. No trusted forecast, no mastery (rule 9). With no evidence the stored state stays (`unexplored`, or
   `missing` / `correlated` set by someone else). Evidence on a `missing` or `correlated` node replaces that state.
   *Creation states (repair round 3, RT-2; human decision A13):* `add_node` accepts only `graph.START_STATES` =
   `unexplored`, `missing`, `correlated`; `weak` and `mastered` raise ValueError and store nothing. Those two are
   reached only by recording outcomes. The table's CHECK still allows all five states, so every state is stored
   and read back.
5. **Seeds.** 12 nodes cover the 29 Practice tasks once each (`graph.SEEDS`). Their instances are Practice task ids
   until AR4 provides sealed ones. **Practice tasks are not sealed**, so a seed marked `mastered` on them means
   mastered on Practice. Each task keeps its own verifier; the node's verifier is the one for its first instance.
   The 6 seed edges are guesses, written down as guesses.
6. **Clustering.** A task failed only when the root span of its last attempt errored (RT-7); its decisive step is
   taken from that attempt. Step text = decisive span name + error class. Words are Unicode `\w+` over `casefold()`
   (RT-5). Steps with no words and rows that are not mappings are dropped and counted (RT-6, RT-9;
   `nightly_report().skipped`, and a stderr line from the CLI). Steps are only compared within one family; two steps
   link when Jaccard > 0.5 (strict); clusters are connected components. Linking uses a prefix filter over an
   inverted word index (exact: same components as comparing every pair), with at most 20,000 distinct texts per
   family per night, keeping those with the most traces (RT-8). Missing family → `unassigned`.
7. **Candidates.** A cluster is explained when one node is `weak` or `missing` and every trace id in the cluster is
   one of that node's instances (A12, human decision 2026-10-11), or when its `candidate_id` already exists.
   Sharing the family is not enough: a cluster on a mastered or unexplored node's tasks, or spread over several
   nodes, becomes a candidate.
   Each unexplained cluster becomes a node `missing.<family slug>.<sha256 of family + texts, 12 hex>` with
   verifier `tanishi.frontier.graph.no_verifier` (always scores 0). Its instances are the cluster's trace ids (RT-4),
   leaving out any that cannot be an instance id, so outcomes on those tasks move it out of `missing`. Explanation
   is judged against the graph as it stood before the run.
8. **Nightly trigger.** `python -m tanishi.frontier.clustering YYYY-MM-DD` reads that day's OBS1 trace file. Nothing
   schedules it yet, and trace records carry no task family, so from the CLI every step is `unassigned` until
   something joins traces to Arena tasks.
9. **Forecasts (repair round 2, RT-3; human decision, as L1's rule R1 in 0026: the ledger, never the caller, is the
   source of a prediction).** `forecast_from_ledger(node_id, prediction_id)` takes the forecast from a CS5 row
   (decision 0014) in the graph's db whose `about` is `capability:<node_id>`; the forecast is its P(success).
   It is **trusted** (`Node.forecast_trusted`) only when the row's `ts` is earlier than the node's
   `first_outcome_at`, which the first `record_outcome` sets and nothing moves. *Repair round 3 (human decision
   A14):* only a trusted forecast counts toward `mastered` (`_recompute` passes `predicted_rate` only when
   `forecast_trusted`). A direct `set_predicted_rate` is kept as a stored what-if: never trusted, never counts, and it
   replaces any earlier ledger forecast, so it takes mastery away. `mastered` now implies `forecast_trusted`; gates
   no longer need to check both. The nightly `explained()` reads only `weak` / `missing`, so it needs no change.
   Consequence: a node with any outcome before its forecast can never be mastered (known-bugs).
10. **Verifiers** must be dotted paths starting with `tanishi.` (RT-10). **`seed()`** skips a seed `requires` edge
    that would close a cycle with an existing edge and logs it; the 12 nodes always land (RT-11).
