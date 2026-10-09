# OBS3 v0: known limits (not fixed)

- **Interacting changes are double-credited by `ablate`.** If a and b only help together, each gets the full joint gain, so the
  per-change gains add up to more than the total. Pinned by `test_ablate_credits_interaction_to_both_partners`. Fix later with
  Shapley values or pairwise ablation if the number of changes stays small.
- **`ablate` does not test whether each attributed gain is real.** It gives one number per change, so `run_fn` has to average its
  own seeds. Feeding per-seed lists from each ablation into `is_real_gain` would need a different interface.
- **There is no significance test beyond 2x noise.** With many seeds, a real but small gain (under 2 sigma per run) is never accepted. That
  is strict on purpose, but sometimes it will throw away a real gain.
- **The CS6 "merge hook" is not built here.** graph.yaml CS6 says every merge writes a Genome record "wired in OBS3's merge hook or CI",
  but OBS3's spec lists no hook and no file for one. Whoever builds CS6 should own it, or the graph needs a node for it.
- **`record_run` needs an integer seed** (the column is INTEGER). `interleave` accepts any seeds.
