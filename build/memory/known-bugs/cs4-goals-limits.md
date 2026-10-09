# CS4 · goals: known limits

- [x] **Owner is not checked on a raw insert.** Fixed in CS4 repair 2: `goals_check_insert` in 0002 refuses any owner
  other than exactly `'user'` or `'tanishi'` (NULL, `'USER'`, `' user'` and `''` included), for INSERT and
  INSERT OR REPLACE alike. Red-team G3 was rewritten to expect this and now passes. Once stored, the owner cannot change.
- [x] **Tree triggers come from `goals.py`, not a migration.** Fixed in the CS4 repair: they are migration 0002.
- **`goal_ancestors` can be written directly.** Raw SQL that edits or deletes its rows defeats the cycle and depth
  checks, and so does `DROP TRIGGER`. No trigger can tell its own writes apart from a caller's.
- **`PRAGMA foreign_keys = OFF` lets a parent with children be deleted.** The children keep a dangling `parent_id`,
  and the delete trigger drops only the rows that name the deleted goal.
- [x] **`INSERT OR REPLACE` on an existing leaf left stale ancestor rows.** Confirmed by a test in CS4 repair 2
  (`test_goals_impl.py::test_replace_of_a_leaf_keeps_ancestors_exact`): a leaf re-inserted under another parent, or as
  a root, kept its old ancestors too. REPLACE deletes the old row without firing `goals_tree_delete` (recursive
  triggers are off). Fixed in 0002: `goals_check_insert` ends with `DELETE FROM goal_ancestors WHERE goal_id = NEW.id`,
  after every refusal check, and `goals_tree_insert` writes the new rows. A REPLACE of a goal that has children is
  still refused outright ("goal id already has children"). Owner changes through REPLACE are refused.
- Every call opens the db, runs `migrate()` and closes it again. That is fine for a goal list. Do not call it in a hot loop.

## Red-team breaks (2026-10-09, see `runs/redteam-CS4-20261009.md`)

- [x] G2 (medium): a goal's `owner` can be updated from `tanishi` to `user`. Fixed in 0002: `goals_owner_fixed` refuses
  the UPDATE, and `goals_check_insert` refuses an `INSERT OR REPLACE` that changes the owner.
- [x] G1 (low): huge int rank raises `OverflowError`, not `ValueError`. Fixed in `add_goal`.
- [x] G4 (low): non-string `parent_id` raises `ProgrammingError`, not `ValueError`. Fixed in `add_goal`.
- [x] G8 (low): `goal_ancestors` grows O(depth²). Bounded now: the tree is capped at 64 levels, so one chain holds at
  most 2,080 ancestor rows. The tester rewrote G8 to check the cap, and it passes.
- [x] G3 (rewritten): a raw insert with a bad owner is refused (see the first entry). Its xfail mark is removed.
