# 0020 · Model router: rules, refusals, events, spend (SUB3)

Date: 2026-10-10. Decided by: the SUB3 implementer, adopting the exam's assumptions R1–R9
(`open-problems/SUB3-exam-assumptions.md`) without change.

1. **Context, not Step.** `needs_tools`, `personal`, `local_up`, `personal_local_only` (default True = rule on) live on
   `RouteContext`. `Step` is unchanged. Probing whether Ollama is up is the caller's job.
2. **Order.** Personal with the rule on goes local, or is refused (tools needed: fail; local down: wait). It never asks
   the Warden to spend. Every other step goes to `CLAUDE_SIMPLE`, or to `CLAUDE_COMPLEX` at or above
   `routing.COMPLEXITY_THRESHOLD_TOKENS` (len/4 tokens). There is no local-first path: `LOCAL_FIRST` is ignored.
   Cost is controlled by the Warden's cap, not by guessing which steps are "chitchat".
3. **Spend gate.** With `_GUARD_ON` (TANISHI_GUARD, parsed as in `tools/registry.py`, read at import), the router calls
   `guard.check_spend(estimate)` before choosing a paid model. Only `allow` allows. `ask`, `deny` or an exception all
   count as not allowed: the step falls back to local if it needs no tools and local is up, otherwise it waits.
   The estimate uses rough list prices keyed by `models.*` ids plus 1024 output tokens; an unknown id is priced as the
   dearest.
4. **Refusal = `RouteRefused(reason, wait)`**, a `RuntimeError`. `wait=True` means it may work later (local back up,
   cap reset); `wait=False` means it never will under these rules (personal + tools).
5. **Events.** One `model_route` event per call, actor `router`, written before returning or raising. Payload:
   `outcome` (routed | wait | fail), `model_id`, `provider`, `reason`, `task_id`, the four context flags, `guard_on`,
   `step_id`, and `spend` {model_id, estimate, allowed, reason} when a check ran. The step text is never logged. If the
   event cannot be written, `route()` raises (fail closed, like tool calls in 0008 item 11).
6. **Providers** are `"anthropic"` and `"local"` (not "ollama": the exam's literal scan rejects anything containing
   "llama").
7. **Reporting.** `report_spend(decision, actual_cost)` calls `guard.record_spend` for a paid decision with the gate
   on; it raises `ValueError` for a cost that is not a finite number >= 0 and does not catch Warden errors.
