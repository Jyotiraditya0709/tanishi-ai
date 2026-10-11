# L1 · repair round conflicts and readings (2026-10-12)

No card item conflicted with a test: all 24 red-team proofs and the 46-test exam pass unchanged. These are the places
where the card could not be built as written, or where I had to choose a reading. Each needs a human yes or no.

1. **R8 "inside one transaction" is a lock, not one SQL transaction.** CS2's `emit()` and CS3's `add_belief()` each
   open their own connection and `BEGIN IMMEDIATE`; neither accepts a caller's connection, and both files belong to
   other nodes. So "find or record" and "check for an earlier landing, land, log" are each one critical section under
   `experience.attempt_lock()`: a process-wide thread lock plus an `flock` on `<core_state.db>.compiler.lock`. That
   serialises every compiler that shares the db (threads and processes); it does not make a crash atomic. A crash
   between a belief landing and its `compiler_emit` leaves a belief that a re-compile will land again, because the
   duplicate check reads `compiler_emit`. Real fix: a `conn=` parameter on `emit()` and `add_belief()` (CS2/CS3 card).
2. **SB1's interface is a guess.** SB1 has not landed. `passes._sandbox()` imports `tanishi.sandbox` and uses
   `run_python(script, *, stdin, timeout_s) -> (returncode, stdout)` (raising `TimeoutError`) if it exists; otherwise
   it warns and uses the isolated interpreter. SB1's implementer should provide that function or change `_sandbox()`.
   A sandbox that raises is a refusal, never a pass.
3. **"The pass marker is a per-run nonce the candidate cannot read"** cannot be guaranteed inside one process. The
   nonce lives only in the driver's locals, and an audit hook refuses every route to them I know of in CPython 3.12
   (frames, `gc`, tracers, `ctypes`, new code objects, rewriting a function's `__code__`, `/proc`). That is defence in
   depth; only SB1 (or a verdict computed in a process the candidate never entered) makes it a guarantee.
4. **"Actor" in the attempt identity** is read as the writer of the record (`compiler.experience`), as the red-team
   fix proposed: the Experience has no field naming the agent that acted. If the human meant the acting agent, the
   Arena must add it to `context` and `attempt_key()` must include it.
5. **A non-task prediction refuses the experience** rather than compiling it without resolving. The card says only
   task predictions are resolved; surprise against a tool forecast is not the task's surprise, so landing it would put
   a wrong gain in front of OBS3. Refused with `compiler_refused {prediction_id, reason}`; the row stays open.
6. **Sealed tiers are known only from `context["tier"]`** (`frontier`, `sealed`, `sealed_vault`, any case, `-` or space
   for `_`). The Arena must set it when it builds an Experience; one without it is treated as Practice and its
   verifier reason is logged. Only `verifier_result` is reduced to `{"passed"}`; `outcome` is the agent's own claim.
7. **R4 with a 3000-character tool name.** "Clip first, then check" alone makes that candidate a refusal (its code no
   longer parses), but the proof requires it to land. The candidate now matches the tool by digest and keeps a 120-char
   label, so its code is short whatever the tool name is. A future pass emitting long code still needs its own store.
8. **The red-team file had no xfail marks** (its tests simply failed), so there were no marks to remove.
