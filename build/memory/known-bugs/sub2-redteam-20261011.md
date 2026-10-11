# SUB2 · red-team breaks (2026-10-11): all fixed in the repair round

Details: `build/memory/runs/redteam-SUB2-20261011.md`. Tests: `tests/substrate/test_loop_redteam.py`, xfail marks
removed with the fix (commit 6c73539). Repair notes: `build/memory/runs/SUB2-repair-20261011-2347.md`.

- B1 (high), fixed: `parse_reply` only tries JSON on a body that starts with `{`, is at most 100 000 chars and nests
  at most 32 deep, and catches `RecursionError`. `go()` also turns any loop bug into `internal_error`, never a raise.
- B2, fixed: a blank or non-text reply stops with `empty_reply`, `success=False`.
- B3, fixed: cancellation writes `loop_stop` (`cancelled`), scores the interrupted act `{"success": false}`, re-raises.
- B6, fixed: recall searches the whole task, then up to 6 key terms; hits found by more queries rank first.
- B7, fixed: `brain._substrate_enabled()` reads the env var; the substrate is imported only on the flag-on path.
- B8, fixed: a loop that cannot be built falls back to `_think` and writes `loop_fallback {"reason"}`.
- B9, fixed: both paths build the system prompt with `TanishiBrain._system_prompt()` (skills, dream block, extra).
- B4, fixed: ```` ``` ````, ```` ```json ````, ```` ```JSON ```` fences all parse; other tags are not actions.
- B5, fixed: only `{"action": {"tool", "input"}}` with exactly those keys, naming a listed tool, is an act.

Still true (not a red-team break): the Arena A/B has not run. Do not turn `TANISHI_SUBSTRATE` on before it does.
