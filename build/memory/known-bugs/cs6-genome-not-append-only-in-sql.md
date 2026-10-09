# CS6 · `genome` is append-only only through `record_version()`

`record_version()` refuses a second write of a version (PRIMARY KEY), but any code holding an `open_db()` connection can
still `UPDATE genome ...` or `DELETE FROM genome ...`. `events` is protected by triggers (decision 0007); `genome` is not.

Not fixed in CS6: the fix is a new migration (0001 is frozen), and the CS1 exam asserts `migrate(...) == 1` in
`tests/core_state/test_db.py` and `tests/core_state/test_migrations.py`. A `0002` would fail those tests, and the
implementer may not change them.

Fix, once the CS1 exam owner lets the schema version move past 1:

```sql
-- 0002_genome_append_only.sql
CREATE TRIGGER genome_no_update BEFORE UPDATE ON genome BEGIN SELECT RAISE(ABORT, 'genome is append-only'); END;
CREATE TRIGGER genome_no_delete BEFORE DELETE ON genome BEGIN SELECT RAISE(ABORT, 'genome is append-only'); END;
```

Those exam assertions should then compare against the number of migration files instead of the literal `1`.
