# CS4 · goals: known limits

- **Owner is not checked on a raw insert.** A raw `INSERT` with an owner such as `'USER'` or `'admin'` is stored, and
  `active_goals()` sorts it after the user's goals, among Tanishi's. `add_goal` checks it in Python. A DB check would
  break red-team G3 (`open-problems/CS4-repair-conflicts.md`). Once stored, the owner can no longer change.
- [x] **Tree triggers come from `goals.py`, not a migration.** Fixed in the CS4 repair: they are migration 0002.
- **`goal_ancestors` can be written directly.** Raw SQL that edits or deletes its rows defeats the cycle and depth
  checks, and so does `DROP TRIGGER`. No trigger can tell its own writes apart from a caller's.
- **`PRAGMA foreign_keys = OFF` lets a parent with children be deleted.** The children keep a dangling `parent_id`,
  and the delete trigger drops only the rows that name the deleted goal.
- **`INSERT OR REPLACE` on an existing leaf probably leaves stale ancestor rows** (reasoned, not yet proven by a test).
  REPLACE deletes the old row without firing `goals_tree_delete` (recursive triggers are off), so a leaf re-inserted
  with a new `parent_id` keeps its old ancestors as well. That makes the tree checks stricter (a false "cycle" or
  "too deep"), not looser. Owner changes through REPLACE are refused.
- Every call opens the db, runs `migrate()` and closes it again. That is fine for a goal list. Do not call it in a hot loop.

## Red-team breaks (2026-10-09, see `runs/redteam-CS4-20261009.md`)

- [x] G2 (medium): a goal's `owner` can be updated from `tanishi` to `user`. Fixed in 0002: `goals_owner_fixed` refuses
  the UPDATE, and `goals_check_insert` refuses an `INSERT OR REPLACE` that changes the owner.
- [x] G1 (low): huge int rank raises `OverflowError`, not `ValueError`. Fixed in `add_goal`.
- [x] G4 (low): non-string `parent_id` raises `ProgrammingError`, not `ValueError`. Fixed in `add_goal`.
- [ ] G8 (low): `goal_ancestors` grows O(depth²). Bounded now: the tree is capped at 64 levels, so one chain holds at
  most 2,080 ancestor rows. The red-team test still fails, because it builds a 300-deep chain that the cap refuses
  (`open-problems/CS4-repair-conflicts.md`).
