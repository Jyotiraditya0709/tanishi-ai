# 0007 · CS1 hardening after the red team

Date: 2026-10-09. Decided by: the human, for the CS1 repair round. Conflicts were settled by the human or by the rule
"the test wins" (details: `open-problems/CS1-exam-conflicts.md`).

1. **Schema fixes went into `0001_init.sql`**, not a new 0002, because nothing has shipped. From now on 0001 is frozen:
   `migrate()` refuses a database whose applied migration file has changed. Every new schema change is a new `000N_name.sql`.
2. **`events` is append-only.** Triggers abort every UPDATE, and every DELETE of a row with a `hash`. Rows with no hash can
   still be deleted, because the red-team test for id reuse (R11) deletes one. `prev_hash` and `hash` are UNIQUE, so the chain
   cannot fork. `id` is `INTEGER PRIMARY KEY AUTOINCREMENT`.
3. **Constraints.** Text primary keys are NOT NULL. Every `confidence` is `REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1)`.
   NOT NULL is needed because SQLite stores a bound NaN as NULL. Every JSON column has `CHECK (col IS NULL OR json_valid(col))`.
   Foreign keys: `evidence.belief_id -> beliefs.id`, `evidence.event_id -> events.id`, `goals.parent_id -> goals.id`,
   `genome.parent -> genome.version`. `genes.introduced_in` has no FK, because the spec does not say it holds a genome version.
   There are indexes on `evidence(belief_id)`, `evidence(event_id)`, `beliefs(subject, predicate)`, `events(session_id)`,
   `goals(parent_id)` and `genome(parent)`.
4. **Checksums.** `migrate()` records the sha256 of each migration file (CRLF normalised to LF) in a table it owns,
   `schema_checksum(version, checksum)`. They are not in a `schema_version.checksum` column, because the exam asserts
   `schema_version` is exactly `(version, applied_at)`. `migrate()` raises if an applied file changed, if a recorded version
   has no file or no checksum, if the recorded versions have gaps, or if a `.sql` file in `migrations/` does not match
   `NNNN_lowercase_name.sql`. `migrate()` also creates `schema_version` itself if a migration file did not.
5. **No transaction control in migrations.** A top-level `BEGIN`, `COMMIT`, `END`, `ROLLBACK`, `SAVEPOINT` or `RELEASE`
   makes `migrate()` raise before it touches the database. Trigger bodies are fine. The splitter ignores `--` and `/* */` comments.
6. **The legacy guard uses real paths.** `resolve_path()` resolves symlinks, then refuses a legacy file name (given or resolved),
   the real path of `$TANISHI_HOME/tanishi.db`, `~/.tanishi/tanishi.db`, `./data/db.sqlite` or `$TANISHI_DB_PATH`, and any hard
   link to one of those files that exists. `open_db("")` raises.
7. **Permissions.** Directories that `open_db` creates get mode 0700. `$TANISHI_HOME` is tightened to 0700 when the default path
   is used. A directory the caller named is never chmod-ed. The db file is created 0600, and the db, `-wal` and `-shm` files are
   tightened to 0600 on every open.
8. **Paths.** The default is `$TANISHI_HOME/core_state.db`, and `TANISHI_HOME` defaults to `~/.tanishi`. `tests/conftest.py` has an
   autouse fixture that gives every test a temporary `HOME` and `TANISHI_CORE_STATE_DB`, and *unsets* `TANISHI_HOME`, so the
   default still follows `HOME` (the exam needs this). Under that fixture no test can reach the real `~/.tanishi`.
   `open_db()` does not refuse the default path under pytest, so R8 stays open (the human chose the exam over R8).
