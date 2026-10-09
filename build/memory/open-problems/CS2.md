# CS2 · a stray unhashed row stops all logging (red-team C3)

**What is wrong.** `events.hash` is nullable (`UNIQUE` allows many NULLs). A raw `INSERT INTO events (ts, kind, actor) ...`
from any future node makes the row with the highest id have `hash IS NULL`. Every later `emit()` then computes
`None + canonical` and raises `TypeError`, for ever. Proving test: `test_one_stray_unhashed_row_must_not_stop_all_later_logging`
(still `xfail(strict=True)`).

**Why not fixed in CS2.** The fix is a schema change, and the repair round allows no new migration.
The human kept C3 open for that reason.

**What I would do (one later migration).** SQLite cannot add `NOT NULL` to an existing column, so either:
- rebuild `events` with `hash TEXT NOT NULL UNIQUE` and `prev_hash TEXT NOT NULL`, copying the rows and recreating the
  append-only triggers in the same migration; or, cheaper,
- add `CREATE TRIGGER events_hash_required BEFORE INSERT ON events WHEN NEW.hash IS NULL OR NEW.prev_hash IS NULL
  BEGIN SELECT RAISE(ABORT, 'events rows must come from emit()'); END;`

The trigger is enough against accidents, which is the risk here. A deliberate attacker who drops triggers is the anchor
problem, not this one. The red team's other option needs no migration: `emit()` picks its head
`WHERE hash IS NOT NULL`, and `verify_chain` still reports the stray row. It was not chosen this round because the
human kept C3 for the migration. Whichever fix lands, `verify_chain` must keep reporting the stray row.

**Until then.** Each failed emit leaves a gap line in `$TANISHI_HOME/event_gaps.jsonl`, and tool calls fail closed, so
the effect is visible (tools refuse with "Event log unavailable"), not silent.
