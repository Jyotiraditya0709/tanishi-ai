# SUB2 · red-team breaks (2026-10-11), not yet fixed

Details and fixes: `build/memory/runs/redteam-SUB2-20261011.md`. Tests: `tests/substrate/test_loop_redteam.py`
(`xfail(strict=True)`; remove the mark with the fix).

- B1 (high): `RecursionError` from `json.loads` on a deeply nested reply escapes `run()`.
- B2 (medium): an empty reply is `success=True`.
- B3 (medium): cancellation mid-tool leaves no `loop_stop` and an unscored prediction.
- B6 (medium): recall uses the whole task as a LIKE pattern; the real `MemoryManager` returns nothing.
- B7 (medium): flag off still imports the substrate on every `think()`.
- B8 (medium): a bad program file raises out of `think()` with the flag on.
- B9 (medium): flag on drops matching skills and the dream-cycle block from the system prompt.
- B4 (low): upper-case ```` ```JSON ```` fence is shown as the answer with `success=True`.
- B5 (low): a JSON answer with an `"action"` key is discarded as malformed.

Do not turn `TANISHI_SUBSTRATE` on for the Arena A/B before B1, B2, B6 and B9 are fixed: B2 inflates the score, B6 and
B9 make flag-on a different, weaker brain.
