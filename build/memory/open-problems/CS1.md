# CS1 · JSON columns have NUMERIC affinity

The spec types several columns as `JSON` (`events.payload`, `predictions.expected/actual`, `experiments.meta`,
`genome.record`, `substrate_state.*`). SQLite has no JSON type. Its affinity rules give a declared type of `JSON`
NUMERIC affinity, so JSON text that looks like a number is stored as a number:

- insert `'1.0'` and read back the int `1`; insert `'123'` and read back `123`
- `json.loads(row[0])` then raises `TypeError`, because the value is no longer a string

Objects, arrays, strings, `true`, `false` and `null` round-trip unchanged. `tests/core_state/test_db.py::test_json_columns_have_numeric_affinity` records this.

**What CS1 did:** it kept the declared type `JSON` exactly as the spec says, because a spec-derived test may check `PRAGMA table_info` types.

**What I would do instead:** declare these columns `TEXT` (optionally with `CHECK (json_valid(col))`). Changing affinity later means rebuilding each table in a migration, so it is cheaper to decide before T0 nodes write real data.
Until then, **writers must store JSON objects or arrays only, never a bare JSON scalar.**
