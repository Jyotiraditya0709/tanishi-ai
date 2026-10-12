# L1 exam · where the spec is silent

The exam (`tests/compiler/test_compiler.py`) was written from the L1 spec and the decisions of CS2, CS3, CS5, AR1 and
OBS3 alone, with no compiler code read. The spec names two files and no functions, so the exam pins a surface.
If the implementer disagrees, say so here and let the human decide; "the test wins" unless the human rules otherwise.
The surface is listed in the test file's docstring. Readings that are choices, not spec:

1. **Experience** is a dataclass with keyword fields `task, context, actions, outcome, verifier_result, cost,
   prediction`, equality by value, `to_dict()` / `from_dict()` (JSON-safe round trip). `prediction` is a dict
   `{"id", "expected": {"success": bool}, "confidence"}` written through the CS5 ledger before acting. A missing or
   None prediction, a blank task, non-list actions, or a None outcome/verifier_result raises ValueError or TypeError.
2. **Action** dicts carry `tool`, `input`, `output`, `ok` (bool). **verifier_result** carries `passed`, `score`, `reason`.
3. **Success is the verifier's `passed`**, not `outcome["success"]` (the agent does not grade itself).
4. **Surprise is the CS5 Brier score** of the prediction against the verified outcome (decision 0014 item 1).
5. **Decisive step** is the index of the first action with `ok == False` on a failure; `None` when there are no actions.
6. **`record(exp)`** writes one CS2 event of kind `experience` (redacted by `emit()`). `compile_experience` records the
   experience itself if the caller has not; the exam allows at most one extra record when the caller already did
   (the Experience has no event-id field in this pinned shape).
7. **compile_experience resolves the prediction** with `{"success": passed}` exactly once, and tolerates one that is
   already resolved.
8. **Registry** is `Registry().register(name, version, run, check)`; versions are ints >= 1; duplicate (name, version)
   is a ValueError; `get(name)` is the latest. Default names are `pass1`, `pass3`, `pass4`, emitting kinds `outcome`,
   `belief`, `skill_candidate`.
9. **run/check shapes.** `run(exp)` returns a list of `{"kind", "payload", "gain"}` or None. `check(exp, item)` returns
   `(ok, reason)`. A raising run or check is a refusal, not a crash, and does not stop other passes.
10. **Gain** is supplied by the pass (finite real, not bool); **compute** is measured by the compiler in seconds.
    Each emission, landed or refused, logs a `compiler_emit` event `{pass, version, gain, compute, landed}`.
11. **Landing.** A `belief` item lands through `beliefs.add_belief` with `evidence_event_id` = the experience event, so
    `evidence_for` is non-empty. Confidence outside [0, 1] does not land. A skill candidate lands only when its code
    parses, its test is non-empty, and the test passes against the code in a clean interpreter (test source is exec'd
    after the code in one namespace; any `test_*` callables are then called).
12. **Pass 3 on a failed run** emits at least one belief that names the failing tool; **pass 4 on a failed run** emits at
    least one skill candidate. What a success emits is not pinned (a pass may emit nothing).
13. **Not covered:** how the skill candidate is later promoted (L2+), Arena evaluation of candidates, and any model call.
    Passes here are deterministic code; the exam would not accept an LLM-judged check.
