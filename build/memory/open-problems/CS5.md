# CS5 · the ledger cannot prove a prediction came before its action

Recorded 2026-10-09 in the CS5 repair round (human decision, from red-team design point). **Read this before AR2,
AR3 or the cognitive loop (SUB2) reads `calibration()`.**

## The problem

`predict()` and `resolve()` are public. Any caller can predict at confidence 1.0 and resolve it a microsecond later
with the outcome it already knows, for a Brier score of 0. Nothing in a `predictions` row ties it to an action:

- no link from a prediction to the `tool_call` event it was made for, so "predicted before acting" is not checkable,
- no link from `actual` to a `tool_result` event, so "scored by reality" is not checkable either.

Today the only writer is `ToolRegistry.execute()`, which does predict before the `tool_call` event and resolve from
the handler's result, so today's numbers are honest. That is a property of one caller, not of the ledger.

## The rule until it is fixed

**`calibration()` must not be used as an Arena score, or as any score an agent or evolved program can raise.**
A score that the scored party can write both halves of will be gamed by the first optimiser that finds it (Goodhart).
It is fine as a diagnostic for humans and for the Self Model's day-one forecaster.

- **AR2 / AR3 (Arena scoring):** do not read `predictions` or `calibration()` for a score. If calibration must be
  scored, compute it from the event log: pair each `prediction` event with the `tool_call` that follows it (same
  `call_id`) and the matching `tool_result`, and use the result event's `success`, not the row's `actual`.
- **SUB2 (cognitive loop):** the loop's "predict" step must write the prediction before its act step and pass the
  same `call_id` the act's `tool_call` event will carry. Do not resolve a loop prediction from the loop's own view
  of what happened; resolve it from the tool result.

## What I would do

1. Add `call_id` to `predict()` (optional argument) and to the `prediction` event payload. `execute()` already
   creates the `call_id` before predicting, so it is a one-line pass-through there.
2. A scorer (Arena side, not the ledger) accepts a prediction only if its `prediction` event precedes a `tool_call`
   with the same `call_id` in the hash-chained log, and takes the outcome from that call's `tool_result`.
3. Optional, later migration: a `call_id` column so the check does not need a log scan.

Not done in this round: the card asks for the note, not the change, and it touches the CS5 interface.

## Related

- Calls that never reached the tool (denied, no approval callback, no `tool_call` event, cancelled) now leave the
  prediction **unresolved** (repair R1/R2). The stale helper lists them after 24 h. A later `void` state (resolved,
  no score, with a reason) would separate "forgotten" from "never ran"; it needs a migration and an interface change.
- Approved calls still measure latency from before the approval prompt, so a slow human inflates `latency_ms`.
