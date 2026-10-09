# CS3 repair round · fixes that conflict with a test (the test won)

Date: 2026-10-09. Card: CS3 repair round. Rule: when a fix and a test truly conflict, the test stays and the conflict is written here.

## 1. R5 dedupe vs the exam's random history (unresolved: R5 stays xfail)

- **Card, fix 4:** adding a triple that matches an existing active belief returns that belief and links the new evidence
  instead of adding a second row.
- **Exam:** `tests/core_state/test_beliefs.py::test_random_history_invariants` asserts
  `SELECT COUNT(*) FROM beliefs == len(seen)` after every step, where `seen` gets one id per `add_belief` call. Its
  2 x 2 x 3 triple space repeats a live triple within a few steps.
- **Proof:** with dedupe built (normalised key, live beliefs), all 8 seeds fail (`assert 7 == 8` on seed 0). Removed again.
- **Result:** `test_beliefs_redteam.py::test_identical_triple_is_not_stored_twice` stays `xfail(strict=True)`. The suite
  cannot reach 0 xfailed while both tests stand.
- **What I would do:** the exam's author (or the human) decides which rule holds. If dedupe wins, the exam line should count
  distinct ids (`len(set(seen))`). Dedupe is about ten lines in `_add` (look up a live twin on the normalised key, link the
  evidence, return it); the version I tried is in this round's run notes.

## 2. R1 retire_reason evidence row vs the exam's evidence count (resolved by a workaround)

- **Card, fix 5:** the free-text reason goes in an evidence row with kind `retire_reason`.
- **Exam:** `test_retire_changes_status_and_keeps_the_row` asserts the belief's evidence count
  (`WHERE belief_id = ?`) is still 1 after `retire()`.
- **What I built:** the row has `belief_id` NULL and `event_id` pointing at the `belief_retired` event, which names the
  belief. `evidence_for()` follows that link. Both pass.
- **Cost:** if `emit()` fails (fail-open), the row has neither link and the reason cannot be tied to its belief. Someone
  should confirm the workaround, or relax the exam to count `kind = 'support'` rows only, so the row can carry `belief_id`.

## 3. R6 "refuse an unknown id" vs the red-team test (resolved: returns [])

- **Card, fix 6:** `contradictions()` looks the belief up by id and refuses an unknown id.
- **Red-team test:** `test_contradictions_ignores_a_forged_belief_object` asserts `contradictions(forged) == []` for an
  unknown id, so raising `LookupError` would fail it.
- **What I built:** lookup by id, stored fields only; an unknown id returns `[]` (refused, without an error).
