# 0008 · Belief store semantics (CS3)

Date: 2026-10-09. Decided by: the CS3 implementer, where the spec is silent. Pinned by `tests/core_state/test_beliefs_impl.py`.

1. **Statuses** are `active`, `contested`, `retired`. "Live" means active or contested; `contradictions()` only looks at live beliefs,
   and returns `[]` for a belief that is not live.
2. **Contradiction** is an exact string match: same `subject` and `predicate`, different `object`. One `belief_conflict` event per
   add that finds conflicts, payload `{"belief_id": new, "conflicts_with": [ids]}`. **Ids only, never belief text** (it may be personal).
3. **retire(id, reason)** sets `retired`, writes a `belief_retired` event `{"belief_id", "reason"}` and adds no evidence row.
   A contested peer left with no live contradiction goes back to `active`. Retiring a retired belief is a no-op.
   Unknown id: `LookupError`. Blank reason: `ValueError`.
4. **Confidence**: clamped to [0, 1]; NaN raises `ValueError`; non-numbers (strings, bools, None) raise `TypeError`.
5. **Events are written as plain unhashed rows** until CS2's `emit()` lands. CS2 should replace `beliefs._emit` with `emit()`.
6. **Every public call opens, migrates and closes the Core State db** (no connection in the spec's interface).
7. **Legacy mapping** (`scripts/import_legacy_memory.py`): `core_memory(key, value)` -> `("user", key, value)` at 0.9;
   `memories(id, content, category, importance)` -> `("memory:<id>", category or "fact", content)` at `importance`.
   Belief id = uuid5 of (table, key, value), so a re-run skips seen rows and a changed `core_memory` value becomes a new,
   contested belief rather than overwriting the old one.
