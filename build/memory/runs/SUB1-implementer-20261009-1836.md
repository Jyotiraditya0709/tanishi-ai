# SUB1 · implementer run

1. **What did we learn?** CS1 had already created `substrate_state` in 0001 with exactly the right columns, so SUB1
   needed no migration, which avoided a 0003 number clash with parallel nodes. With one row per task, a single
   `INSERT OR REPLACE` makes save atomic, and the exam's SIGKILL tests (killed mid-step and mid-save) pass without any
   extra crash handling. Snapshotting is free: `save` serialises to JSON and `load` builds fresh objects.
2. **What failed, and why?** Nothing in the exam failed (all 200 tests passed the first time; the full suite has 1173
   passing). Ruff's TRY004 flagged type checks raising ValueError, so wrong types now raise TypeError and bad values
   raise ValueError.
3. **Which assumption was wrong?** I first thought `json.dumps` was a strict enough check. It is not: it silently turns
   tuples into lists, int keys into strings and an IntEnum into an int, and it writes NaN, which SQLite's `json_valid`
   CHECK then rejects. `save()` now walks the value first and refuses anything that would not round-trip type for type.
4. **What should the next agent know?** Save after every completed step, then resume with `load(task_id).plan.next_step()`.
   Only `"done"` counts as completed (decision 0014). The `goal` column holds `goal_ids`. Keep big outputs out of
   `working` and point to them with `result_ref`, because every save rewrites the whole row. Limits are in
   `known-bugs/sub1-state-limits.md`. My own tests are in `tests/substrate/test_state_impl.py`; the exam is
   `test_state.py`. SUB1 produces no intelligence, so there is no benchmark.
