# CS5 · prediction ledger: known limits (2026-10-09)

None of these breaks an acceptance line.

- [ ] **Resolve-once is enforced in code, not SQL.** Raw SQL can rewrite `score`, `actual` or `resolved_at`, or clear
  `resolved_at` to allow a second resolve. A trigger needs a new migration (0003); not added because other open
  branches may claim that number. The event log still holds the first `prediction_resolved` event.
- [ ] **Row and event are not one transaction.** emit() takes its own write lock, so a crash between the commit and the
  emit leaves a ledger row with no event (emit() notes the gap only if emit() itself ran and failed).
- [ ] **Every call opens the db and runs migrate().** A tool call now does this three times more (forecast, predict,
  resolve). Fine for now; cache a connection if profiling says so.
- [ ] **Synchronous db work inside async `execute()`**, as with the CS2 events: up to the 5 s busy timeout under contention.
- [ ] **Tool forecast ignores the input.** Every call of a tool gets the same forecast; a search for a bad URL and a good
  one are predicted alike. Fine for day one; the World Model should condition on input.
- [ ] **`about` is redacted before storage**, so an `about` that looks like a secret groups under the redacted text.
