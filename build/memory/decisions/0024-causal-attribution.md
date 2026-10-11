# 0024 · Causal Attribution, full (E2)

Date: 2026-10-12. Decided by: the E2 implementer, matching the E2 exam (`open-problems/E2-exam-assumptions.md`, A1-A6).
Renumber if another branch claims 0024.

1. **`causal.verify_gain` and `causal.attribute` call `run_fn` themselves.** Callers pass a scoring function, not score
   lists, so nobody can hand-pick runs. One call per (arm, seed). Seeds are shuffled and each seed's arms run back to
   back in a shuffled order; the shuffle is seeded with sha256 of the inputs, so it is reproducible across processes.
2. **The statistics are OBS3's, unchanged** (`attribution.is_real_gain`, decision 0011). E2 adds no new thresholds.
3. **Each gene gets its own `Verdict`**: (all minus gene) vs (all), paired by seed. The OBS3 `ablate` (one number per
   gene, no verdict) stays for old callers; new code uses `attribute`.
4. **An empty change list raises `ValueError`** (there is nothing to compare). Change names are non-blank `str`.
5. **`organ_scores` is the sum (not mean) of `gain` over an organ's proposals with `real is True`**, computed with
   `math.fsum` so order cannot change it. `real` must be a real `bool`; an organ name must equal its stripped form.
6. **`Attribution.as_dict()` is the value for `genome.record_version(attribution=...)`**: `{"total": verdict, "genes":
   {name: verdict}}`, each verdict holding `real, effect, noise, threshold, n_baseline, n_candidate, reason`.
7. **`causal` does not write the experiments ledger.** The card does not ask for it; R2 (Experiment Engine) can record
   runs by wrapping `run_fn`.
