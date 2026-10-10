# 0022 · The Frontier tier boundary (AR3)

Date: 2026-10-10. Written by the AR3 implementer as 0020; renumbered 0022 and revised in the AR3 repair round after the
red-team report (`runs/redteam-AR3-20261010.md`). Points marked **(human)** are the human's decisions from that round.
AR4 (Sealed), E5 and M1 build on it.

1. **API.** `FrontierService(store_dir, *, executor, env_passthrough=())` and `.evaluate(candidate, seeds=1)`
   return a list of `{task_id_hash, score, cost}`, one per task, sorted by hash. `score` is the mean over seeds,
   `cost` the sum. Nothing else leaves: no reasons, failure kinds, errors, outputs, ids or prompts.
2. **No rows in the caller's Core State.** The service runs AR1 `run()` against a throwaway db in a 0700 temp dir and
   deletes it afterwards. A caller that wants a record writes the scores it was given. (AR1 rows carry `task_id` and `reason`.)
   `run()` reads its db path from the environment, so the swap holds a module-level lock and restores what it found:
   overlapping in-process calls run one after another (human, RT-7). Other threads still see the throwaway path
   while a call runs; giving `run()` a `db_path` argument (AR1's file) would remove the swap entirely.
3. **Hash.** `HMAC-SHA256(store/.salt, task_id)`, first 32 hex. `.salt` is 32 random bytes. It is written to a temp
   file in the store, fsynced, then `os.link`ed to `.salt` (atomic, never overwrites, so concurrent first calls agree).
   A salt shorter than 32 bytes (a crash under the old code) is replaced: it was refused, so it never hashed anything
   (human, RT-6). Deleting `.salt` breaks comparison with past scores.
4. **Store.** `*.yaml` AR1 task files at the top level, plus the service's own `.salt`, `.queries` and crash leftovers
   `.salt-*.tmp`. Any other entry (a `.yml` file, a subdirectory, a stray file) refuses the whole store with
   `ValueError` and no file name; nothing is skipped (RT-11; the human asked for `.yml` to load, the red-team test
   requires refusal; see `open-problems/AR3-repair-conflicts.md`). The dir, every task file, the salt and the ledger must
   be owned by the effective user with no group/other bits; no task file may be a symlink; the store must be outside
   the checkout. Any invalid task file or duplicate id refuses the store. Every task's verifier is imported before any
   attempt; if one fails, the store is refused as broken (`ValueError`), never scored 0 (human, RT-10).
5. **Cost (human, RT-1, RT-9).** The executor's cost counts only if it is a finite, non-negative number (else 0). Each
   attempt's cost is rounded to cents and capped at `MAX_COST_PER_TASK` (20.0); so is the task's sum over seeds. A
   non-finite sum raises rather than being reported. The CLI prints with `allow_nan=False`. The service does not
   replace cost with its own wall time: the exam pins `cost` to the executor's reported cost for one seed, and wall
   time is not money. The channel is now about 11 bits per task per call, not 48.
6. **Limits (human, RT-5, RT-8).** `seeds` must be 1..`MAX_SEEDS` (10). Each candidate string gets
   `DAILY_QUERY_BUDGET` (20) evaluations per store per UTC day. Past it, `evaluate` raises `QueryBudgetExceeded`
   and the refusal is written to `.queries` (JSON lines, 0600, flock'd). A refused or empty store spends no query.
   The constants live in `frontier_service.py`, not `tanishi/config/`, which autoresearch may rewrite.
7. **Empty store (human).** `evaluate` on a store with no tasks returns `[]` (the exam asserts this). **Any gate
   that uses Frontier scores must check the list is non-empty**, or a mis-pointed store reads as a pass.
8. **Deployment and the CLI (human, RT-4).** The service must run as its own OS user (e.g. `tanishi-frontier`):
   `python -P -m tanishi.arena.frontier_service STORE CANDIDATE --executor mod.fn` (`-P` keeps the cwd off `sys.path`,
   so a module planted there cannot shadow the stdlib in the printing process). The CLI checks the store, spends the
   query and computes the expected hashes itself, without importing the executor. A child (`python -I`, environment
   built from `PATH LANG LC_ALL HOME TMPDIR` plus `--env-passthrough` names) imports the executor and runs the
   evaluation; it writes only `ok <hash> <score> <cost>` lines to a private pipe, stdout/stderr go to /dev/null. The
   parent accepts exactly the expected hashes, scores in [0, 1], costs in cents within the cap, and prints JSON it
   built itself. A child failure is reported by exception class name only.

## Not closed here

- RT-2/RT-3: the executor runs unsandboxed as the store owner, so it can write the prompt anywhere and read the whole
  store. Owned by SEC1 (human); see `open-problems/AR3-sandbox.md`.
- RT-5: a query budget does not stop a 3-call bisection; scores are an oracle by design. See
  `open-problems/AR3-repair-conflicts.md`.
- The executor module is imported in the evaluating child, so it can fake its own scores there. Only a sandbox that
  keeps the executor out of the process that runs the verifier closes that (same owner as RT-2/3).
