# CS4 · goals: known limits (not fixed)

- **Owner is checked in Python only.** A raw `INSERT` with an owner such as `'USER'` or `'admin'` is stored, and
  `active_goals()` sorts it after the user's goals, among Tanishi's. A DB check would break the CS1 round-trip exam
  (`open-problems/CS4.md`).
- **Tree triggers come from `goals.py`, not a migration.** Until the first `add_goal`/`active_goals` call, a db has no
  cycle protection (`open-problems/CS4.md`).
- **`goal_ancestors` can be written directly.** Raw SQL that edits or deletes its rows defeats the cycle check, and so
  does `DROP TRIGGER`. No trigger can tell its own writes apart from a caller's.
- **`PRAGMA foreign_keys = OFF` lets a parent with children be deleted.** The children keep a dangling `parent_id`,
  and the delete trigger drops only the rows that name the deleted goal.
- Every call opens the db, runs `migrate()` and closes it again. That is fine for a goal list. Do not call it in a hot loop.
