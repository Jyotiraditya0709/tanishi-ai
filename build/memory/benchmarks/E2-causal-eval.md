# E2 · Causal Attribution evaluation (2026-10-12)

Source: `tests/observability/test_causal_eval.py` (run with `-s` to print). Synthetic systems: 2 real genes with
equal effect, 2 passenger genes (effect 0), independent Gaussian noise sigma on every run, 6 seeds, OBS3 rules
(2 x noise, 0.01 floor). Per-gene verdicts from `causal.attribute`.

| effect / sigma | passenger false-accept | real-gene recall |
|---|---|---|
| 1 | 0.005 | 0.06 |
| 2 | 0.005 | 0.39 |
| 3 | 0.005 | 0.84 |
| 4 | 0.005 | 0.985 |
| 6 | 0.005 | 1.00 |

(sigma = 0.05, 100 systems per row. sigma 0.02 and 0.1 at 200 systems give the same false-accept, 0.005.)

Autoresearch v0's failure (+0.001 kept against ~0.1 noise): `verify_gain` accepts it in 0.5% of 200 trials.

Reading: credit to a passenger is rare (~1 in 200). The price is recall: a gene needs about 3 to 4 x the per-run
noise before it is reliably found, because the noise bar is per-run sd, not a standard error (decision 0011, rule 1).
More seeds do not lower that bar; they only steady it.
