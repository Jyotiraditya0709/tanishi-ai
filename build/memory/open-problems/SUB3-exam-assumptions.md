# SUB3 · what the spec leaves open (written by the tester, from the spec alone)

The card gives `route(step, context) -> RouteDecision(model_id, provider, reason)` and five acceptance lines. It does not
name `RouteContext`'s fields, say how "a local model is up" is known, say what "wait or fail" looks like, or say how the
spend report is made. `Step` (SUB1, `tanishi/substrate/state.py`) has no tools or personal flag and is not in this node's
files, so those facts must arrive on `RouteContext`. The exam in `tests/substrate/test_router.py` assumes only the items
below. Each is a constant or helper at the top of that file. If the implementer disagrees, say so in
`build/memory/open-problems/SUB3-exam-conflicts.md` and the human decides; do not weaken the tests.

- **R1 · `RouteContext` fields**, all keyword, all optional: `needs_tools: bool = False`, `personal: bool = False`,
  `local_up: bool = True`, `personal_local_only: bool = True` (False = "rule switched off"). Defaults matter: omitting
  `personal_local_only` must mean the rule is ON. `RouteContext` and `RouteDecision` live in `tanishi/substrate/router.py`.
- **R2 · Local vs cloud is by model id.** `OLLAMA_DEFAULT` is the only local model and has no tool support. Every
  `CLAUDE_*` in `models.py` is paid cloud and supports tools. A decision's `model_id` must be one of those four.
- **R3 · Wait or fail** = `route()` raises any `Exception`. A returned decision for a model that must not be used fails
  the test. A router that wants a "wait" result object instead needs the human to change `outcome()` in the test file.
  A refusal must also write an event (acceptance line 3, "every decision").
- **R4 · Events.** Kind and payload keys are free. The exam only requires a new `core_state.events` row per call whose
  payload contains `decision.reason` and `decision.model_id`, and that the chain still verifies. Secrets in the step
  text must not appear in the event.
- **R5 · Spend.** The router reaches the Warden through `tanishi.guard.client.check` (what `check_spend` and
  `record_spend` call), so the exam patches that one function. Estimate must be a finite number >= 0 and must grow
  with the step's text length (the only size signal the spec gives). No spend call at all when the choice is local,
  when the step is personal with the rule on, or when the switch is off. A Warden that raises counts as deny.
- **R6 · The switch.** `TANISHI_GUARD` is read once at import by `tools/registry.py` (`_GUARD_ON`). The exam sets the
  env var and also `registry._GUARD_ON` and `router._GUARD_ON` (the last only if it exists). A router that reads
  the env var per call, or reuses `registry._GUARD_ON`, passes; one with its own import-time copy under another name
  does not, and should use `_GUARD_ON`.
- **R7 · Reporting actual cost.** The spec says "after the call it reports `record_spend(actual)`" but the router
  only routes. The exam assumes `router.report_spend(decision, actual_cost)`, which calls `guard.record_spend` for a
  paid decision when the switch is on, and does nothing for a local decision or when the switch is off. This is the
  weakest assumption; if the real design puts it elsewhere, move the three `_report` tests, do not drop them.
- **R8 · Deny on a tool step.** Local has no tools, so a tool step whose spend is denied must wait or fail, not run
  on the local model. Same for tools + personal with the rule on. The spec implies both from lines 1 and 2 together.
- **R9 · Pure.** The same step and context give the same model (checked in the property test).

## Not covered, and why

- **Choosing between SIMPLE and COMPLEX.** The spec only says "by need". Any `CLAUDE_*` passes.
- **Probing whether Ollama is really up.** `local_up` is injected; a live probe needs a network and belongs to the wiring node.
- **Wiring into `TanishiBrain.think()`.** SUB4 / the legacy brain, not this node.
- **Cost accuracy.** No price table in the spec; only shape and monotonic growth are tested.
