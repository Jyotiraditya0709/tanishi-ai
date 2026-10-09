# 0014 · Where the North Star numbers come from (OBS2)

Date: 2026-10-09. Decided by: the OBS2 implementer, within the spec. The human should confirm items 2, 3 and 5
against the master plan (see open-problems/OBS2.md).

1. **Human Effort is stored as events.** `tanishi time start` and `stop` call `events.emit("effort_start" | "effort_stop", {},
   actor="human")`. No new table and no migration. Hours come from replaying those events in id order: a start while the
   timer runs and a stop while it is idle are skipped, so doubled verbs or racing terminals never count time twice.
   Only finished sessions count. A running one is reported apart (`running_since`, `running_hours`). The window clips a
   session that began before it.
2. **RCR = discoveries / (hours / 8) x 30**: discoveries per researcher-month, where a researcher-day is 8 hours and a
   month is 30 researcher-days. The 30 is inferred from the card's example (3 discoveries in 10 hours gives 72), not read
   from the master plan.
3. **The funnel is the OBS3 ledger.** "experiments" means the (baseline, candidate) pairs with any ledger row in the window,
   "judged" means those with at least `MIN_SEEDS` paired seeds, and "discoveries" means the judged pairs where
   `is_real_gain(...).real`. A ledger that OBS3 refuses to read (duplicate rows) counts under "unreadable" and never as a
   discovery.
4. **No hours but some discoveries gives RCR `None`, not 0 or inf.** No hours and no discoveries gives 0.
5. **CEI's B is `COUNT(capabilities WHERE state = 'mastered')`.** Whoever writes capability state (the Reality Engine
   when it exists) must use the exact string `mastered` for Reality-verified mastery. T, H, N, A and P have no source yet.
   CEI is 0 when a measured factor is 0, `None` when a factor is unmeasured, and the product otherwise.
6. **CAR, AR and IA show `None`** with "definition not in the repo" until the human writes their definitions down.
7. **Shape.** `compute()` returns exactly six top-level keys: `CEI`, `RCR`, `CAR`, `AR`, `IA` and `human_effort`. Each has `value`,
   `inputs`, `unit` and `explanation`. `compute()` and `effort.report()` never create, migrate or write the db.
   A missing or unmigrated db reads as empty.
