# CS4 repair · where the human's rulings and the red-team tests conflict

Found in the CS4 repair round on 2026-10-09. The implementer may not change a red-team assertion, so these need the
human (or the red-team test's owner). The suite has **1 known failure** until this is settled.

| Ruling | Test | What happens | What I did | What would settle it |
|---|---|---|---|---|
| G8: cap the tree at 64 levels | `test_goals_redteam.py::test_g8_deep_chain_ancestor_table_is_not_quadratic` builds a 300-deep chain with `add_goal` and then counts ancestor rows | With the cap, the 65th `add_goal` raises ValueError. Without it, the test fails on `45150 <= 6000`. It fails either way. | Built the cap. The test was red before the cap and stays red, so the cap breaks nothing that passed. | Rewrite G8 to build up to `MAX_LEVELS` and assert the cap (65th add refused, row count at most 64·65/2 per chain), or drop the cap and replace the closure table (out of scope for this card). |
| G2/owner: in 0002, owner must be `'user'` or `'tanishi'` on insert | `test_goals_redteam.py::test_g3_raw_owner_variant_never_sorts_above_a_user_goal` inserts `owner = 'USER'` by raw SQL and expects the row to be stored | A strict insert check (tried and run) makes G3 fail with `IntegrityError: goal owner must be user or tanishi`. G3 passes today. | Left the insert check out (the test wins). Built the other half of the ruling: owner can never change after insert. | Change G3 to expect the raw `'USER'` insert to be refused. Then add `SELECT RAISE(ABORT, ...) WHERE NEW.owner IS NULL OR NEW.owner NOT IN ('user', 'tanishi')` to `goals_check_insert`. That would need a new migration (0003) once 0002 has shipped to any real db, because migrate() refuses an applied file that changed. |

The CS1 round-trip exam is no longer a blocker for the owner check: since PR #5 it only writes `'user'` or `'tanishi'`.

## Status

Closed (CS4 repair 2, 2026-10-09). Both rows were settled by rewriting the red-team tests.

Status (tester fix, 2026-10-09): G8 and G3 were rewritten to the human's decisions. G8 now checks the 64-level cap
(a 64-level chain is accepted, the 65th level is refused, at most 2080 rows in goal_ancestors). G3 now expects a raw
insert with a bad owner to be refused, and is xfail(strict) until the builder adds the owner check.

Status (builder, CS4 repair 2, 2026-10-09): the owner check is in `goals_check_insert` in 0002. It was edited in place,
not added as 0003, because CS4 has never been merged and 0002 has never run on a real db. G3's xfail mark is removed
and G3 passes. G8 passes against the cap. Suite: 280 passed, 0 failed, 0 xfailed.
