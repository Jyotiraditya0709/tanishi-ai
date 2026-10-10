# SUB4 · capability calibration: known limits (2026-10-10, updated in the repair round)

None of these breaks an acceptance line. Rules: decision `0021-self-model.md`.

- [ ] **"Every state item carries a confidence" is not built.** It is in the goal but has no acceptance line or
  interface. SUB1's state items have no confidence field, and adding one changes SUB1's stored shape. Needs a spec line.
- [ ] **Nothing tags predictions yet.** `ToolRegistry.execute()` writes `tool:<name>` predictions with no
  `capability` field, so in production the table stays empty until a caller (SUB3 or a CS5 change) adds the tag.
- [ ] **Full scan per refresh.** A refresh (on resolve, or a stale read) scans resolved predictions with
  `json_extract`, about 100 ms on a 60k-row ledger with 20k rows of one capability. A fresh read is about 6 ms there,
  and a read of an unknown capability about 9 ms (a `json_extract` scan). Fix with an expression index on
  `json_extract(expected, '$.capability')` in a new migration once the ledger is large.
- [x] ~~No evidence count in the read.~~ Fixed in the repair (B6): `evidence` and `low_evidence` on the reading. The
  count is not stored. It is recounted once per row version per process, so the first read after a restart costs
  one scan.
- [x] ~~Tags pass through CS5's redaction.~~ Fixed in the repair (B4): such tags are untagged.
- [ ] **The CS5 hook needs the import.** `on_resolve` only runs SUB4's hook in a process that imported
  `tanishi.substrate.uncertainty`. Elsewhere the row is refreshed on the next `get_capability()`, not on resolve. A
  raw SQL read of `capabilities` in such a process can be stale.
- [ ] **Freshness trusts the wall clock.** The read-through compares `resolved_at` with `updated_at` (both UTC ISO
  text from `datetime.now`). If the clock steps backwards between a refresh and a later resolve, that resolve can look
  older than the row and be missed until the next refresh. The hook still covers it when it is registered.
- [ ] **A jump at 5.** Ability is `(s+1)/(n+2)` below 5 and `s/n` from 5 on, so 4/4 reads 0.83 and 5/5 reads 1.0. It
  was chosen so the prior cannot hide B7. SUB3 should ignore ability while `low_evidence` is set.
- [ ] **Unbounded history.** Old evidence weighs the same as new, so a capability that improved moves slowly. CS5's
  `tool_forecast()` uses a window of 50. A window here would need a spec decision.
