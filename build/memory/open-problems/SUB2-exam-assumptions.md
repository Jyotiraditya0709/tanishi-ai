# SUB2 · what the spec leaves open (written by the tester, from the spec alone)

The card gives `CognitiveLoop(router, memory, tools, budget: StepBudget).run(task) -> Result`, a flag, and four
acceptance lines. It does not define `StepBudget`, `Result`, how the loop calls a model, how a model asks for a tool, or
what the "program" file looks like. The exam in `tests/substrate/test_loop.py` assumes only the items below. Each is a
constant or a helper at the top of that file. If the implementer disagrees, say so in
`build/memory/open-problems/SUB2-exam-conflicts.md` and the human decides; do not weaken the tests.

- **L1 · Names.** `tanishi/substrate/loop.py` exports `CognitiveLoop`, `StepBudget`, `Result`, `enabled()`,
  `PROGRAM_PATH` and `load_program()`.
- **L2 · `StepBudget(max_steps: int)`.** Keyword `max_steps`, a non-negative int; a negative value raises.
  A "step" is one pass through the program's phases that may act: the exam only requires that `max_steps` bounds
  tool executions and `Result.steps`, never that one step equals one phase.
- **L3 · `Result`** has `content: str`, `success: bool`, `stop_reason: str`, `steps: int`. `stop_reason` is the
  exported string `STOP_BUDGET = "budget"` when the budget ran out, and some other non-empty string otherwise.
  Budget exhaustion is `success=False`.
- **L4 · `run` is a coroutine** (`await loop.run(task)`), `task` is a `str`. It returns a `Result` rather than raising
  when the router refuses (`RouteRefused`), with `success=False` and a stop reason that is not the budget one.
- **L5 · Model calls.** The card's constructor has no model client, so the exam passes a keyword-only
  `generate=` async callable: `await generate(decision, prompt: str) -> str`, where `decision` is the `RouteDecision`
  the router returned for that step. The router is any object with `route(step, context)` (the SUB3 module qualifies).
- **L6 · How a model asks to act.** `generate` returns a JSON object text `{"action": {"tool": <name>, "input": {...}}}`
  to act. Any other text is the final answer, and ends the loop. The exam builds both through two helpers.
- **L7 · Tools** are any object with `async execute(tool_name, tool_input, ...) -> ToolResult` (as `ToolRegistry`).
- **L8 · Act = predict, then call, then resolve.** Before `tools.execute`, `predictions.predict(about, expected,
  confidence)` has been called with `about` containing the tool name; the `prediction` event exists then. After the
  call the prediction is resolved with `actual["success"]` = the tool's `success`. At least one further event (any
  kind other than the two ledger kinds) whose payload names the tool is written for the act step.
- **L9 · Memory** is any object with `recall(query, limit)` or `search(query, top_k)` (as `MemoryManager`), returning
  objects with `.content`. Recalled content reaches a later prompt. A memory that raises is survived (fail open, as the
  event log is, decision 0008 item 6): the loop goes on without it.
- **L10 · The program.** `loop.PROGRAM_PATH` is a `pathlib.Path` to a git-tracked JSON file read at construction
  or run time (not import time), shaped `{"version": <int|str>, "steps": [{"name": <str>, "budget": <int >= 0>}, ...]}`.
  The default names are, in order: attend, recall, deliberate, predict, act, reflect. `load_program()` returns that
  dict. A program with no `recall` never calls memory; one with no `act` never executes a tool.
- **L11 · Phase events.** Each phase that runs writes an event whose payload has `"step": <phase name>`. The exam checks
  only the order in which phases first appear against the program's order.
- **L12 · Delegation.** With `TANISHI_SUBSTRATE=1`, `TanishiBrain.think()` awaits `CognitiveLoop.run` and returns a
  `BrainResponse` whose `content` is `Result.content`; the legacy model path is not called. `task_start` and `task_end`
  events are still written. With the flag off the loop is never constructed or run.

## Not covered, and why

- **Per-phase budgets in the program.** The card says "with what budgets" but not the unit or what exhausting one does.
  The exam checks only that each is a non-negative int.
- **Real model quality or Practice scores with the flag on.** That is the Arena's job, once the loop is wired.
- **Whether a ledger failure blocks the act.** "Writes a prediction first" could mean refuse to act without one, or
  fail open as the tool gate's ledger does (decision 0014 item 8). Decide, then add the test.
- **Double predictions with the real `ToolRegistry`.** It also writes a `tool:<name>` prediction (CS5). The exam uses a
  fake `tools`, so it cannot tell whether the loop should skip its own prediction there.
- **The Practice regression itself.** The exam proves the flag-off path never touches the loop; the Practice tasks
  already run in CI with the flag unset and must keep their scores.
