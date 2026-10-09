# SUB1 repair · where the red-team tests and the exam (or the human's fix) conflict

## Settled: B2, B3 (step ids)

The human ruled step ids unique and non-empty; the tester changed the exam's generator (`_rand_state` in
`tests/substrate/test_state.py`) to draw `f"s{k}-..."` ids. Repair 2 added the checks in `_validate_shape`
(blank after trimming, or an exact repeat, raises `ValueError`). Both red-team tests and the exam pass.

## Open: R3, a secret inside `task_id` (found in repair 2, 2026-10-09)

Status: R3 is resolved by refusing a secret-looking task_id (the human chose option 1, 2026-10-09). The red-team test is now `test_secret_in_task_id_is_refused`; it fails until the builder re-adds the `_validate_shape` check.

### Repair 3 (2026-10-09 23:09): check added; the test still fails, on its setup

`save()` now raises `ValueError` for that id before opening the db (decision 0017 §4, §9). The test gets past
`pytest.raises`, then fails in its `_raw()` helper with `sqlite3.OperationalError: no such table: substrate_state`.
Nothing in that test creates the table: the conftest gives each test a fresh db, and the refused `save()` is the
test's only call. The other tests that use `_raw()` call a `save()` that succeeds first.

I did not change `save()` to create the schema before it validates. That would be a change to decision 0017 §4
("refuses … before touching the db"), and the card did not ask for it. Two ways to fix it:

1. **Fix the test's setup (recommended, tester):** call `save(_state())` (or `migrate()` on an `open_db()`) before
   the `pytest.raises` block, then check that the only row is `"t"` instead of `== []`. No assertion is weakened.
   `test_state_impl.py::test_secret_task_id_is_refused_and_nothing_stored` already checks it this way and passes.
2. **Change `save()` (human):** run `migrate()` before `_validate()`. A refused save would then create the db file
   and schema, though it would still write no row. That contradicts 0017 §4.

The card says: a `task_id` that `redact()` would change is refused with `ValueError`.
The red-team test `test_state_redteam2.py::test_r2_secret_in_task_id_is_stored_in_plaintext` does

```python
save(_state(task_id=f"job-{KEY}"))   # no pytest.raises: save() must succeed
assert KEY not in _raw()
```

so a refusal fails it on the first line. Under this round's rules the test wins: I built the refusal, saw it fail
the test, and took it out. `save()` stores the id as given, and this one test fails. **Suite: 1 known failure.**

What would settle it (the human, or the red team as the test's owner):

1. **Refuse (the human's choice)**: the red-team test changes to `with pytest.raises(ValueError): save(...)` and still
   checks that nothing reached the table. Then I add the two-line check in `_validate_shape`. Recommended: it is the
   smallest change, and a task id is the caller's choice, so asking for another one costs nothing.
2. **Store a key derived from the id**: the `task_id` column holds `redact(id) + "#" + sha256(id)` when
   `redact(id) != id`, and `load(id)` computes the same key. The test passes as written and `load()` still finds the
   task, but the stored key no longer equals the id for those rows, anything reading `substrate_state` by raw SQL sees
   the derived key, and an unsalted hash of a low-entropy secret can be guessed. I did not build this: it is a
   storage change the card did not ask for.
