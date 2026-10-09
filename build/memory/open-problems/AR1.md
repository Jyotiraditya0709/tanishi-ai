# AR1 · what the spec does not say, and what was built instead of guessing

## How a candidate produces output

The spec says `candidate = git ref or config id` but not how either one turns into an answer. AR1 does **not** check out
git refs or look up config ids. `run(..., executor=None)` takes an executor, a picklable module-level function
`executor(task, candidate, seed) -> Attempt | str`, which owns that step. With no executor the verifier alone is the attempt
(it receives `output=None` and checks the candidate itself, as a code task would).

What I would do next (a follow-up node, not AR1): a `git_ref` executor that makes a `git worktree add` of the ref in the
attempt's temp home and calls the candidate's entry point there, and a registry mapping config ids to executors. That
needs an agreed entry point (`TanishiBrain.think()` needs a model and tools; tool tasks need a tool-capable model) and
must go through `ToolRegistry.execute()`, so it should be specified, not improvised here.

Until then, "a crashed or empty run scores 0" is enforced for whatever executor is passed: an exception, a hard exit,
a timeout, a non-`Attempt` return or blank output all score 0 without calling the verifier.

## `setup`

The spec names the field but not its meaning. AR1 runs it as a shell script (`sh -c`) with cwd = the attempt's temp HOME and
the attempt's isolated environment, before the executor. A non-zero exit or a timeout scores 0. If the plan meant something
else (files to copy, a Python hook), change it in one place: `runner._attempt`.
