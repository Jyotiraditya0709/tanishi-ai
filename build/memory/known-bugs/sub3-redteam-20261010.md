# SUB3 · red-team breaks (2026-10-10)

Details: `build/memory/runs/redteam-SUB3-20261010.md`. Tests: `tests/substrate/test_router_redteam.py`.

## Fixed in the repair round (2026-10-10), xfail marks removed, decision 0020 updated

- B2 (high): falsy non-False `personal_local_only` switched the personal rule off. Now only the bool `False` does.
- B4 (medium): `report_spend` left no trace. Now every report is a `model_spend` event; a report the Warden did not
  take logs a warning and the event says `recorded: false`.
- B1 (medium): no async entry. `aroute()` runs `route()` in `asyncio.to_thread`.
- B3 (medium): `estimate_tokens` counted characters. Now UTF-8 bytes // 4 (`surrogatepass`, so a lone surrogate
  cannot crash it).
- B5 (low): `COMPLEXITY_THRESHOLD_TOKENS` copied at import. Now `routing.COMPLEXITY_THRESHOLD_TOKENS` per call.
- Written up, not tested: the estimate saw only the step text. `RouteContext.input_tokens` (optional) now lets the
  caller give the whole prompt's size; until a caller fills it, the estimate is still the step text only.

## Still open (human decisions, listed in decision 0020 "Open")

- The off switch is per call, not a user-level setting.
- Warden `ask` on a non-tool step falls back to local; nobody is asked.
- `route()` ignores `step.status`; a DONE step routed again spends again.
- `TANISHI_GUARD` typos read as off.
