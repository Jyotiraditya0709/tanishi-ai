# 0020 · The Frontier service boundary (AR3)

Date: 2026-10-10. Written by the AR3 implementer. AR4 (Sealed), E5 and M1 build on it.

1. **API.** `FrontierService(store_dir, *, executor, env_passthrough=())` and `.evaluate(candidate, seeds=1)`
   return a list of `{task_id_hash, score, cost}`, one per task, sorted by hash. `score` is the mean over seeds,
   `cost` the sum. Nothing else leaves: no reasons, failure kinds, errors, outputs, ids or prompts.
2. **No rows in the caller's Core State.** The service runs AR1 `run()` against a throwaway db in a 0700 temp dir and
   deletes it afterwards. A caller that wants a record writes the scores it was given. (AR1 rows carry `task_id` and `reason`.)
3. **Hash.** `HMAC-SHA256(store/.salt, task_id)`, first 32 hex. `.salt` is 32 random bytes, created 0600 on first use.
   The same store gives the same hashes; another store, or a new salt, gives different ones. Deleting `.salt` breaks
   comparison with past scores.
4. **Store.** Top-level `*.yaml` AR1 task files plus `.salt`. Refused (PermissionError) unless the dir, every task file
   and the salt are owned by the effective user and have no group/other bits, no task file is a symlink, and the store
   is outside the checkout. A missing store raises FileNotFoundError. Any invalid task file or duplicate id refuses
   the whole store, with a message that names no file content. A silently skipped task would change what the score means.
5. **Deployment.** Mode bits only keep out users other than the owner. The service must run as its own OS user
   (e.g. `tanishi-frontier`), invoked as `python -m tanishi.arena.frontier_service STORE CANDIDATE --executor mod.fn`,
   which prints only the JSON list. Running it in-process as the agent's user gives no protection.
