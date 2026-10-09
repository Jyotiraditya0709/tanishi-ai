# 0008 · The Arena attempt contract (AR1)

Date: 2026-10-09. Decided by: the AR1 implementer, where the spec was silent. Later Arena nodes (AR2+, OBS3, M1) build on it.

1. **Verifier:** `verifier(task, output) -> (score, reason)`, named by dotted path. `output` is the executor's text, or `None`
   when the run has no executor. It runs inside the attempt's isolated environment, so it may inspect files the attempt
   wrote under `$HOME` / `$TANISHI_HOME`. Building blocks: `tanishi.arena.verifiers.exact`, `contains`, `number`.
2. **Never positive on failure.** A raise, import failure, timeout, NaN, inf, a score outside [0, 1], a bool or a wrong
   shape scores **0** (not clamped). A crashed, hard-exited, timed-out or blank-output executor scores 0 and the verifier is
   not called.
3. **Executor:** `executor(task, candidate, seed) -> Attempt(output, cost) | str`, a picklable module-level function.
4. **Isolation:** each (task, seed) runs in a spawned process that leads its own process group, with a new temp dir as
   `HOME`, `$HOME/.tanishi` as `TANISHI_HOME`, a fresh migrated `core_state.db` there, cwd in the temp dir, and
   `TANISHI_DB_PATH`, `DB_PATH`, `MEMORY_PATH`, `SKILLS_PATH`, `LOGS_PATH` unset. The whole group is killed and the temp dir
   removed afterwards. The caller's `os.environ` is never modified. `HOME` is redirected because legacy tools write to
   `Path.home() / ".tanishi"` directly.
5. **Timeout:** `timeout_s` covers setup + executor + verifier and starts once the process is up (startup is not charged).
6. **Rows:** one `experiments` row per (task, seed) in the caller's Core State db: `candidate`, `seed` (0..seeds-1),
   `score`, `cost` (finite, >= 0; garbage becomes 0), `task_set` = `tasks:<sha256 of sorted ids>[:16]`, `baseline` NULL,
   `meta` = JSON `{run_id, task_id, family, tier, reason, duration_s}`. Rows are committed one by one.
