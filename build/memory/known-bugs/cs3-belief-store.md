# CS3 · belief store limits known at merge

- [ ] **Every predicate is treated as single-valued** (kept as a known limit by the human, 2026-10-09). Two live beliefs
  `("user", "likes", "tea")` and `("user", "likes", "coffee")` are contested, though both can be true. The spec defines
  contradiction this way. The legacy importer avoids it by giving every free-text memory its own subject (`memory:<id>`).
  Fix later: a list of multi-valued predicates, or a `cardinality` on predicates.
- [x] ~~Matching is exact.~~ Fixed in the repair round: subject, predicate and object compare on a normalised key
  (decision 0009 item 8). Still open: `find(subject=, predicate=)` matches exactly, so `find(subject="User")` misses `"user"`.
- [ ] **The contradiction check scans the whole `beliefs` table** (new in the repair round): normalised keys are computed in
  Python per row, so the `(subject, predicate)` index is not used. Fine for thousands of beliefs. Fix: a stored key column
  with an index (needs a migration).
- [x] ~~Conflict and retire events are unhashed.~~ Fixed: every CS3 event goes through `emit()` and joins the hash chain.
- [ ] **A belief change and its event are not atomic.** `emit()` takes its own write lock, so CS3 emits after commit and fails
  open. A failed emit leaves a log gap (recorded by `emit()`) and, for retire, a `retire_reason` evidence row with no link.
  Fix: an `emit()` that can join the caller's transaction (CS2's module).
- [x] ~~The importer opens and migrates the db once per legacy row.~~ Fixed: one connection per import.
- [x] ~~A changed `core_memory` value leaves the old belief contested.~~ Fixed: superseded legacy values are retired and a
  returning value is reactivated (decision 0009 item 11).
- [ ] **A legacy key deleted from the legacy file stays live** in the Core State. The importer only sees rows that exist.
