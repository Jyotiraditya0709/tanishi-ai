# E3 · Capability Graph limits (2026-10-11)

Referenced from `tanishi/frontier/graph.py` and `clustering.py`. See decision 0027.

- [ ] **Self-reported outcomes.** `record_outcome` trusts its caller, like SUB4's predictions (0021 rule 9).
      Anything can mark a node mastered by writing passes and a matching forecast. Wire it to Arena `experiments`
      rows (verifier-scored) before any promotion gate reads `mastered`.
- [ ] **Seeds are not sealed.** Instances are Practice task ids (AR2), which the candidate can see. Replace them
      with AR4 sealed instances when AR4 lands.
- [ ] **A12 hides Practice failures** (see `open-problems/E3-exam-conflicts.md`).
- [ ] **CLI steps have no family.** OBS1 records do not carry the Arena family, so `python -m
      tanishi.frontier.clustering` files every cluster under `unassigned`. After the first night, every
      `unassigned` cluster counts as explained.
- [ ] **Single linkage** can chain long, varied texts. Fine for name + error class; revisit if step text grows.
- [ ] **Not synced with CS1 `capabilities`.** SUB4's router reads that table; the graph does not write to it.
