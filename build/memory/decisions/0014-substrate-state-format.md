# 0014 · Substrate task state: status words, storage, errors

Date: 2026-10-09. Decided by: the SUB1 implementer, adopting the exam's assumptions
(`open-problems/sub1-step-status.md`). The human may overrule it. If another node took 0014 first, renumber this one.

1. **Status words.** `tanishi.substrate.state` exports `PENDING = "pending"`, `RUNNING = "running"`, `DONE = "done"`,
   `FAILED = "failed"`. Only `DONE` means anything to the store: `Plan.next_step()` is the first step whose status is not
   `DONE`, which is where a resumed task carries on. Any other status is opaque text and round-trips unchanged, but
   it must be non-empty, lowercase and without surrounding blanks (repair, red-team B4): `"DONE"` or `" done"` raises
   `ValueError` instead of being stored and silently re-run.
2. **Storage.** One row per task in `substrate_state` (from migration 0001; SUB1 adds no migration). `working`, `plan`,
   `goal`, `hypotheses` are JSON TEXT (decision 0006). `plan` is `{"steps": [{id, description, status, model,
   result_ref}, ...]}`. The `goal` column holds the `goal_ids` list. `save()` is a single `INSERT OR REPLACE`, so it is
   atomic and the last save wins. Only the latest state is kept; there is no history.
3. **Save after each completed step.** "Resume from the last completed step" holds only if the caller saves when a step
   completes. Work done after the last save is lost by design.
4. **Errors.** `load()` of an unknown task raises `LookupError`. `save()` refuses anything that would not come back
   unchanged, before touching the db: a wrong type (tuple, set, bytes, a non-str key, an IntEnum, a non-Step step)
   raises `TypeError`; a bad value (NaN, ±inf, a blank task id, a cyclic structure) raises `ValueError`.
   A row that `save()` could not have written (a NULL column, or valid JSON of the wrong shape, only reachable by raw
   SQL) makes `load()` raise `ValueError`.
6. **Secrets are stored redacted** (repair, red-team B1). Any string that `events.redact()` would change, and any text
   value under a secret-named key, is written redacted. That one string does not round-trip; everything else does,
   byte for byte. The red-team test requires `save()` to succeed rather than refuse, so redacting was chosen over
   refusing.
7. **Step ids are free text for now**: empty and repeated ids are accepted, because the exam round-trips them.
   Open: `open-problems/SUB1-repair-conflicts.md`.
5. **Goal ids are not checked** against the `goals` table. The spec says nothing about it, and the exam uses ids that do
   not exist there.
