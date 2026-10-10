# SUB2 · implementer · 2026-10-11

Built `tanishi/substrate/loop.py`, `loop_program.json` (v1), the `think()` delegation and
`tests/substrate/test_loop_impl.py` (28 tests). Exam: 35/36 pass; the 36th fails on its own helper
(`open-problems/SUB2-exam-conflicts.md`). Decision 0024, limits in `known-bugs/sub2-loop-limits.md`.

1. **What did we learn?** "Every act writes a prediction first" only holds for *any* program if the act phase owns
   the guarantee. A separate predict phase can be reordered, removed or out of budget by evolution, so act predicts
   itself when predict did not. Likewise predict must not run when act cannot, or the ledger fills with forecasts for
   acts that never happen (the exam caught this in my first version).
2. **What failed, and why?** My first predict phase predicted whenever an action was pending, even with no act phase
   in the program: `test_a_program_without_act_never_executes_a_tool` failed on a stray prediction. The exam's
   property test fails before the loop runs because its ledger helper reads an unmigrated db.
3. **Which assumption was wrong?** That a per-phase "budget" has an obvious unit. It does not; I chose "times this
   phase may run per task" (0 = never), one unit for every phase, so evolution can tune them without new semantics.
   The tester's guess that L5 (`generate=`) would be contradicted was wrong: the router picks only a model id, so a
   separate model-call seam was the right shape.
4. **What should the next agent know?**
   - The flag is off by default and nothing has measured the loop with real models. Run the Arena A/B before anyone
     sets `TANISHI_SUBSTRATE=1`.
   - With the real `ToolRegistry` each act writes two predictions, `act:<tool>` (loop) and `tool:<tool>`
     (registry). That is deliberate (decision 0024 item 6).
   - Acts fail closed on ledger or event failure (`unrecorded`); every other phase event fails open.
   - To evolve the loop, commit a new `loop_program.json` with a bumped `version`; `loop_start` logs it.
