# SUB2 · where the implementation and the exam conflict

## `test_invariants_hold_for_random_scripts` fails before the loop runs

35 of 36 exam tests pass. The property test fails at its first line, `before = {p["id"] for p in predictions()}`,
with `sqlite3.OperationalError: no such table: predictions`. The exam's `predictions()` helper calls `open_db()`,
which does not migrate, and `tests/conftest.py` gives every test a fresh, empty Core State db. In every other exam
test the helper is called only after the loop has run, and the loop's first event (`events._open()` migrates) has
created the tables. In the property test it is called before anything has run, so it would fail against any
implementation.

What I did not do: create or migrate the db when `tanishi.substrate.loop` is imported. That would be an import-time
side effect on the user's real `~/.tanishi/core_state.db`, done only to satisfy a test helper.

What I did: `tests/substrate/test_loop_impl.py::test_invariants_hold_for_random_scripts_on_a_migrated_db` checks the
same invariants (budget bounds tool calls and steps, one prediction per executed act, written first, all resolved,
chain verifies) after migrating first, and it passes.

What I would ask the tester or the human: add `migrate(conn)` to the exam's `predictions()` helper (the same one-line
fix as `rows()` in my test file), or call it once before the loop in the property test. No assertion changes.
