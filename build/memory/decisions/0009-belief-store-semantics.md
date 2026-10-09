# 0009 · Belief store semantics (CS3)

Date: 2026-10-09. Decided by: the CS3 implementer, where the spec is silent. Pinned by `tests/core_state/test_beliefs_impl.py`.
First filed as `0008-belief-store-semantics.md`; renumbered by the human because CS2 also took 0008. Older run notes say
"decision 0008" for this file.

1. **Statuses** are `active`, `contested`, `retired`. "Live" means active or contested; `contradictions()` only looks at live beliefs,
   and returns `[]` for a belief that is not live.
2. **Contradiction**: same `subject` and `predicate`, different `object`. One `belief_conflict` event per
   add that finds conflicts, payload `{"belief_id": new, "conflicts_with": [ids]}`. **Ids only, never belief text** (it may be personal).
   (Item 8 replaces "exact string match".)
3. **retire(id, reason)** sets `retired`. A contested peer left with no live contradiction goes back to `active`. Retiring a retired
   belief is a no-op. Unknown id: `LookupError`. Blank reason: `ValueError`. (Item 9 replaces what gets logged.)
4. **Confidence**: clamped to [0, 1]; NaN raises `ValueError`; non-numbers (strings, bools, None) raise `TypeError`.
5. ~~Events are written as plain unhashed rows until CS2's `emit()` lands.~~ Replaced by item 7.
6. **Every public call opens, migrates and closes the Core State db**, unless the caller passes `conn=` from `beliefs.connect()`
   (keyword-only, on `add_belief_if_absent`, `find`, `retire`, `reactivate`). The spec's four functions keep their signatures.
7. **Legacy mapping** (`scripts/import_legacy_memory.py`): `core_memory(key, value)` -> `("user", key, value)` at 0.9;
   `memories(id, content, category, importance)` -> `("memory:<id>", category or "fact", content)` at `importance`.
   Belief id = uuid5 of (table, key, value), so a re-run skips seen rows. (Item 11 replaces "a changed value is contested".)

## Repair round, 2026-10-09 (decided by the human after the red team; built by the CS3 implementer)

7. **Events go through `emit()`** from `tanishi.core_state.events`, actor `core_state.beliefs`. No raw INSERT into `events`
   in CS3 code (a test greps for it). `emit()` takes its own connection and write lock, so CS3 emits **after** the belief
   transaction commits; emitting inside it would wait on our own lock. The two are therefore not atomic. **Fails open**, as
   CS2's wiring does (0008 item 6): if `emit()` raises, CS3 logs a warning with the exception type, the belief change stands,
   and `emit()` has already noted a log gap. The exam needs this: it inserts unhashed events, which `emit()` cannot chain onto.
8. **Normalised matching (R4).** Subject, predicate and object compare on `belief_key(x) = " ".join(x.split()).casefold()`,
   a SQLite function registered on each connection. The original text is stored and returned. `find(subject=, predicate=)`
   still matches exactly (the card did not ask to change it). Cost: the contradiction check no longer uses the
   `(subject, predicate)` index, so it scans the table.
9. **Retire logging (R1).** The `belief_retired` event is `{"belief_id", "reason": <code>}`, code one of `superseded`,
   `user_correction`, `contradiction_resolved`, `other`. `retire(id, reason, code=None)`: `code` defaults to `reason` when that
   is a code, else `other`; an unknown code raises `ValueError` before anything is written. The free text, through `redact()`,
   goes in an `evidence` row of kind `retire_reason` with **`belief_id` NULL and `event_id` = the retire event**: the exam
   pins that retire() leaves a belief's evidence count unchanged (see `open-problems/CS3-repair-conflicts.md`). If the emit
   failed, that row has no event and so no link. Evidence rows are not in the hash chain.
10. **`evidence_for(belief_id) -> list[Evidence]`** (R7): rows with that `belief_id`, plus `retire_reason` rows whose event
    names the belief, oldest first, each with `belief_id` filled in. Unknown id: `[]`.
    **`contradictions(belief | id)`** (R6) uses only the id and reads the stored row; an unknown id returns `[]` (the red-team
    test asserts `[]`, so it does not raise). `evidence_event_id` must be an `int`, not a `bool` (R2): `TypeError`.
    An int too big for a float clamps to 1.0, or 0.0 if negative (R3).
11. **Legacy history (R9).** A legacy key is a `core_memory` key (`("user", key)`) or a `memories` id (subject `memory:<id>`).
    For each row the importer first retires, with code `superseded`, every live legacy-sourced belief on that key with
    another id, then adds the row's belief, or **reactivates** it if it is retired. `reactivate(id) -> bool` sets it active, or
    contested if a live belief now disagrees, and emits `belief_reactivated {"belief_id"}` (plus `belief_conflict` if contested).
    Beliefs from other sources are never retired by the importer.
12. **Bad legacy text (R8).** The source connection decodes TEXT with `errors="replace"`. A row that still fails (a lone
    surrogate, or a ValueError/TypeError while writing it) is skipped and counted. The summary line is
    `imported N new, retired N, reactivated N, skipped N unreadable row(s) from <path>`. Errors print SQLite's message
    (no row values) or, for anything else, only the exception type.
13. **One connection (fix 7).** The importer does all its Core State reads and writes over one `beliefs.connect()`.
    `emit()` still opens its own connection per event; that is CS2's design.
14. **Duplicates (R5) are not merged**: the exam requires one new row per `add_belief` call. See the conflicts file.
