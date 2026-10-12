# E2 Causal Attribution: known limits

- **Interacting changes still get overlapping credit.** Ablation is one-at-a-time, so if a and b only help together
  each gets the full joint gain and gene effects sum to more than the total (as OBS3, decision 0011). Organ scores
  then double-count it. Fix later with pairwise ablation or Shapley values while the change count stays small.
- **Cost is (n + 2) x seeds runs** for n changes. Fine for a handful of genes per merge; too slow for dozens.
- **Small real genes are missed.** A gene needs ~3-4 x per-run noise to be found reliably (benchmarks/E2-causal-eval.md).
  The bar is strict on purpose (decision 0011, rule 1).
- **Gene verdicts are not corrected for multiple comparisons.** With many genes the chance that some passenger is
  credited grows (~0.5% each at 6 seeds).
- **Redundant changes get no credit.** If either of two genes alone gives the gain, removing one costs nothing, so
  neither is real. `Attribution.complete` is False and `unattributed` shows how much is missing (decision 0028,
  rule 13), but which gene earned it stays unknown. Pairwise ablation or Shapley values would split it.
- **The caller still chooses the seeds.** 5 seeds cut a null change's accept rate to 0.37%, but a caller who tries many
  seed lists can still find one that passes. R2 must fix the seed list first and record how many it tried.
- **`organ_scores` trusts its input.** It does not check that `gain`/`real` really came from an `Attribution`; a caller
  can hand it any numbers. The `gene` id is optional (the exam's proposals carry none), so proposals without one can
  still repeat. Use `Attribution.proposals`. Nothing wires organ scores into the Genome or the router yet.
