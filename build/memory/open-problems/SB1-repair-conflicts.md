# SB1 · repair round conflicts (2026-10-12, written by the implementer; the human decides, no test was edited)

Repair card: human decisions 1-8 after `runs/redteam-SB1-20261012.md`. 8 of the 9 red-team proofs pass with their
xfail marks removed. These are the places where the card, the tests and the code do not fit together.

## R1 · `test_rt5_candidate_reads_the_environment_of_other_processes_of_the_user` cannot pass as written

**RESOLVED 2026-10-12 by the human's decision: the test changed, not the code** (run note `runs/SB1-rt5-*.md`).
rt5 is now `test_rt5_candidate_cannot_read_the_arguments_or_environment_of_another_process_of_the_user`: the token
lives only in a helper process's environment, the sandbox is `Sandbox(trusted=False)` with the default backend
(Docker on macOS; skips without it), the candidate scans /proc, sysctl KERN_PROCARGS2 and `ps` listings, no xfail.
`test_rt5b_candidate_can_read_its_own_arguments_which_is_not_a_sandbox_break` keeps the old attack as a plain passing
test (a process can always read its own argv). The text below is the original analysis, kept for the record.

The proof stays `xfail(strict=True)`. Two reasons, either one enough:

1. **It builds a Sandbox that the exam requires to be sandbox-exec.** `_sb()` is `Sandbox(prefer_docker=False, ...)`
   with no `trusted` flag. Decision 2 says candidate code (`trusted=False`) must never get sandbox-exec, and the code
   does that (fail closed without Docker). But the default cannot be `trusted=False`: the SB1 exam's
   `test_works_without_docker_on_the_path` (`Sandbox()` with no docker on PATH) and
   `test_prefer_docker_false_never_uses_docker` (`Sandbox(prefer_docker=False)`, `backend != "docker"`) require an
   unflagged Sandbox to run under sandbox-exec on macOS. The exam wins, so unset `trusted` keeps sandbox-exec, and
   rt5's sandbox is a sandbox-exec one, where `KERN_PROCARGS2` is open.
2. **Its candidate finds the token in its own argv.** The program is passed with `-c` and contains the token as a
   literal (`want = '<token>'.encode()`). Its scan includes its own pid, whose procargs hold the whole program. Checked
   on this machine with no other process holding the token: `SELF` printed, `FOUND True`. So it would fail under a
   separate uid, Docker (if it could run there; `libSystem.B.dylib` does not exist in a Linux container) or any fix.

Rechecked by the second repair agent (same day): under `(allow default)` plus each of
`(deny sysctl-read (sysctl-name "kern.procargs2"))`, `(deny sysctl-read (sysctl-name-prefix "kern.procargs"))`,
`(deny process-info* (target others))` and `(deny process-info-pidinfo (target others))`, a child still read both its
parent's and its own procargs2 through the numeric MIB. No profile rule closes it, and reason 2 holds on any backend.

The candidate path is covered instead by `tests/sandbox/test_sandbox_impl.py::
test_untrusted_candidate_cannot_find_another_processes_environment` (token sent reversed; Docker on macOS: passes)
and `test_untrusted_code_is_refused_on_macos_without_docker`.

What I would do: the red team (or the human) changes rt5 to (a) build its sandbox with `trusted=False` and skip, or
expect `RuntimeError`, when Docker is absent, (b) not put the token in the candidate's argv (send it on stdin or
reversed), and (c) scan `/proc/*/environ` as well as the macOS sysctl, so it runs in the container. Then remove the mark.

## R2 · AR1's executor stage runs on sandbox-exec (`trusted=True`), not on an isolating backend

Decision 2 lists AR1 among the callers that never pass `trusted=True` *for candidate code*. AR1's executor stage runs
the caller's executor function and the task's setup; neither is model-written today, and the model's output is only
executed by verifiers (`run_python_tests`, now `trusted=False`). I tried `trusted=False` for the stage (Docker on
macOS): every attempt died at start, because the child sets `sys.path` to the host's paths (macOS stdlib,
`lib-dynload` and site-packages with darwin wheels) and imports `tanishi.arena.runner` with the container's Linux
interpreter. Real executors (an LLM SDK with compiled wheels such as pydantic_core) cannot run there either, and the
pickle protocol needs the same code on both sides. So the stage stays `trusted=True`, written explicitly with a
comment in `runner._isolated_attempt`.

Consequence: an executor can still read the environment of the user's other processes on macOS (it already holds
whatever `env_passthrough` gives it). Before the loop (M1) runs a candidate git ref inside the executor stage, a
human must choose one of:
- a Linux image that carries the repo's dependencies, and AR1 sending only repo paths (not the host stdlib) in its
  bootstrap, so the stage runs in Docker on macOS; or
- a separate OS user for the stage (sudo setup once), as the card's own wording allows; or
- the loop runs candidate code only on Linux (Landlock), and macOS runs only system-written executors.

## R3 · scope notes (not conflicts)

- Linux was not run in this round (no host). The seccomp `socket(AF_UNIX)` rule, the recursive Landlock carving
  and the RLIMIT_NPROC count by `/proc/*/status` Threads are untested on a kernel; the BPF program is checked by a
  small interpreter in `test_sandbox_impl.py` for both machines. If Linux CI turns red at sandbox start (exit 126),
  look at `_launch._seccomp_program` first.
- The red team's suggestion to copy only `git ls-files` was not taken (not on the card); the copy filter is by name.
