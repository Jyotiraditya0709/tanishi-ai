# Red team · OBS2 · 2026-10-09

Tests: `tests/observability/test_redteam_obs2.py` (9 fail = 9 proven breaks in 4 root causes; 3 pass = attacks that held).
Nothing in `tanishi/` was changed; fixes below are the smallest ones.

## Breaks

| # | Sev | Break | Smallest fix |
|---|-----|-------|--------------|
| 1 | Med | **Tiny timed sessions inflate RCR.** One start/stop moments apart plus one discovery gives RCR ~4.7e8. A number that looks like a breakthrough but is a timer artefact. | Hours below a floor (e.g. 0.1 h) give RCR `None` with "not enough Human Effort timed to rate". |
| 2 | Med | **Huge `window_days` (1e9, 1e12, 10**400) raises `OverflowError`** in `compute()` and `effort.hours()`/`report()`, not `ValueError`. `tanishi time report 1e9` prints a traceback (the CLI catches only ValueError/OSError/sqlite3.Error). | Cap `window_days` (e.g. <= 36500) in `_window` and `_check_window`. |
| 3 | Low | **`cei` / `rcr` raise `OverflowError` for huge ints** (`math.isfinite(10**400)`), breaking decision 0011 rule 12 (bad numbers raise ValueError). | In `_number`, catch OverflowError and raise ValueError. |
| 4 | Low | **`cei` and `rcr` return `inf`** when a product or quotient overflows (1e200 x 1e200; 1e308 / 1e-300). inf is not strict JSON and not a measurement. | After computing, raise ValueError if the result is not finite. |

## Attacks that held

Other actors' `effort_start/stop` events do not count. `compute()` and `report()` leave an existing db byte-identical.
The result is strict JSON. Doubled start/stop replay safely. The hash chain is written by `events.emit` under
BEGIN IMMEDIATE, so racing terminals cannot fork it.

## Not fixed, and no test here

- **Forged hours / `state='mastered'`**: any code can emit actor `human` events or set `capabilities.state` (already in `obs2-limits.md`).
- **No idle cap**: a forgotten timer stopped days later books all of it (already in `obs2-limits.md`; `emit` stamps its own clock, so I could not back-date it in a test).
- **Multiple comparisons**: each (baseline, candidate) pair with a ledger row is an experiment, and each judged pair that clears `is_real_gain` is a "discovery". One candidate against many baselines, or many near-identical candidates, raises the discovery count with no correction, and RCR rewards it.
- **Window mismatch**: the funnel picks pairs by ledger ts in the window, but `seed_scores` reads every row of the pair, old or new. CEI's B ignores the window.
- `stop()` returns `sessions()[-1]` after emitting; if another terminal starts a session in between, it reports 0.0 hours for the one it closed (cosmetic).

## After-task answers

1. Learned: the numeric helpers validate type and sign but not magnitude, and the window is unbounded.
2. Failed: my idle-cap test passed vacuously (cannot back-date events), so I removed it rather than ship a green test that proves nothing.
3. Wrong assumption: that "finite and >= 0" inputs give finite outputs, and that a timed-hours denominator is a trustworthy scale.
4. Next agent: fix 1-4 above, then add a floor on hours and a multiple-comparisons rule to the funnel before RCR is shown as a headline number.
