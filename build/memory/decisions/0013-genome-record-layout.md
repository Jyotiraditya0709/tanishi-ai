# 0013 · Genome record layout (CS6)

Date: 2026-10-09. Decided by: the CS6 implementer, matching the CS6 exam. (First filed as 0008; renumbered by the human.)

`genome.record` is a JSON object (TEXT column, decision 0006) with exactly these keys:
`version`, `parent`, `genes_changed`, `compiler_version`, `substrate_version`, `arena`, `attribution`, `mirror_id`.
It is written with `sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`. `created_at` is a UTC ISO-8601 timestamp.

Write genome rows only through `tanishi.core_state.genome.record_version()`. Do not INSERT, UPDATE or DELETE `genome`
rows with raw SQL: the table has no triggers yet (see `known-bugs/cs6-genome-not-append-only-in-sql.md`), so
append-only holds only on that path. A parent must be recorded before its child (FK `genome.parent -> genome.version`).

## Repair round (2026-10-09), decided by the human after the red team

`record_version()` now refuses, before it opens the database:

- **Types (G5).** `version` and `parent` are `str` (`parent` may be `None`); `genes_changed` is a list of `str` (any
  iterable except a `str` is accepted and turned into a list once, so a generator is stored in full, G1);
  `compiler_version` and `substrate_version` are `str`; `arena` and `attribution` are `dict`; `mirror_id` is `str` or
  `None`. A wrong type raises `TypeError`.
- **Ids (G2, G3).** A blank version or parent, one that differs from its stripped form (`"abc\n"`), or a parent equal
  to its version raises `ValueError`. Callers strip `git rev-parse` output before calling.
- **Plain JSON only (G4).** Inside `arena` and `attribution`: dicts with `str` keys, lists, `str`, `int`, finite
  `float`, `bool`, `None`. Exact types, so tuples, sets, `str`/`dict` subclasses and non-`str` keys raise `ValueError`.
  What reads back equals what was written. Nesting too deep for Python's recursion limit (or a cycle) raises `ValueError`.
- **Gene names (G6).** A blank name raises `ValueError`. Duplicate names are kept for now, because the CS6 exam writes
  them; see `open-problems/CS6-repair-conflicts.md`.

Still open: SQL-level append-only (G7), a later migration after CS4's `0002`.
