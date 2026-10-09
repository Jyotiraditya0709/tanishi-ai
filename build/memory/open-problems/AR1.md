# AR1 · what the spec does not say, and what was built instead of guessing

## How a candidate produces output

The spec says `candidate = git ref or config id` but not how either one turns into an answer. AR1 does **not** check out
git refs or look up config ids. `run(..., executor=...)` takes an executor, a picklable module-level function
`executor(brief, candidate, seed) -> Attempt | str`, which owns that step. Since the repair round the executor is
required: `run()` without one raises `ValueError` (human decision, RT-2), because otherwise the candidate is never used.

What I would do next (a follow-up node, not AR1): a `git_ref` executor that makes a `git worktree add` of the ref in the
attempt's temp home and calls the candidate's entry point there, and a registry mapping config ids to executors. That
needs an agreed entry point (`TanishiBrain.think()` needs a model and tools; tool tasks need a tool-capable model) and
must go through `ToolRegistry.execute()`, so it should be specified, not improvised here. A model-calling executor will
need its API key named in `env_passthrough`, because attempts no longer inherit the caller's environment.

"A crashed or empty run scores 0" is enforced for whatever executor is passed: an exception, a hard exit, a timeout,
a non-`Attempt` return or blank output all score 0 without calling the verifier.

## `setup`

The spec names the field but not its meaning. AR1 runs it as a shell script (`sh -c`) in the executor's process, with
cwd = the attempt's temp HOME and the attempt's minimal environment, before the executor. A non-zero exit or a timeout
scores 0 as `infra`. The candidate shares that process, so `setup` must not hold anything the candidate should not see.
If the plan meant something else (files to copy, a Python hook), change it in one place: `runner._executor_stage`.

The full contract is `decisions/0012-arena-attempt-contract.md`.
