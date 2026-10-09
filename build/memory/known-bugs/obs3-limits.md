# OBS3 v0: known limits

Kept on purpose (the human ruled "documented limit, no change" in the repair round):

- **Interacting changes are double-credited by `ablate`.** If a and b only help together, each gets the full joint gain, so the
  per-change gains add up to more than the total. Pinned by `test_ablate_credits_interaction_to_both_partners`. Fix later with
  Shapley values or pairwise ablation if the number of changes stays small.
- **There is no significance test beyond 2x noise.** With many seeds, a real but small gain (under 2 sigma per run) is never accepted. That
  is strict on purpose, but sometimes it will throw away a real gain. The 0.01 floor (decision 0011, rule 7) makes it stricter still.

Open:

- **`ablate` does not test whether each attributed gain is real.** It gives one number per change, so `run_fn` has to average its
  own seeds. Feeding per-seed lists from each ablation into `is_real_gain` would need a different interface.
- **The CS6 "merge hook" is not built here.** graph.yaml CS6 says every merge writes a Genome record "wired in OBS3's merge hook or CI",
  but OBS3's spec lists no hook and no file for one. The human will assign it.
- **One-row-per-run is not enforced by the database.** `record_run` checks with a SELECT inside its insert transaction (no new migration
  was allowed). Two connections inserting the same run at the same moment, or a raw INSERT that bypasses `record_run`, can still
  make a duplicate. `seed_scores` raises if it finds one. A later migration could add a UNIQUE index on
  `(baseline, candidate, task_set, seed, json_extract(meta, '$.arm'))`.

Fixed:

- [x] `record_run` needed an integer seed but `interleave` accepted any seeds. Both now take only a plain `int` (decision 0011, rule 10).

Red-team breaks: see `obs3-redteam-20261009.md`.
