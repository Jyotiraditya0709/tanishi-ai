# L1 · Learning Compiler: known limits (2026-10-11, revised in the repair round 2026-10-12)

None of these breaks an acceptance line.

- [ ] **Record and duplicate lookups are SQL scans with `json_extract`**, no index (fast at thousands of events; an
  index needs a migration in CS1's folder). `find_recorded` and `_landed_before` are the two queries.
- [ ] **Skill tests are not sandboxed until SB1 lands.** Without `tanishi.sandbox`, `run_skill_test` uses `python -I`, a
  temp cwd/HOME, a stripped env and an audit hook; the code still runs as this OS user and can read files and use the
  network. The audit hook keeps the nonce out of reach of the routes known today; it is not a proof.
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
