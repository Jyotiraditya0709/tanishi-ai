# 0027 · The Capability Graph and nightly clustering (E3)

Date: 2026-10-11. Decided by: the E3 implementer, adopting every name the exam pinned
(`open-problems/E3-exam-assumptions.md`, A1..A14). R1 (Hypothesis Engine), AR4 and M1 read this graph.

1. **Store.** `CapabilityGraph(db_path=None)` opens the Core State db (`core_state.db.open_db`, so legacy dbs are
   refused) and creates `capability_nodes`, `capability_outcomes`, `capability_edges` with `IF NOT EXISTS`. No
   migration file: the exam opens a fresh path without `migrate`. These tables are separate from CS1's `capabilities`
   table, which SUB4 fills from self-graded predictions (0021). The two are not synced yet.
2. **Node ids, state strings and edge kinds are the contract.** States: `mastered weak missing correlated unexplored`.
   Edges: `requires` (directed, no cycles), `fails_together` (undirected, stored with src < dst), `composes_into`.
3. **Estimates.** ability = (passes + 1) / (n + 2), confidence = n / (n + 10), over the node's own instances only.
   n = instances with an outcome; a re-run replaces that instance's outcome, so n never falls.
4. **States.** `record_outcome`, `set_predicted_rate` and `refresh` recompute in the same transaction.
   With evidence: `mastered` if observed >= 0.9 and |predicted - observed| <= 0.10 (1e-9 slack for float error),
   else `weak`. No forecast, no mastery. With no evidence the stored state stays (`unexplored`, or `missing` /
   `correlated` set by someone else). Evidence on a `missing` or `correlated` node replaces that state.
5. **Seeds.** 12 nodes cover the 29 Practice tasks once each (`graph.SEEDS`). Their instances are Practice task ids
   until AR4 provides sealed ones. **Practice tasks are not sealed**, so a seed marked `mastered` on them means
   mastered on Practice. Each task keeps its own verifier; the node's verifier is the one for its first instance.
   The 6 seed edges are guesses, written down as guesses.
6. **Clustering.** Step text = decisive span name + error class. Words are `[a-z0-9_]+`, lowercased. Steps are only
   compared within one family; two steps link when Jaccard > 0.5 (strict); clusters are connected components.
   Missing family → `unassigned`.
7. **Candidates.** A cluster is explained when one node is `weak` or `missing` and every trace id in the cluster is
   one of that node's instances (A12, human decision 2026-10-11), or when its `candidate_id` already exists.
   Sharing the family is not enough: a cluster on a mastered or unexplored node's tasks, or spread over several
   nodes, becomes a candidate.
   Each unexplained cluster becomes a node `missing.<family slug>.<sha256 of family + texts, 12 hex>` with
   verifier `tanishi.frontier.graph.no_verifier` (always scores 0) and no instances. Explanation is judged against
   the graph as it stood before the run.
8. **Nightly trigger.** `python -m tanishi.frontier.clustering YYYY-MM-DD` reads that day's OBS1 trace file. Nothing
   schedules it yet, and trace records carry no task family, so from the CLI every step is `unassigned` until
   something joins traces to Arena tasks.
