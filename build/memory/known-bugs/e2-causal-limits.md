# E2 Causal Attribution: known limits

- **Interacting changes still get overlapping credit.** Ablation is one-at-a-time, so if a and b only help together
  each gets the full joint gain and gene effects sum to more than the total (as OBS3, decision 0011). Organ scores
  then double-count it. Fix later with pairwise ablation or Shapley values while the change count stays small.
- **Cost is (n + 2) x seeds runs** for n changes. Fine for a handful of genes per merge; too slow for dozens.
- **Small real genes are missed.** A gene needs ~3-4 x per-run noise to be found reliably (benchmarks/E2-causal-eval.md).
  The bar is strict on purpose (decision 0011, rule 1).
- **Gene verdicts are not corrected for multiple comparisons.** With many genes the chance that some passenger is
  credited grows (~0.5% each at 6 seeds).
- **`organ_scores` trusts its input.** It does not check that `gain`/`real` really came from an `Attribution`; a caller
  can hand it any numbers. Nothing wires organ scores into the Genome or the router yet.
