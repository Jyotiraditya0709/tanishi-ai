# 0008 · Genome record layout (CS6)

Date: 2026-10-09. Decided by: the CS6 implementer, matching the CS6 exam.

`genome.record` is a JSON object (TEXT column, decision 0006) with exactly these keys:
`version`, `parent`, `genes_changed`, `compiler_version`, `substrate_version`, `arena`, `attribution`, `mirror_id`.
It is written with `sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`. `created_at` is a UTC ISO-8601 timestamp.

Write genome rows only through `tanishi.core_state.genome.record_version()`. Do not INSERT, UPDATE or DELETE `genome`
rows with raw SQL: the table has no triggers yet (see `known-bugs/cs6-genome-not-append-only-in-sql.md`), so
append-only holds only on that path. A parent must be recorded before its child (FK `genome.parent -> genome.version`).
