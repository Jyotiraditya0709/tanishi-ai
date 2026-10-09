# AR1 · exam conflicts (written by the implementer)

The implementer did not edit the exam. The human or the tester decides.

## C1 · `test_unresolvable_verifier_scores_zero` cannot run (exam bug, not a spec conflict)

`tests/arena/test_runner.py:125` calls `make_task("full", verifier="no_such_module_anywhere.check")`. `make_task`'s first
positional parameter is `verifier`, so Python raises `TypeError: got multiple values for argument 'verifier'` inside the
test, before the runner is called. No implementation can make it pass.

Suggested fix for the tester: `make_task("full")` and then `dataclasses.replace(t, verifier="no_such_module_anywhere.check")`,
or build the `Task` directly. The behaviour it means to check (an unimportable verifier scores 0 on every seed and the
rows are still written) passes in `tests/arena/test_runner_executor.py::test_unresolvable_verifier_scores_zero`.

## Choices made where the exam left them free (A6)

- An out-of-range score (7.0, -3.0, inf) scores **0**, not a clamped value. Clamping 7.0 to 1.0 would give full credit to a
  broken verifier, which is the legacy failure mode ("failures scored positively").
