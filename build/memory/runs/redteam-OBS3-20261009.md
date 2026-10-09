# Red team · OBS3 · 2026-10-09

Proofs: `tests/observability/test_redteam_obs3.py`. Breaks are `xfail(strict=True)`; the fixer removes the marker when fixed.

## Breaks (most severe first)

1. **HIGH · zero-noise arms accept any gain.** `is_real_gain([.5]*3, [.5001]*3)` is real (noise 0, threshold 0). If a seed is ignored
   or a scorer is deterministic, noise is 0 and the 2x-noise rule vanishes: the 0.001-keep failure returns. Fix: require
   `threshold = max(2*noise, min_effect, floor)` with a non-zero default floor (e.g. 1e-3 or a caller-supplied resolution), and/or flag zero-variance arms as "unmeasured noise".
2. **HIGH · repeated rows for one seed are silently averaged** (`seed_scores`). Retries or reruns collapse to one point and the earlier scores
   vanish; a rerun of a bad seed can erase it (survivorship). Fix: reject a second row for the same (arm, task, seed) in `record_run`, or add a UNIQUE check.
3. **HIGH · unpaired / partial data.** A seed present in only one arm gives lists of different length (3 vs 2) that `is_real_gain` accepts;
   a seed missing a task for one arm gives a biased per-seed mean (candidate "wins" by dropping its hard task). Fix: `seed_scores` returns only
   seeds where both arms have the same task set, and raises or reports the dropped ones.
4. **MED · fractional seeds truncate** (`int(1.9)` → 1), so distinct seeds collide and get averaged. **Bool seed** accepted as 1. Fix: require `isinstance(seed, int) and not bool`.
5. **MED · `interleave("ab…")` with a string for `tasks`/`seeds`** splits it into characters. Arms `1` and `"1"` pass the `baseline == candidate` check
   but `record_run` stringifies them to the same arm. Fix: reject `str` for tasks/seeds; compare `str(baseline) == str(candidate)`.
6. **LOW · `cost=nan`** is stored as NULL by sqlite with no error; `is_real_gain` accepts bool scores. Fix: check `cost` is finite; reject `bool`.

## Tried, held
- Null false positive rate (n=3 per arm, 20000 trials, equal means): ≤ 5%. Passes (still the weakest part: 3 samples estimate noise poorly; see decision 0008).
- Bad meta (non-JSON) leaves no row. `ablate` NaN raises. Ordering/determinism of `interleave`.
- Not reachable here: hash chain (the `experiments` table is not chained), vault/frontier leakage (nothing in OBS3 touches them), secrets (no logging; `meta` is caller-supplied and unchecked, a note for callers).

## Not fixed here
Per the red-team role I only added tests and this note. The implementer owns the fixes.
