# E3 · where the implementer disagrees with the exam (for the human)

The implementation follows the exam everywhere. Nothing in `tests/frontier/test_graph.py` was edited. One reading
makes the feature weaker than the card intends:

## A12: "explained" means "some node shares the family"

With the 12 seeds covering all 7 Practice families, **no failure cluster on a Practice task can ever become a
candidate**. A dense cluster of `tool.read_file TimeoutError` in `tool_use` is "explained" by `tool.file_read`
even if nothing in the graph is about timeouts. The nightly pass only proposes candidates for families the graph
has never seen (new Arena families, or `unassigned` steps from the CLI).

`test_nightly_leaves_a_cluster_a_node_already_explains` pins this: one `tool_use` node with no evidence must explain
a `tool_use` cluster. So any stricter rule fails the exam.

**What I would do instead:** a node explains a cluster when the cluster's traces are attempts on that node's
instances (the trace's task id is in `node.instances`) **and** the node is already `weak` or `missing`, meaning the
graph already knows she fails there. A cluster spread over several nodes' instances, or over nodes the graph thinks
are fine, becomes a candidate with `fails_together` edges to the nodes involved. This needs steps to carry the
Arena task id (they already have `trace_id`, which is the task id from OBS1).

If the human agrees: change `clustering.explained()` and the one exam test. Until then A12 holds.

## Smaller points (no change needed)

- **`correlated`** can be stored but nothing sets it. A natural rule: an unexplored node with a `fails_together`
  edge to a `weak` node is `correlated`. Not built, since the card gives no rule.
- **No minimum sample size for mastery** (A7). One instance passed with forecast 1.0 is `mastered`. Five of the
  seeds have a single Practice instance, so this will happen. Confidence (n / (n + 10)) reports how thin it is.
