# SUB2 · reasoning loop: known limits (not fixed)

- **No flag-on quality number yet.** Nothing has run the loop against real models on the Practice set. The loop is
  only proven correct in structure (budgets, ledger, events). The Arena A/B (flag on vs off, 3 seeds, OBS3 rules)
  must run before anyone turns the flag on.
- **Spend is estimated, not measured.** The brain's `generate` reports `estimate_cost(model, prompt, input_tokens)`,
  which assumes a full 1024-token reply. It errs high on purpose, so the Warden's cap trips early, not late.
- **One action per model reply.** Claude's native multi-tool `tool_use` is not used; the model must reply with one
  JSON object. Weaker models that wrap it in prose get it treated as a final answer (only bare or ```json-fenced
  JSON is parsed).
- **Recall uses the task text only**, once per task (default program). It is not re-queried with tool results.
- **Canvas frames** (`emit_canvas`) are not collected on the loop path; the legacy path still does it.
- **The prompt grows only through observations** (last 6 shown, 2000 chars each) and memories (500 chars each);
  `context` (conversation history) is passed whole and not clipped.
- **Exam property test** fails on an unmigrated db, not on the loop: `open-problems/SUB2-exam-conflicts.md`.
