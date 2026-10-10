# SUB4 · repair round: where the fix and a test conflicted

## B1: the read-through alone cannot pass `test_b1_prediction_resolved_through_cs5_leaves_the_table_empty`

The card's decided fix for B1 and B1b is a read-through in `get_capability()`, "so ToolRegistry.execute() and any CS5
caller are covered without a hook". The B1 proof does `P.predict()`, `P.resolve()`, then reads
`capabilities.calibration_error` **by SQL**. No SUB4 function runs in between, so no read-through can fire.

The test wins. What I did:
- Built the read-through as decided. It fixes B1b, and it is what keeps a router read right when the resolve happened
  in a process that never imported SUB4.
- Added the smallest change that makes B1 pass: `predictions.on_resolve(hook)` in CS5, run after `resolve()` commits
  and after its event, failing open (exception type logged only). SUB4 registers one hook at import. The card allows a
  CS5 change only when a fix is impossible otherwise. The only alternative I found that leaves `predictions.py` alone
  is a SQLite trigger in a new migration. It would duplicate the ECE, the tag rules and the low-evidence prior in SQL,
  and migration numbers are contended across open branches. Every CS5 test still passes.

What I would ask the human: confirm the hook may stay. If not, B1's proof would need to call `get_capability()`
before its SQL read, and that is an assertion change only the red team or the human can make.
