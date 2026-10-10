# OBS1 · red-team breaks (2026-10-10)

Full table with fixes: `build/memory/runs/redteam-OBS1-20261010.md`. Proofs: `tests/observability/test_redteam_obs1.py`
(xfail strict; remove the mark when fixed). All open, none fixed by the red team.

- [ ] R1 High: `load()` uses `splitlines()`; U+2028/U+2029/U+0085 in a value hides the span from the CLI.
- [ ] R5 High: short `os.write` glues the next record onto a half line; both lost.
- [ ] R6 Medium: one unserialisable attr (`10**5000`) drops the whole span and its event links.
- [ ] R2/R3 Medium: CLI prints name, model, tool names raw: newline forges tree lines, ESC sequences reach the terminal.
- [ ] R4 Medium: `tree()` recursion fails at ~1000 nested spans.
- [ ] R9 Medium: span opened before `task_scope` has null task_id and is invisible.
- [ ] R10 Medium: span held across a `yield` steals the consumer's events.
- [ ] R7 Low-Med: no per-record size cap (2.7 MB line from in-cap containers).
- [ ] R8 Low: `event_ids` cut at 200 with no marker.
- [ ] R11/R12 Low: int task_id not found by CLI; tool double-counted when caller and registry both report it.
- [ ] Spec gap: nothing in production calls `span()` yet (see obs1-trace-limits.md).
