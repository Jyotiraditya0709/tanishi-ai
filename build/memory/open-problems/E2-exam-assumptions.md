# E2 · what the spec leaves open (written by the tester, from the spec alone)

The E2 card names one file (`tanishi/observability/causal.py`) and three acceptance lines, but no function names.
`tests/observability/test_causal.py` pins the interface below. If the implementer disagrees with an item, say so in
`build/memory/open-problems/E2-exam-conflicts.md` and the human decides; do not edit the tests.
Builds on OBS3 (`attribution.is_real_gain`, decision 0011) and CS6 (`genome.record_version`).

- **A1 · `verify_gain(run_fn, baseline, candidate, seeds, *, min_effect=None) -> Verdict`.** `run_fn(version, seed) -> float`
  scores one version on one seed (int). It is called exactly once per (arm, seed), arms interleaved: the two calls for
  a seed are back to back, in a seeded random arm order, and the order of seeds is shuffled. Same inputs give the same
  call order (reproducible). The result has the OBS3 `Verdict` fields (`real`, `effect`, `noise`, `threshold`) and the
  same rules (decision 0011: >= 3 seeds, noise bar 2 x max sample sd, 0.01 floor, strict "larger than").
- **A2 · Bad input raises `ValueError`** (rule 12): fewer than 3 seeds, repeated seeds, non-`int` or `bool` seeds, a `str`
  for seeds, `run_fn` returning NaN, inf, bool, str or None, a bad `min_effect`, the same arm twice. An exception from
  `run_fn` propagates.
- **A3 · `attribute(changes, run_fn, seeds, *, min_effect=None) -> Attribution`.** `run_fn(applied: tuple[str, ...], seed)
  -> float` scores the system with only `applied` switched on. Arms run: `()` (nothing), the full tuple (in the
  caller's order), and the full tuple minus one change, for each change. Never two changes off at once beyond `()`.
  `.total` is the Verdict of nothing vs all. `.genes` maps each change to the Verdict of
  (all minus change) vs (all), paired by seed, so `effect` is the drop that change's removal causes.
- **A4 · `Attribution.as_dict()`** returns plain JSON (exact `dict`/`list`/`str`/finite `float`/`bool`) with
  `total` and `genes`, each gene holding `effect` and `real`. It is accepted by `genome.record_version(attribution=...)`.
- **A5 · `organ_scores(proposals) -> dict[str, float]`.** `proposals` is an iterable of dicts with `organ` (str),
  `gain` (float, from the attribution) and `real` (bool). An organ's score is the sum of the gains of its *real*
  proposals; an organ whose proposals are all unreal appears with 0.0. Order does not matter. Empty gives `{}`.
  A non-finite, bool or str gain, a blank organ or a missing key raises `ValueError`. (Sum vs mean is an assumption;
  the card says only "scored on the gain its proposals produce".)
- **A6 · Duplicate change names** raise `ValueError`. An empty `changes` list may raise `ValueError` or return no genes.

## Not covered, and why

- **Interacting changes.** Decision 0011 keeps overlapping credit as a documented limit; the exam checks the
  additive case only.
- **Ledger persistence.** The card does not say whether `causal` writes to the experiments table.
