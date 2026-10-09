# 0008 · How OBS3 decides a gain is real

Date: 2026-10-09. Decided by: the OBS3 implementer, within the spec.

1. **Noise is the sample standard deviation (ddof 1) of one arm's per-seed scores.** It is not a standard error, so more
   seeds do not shrink the bar. That is deliberately strict.
2. **The bar is 2 x max(noise(baseline), noise(candidate))**, not pooled noise, so a noisy candidate cannot hide behind a quiet
   baseline. A gain must also be > 0 and >= `min_effect` when one is given.
3. **Fewer than 3 seeds in either arm raises `ValueError`.** It never returns a verdict, so a caller cannot mistake a refusal for "not real".
   NaN or infinite scores also raise.
4. **One list element is one seed.** The ledger (`experiments.seed_scores`) averages over tasks per seed before `is_real_gain` sees the scores.
5. **Ledger rows** use the CS1 `experiments` table: `baseline`/`candidate` name the experiment, `task_set` holds the task,
   `meta.arm` says which arm ran. Do not add columns for this without a new migration.
6. **`ablate` removes one change at a time**: gain(c) = score(all) - score(all except c). `run_fn(version, applied_tuple)` must
   average its own seeds. Gains do not sum to the total when changes interact (see known-bugs/obs3-limits.md).
