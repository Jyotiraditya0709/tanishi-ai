# SUB3 · red-team report · 2026-10-10

Tests: `tests/substrate/test_router_redteam.py` (9 xfail-strict breaks, 6 passing attacks that held). No product code changed.

## Breaks

| # | Severity | Break | Smallest fix |
|---|----------|-------|--------------|
| B2 | **High** (privacy) | `personal_local_only=None`, `0`, `""` or `[]` is falsy, so the rule is OFF and a personal step goes to the cloud. An unset config value switches the privacy rule off silently. | In `route()` use `context.personal_local_only is False` as the only way to switch off; anything else is on. Validate the type in `RouteContext.__post_init__`. |
| B4 | **Medium** (cost) | `report_spend` returns the Warden's `Decision` and nothing else. The client fails closed by *returning* deny (Warden down), not raising, so a lost `record_spend` leaves no event, no log, no exception, and the daily cap undercounts. The doc says "does not catch Warden errors", but there are none to catch. Successful reports are not in the log either. | Emit a `model_spend` event (model, actual cost, Warden answer) in `report_spend`; log a warning or raise when the answer is not allow. |
| B1 | **Medium** (perf) | `route()` makes a blocking Unix-socket call (5 s timeout) on the caller's thread. `ToolRegistry.execute()` was fixed to use `asyncio.to_thread` for exactly this; the first async caller of `route()` freezes the runtime when the Warden hangs. | Add `async def aroute(step, context)` that runs `route` in `asyncio.to_thread`; wiring nodes must use it. |
| B3 | **Medium** (cost/gate) | `len(text)//4` assumes ASCII. 4000 CJK chars are estimated at 1000 tokens, so the Warden is asked about a fraction of the cost and a long non-ASCII step gets the simple model. | `max(1, len(text.encode("utf-8")) // 4)`. |
| B5 | **Low** | `from routing import COMPLEXITY_THRESHOLD_TOKENS` copies the value at import. `routing.py` says "mutated by autoresearch", so a mutation is never seen. | `from tanishi.config import routing` and read `routing.COMPLEXITY_THRESHOLD_TOKENS` per call. |

## Not tested, written up

- **The estimate ignores everything but `step.description`.** The real call carries the system prompt, memory context and
  history, none of which the router sees, so the spend check is on a lower number than the call. `RouteContext` needs an
  `input_tokens` (or `context_chars`) field that the caller fills. Extends the known "estimate is rough" limit.
- **The off switch is a per-call flag**, so any caller can disable the privacy rule for one step. It should come from one
  user-level setting read in one place, not be passed around.
- **`ask` from the Warden is treated as deny**: a non-tool, non-personal step silently runs on the weak local model and no
  approval is ever requested. Decision 0020 chose this; flagging that nothing tells the user.
- **`route()` does not look at `step.status`**: a DONE step routed again spends again. Caller's job, but nothing says so.
- `TANISHI_GUARD` typos ("enabled") read as off, so spend is ungated. Same as the tool gate; one parser would be better.

## What I tried that held

Secret in `step.id` and in the Warden's reason (redacted in the event, exception message is the raw reason but events are
clean); step text never in the event; `ask`/`deny`/raising Warden on a tool step waits, never goes local; personal with
the rule on never calls the Warden; non-str text raises before any Warden call; refusals write an event.
