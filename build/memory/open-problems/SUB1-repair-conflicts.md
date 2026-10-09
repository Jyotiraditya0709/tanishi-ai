# SUB1 repair · where the red-team tests and the exam conflict

Found in the SUB1 repair round on 2026-10-09. The implementer may not change either test, so these need the human (or
the tests' owners). The suite has **2 known failures** until this is settled.

| Break | Red-team test | Exam test it conflicts with | What I did | What would settle it |
|---|---|---|---|---|
| B2: duplicate `Step.id` accepted | `test_state_redteam.py::test_duplicate_step_ids_are_accepted` | `test_state.py::test_property_round_trip_is_identity` builds step ids with `_rand_text`, which can repeat within one plan | Built the check, ran it: ~30 of the 150 property seeds failed. Removed the check (the exam wins). | Either the spec says step ids are unique and non-empty, and the exam's generator draws unique, non-empty ids (e.g. `f"s{k}-{_rand_text(rng)}"`), then add the two checks in `_validate_shape`; or the human rules ids are free text and B2/B3 are rewritten to assert acceptance. |
| B3: empty `Step.id` accepted | `test_state_redteam.py::test_empty_step_id_is_accepted` | Same property test: `_rand_text` returns `""` one time in sixteen | Same as B2. | Same as B2. |

My recommendation is the first option. `result_ref` bookkeeping and any "which step is done" lookup by id are ambiguous
without unique ids. Today nothing in the store keys on id: `next_step()` works by position, so the gap does no harm yet.
