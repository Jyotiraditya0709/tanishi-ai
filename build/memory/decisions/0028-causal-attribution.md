# 0028 · Causal Attribution, full (E2)

Date: 2026-10-12. Decided by: the E2 implementer, matching the E2 exam (`open-problems/E2-exam-assumptions.md`, A1-A6).
Rules 8-15 by the human, in the E2 repair round. (This file was first written as 0024.)

1. **`causal.verify_gain` and `causal.attribute` call `run_fn` themselves.** Callers pass a scoring function, not score
   lists, so nobody can hand-pick runs. One call per (arm, seed). Seeds are shuffled and each seed's arms run back to
   back in a shuffled order; the shuffle is seeded with sha256 of the inputs, so it is reproducible across processes.
2. **The statistics are OBS3's, unchanged** (`attribution.is_real_gain`, decision 0011). E2 adds no new thresholds, except the 5-seed minimum (rule 8).
3. **Each gene gets its own `Verdict`**: (all minus gene) vs (all), paired by seed. The OBS3 `ablate` (one number per
   gene, no verdict) stays for old callers; new code uses `attribute`.
4. **An empty change list raises `ValueError`** (there is nothing to compare). Change names are non-blank `str`.
5. **`organ_scores` is the sum (not mean) of `gain` over an organ's proposals with `real is True`**, computed with
   `math.fsum` so order cannot change it. `real` must be a real `bool`; an organ name must equal its stripped form.
6. **`Attribution.as_dict()` is the value for `genome.record_version(attribution=...)`**: `{"total": verdict, "genes":
   {name: verdict}}`, each verdict holding `real, effect, noise, threshold, n_baseline, n_candidate, reason`.
7. **`causal` does not write the experiments ledger.** The card does not ask for it; R2 (Experiment Engine) can record
   runs by wrapping `run_fn`.

## Repair round (red-team breaks, 2026-10-12)

Conflicts between these rules and the tests: `open-problems/E2-repair-conflicts.md`.

8. **E2 needs at least 5 seeds** (`causal.MIN_SEEDS`); fewer raises `ValueError`. OBS3's `is_real_gain` keeps its own
   3-seed rule (decision 0011, rule 3); E2 is stricter on purpose. The verdict's `n_baseline`/`n_candidate` record the
   seeds used. Rate at which a change that does nothing is accepted (equal means, sigma 0.1, red team, 6000 trials):

   | seeds | 3 | 4 | 5 | 6 |
   |---|---|---|---|---|
   | null false-accept | 2.0% | 0.7% | 0.37% | 0.07% |

   (E2's own eval at 5 seeds, 2000 trials: 0.55%.) The caller still owns the seed list: R2 must fix it before it sees
   a score and record how many seed sets it tried.
9. **Every bad input raises `ValueError`**, never `TypeError` or `OverflowError`: non-iterable `seeds`/`changes`/
   proposals, ints too big for a float, and scores whose mean, noise or sum overflows a float. Scores, `min_effect`
   and gains must be finite floats.
10. **Arms are kept apart by index, not value.** Unhashable versions work; `1`, `1.0` and `True` are separate arms (they
    print differently, so rule 11 of decision 0011 does not refuse them).
11. **`Verdict.effect`, `noise` and `threshold` are plain floats**, so a verdict is JSON-safe even with a `Fraction`
    `min_effect`.
12. **`Attribution.genes` is a plain dict**, so `asdict`, pickle and `deepcopy` work.
13. **`Attribution.unattributed`** = the real total gain (0 if the total is not real) minus `credited`, the summed
    effects of the real genes. When it is larger than `total.threshold`, `Attribution.complete` is False. `as_dict()`
    carries `credited`, `unattributed` and `complete`. Redundant genes (either alone gives the gain) are the usual cause.
14. **Organ scores count harm.** A real negative gain lowers the organ's score; scores can be negative. A proposal may
    carry a `gene` id; the same id twice in one call is refused. `Attribution.proposals(organ_of)` builds proposals
    with ids and with `real` True when the gene's harm is real (removing it is a real gain by the same OBS3 rules).
    The id is optional, not required, because the exam's proposals carry none.
15. **OBS3's 0.01 floor is compared with a 1e-12 tolerance** (`attribution.FLOOR_TOLERANCE`, the one OBS3 change this
    round): a gain within 1e-12 of 0.01 is not real, at every score level. Before, a true +0.01 was called real at
    about 60% of score levels because of float error. OBS3's tests pass unchanged.
