# L1 · Learning Compiler: known limits (2026-10-11)

None of these breaks an acceptance line.

- [ ] **`find_recorded` scans every `experience` event** to find the attempt's record by prediction id. O(n) per compile.
  Fix: an index (a table keyed by prediction id, needs a migration) or let callers pass the event id.
- [ ] **Skill tests are not sandboxed.** `run_skill_test` uses an isolated interpreter, temp dirs and a stripped env, but
  the code runs as this OS user and can read files. Fine for pass 4's own generated code; a pass that turns model text into
  code must wait for SEC1's Arena sandbox. The marker stops `os._exit(0)` faking a pass, not a test that reads the marker
  from its own stdin.
- [ ] **Recording and landing are not one transaction.** A crash after `record()` and before the passes leaves an
  experience with no emissions; a crash after a belief lands and before `compiler_emit` leaves a belief with no emit log
  (its evidence link still names the experience). Re-compiling the same Experience reuses the record but would land the
  same beliefs again (CS3 stores duplicates, decision 0009 item 14).
- [ ] **Landed skill candidates live only in the event log**, so `emit()` clips any string over 4000 chars. Pass 4's code is
  well under that; a future pass emitting long code needs its own store.
- [ ] **Pass 3 beliefs collide under CS3's single-valued predicates.** Two different failures of one tool
  (`tool:x failed_with timeout`, `... failed_with 503`) become contested (known-bugs/cs3-belief-store.md, first item).
- [ ] **The exam file fails `ruff check` (I001)**: it was written before `tanishi.compiler` existed, so ruff now sorts that
  import as first-party. Left alone: the implementer may not edit the exam. A one-line `ruff --fix` by the tester fixes it.
