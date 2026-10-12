# SUB2 · reasoning loop: known limits (not fixed)

- **No flag-on quality number yet.** Nothing has run the loop against real models on the Practice set. The loop is
  only proven correct in structure (budgets, ledger, events). The Arena A/B (flag on vs off, 3 seeds, OBS3 rules)
  must run before anyone turns the flag on.
- **Spend is estimated, not measured.** The brain's `generate` reports `estimate_cost(model, prompt, input_tokens)`,
  which assumes a full 1024-token reply. It errs high on purpose, so the Warden's cap trips early, not late.
- **One action per model reply.** Claude's native multi-tool `tool_use` is not used; the model must reply with exactly
  one JSON object `{"action": {"tool", "input"}}`. Prose around it, a missing `input`, an extra key or an unlisted
  tool name make the reply the final answer (decision B5), so a sloppy model's action JSON can be shown to the user.
- **"Listed tool" is only checked when the tool set lists tools** (`get_claude_tools()` non-empty). An empty
  registry, or a fake that lists none (the exam's), accepts any name; the registry then refuses unknown tools.
- **Recall is keyword search** over the task and up to 6 key terms (English stop words dropped), once per task with
  the default program. It is not re-queried with tool results, and `MemoryManager.search` is still `LIKE`.
- **Personal is sticky only for what the loop adds.** Memories from its own recall and tool results with
  `personal=True` mark the task personal. The dream block in the system prompt (shared with legacy, B9) does not;
  `ToolResult` has no `personal` field yet, and there is no Twin data yet. See `open-problems/SUB2-repair-conflicts.md`.
- **The wall-time budget cannot stop a blocking call.** `asyncio.timeout` cancels the await; a model call in
  `asyncio.to_thread` keeps running in its thread (and may still be billed) after the loop has stopped.
- **The final answer-only step is not a step.** `Result.steps` counts tool passes (<= `max_steps`, as the exam
  requires); the extra deliberate shows in `loop_step` events with `final: true`.
- **Canvas frames** (`emit_canvas`) are not collected on the loop path; the legacy path still does it.
- **The prompt grows only through observations** (last 6 shown, 2000 chars each) and memories (500 chars each);
  `context` (conversation history) is passed whole and not clipped.
