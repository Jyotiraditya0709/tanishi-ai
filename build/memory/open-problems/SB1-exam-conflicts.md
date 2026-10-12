# SB1 · exam conflicts (written by the implementer; the human decides, no test was edited)

**Status 2026-10-11: C1 RESOLVED.** The human decided the test keeps its assertions and changes only how the
executor signals "started": it now writes `second_started` in its own home (`$HOME`, inside the sandbox scratch
folder) and the parent polls `TMPDIR/tanishi-sandbox-*/home/second_started`. The test passes with RT-2 enforced.

## C1 · `tests/arena/test_redteam_ar1.py::test_partial_run_leaves_no_rows` cannot pass once RT-2 passes

The test's executor signals "the second attempt has started" by writing `tmp_path / "second_started"`, a file
outside its temp home. SB1's acceptance line 3 (and AR3 RT-2, whose xfail the SB1 tester removed) says exactly that
write must fail. With the executor stage confined, the write raises, the executor returns, the run finishes, and the
test fails on `proc.poll() is None` before it ever kills the run. Every other test in the suite passes (macOS and a
Linux container: 1 failed, 2231 passed).

No sandbox can satisfy both: any channel the executor can use to tell the outside world "I started" (a file, a socket)
is a channel RT-2 forbids.

What I would do instead (AR1's tester, not me, should change the test):

- Detect the second attempt from outside the sandbox instead of from inside it, e.g. poll for a second
  `tanishi-sandbox-*` scratch folder under the child's `TMPDIR` (set `TMPDIR` to a fresh dir in the child env), or
  for a process whose argv carries the runner's bootstrap; or
- have the first attempt's executor sleep and kill the run on a timer after the first attempt's row would exist
  (weaker: timing based).

## Assumptions A1-A8: agreed

- A6: the candidate gets a *copy* of the worktree; the real checkout is never mounted or reset. "Reset" = the copy is
  thrown away and re-made (`rollback()`), and the guarded folders hash the same before and after.
- A8: RT-2/RT-3 pass through AR1's `run()`: its executor stage runs in `Sandbox` (see decision 0025).
