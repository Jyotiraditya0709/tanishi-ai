# E3 - what the spec leaves open (written by the tester, from the spec alone)

The card gives three files and four acceptance lines, no signatures. `tests/frontier/test_graph.py` pins the names
below. If the implementer disagrees, write `E3-exam-conflicts.md`; the human decides. Do not edit the tests.
The implementation was not read. Inputs referenced: Arena `Task` fields (`family`, `verifier` dotted path) and
decision 0023 span records (`span_id`, `parent_id`, `name`, `status`, `error`, `start_ts`).

- **A1 - Modules.** `tanishi.frontier.graph` and `tanishi.frontier.clustering` (the package needs `__init__.py`).
- **A2 - Vocabulary.** `graph.STATES == ("mastered","weak","missing","correlated","unexplored")`,
  `graph.EDGE_KINDS == ("requires","fails_together","composes_into")`, in that order.
- **A3 - Store.** `CapabilityGraph(db_path=None)`; None means `TANISHI_CORE_STATE_DB`. It creates its own tables, so the
  exam does not call `migrate` first. State survives re-opening the same path.
- **A4 - Nodes.** `add_node(id, task_family, verifier, instances=(), state="unexplored")` returns a Node with
  `id, task_family, verifier, instances, state, ability, confidence`. `node(id)`, `nodes()`. Duplicate id, blank
  family, non-dotted verifier, duplicate instance ids and unknown state are refused. `add_node` does not import the verifier.
- **A5 - Ability and confidence.** Both in [0,1]. Ability is on the pass-rate scale (monotone in passes, below .5 at 10%,
  above .5 at 90%). Confidence is 0 with no evidence, grows with sample size at the same rate and never falls as outcomes
  accumulate. The exact estimator (Wilson, Beta posterior, ...) is free.
- **A6 - Evidence.** `record_outcome(node_id, instance_id, passed: bool)` (the instance must be one of the node's sealed
  instances; a re-run of an instance replaces its earlier outcome), `set_predicted_rate(node_id, p)` with p in [0,1],
  `refresh(node_id)` recomputes and returns the node. With zero outcomes the state stays as stored (unexplored).
- **A7 - Mastered.** Observed pass rate over sealed instances >= 0.9 AND a predicted rate set AND |pred - obs| <= 0.10
  (inclusive). With evidence but not mastered the state is `weak`. `graph.state_for(observed, predicted, n)` is the
  pure rule; n == 0 gives `unexplored`. `missing` and `correlated` come from clustering and edges, not from pass rates.
  No minimum sample size is pinned (the card gives none).
- **A8 - Edges.** `add_edge(src, dst, kind)`, `edges(kind=None)` returns objects with `.src .dst .kind`. Unknown nodes
  raise KeyError; self edges and unknown kinds raise ValueError; duplicates collapse; `fails_together` is undirected;
  a `requires` cycle is refused. `requirements(id)` is the transitive set of required node ids.
- **A9 - Seeds.** `seed()` makes 12 nodes, idempotent, none mastered at start, each with a family, an importable
  verifier callable, and only valid edges. Which 12 is the implementer's call (the master plan is not in the repo).
- **A10 - Decisive step.** `clustering.decisive_step(spans)`: among spans with `status == "error"` that have no erroring
  child (via `parent_id`), the one with the earliest `start_ts`; None when no span errored. Rationale: a parent only
  says "a child failed", so the decisive step is where the failure began.
- **A11 - Clustering input.** `cluster_failures(steps, min_size=3)` takes dicts with `trace_id`, `text` (name + error
  class of the decisive step; ids and lengths only, decision 0023 rule 10) and optional `family`. Returns objects with
  `.size` and `.trace_ids`. Similarity is the implementer's (stdlib only). Dense means at least `min_size` distinct
  traces; one trace counts once; a trace is in at most one cluster; input order never matters.
- **A12 - Explained.** A cluster is explained by a node when the node's `task_family` equals the family of the cluster's
  steps. (The card says only "a node explains".)
- **A13 - Nightly.** `nightly(graph, steps, min_size=3)` returns the new candidates and registers each as a node with
  state `missing` and a deterministic id. Running it again on the same failures adds nothing and returns [].
- **A14 - Hostile text.** Control characters, SQL and template markup in step text must not crash or reach node ids.

## Not covered, and why

- **Wiring to real traces and the Practice tier.** No trace reader or scheduler API is named on the card; the "every
  night" trigger (cron, Prime queue) is a later node.
- **Which similarity measure clusters well.** A quality number needs the real trace corpus; the exam uses a small
  synthetic set and checks structure, not quality.
- **`correlated` assignment.** The card lists the state but not the rule that sets it.
