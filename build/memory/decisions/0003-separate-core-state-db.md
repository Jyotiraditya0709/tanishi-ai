# 0003 · The Core State gets its own database

Date: 2026-10-09.

New code writes only to the Core State database (`TANISHI_CORE_STATE_DB`, default `~/.tanishi/core_state.db`).
The legacy databases (`data/db.sqlite`, `~/.tanishi/tanishi.db`, `~/.tanishi/finance.db`) are read only by the legacy code
and by the one-off importer `scripts/import_legacy_memory.py`. This keeps the new spine clean and lets the old app keep running.
