# AR1 repair round · tests that still fail after the human's fixes (2026-10-09)

The implementer did not edit these tests or remove their markers. Both stay `xfail(strict=True)` and show as `xfailed`.
The human or the red-team agent decides. The target "0 xfailed" is not reachable without changing them.

## R1 · `test_candidate_that_does_not_exist_cannot_score_without_an_executor` (RT-2) contradicts the exam

The proof calls `run(tasks, "refs/heads/does-not-exist-anywhere", seeds=1)` with no executor and asserts
`res.results[0].score == 0.0`. The human chose the other option the red-team offered: no executor → `ValueError`, no rows.
The exam test `test_run_without_an_executor_is_refused_and_records_nothing` (test_runner.py) requires exactly that.
The proof now ends in that `ValueError`, which strict xfail counts as an expected failure.

The break itself is fixed: a candidate can no longer score without an executor. Suggested change to the proof:
`with pytest.raises(ValueError): run(...)` and assert no rows, then remove the marker. Its reason string is stale too.

## R2 · `test_partial_run_is_marked_incomplete` (RT-8) no longer reaches a partial run

The proof uses RT-6 (`timeout_s=1e300`) to kill `run()` halfway. RT-6 is fixed (timeout capped at 3600, any attempt
error scores 0), so `run()` now finishes and writes 2 complete rows. The proof then requires every row of that
**complete** run to have `complete is False` or an `incomplete` key. Under the human's RT-8 decision (one transaction
at the end, shared `run_id`) a partial run leaves **no** rows, so there is nothing to mark.

I could make it pass by adding `"incomplete": false` to every row's meta (the proof only checks that the key is there),
but that would make a proof pass without testing what it claims to test, so I did not.
The behaviour is proven instead by `tests/arena/test_runner_executor.py::test_a_run_that_dies_partway_leaves_no_rows`
(a `KeyboardInterrupt` on the second attempt leaves 0 rows).

Suggested change to the proof: kill the run on purpose (monkeypatch `tanishi.arena.runner._isolated_attempt` to raise
`KeyboardInterrupt` on the second call) and assert the experiments table is empty, then remove the marker.

## Stale notes (not mine to edit)

- `open-problems/AR1-exam-assumptions.md` still says `run()` works without an executor (the tester noted this).
- `tests/arena/arena_helpers.py` docstring cites "decision 0008"; the decision is now `decisions/0012-arena-attempt-contract.md`.
