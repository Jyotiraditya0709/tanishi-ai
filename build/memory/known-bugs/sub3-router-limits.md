# SUB3 · model router: known limits

- **The legacy brain still has the bug.** `TanishiBrain._select_model()` still reads `routing.LOCAL_FIRST` and sends
  short prompts that need tools (but miss its regex) to Ollama. The router fixes it only for callers that use it; the
  card lists only `router.py`, so the wiring belongs to the loop node that depends on SUB3 (SUB2/SUB4). Until then the
  reported bug is live in chat.
- **`needs_tools` and `personal` are trusted.** The router does not infer them from the text. A caller that forgets to
  set `personal=True` sends personal text to the cloud.
- **Estimate is rough.** List prices are hard-coded per model id and the output is assumed to be 1024 tokens. A long
  reply costs more than was checked; `report_spend` corrects the Warden's total afterwards, not the decision. The
  input side is only as good as `RouteContext.input_tokens`: a caller that leaves it None gets the step text alone,
  which misses the system prompt, memory and history. UTF-8 bytes // 4 is itself a rough guess per language.
- **`route()` blocks.** With the gate on it waits on the Warden's socket. Async code must call `aroute()`.
- **Check and record are separate.** Two concurrent steps can both pass `check_spend` just under the cap and together
  exceed it. Fixing that needs a reservation in the Warden, which is a protected path.
- **Reload replaces classes.** `importlib.reload(router)` (the exam does it) creates new `RouteRefused` and
  `RouteContext` classes; code holding the old ones by `from ... import` will not catch the new exception. Tests should
  use `router.RouteRefused`.
