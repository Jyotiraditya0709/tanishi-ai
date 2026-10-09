# 0010 · Goal rank: higher comes first (and the CS4 repair rulings)

Was `0008-goal-rank-direction.md`. Renumbered by the human so nodes do not collide.

Date: 2026-10-09. Decided by: the CS4 implementer, because the spec says "then by rank" and gives no direction. The
human may overrule it. The human confirmed "higher rank comes first" in the CS4 repair round.

- `active_goals()` orders by owner (all `user` goals, then all `tanishi` goals), then `rank DESC`, then `created_at`,
  then rowid. A rank is a priority score, so 0.9 matters more than 0.1. The default is 0.5.
- Rank is any finite number. `add_goal` rejects NaN, ±inf, bools and non-numbers. There is no [0, 1] bound, because the
  exam uses ranks like -1e9 and 1e6.
- Only `status = 'active'` is listed. `add_goal` writes `active`. Other modules may set any other status (the exam uses
  `done` and `dropped`).
- Goal ids are `uuid4().hex` and never change. A trigger refuses `UPDATE goals SET id`.

## CS4 repair round (2026-10-09, decided by the human)

1. **The goal tree is migration 0002** (`tanishi/core_state/migrations/0002_goal_tree.sql`). CS4 owns number 0002.
   `goals.py` installs nothing at runtime. The migration drops any tree objects the old `goals.py` created, recreates
   them, and fills `goal_ancestors` for goals that already exist.
2. **Owner is fixed after insert.** `goals_owner_fixed` refuses `UPDATE ... SET owner` to a different value, and
   `goals_check_insert` refuses an `INSERT OR REPLACE` that would give an existing id a different owner (REPLACE never
   fires an UPDATE trigger). The ruling also asked for "owner must be 'user' or 'tanishi' on insert"; that part is not
   built, because red-team G3 inserts `'USER'` by raw SQL and must keep passing (`open-problems/CS4-repair-conflicts.md`).
3. **The tree is capped at 64 levels** (`goals.MAX_LEVELS`). A root is level 1, and a goal's level is its number of
   `goal_ancestors` rows. Adding a goal under a level-64 parent is refused (ValueError from `add_goal`, IntegrityError
   from raw SQL). Moving a subtree is refused if its deepest goal would land below level 64.
4. **Bad input is a ValueError.** An int rank too big for a float (10**400) and a `parent_id` that is not a string or
   None both raise ValueError before the db is touched.
5. **Kept:** re-parenting by raw `UPDATE goals SET parent_id` stays allowed when it makes no cycle and stays within
   64 levels. Higher rank comes first.
