# 0017 · Substrate task state: status words, storage, errors

Date: 2026-10-09. Decided by: the SUB1 implementer, adopting the exam's assumptions
(`open-problems/sub1-step-status.md`), then the human's repair decisions (round 2). Was 0014 until renumbered.

1. **Status words.** `tanishi.substrate.state` exports `PENDING = "pending"`, `RUNNING = "running"`, `DONE = "done"`,
   `FAILED = "failed"`. Only `DONE` means anything to the store: `Plan.next_step()` is the first step whose status is not
   `DONE`, which is where a resumed task carries on. Any other status is opaque text and round-trips unchanged, but
   it must be plain: non-empty printable ASCII with no capitals and no blanks (red-team B4, R5). `"DONE"`, `" done"`,
   `"done​"` (zero-width space) or a Cyrillic look-alike raise `ValueError` instead of being stored and silently
   re-run.
2. **Storage.** One row per task in `substrate_state` (from migration 0001; SUB1 adds no migration). `working`, `plan`,
   `goal`, `hypotheses` are JSON TEXT (decision 0006). `plan` is `{"steps": [{id, description, status, model,
   result_ref}, ...]}`. The `goal` column holds the `goal_ids` list. `save()` is a single `INSERT OR REPLACE`, so it is
   atomic and the last save wins. Only the latest state is kept; there is no history.
3. **Save after each completed step.** "Resume from the last completed step" holds only if the caller saves when a step
   completes. Work done after the last save is lost by design.
4. **Errors.** `load()` of an unknown task raises `LookupError`, and so does an id SQLite cannot encode (a lone
   surrogate, R6). `save()` refuses anything that would not come back unchanged, before touching the db: a wrong type
   (tuple, set, bytes, a non-str key, an IntEnum, a non-Step step) raises `TypeError`; a bad value (NaN, ±inf, a blank
   task id, a cyclic structure, a bad step id or status) raises `ValueError`. A row that `save()` could not have
   written (a NULL column, or valid JSON of the wrong shape, only reachable by raw SQL) makes `load()` raise
   `ValueError`. Error messages name a dict key by position (`working[key #2]`), never by its text, since a key can
   itself be a secret (R4).
5. **Goal ids are not checked** against the `goals` table. The spec says nothing about it, and the exam uses ids that do
   not exist there.
6. **Secrets are stored redacted** (red-team B1, R2). Any string that `events.redact()` would change is written
   redacted, and any value but `None` and `""` under a secret-named key (dict and list included) is written as
   `"[REDACTED]"`, the same key rule `events.redact()` uses. Those values do not round-trip; everything else does, byte
   for byte. The red-team test requires `save()` to succeed rather than refuse, so redacting was chosen over refusing.
9. **A secret-looking task id is refused** (R3, human decision, repair 3). `task_id` is the row's key, so it cannot be
   stored redacted: if `_without_secrets(task_id) != task_id` (what `events.redact()` would change), `save()` raises
   `ValueError` in `_validate_shape`, before the db is opened, and stores nothing. The message does not quote the id.
   `test_state_redteam2.py::test_secret_in_task_id_is_refused` still fails, on its own setup rather than on the
   refusal: see `open-problems/SUB1-repair-conflicts.md`.
7. **Step ids are unique and non-blank** (human decision, red-team B2, B3). A plan with an empty or whitespace-only
   id, or with two steps of the same id (exact text), raises `ValueError`. `next_step()` still works by position.
8. **redact() is linear** (R1). `events._ASSIGNED_SECRETS[0]` starts a match only where a name starts and eats it
   possessively, so 320 kB of repeated secret words redacts in milliseconds. This is CS2's file, changed on the human's
   instruction; `tests/core_state/test_redact_speed.py` pins it.
