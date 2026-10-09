# CS4 · the goal tree triggers cannot be a migration yet

**The conflict.** Decision 0007 says every new schema change is a new `000N_name.sql`. The CS4 tester expected a
`0002_*.sql` holding the cycle triggers. But the CS1 exam pins the schema version at 1: `migrate()` must return 1,
`schema_version` must be `[1]`, and so on. A `0002_goal_tree.sql` was tried, and 34 CS1 exam cases failed on `2 == 1`.
Three more `test_property_rows_round_trip[*-goals]` cases failed because a DB-level owner check rejected their random
owners.

**What was built instead (the test wins, as in CS1).** `tanishi/core_state/goals.py` installs the `goal_ancestors` table,
its index and six triggers on first use, inside one `BEGIN IMMEDIATE` transaction. On later calls it checks that all
eight objects are there and raises if only some of them are. Owner is checked in Python only, not in the database.

**What it costs.**
- A Core State db that has been migrated but never touched by `goals.py` has no cycle protection until the first
  `add_goal`/`active_goals` call. On that call, existing rows are backfilled into `goal_ancestors`.
- Schema objects exist outside `migrate()`'s checksum regime.

**What I would do instead (needs the human).**
1. Change the CS1 exam to assert `migrate() == len(migrations)` (or `>= 1`) instead of `== 1`.
2. Move `_TREE_SQL` from `goals.py` into `migrations/0002_goal_tree.sql` unchanged, and delete `_install_tree`.
   The object names are the same, so a db where `goals.py` already installed them needs `CREATE ... IF NOT EXISTS`
   in that migration, or a one-time drop-and-recreate.
3. If the human wants owner enforced in the db, the round-trip exam must stop writing random owners into `goals`.
