# E3 · Capability Graph limits (2026-10-11)

Referenced from `tanishi/frontier/graph.py` and `clustering.py`. See decision 0027.

- [ ] **Self-reported outcomes.** `record_outcome` trusts its caller, like SUB4's predictions (0021 rule 9).
      Anything can mark a node mastered by writing passes and a matching forecast. Wire it to Arena `experiments`
      rows (verifier-scored) before any promotion gate reads `mastered`.
- [ ] **Seeds are not sealed.** Instances are Practice task ids (AR2), which the candidate can see. Replace them
      with AR4 sealed instances when AR4 lands.
- [x] **A12 hid Practice failures.** Fixed 2026-10-11: `explained()` now needs the cluster's traces to be one
      weak/missing node's own instances, not just a shared family (see `open-problems/E3-exam-conflicts.md`).
- [ ] **Explanation needs task ids as trace ids.** A step's `trace_id` must be the Arena task id (= instance id)
      for a node to explain it. Steps keyed any other way are never explained by a node, so every dense cluster
      becomes a candidate (noisy, but nothing is hidden).
- [ ] **No `fails_together` edges.** A cluster spread over several nodes' instances becomes a candidate, but the
      edges to those nodes that the conflict note suggests are not written yet.
- [ ] **CLI steps have no family.** OBS1 records do not carry the Arena family, so `python -m
      tanishi.frontier.clustering` files every cluster under `unassigned`. A re-run proposes nothing new for a
      cluster with the same texts (`candidate_id`), but a cluster whose texts change gets a new candidate.
- [ ] **Single linkage** can chain long, varied texts. Fine for name + error class; revisit if step text grows.
- [ ] **Not synced with CS1 `capabilities`.** SUB4's router reads that table; the graph does not write to it.
