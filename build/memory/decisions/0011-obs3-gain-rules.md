# 0011 · How OBS3 decides a gain is real

Date: 2026-10-09. Rules 1-6 by the OBS3 implementer, within the spec. Rules 7-12 by the human, in the OBS3 repair round.
This file was `0008-obs3-gain-rules.md`. The human renumbered it so nodes do not collide, and older run notes that say
"decision 0008" mean this file.

1. **Noise is the sample standard deviation (ddof 1) of one arm's per-seed scores.** It is not a standard error, so more
   seeds do not shrink the bar. That is deliberately strict.
2. **The noise bar is 2 x max(noise(baseline), noise(candidate))**, not pooled noise, so a noisy candidate cannot hide behind a quiet
   baseline. (Rule 7 adds the rest of the threshold.)
3. **Fewer than 3 seeds in either arm raises `ValueError`.** It never returns a verdict, so a caller cannot mistake a refusal for "not real".
   NaN or infinite scores also raise.
4. **One list element is one seed.** The ledger (`experiments.seed_scores`) averages over tasks per seed before `is_real_gain` sees the scores.
5. **Ledger rows** use the CS1 `experiments` table: `baseline`/`candidate` name the experiment, `task_set` holds the task,
   `meta.arm` says which arm ran. Do not add columns for this without a new migration.
6. **`ablate` removes one change at a time**: gain(c) = score(all) - score(all except c). `run_fn(version, applied_tuple)` must
   average its own seeds. Gains do not sum to the total when changes interact (see known-bugs/obs3-limits.md).

## Repair round (red-team breaks, 2026-10-09)

7. **A gain is real only if it is larger than the biggest of 2 x noise, `min_effect` (when given) and 0.01** (`MIN_REAL_GAIN`).
   Scores are on a 0 to 1 scale, so 0.01 is the smallest gain ever called real. "Larger than" is strict: a gain equal to
   the threshold is not real. `Verdict.threshold` is that maximum. This stops deterministic arms (noise 0) from keeping
   a +0.0001 change.
8. **One row per (experiment, arm, task, seed).** `record_run` raises `ValueError` on a second one. A rerun is recorded under a
   new arm label (for example `v2-rerun1`), so no score is ever overwritten or averaged away. The check is a SELECT inside
   the insert transaction, not a UNIQUE index, because this round adds no migration (see known-bugs/obs3-limits.md).
9. **Paired data only.** `seed_scores` keeps a seed only when both arms have rows for exactly the same set of tasks on it, and
   returns the other seeds in `.dropped` (the result still unpacks as `base, cand`). `is_real_gain` refuses two lists of different
   length. If the ledger somehow holds two rows for one run, `seed_scores` raises instead of averaging them.
10. **A seed is a plain `int`.** No `bool`, no `float` (even `2.0`), no string. Checked in both `interleave` and `record_run`.
11. **`interleave` refuses a `str` (or `bytes`) for `tasks` or `seeds`**, and compares arms with `str()`, because the ledger stores
    arms as text: `1` and `"1"` are the same arm and are refused.
12. **Scores are real numbers, not `bool`; `cost` is `None` or a finite number >= 0, not `bool`.** Anything else raises `ValueError`
    (in `is_real_gain`, `noise` and `record_run`). Every bad input raises `ValueError`, not `TypeError`, so callers catch one type.
    That is why `experiments.py` silences ruff's TRY004 in two places.

Kept as documented limits on purpose (no change): `ablate` gives interacting changes overlapping credit, and a real gain
under 2 x noise is rejected.
