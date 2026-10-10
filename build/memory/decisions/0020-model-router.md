# 0020 · Model router: rules, refusals, events, spend (SUB3)

Date: 2026-10-10. Decided by: the SUB3 implementer, adopting the exam's assumptions R1–R9
(`open-problems/SUB3-exam-assumptions.md`) without change. Revised the same day in the repair round (items 1, 2, 5, 7
and 8) on the human's decisions for red-team breaks B1–B5 (`runs/redteam-SUB3-20261010.md`).

1. **Context, not Step.** `needs_tools`, `personal`, `local_up`, `personal_local_only` (default True = rule on) and
   `input_tokens` (default None) live on `RouteContext`. `Step` is unchanged. Probing whether Ollama is up is the
   caller's job. **Only the bool `False` switches the personal rule off**; None, 0, "", [] or any other value mean on,
   so an unset config value can never send a personal step to the cloud (B2).
2. **Order.** Personal with the rule on goes local, or is refused (tools needed: fail; local down: wait). It never asks
   the Warden to spend. Every other step goes to `CLAUDE_SIMPLE`, or to `CLAUDE_COMPLEX` at or above
   `routing.COMPLEXITY_THRESHOLD_TOKENS`, read on every call so an autoresearch change is seen (B5). There is no
   local-first path: `LOCAL_FIRST` is ignored. Cost is controlled by the Warden's cap, not by guessing which steps are
   "chitchat". **Size** is `context.input_tokens` when the caller gives it (it should cover the system prompt, memory,
   history and the step); otherwise UTF-8 bytes of the step text // 4, so non-ASCII text is not undercounted (B3).
   One count drives both the size choice and the spend estimate.
3. **Spend gate.** With `_GUARD_ON` (TANISHI_GUARD, parsed as in `tools/registry.py`, read at import), the router calls
   `guard.check_spend(estimate)` before choosing a paid model. Only `allow` allows. `ask`, `deny` or an exception all
   count as not allowed: the step falls back to local if it needs no tools and local is up, otherwise it waits.
   The estimate uses rough list prices keyed by `models.*` ids plus 1024 output tokens; an unknown id is priced as the
   dearest.
4. **Refusal = `RouteRefused(reason, wait)`**, a `RuntimeError`. `wait=True` means it may work later (local back up,
   cap reset); `wait=False` means it never will under these rules (personal + tools).
5. **Events.** One `model_route` event per call, actor `router`, written before returning or raising. Payload:
   `outcome` (routed | wait | fail), `model_id`, `provider`, `reason`, `task_id`, the four context flags (the rule as
   the router read it), `guard_on`, `step_id`, `input_tokens` and `input_tokens_given`, and `spend` {model_id,
   estimate, allowed, reason} when a check ran. The step text is never logged. If the event cannot be written,
   `route()` raises (fail closed, like tool calls in 0008 item 11).
6. **Providers** are `"anthropic"` and `"local"` (not "ollama": the exam's literal scan rejects anything containing
   "llama").
7. **Reporting.** `report_spend(decision, actual_cost, step_id=None)` calls `guard.record_spend` for a paid decision
   with the gate on; it raises `ValueError` for a cost that is not a finite number >= 0. Every report writes one
   `model_spend` event (actor `router`): `model_id`, `provider`, `actual_cost`, `recorded`, `warden`, `warden_reason`,
   `step_id`, `task_id`; never step text. If the Warden's answer is not allow (the client fails closed by returning
   deny), or `record_spend` raises, it logs a warning and the event says `recorded: false`, so an undercounted day is
   visible (B4). It returns the answer (a deny for an exception) rather than raising, because the call is already paid.
   A local decision or the gate off is not a report: no Warden call, no event.
8. **Async callers use `aroute()`** (B1). It has the same contract as `route()` and runs it in `asyncio.to_thread`,
   as `ToolRegistry.execute()` does for the tool gate, so a slow Warden (5 s timeout) never blocks the event loop.
   The current task scope (a ContextVar) is carried into the thread. `route()` stays sync: the exam calls it directly.

## Open, for the human (not built)

- **A user-level privacy setting** instead of the per-call `personal_local_only` switch, read in one place, so no
  single caller can turn the rule off for one step.
- **Warden `ask` on a non-tool step** falls back to local today and nobody is asked. An approval flow comes later.
- **Routing a step whose status is done**: `route()` does not look at `step.status`, so a DONE step routed again
  spends again. Decide whether the router refuses it or the caller owns it.
- **A `TANISHI_GUARD` typo** ("enabled") reads as off, so spend is ungated. Same parser as the tool gate; one shared,
  stricter parser would be better.
