# SUB4 · capability calibration: known limits (2026-10-10)

None of these breaks an acceptance line.

- [ ] **"Every state item carries a confidence" is not built.** It is in the goal but has no acceptance line or
  interface. SUB1's state items have no confidence field; adding one changes SUB1's stored shape. Needs a spec line.
- [ ] **Nothing tags predictions yet.** `ToolRegistry.execute()` writes `tool:<name>` predictions with no
  `capability` field, so in production the table stays empty until a caller (SUB3 or a CS5 change) adds the tag.
- [ ] **Full scan per resolution.** `update_calibration()` scans resolved predictions with `json_extract` on every
  call, with no index on the tag. O(ledger size) per resolve. Fix with an expression index in a new migration once
  the ledger is large; not added because migration numbers are contended across open branches.
- [ ] **No evidence count in the read.** The table has no `n` column, so the router cannot tell ability 1.0 from one
  success apart from ability 1.0 from a thousand. Unsmoothed success rate; SUB3 may want a count or a prior.
- [ ] **Tags pass through CS5's redaction.** A tag that looks like a secret is stored redacted and will not match
  the caller's text in `update_calibration()` / `get_capability()`.
- [ ] **Unbounded history.** Old evidence weighs the same as new, so a capability that improved moves slowly. CS5's
  `tool_forecast()` uses a window of 50; a window here would need a spec decision.
