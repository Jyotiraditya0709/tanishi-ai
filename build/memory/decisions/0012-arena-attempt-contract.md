# 0012 · The Arena attempt contract (AR1)

Date: 2026-10-09. First written by the AR1 implementer as decision 0008 where the spec was silent; renumbered 0012 by the
human (so nodes do not collide) and revised in the AR1 repair round after the red-team report
(`runs/redteam-AR1-20261009.md`). Points marked **(human)** are the human's decisions from that round. Later Arena
nodes (AR2+, AR3, AR4, OBS3, M1) build on it.

1. **Executor is required (human, RT-2).** `run(task_set, candidate, seeds=3, *, executor, env_passthrough=())`.
   Without an executor the candidate is never used, so `run()` raises `ValueError` and writes no rows.
   `executor(brief, candidate, seed) -> Attempt(output, cost) | str` is a picklable module-level function.
2. **Executor and verifier are separate processes (human, RT-1).** Per (task, seed):
   - The executor process runs `setup`, then the executor. It is given a `TaskBrief` (id, family, tier, prompt,
     timeout_s, tags): no `verifier` path and no `Task` object, so neither the path nor the verifier's data is in its memory.
   - Its whole process group is killed. Then a fresh verifier process runs `verifier(task, output) -> (score, reason)`
     in the same temp home. It gets only the task and the output, and may inspect files the attempt left in `$HOME`.
   - Children report to the runner as JSON lines on a private pipe, never as pickle, so nothing a candidate writes
     there can run code in the runner. Every failure reason is the runner's own text, not the child's.
   - Children start with `python -I`, so a `.pth` file the candidate plants in the temp home's user site cannot run in
     the verifier process.
3. **Never positive on failure.** A raise (even `SystemExit`), import failure, timeout, NaN, inf, a score outside [0, 1], a
   bool or a wrong shape scores **0** (not clamped). A crashed, hard-exited, timed-out or blank-output executor scores 0
   and the verifier is not called.
4. **Reasons never carry the expected answer (human, RT-3).** Reasons are stored and read back by failure analysis.
   `exact` says `does not match`, `number` says `wrong number` / `number matches`. Task verifiers must follow the same rule.
5. **Isolation (human, RT-5 and the env item).** Each attempt has a new temp dir as `HOME`, `$HOME/.tanishi` as
   `TANISHI_HOME`, a fresh migrated `core_state.db` there, `$HOME/tmp` as `TMPDIR`, and cwd = `HOME`. The environment is
   built, not inherited: `PATH`, `LANG`, those four, and any variable the caller names in `env_passthrough` (names only;
   the four runner-set names are refused). The caller's `os.environ` is never modified. The temp home is removed afterwards.
6. **Timeout (human, RT-6).** `Task` caps `timeout_s` at 3600 s (larger values become 3600; it does not refuse them, so a
   task set written with a huge timeout still runs). The executor stage (setup + executor) and the verifier stage each get
   `timeout_s`, counted from the moment the process reports it has started (Python startup is not charged). This replaces
   0008's single budget for both stages, which would have let a slow candidate eat the verifier's time. Any exception
   while running an attempt (an `OverflowError` in a timer, a spawn failure) scores that attempt 0 as `infra`; the run goes on.
7. **Rows (human, RT-7 and RT-8).** One `experiments` row per (task, seed) in the caller's Core State db: `candidate`,
   `seed` (0..seeds-1), `score`, `cost` (finite, >= 0; garbage becomes 0), `baseline` NULL,
   `task_set` = `tasks:` + sha256 of the canonical JSON of every field of every task, sorted by id, first 16 hex (RT-4),
   `meta` = JSON `{run_id, task_id, family, tier, reason, failure_kind, error, duration_s}`.
   - `failure_kind` is `null` for a scored attempt, else `candidate`, `verifier` or `infra`. `error` is only an exception
     class name (or null). No exception text, no stderr: setup output goes to /dev/null.
   - All rows of a run are inserted in **one transaction at the end**, sharing `run_id`. A run that dies partway
     (Ctrl-C, a crash in the runner) leaves no rows, so a partial run can never be averaged with complete ones.
8. **Task files** are read with a SafeLoader subclass that refuses duplicate keys (human).

## Known limits (kept on purpose, human)

- A double-forked daemon that calls `setsid` escapes the process-group kill. Real sandboxing comes later.
- Attempts run one at a time.
- Isolation is by environment and process, not a sandbox: the real home is still reachable (`pwd`), and the
  verifier's module is still importable by the executor if it guesses the name (it shares `sys.path`). AR1 hides the
  path; AR3/AR4 must keep verifier code out of the candidate's reach for Frontier and Sealed tasks.
- The executor process runs `setup` and the candidate together, so a candidate can fake a `setup_failed` message and be
  logged as `infra`. It still scores 0.

## Addendum · AR2 repair round (2026-10-09)

Two additions, both from the human's AR2 repair card (RT-AR2-4 and RT-AR2-8). Nothing above changes.

- **Rows carry tags.** `meta` also holds `tags` (the task's tag list), so anything reading `experiments` (CEI, OBS3) can
  leave out `judge:llm` rows without loading the task set.
- **A verifier can blame the candidate.** `tanishi.arena.verifiers.candidate_fault(reason)` returns a `CandidateFault`,
  a `Verdict` subclass (still a plain `(0.0, reason)` pair for any caller). The runner records it as score 0 with
  `failure_kind = "candidate"`. Used when the candidate's query or code runs past a limit the verifier sets, so a
  runaway answer is not logged as a verifier failure.
