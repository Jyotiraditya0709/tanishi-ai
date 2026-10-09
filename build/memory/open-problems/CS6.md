# CS6 · acceptance line 2 cannot be met inside this node

"Every merge to main by the integration agent writes one record (wired in OBS3's merge hook or CI)."

- OBS3 does not exist yet, and CI lives in `.github/`, which is a protected path. CS6 only provides the function.
- OBS3 or the integration agent must call `record_version()` once per merge, with `version` = the merge commit sha
  and `parent` = the first-parent sha (record a root first if the parent was never recorded, or the FK rejects it).
- A re-run of the hook for the same merge raises `sqlite3.IntegrityError`. The hook should treat that as "already
  recorded", not as a failure, so a retried CI job stays green.
- Where CI runs, the genome lives in that runner's `TANISHI_CORE_STATE_DB`. Decide where the record database
  persists before wiring it, or each CI run writes to a throwaway file.
