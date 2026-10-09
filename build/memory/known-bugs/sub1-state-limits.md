# SUB1 · task state: known limits

- **No history.** `save()` overwrites the row. The record of an earlier plan is gone, and nothing is written to the event
  log (CS2) on save. If the loop needs "what did the plan look like before the failure", that needs a new node or an
  event per save.
- **Last writer wins.** Two processes saving the same `task_id` race, with no version check. One task should have one
  owner process.
- **Whole state rewritten every save.** Each save opens the db, runs `migrate()`, and writes every column. A big
  `working` dict (the exam uses 200 kB) costs that much I/O per step. Do not save in a hot loop. Keep large outputs
  elsewhere and point to them with `Step.result_ref`.
- **Goal ids are not foreign keys.** A dropped or unknown goal id is stored and loaded without complaint (decision 0017).
- **Counters under secret-named keys are hidden.** `{"token": 5}` or `{"auth": [...]}` is stored as `"[REDACTED]"`
  (decision 0017 item 6, red-team R2): the same key rule as `events.redact()`. `token_count` matches that rule too; name
  a counter `n_tokens` or `max_tokens`, which do not.
- **Power loss is untested.** The exam kills the process (SIGKILL), which a committed WAL transaction survives. Surviving
  power loss depends on SQLite's `synchronous` setting, which `open_db()` leaves at the default.
