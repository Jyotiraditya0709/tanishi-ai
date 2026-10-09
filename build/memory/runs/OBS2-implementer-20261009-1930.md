# OBS2 · implementer · 2026-10-09 19:30

1. **What did we learn?** The event log is a good store for a timer. Sessions come from replaying start and stop events,
   which survives across processes and makes doubled verbs harmless without any lock. RCR's example pins a 30-day month:
   3 / (10 / 8) x 30 = 72. The OBS3 ledger already gives a real funnel (run, judged, real gain) with no new table.
2. **What failed, and why?** My first `_read_events` handled "no such table" only on its own connection. `compute()` passes
   its connection in, so an unmigrated db crashed. My test for that case caught it. I could not run the CLI by hand
   (permissions), so I relied on the exam's subprocess tests.
3. **Which assumption was wrong?** That "as defined in the master plan" meant the definitions were in the repo. Only B (from
   the zero-explanation text) and RCR's shape could be pinned. T, H, N, A, P, CAR, AR and IA are `None` with reasons.
4. **What should the next agent know?** Read decision 0014 and open-problems/OBS2.md. To wire a factor, replace its `None` in
   `_cei_entry` or `_undefined_entry` with a read from its Core State table, and keep `compute()` read-only. Write
   capability mastery as `state = 'mastered'`. `tanishi time` is routed in `cli.main()` before dotenv and the chat. The
   OBS2 tests (exam plus sources) run in about 12 s because of real sleeps.
