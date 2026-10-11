# 0024 · Reasoning loop: program, steps, acts, stop reasons (SUB2)

Date: 2026-10-11. Decided by: the SUB2 implementer, adopting the exam's assumptions L1-L12
(`open-problems/SUB2-exam-assumptions.md`) and settling the questions it left open.

1. **Program file.** `tanishi/substrate/loop_program.json`, `{"version", "steps": [{"name", "budget"}]}`, shipped
   via `pyproject.toml` package-data. Read when a `CognitiveLoop` is built (`load_program()`), never at import.
   Phases are `attend, recall, deliberate, predict, act, reflect`; an unknown or repeated phase, a bool or blank
   version, or a budget that is not an int >= 0 is refused at construction. Change the program by committing a new
   file with a new `version`; `loop_start` logs the version so Arena results can be tied to it.
2. **Per-phase budget = how many times that phase may run in one task.** A phase "runs" only when it has work (predict
   and act only with a pending action, reflect only after an act or an answer). 0 means it never runs. Default:
   recall 1 (memory once per task), every other phase 32. When no progress is possible because deliberate or act is
   used up, the loop stops with `phase_budget`.
3. **Step = one pass through the program.** `StepBudget(max_steps)` bounds passes; a pass acts at most once, so it
   bounds tool calls. `brain` uses `max_tool_loops` (10) as `max_steps`.
4. **Stop reasons:** `answered` (the only success), `budget`, `phase_budget`, `stalled` (no progress otherwise, e.g.
   the model asks to act and the program has no act), `route_refused`, `route_error`, `model_error`, `unrecorded`.
   The loop never raises for these; it returns a `Result` with `detail`.
5. **Acts fail closed.** The loop writes its own prediction (`about = "act:<tool>"`, expected `{"success": true}`,
   confidence = CS5 `tool_forecast`) and a `loop_step {"step": "act", "tool"}` event before `tools.execute()`. If
   either write fails, the tool does not run (`unrecorded`). An act that never reached the tool leaves its prediction
   unresolved, as decision 0014 item 8. The act phase writes the prediction itself if the program skipped predict or
   put it after act, so "prediction first" holds for any program. Predict does not run if act cannot run.
6. **Two predictions per act with the real registry, on purpose.** `ToolRegistry.execute()` still writes
   `tool:<name>` (the tool's reliability); the loop writes `act:<name>` (the loop's own forecast for its act).
   Different prefixes keep both calibrations clean. Do not merge them without changing CS5's `tool_forecast`.
7. **Other phase events fail open** (decision 0008 item 6). Events carry no task text, prompt, memory or tool input;
   the router never logs step text either.
8. **Model seam.** The model is called through a keyword-only `generate(decision, prompt) -> str` (exam L5). The brain
   supplies one that uses the routed `model_id` for Claude and `_think_ollama` for the local provider, with the
   persona as the system prompt, and reports spend through `router.report_spend` with an estimate that errs high.
9. **Flag.** `enabled()` is `TANISHI_SUBSTRATE == "1"` (after strip), read per call. Flag off: `think()` runs `_think`
   exactly as before and never builds a loop.

## Repair round (2026-10-11, human decisions on the red team's breaks and open questions)

10. **Never raises.** Stop reasons gain `empty_reply` (blank or non-text reply), `timeout`, `cancelled` and
    `internal_error` (a bug in the loop). Every run writes `loop_stop`. Cancellation is recorded and re-raised.
    On `cancelled`, `timeout` and `internal_error` an act already predicted is resolved `{"success": false}`;
    on `unrecorded` it stays unresolved (item 5).
11. **Replies.** An act is exactly `{"action": {"tool": <listed tool>, "input": {...}}}`, bare or in a ```` ``` ```` /
    ```` ```json ```` fence (any case), at most 100 000 chars and 32 deep. Anything else non-blank is the answer.
    The old "malformed action, try again" note is gone.
12. **Program v2** adds optional `wall_time_s` (a finite number > 0; default 300 when absent): the per-task wall-clock
    budget.
13. **Final answer-only step.** When the step budget is spent and the last pass acted, the model gets one more
    deliberate with no tools (`needs_tools=False`). It does not count in `steps`; an act asked for there is not run
    and the loop stops with `budget`.
14. **Personal is sticky.** Memories from the loop's recall, or a tool result with `personal=True`, make every later
    deliberate of the task `personal=True` in its `RouteContext`.
15. **Calibration.** `act:` predictions carry `expected.capability = "planning"`; the registry's `tool:` predictions
    are unchanged. Item 6 still holds; the two no longer share a capability.
16. **Brain.** The flag is read without importing the substrate. Both paths build the system prompt with
    `_system_prompt()`. A loop that cannot be built (bad or missing program, broken import) falls back to `_think`
    for that call and writes `loop_fallback {"reason"}`; a loop that was built never falls back, since it may already
    have acted.
