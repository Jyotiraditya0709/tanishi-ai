# SUB3 · implementer run, 2026-10-10

Built `tanishi/substrate/router.py` and `tests/substrate/test_router_impl.py` (15 tests). The tester's exam
(`tests/substrate/test_router.py`, 47 tests) passes unchanged; full suite 1910 passed, 1 skipped, 5 xfailed. Decision 0020,
known limits in `known-bugs/sub3-router-limits.md`. No benchmark: the router is fixed rules, not learned behaviour; the
exam's 300-case property test is its evaluation.

1. **What did we learn?** The exam's assumptions (R1–R9) were all workable as written, so no conflicts file was needed.
   With one local model that has no tools, "personal" plus "tools" can never be served, and a spend deny on a tool
   step can only wait. The router has to be able to refuse, and `RouteRefused.wait` tells the caller which kind of
   refusal it got.
2. **What failed, and why?** (a) The provider name `"ollama"` failed the exam's model-literal scan because it contains
   "llama", so it is now `"local"`. (b) My own tests passed alone but failed after the exam, which `importlib.reload`s
   the router and so replaces its classes. Fixed by reading `router.X` at call time.
3. **Which assumption was wrong?** That writing the router fixes the reported bug. It does not until the legacy brain
   or the new loop calls `route()`. `brain.py` is outside this card, so the bug stays live (known-bugs).
4. **What should the next agent know?** Wire `route()` in the loop node: build `RouteContext` from the step (tools
   needed? personal?) and from a real Ollama probe, catch `RouteRefused` (wait vs fail), and call
   `report_spend(decision, actual)` after every paid call. Do not reintroduce `LOCAL_FIRST`. Fake the Warden by
   patching `tanishi.guard.client.check`.
