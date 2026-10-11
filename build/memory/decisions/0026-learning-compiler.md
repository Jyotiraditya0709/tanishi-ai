# 0024 · Learning Compiler semantics (L1)

Date: 2026-10-11. Decided by: the L1 implementer, within the spec and the readings pinned by the L1 exam
(`open-problems/L1-exam-assumptions.md`, all accepted as written). Revisit when L2 adds pass 2 or passes start calling a model.

1. **One record per attempt.** `compile_experience` looks for an `experience` event whose `prediction.id` matches and reuses
   it; it records only when none exists. The CS5 prediction id is unique per attempt, so it is the attempt's identity.
2. **Order.** Record, then resolve the prediction (`AlreadyResolvedError` ignored, unknown id only warns), then run the
   latest version of each pass in registration order. Older versions stay registered for replay, but do not run.
3. **The landing law, in order:** item is a dict with a str `kind` → `gain` is a finite real, not bool → `check` returns a
   2-tuple whose first element `is True` → the kind's lander succeeds. Any exception along the way is a refusal.
   A raising or non-list `run` emits nothing and writes `compiler_pass_failed {pass, version, error: <type name>,
   experience_event_id}`.
4. **Landers.** `belief` → `beliefs.add_belief(..., source="compiler:<pass>@v<n>", evidence_event_id=<experience
   event>)`; confidence outside [0, 1] is refused, not clamped. Every other kind (`outcome`, `skill_candidate`, ...) lands as
   its `compiler_emit` event, which then carries `payload`. A landed belief's event carries `belief_id` instead.
5. **`compiler_emit`** = `{pass, version, kind, gain, compute, landed, reason, experience_event_id}` (+ `payload` or
   `belief_id`). Written for every item, landed or not; `gain` is None when it was not a finite real. Fails open (0009 item 7).
6. **Compute** is seconds: the whole pass's run time plus this item's check and landing time. Run time is not split
   between items, so per-item compute over-counts a pass that emits many items; sum per pass with care.
7. **Gain v1** for passes 1, 3 and 4 is the experience's surprise (Brier score): an expected outcome teaches less.
   It is an estimate, not a measured gain. Real gain is OBS3's job once candidates run in the Arena.
8. **Decisive step** on a failure with no failed action is the last action (the answer itself was wrong); None on success.
9. **Pass 3** emits `tool:<name> failed_with <redacted, whitespace-collapsed, 120-char output>` at confidence 0.6 per
   failed action, with `evidence: {step, tool, ok}`. Its check requires that step to exist in this experience, to have
   failed, and to be the belief's tool.
10. **Pass 4** emits one guard per failure (the first failed action): `seen_failing(tool, tool_input=None, output=None)`
    matching the tool plus the input's sha256 or the output signature. Inputs are kept as a digest, never as text.
    Its check runs code then test in `python -I`, temp cwd/HOME, env of PATH/HOME/TMPDIR/LANG only, 20 s timeout, and
    needs a per-run random marker printed after at least one `test_*` callable returned.
