# AR1 · what the spec leaves open (written by the tester, from the spec alone)

**Status:** `run()` now requires an executor (decision 0012); notes below that assume it works without one are superseded.

The AR1 card names `Task`, `run(task_set, candidate, seeds=3) -> RunResult`, the YAML fields and four acceptance lines.
It does not name everything an exam needs. The exam in `tests/arena/` assumes only the items below. If the implementer
disagrees with one, say so in `build/memory/open-problems/AR1-exam-conflicts.md` and the human decides; do not edit the tests.

- **A1 · Names.** `tanishi.arena.task.Task` (constructor takes the eight spec fields as keywords; `setup` optional),
  `tanishi.arena.task.load_task(path) -> Task` (one YAML file), `tanishi.arena.runner.run`. `load_task` is not named in the
  spec; it is the smallest loader that makes "task file (YAML)" testable.
- **A2 · Verifier path.** `verifier` is a dotted path `package.module.function`. The exam's verifiers accept `*args, **kwargs`
  because the spec does not say what a verifier receives. They return `(score, reason)`.
- **A3 · Errors.** An invalid task file raises `ValueError` or `TypeError` (wrap YAML errors). Non-positive `seeds` or an empty
  `candidate` raises `ValueError` and records nothing. An empty task set records nothing (raising is also accepted).
- **A4 · Where rows go.** Rows are written to the caller's own Core State db (`TANISHI_CORE_STATE_DB` at call time), not to
  the per-task fresh db. The per-task db and `TANISHI_HOME` exist only while that task runs, and the caller's environment is restored.
- **A5 · Row shape.** One `experiments` row per (task, seed); `candidate` is the candidate string; seeds are distinct per task;
  the task id appears in `task_set` or `meta`; `score` is in [0, 1] and never NaN; `cost` is a finite number >= 0.
- **A6 · Failure scoring.** A verifier that raises, times out (`timeout_s`), cannot be imported, or returns NaN or a non-tuple
  scores exactly 0.0, and the row is still written. Out-of-range scores (7.0, -3.0, inf) must land in [0, 1]; whether by clamping or by 0 is free.

## Not covered, and why

- **Candidate crash and empty output.** The spec does not say how a candidate (git ref or config id) produces output, so the
  exam cannot make one crash or return nothing. Only verifier-side failures are tested. A follow-up exam is needed once the
  executor interface exists (suggest: `run(..., executor=...)` or a registry of config ids). The acceptance line "a crashed or
  empty run scores 0" is therefore only partly examined.
- **`setup`.** The spec does not say what `setup` is (shell, Python path, files). Only "optional" is tested.
- **`RunResult` fields.** Only "not None" is tested.
- **Real `git ref` checkout.** Not tested here; it needs the executor.
