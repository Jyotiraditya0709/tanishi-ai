# L1 · Learning Compiler: known limits (2026-10-11, revised in the repair round 2026-10-12)

None of these breaks an acceptance line.

- [ ] **Record and duplicate lookups are SQL scans with `json_extract`**, no index (fast at thousands of events; an
  index needs a migration in CS1's folder). `find_recorded` and `_landed_before` are the two queries.
- [x] **Skill tests are not sandboxed until SB1 lands.** Fixed in the SB1-L1 seam round (2026-10-12): skill tests run
  in `tanishi.sandbox.run_python` (Docker on macOS); `tests/compiler/test_skill_test_sandbox_seam.py` proves a host
  file is out of reach.
- [ ] **Template skill tests still run unsandboxed when the sandbox is unavailable** (macOS without Docker): pass 4's
  check falls back to `python -I`, a temp cwd/HOME, a stripped env and an audit hook, and logs
  `skill_test_unsandboxed {reason}`. The code there runs as this OS user and can read files and use the network; it
  is the compiler's own template, never model-written code (decision 0026, seam round).
- [ ] **A sandboxed skill test that times out is reported as "test failed"**, not "test timed out": `run_python`
  returns `timed_out=True` instead of raising `TimeoutError`, and `run_skill_test` reads only the pair. Both refuse.
- [ ] **Recording and landing are a locked critical section, not one transaction.** A crash after a belief lands and
  before its `compiler_emit` lets a re-compile land that belief again (open-problems/L1-repair-conflicts.md item 1).
  If the event log is down, `_log` fails open, so the duplicate check cannot see the landing either.
- [ ] **The output digest in a skill candidate is of the raw output's first 120 characters.** It hides text, but a short
  low-entropy output (a 6-letter password returned as the whole error) could be guessed from it.
- [ ] **Sealed tiers depend on `context["tier"]`**; an Experience without it is treated as Practice.
- [ ] **Landed skill candidates live only in the event log**, so any string over 4000 chars is clipped. The check runs on
  the clipped form, so a long candidate is refused rather than stored broken; a future pass with long code needs a store.
- [ ] **Pass 3 beliefs collide under CS3's single-valued predicates.** Two different failures of one tool
  (`tool:x failed_with timeout`, `... failed_with 503`) become contested (known-bugs/cs3-belief-store.md, first item).
- [x] **The exam file failed `ruff check` (I001)**: fixed in `2a6b9aa`; `ruff check` is clean on it now.
