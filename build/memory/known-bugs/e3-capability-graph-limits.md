# E3 · Capability Graph limits (2026-10-11)

Referenced from `tanishi/frontier/graph.py` and `clustering.py`. See decision 0027.

- [ ] **Self-reported outcomes.** `record_outcome` trusts its caller, like SUB4's predictions (0021 rule 9).
      Anything can mark a node mastered by writing passes and a matching forecast. Wire it to Arena `experiments`
      rows (verifier-scored) before any promotion gate reads `mastered`.
- [ ] **Direct forecasts still earn mastery** (RT-3, exam conflict, `open-problems/E3-repair2-conflicts.md`).
      Only `forecast_trusted` (CS5 ledger row older than the first outcome) is honest. Once a node has an outcome,
      no later forecast can be trusted.
- [ ] **Candidates' instances are OBS1 task ids.** RT-4 gives a `missing` candidate its cluster's trace ids as
      instances. From the Arena those are Arena task ids. From the CLI (`python -m tanishi.frontier.clustering`) they
      are whatever `task_id` OBS1 recorded, and nothing scores outcomes against those ids yet. Steps with no task id
      never reach a cluster, so a candidate with no instances only comes from trace ids that are invalid
      instance ids (control characters, over 200 chars). Those are left out, and if all are invalid it has none.
- [ ] **Candidates freeze their instances.** A candidate's instances are the night's traces; a later night's
      failures of the same kind are a new candidate (new `candidate_id` if the texts changed), not new instances.
- [ ] **Linking is capped.** Over 20,000 distinct step texts in one family in one night, the texts with the fewest
      traces are skipped (logged, counted in `nightly_report().skipped.capped_texts`).
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
- [ ] **Several attempts under one task id:** only the last root (latest `start_ts`) decides failure (RT-7). A
      task with no root (a parent loop) is never a failure.
- [ ] **Not synced with CS1 `capabilities`.** SUB4's router reads that table; the graph does not write to it.
