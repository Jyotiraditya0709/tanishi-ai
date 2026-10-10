# SUB4 · red-team breaks (2026-10-10; repaired the same day except B7)

Details and proofs: `runs/redteam-SUB4-20261010.md`, `tests/substrate/test_uncertainty_redteam.py`. Rules: decision
`0021-self-model.md`.

- [x] B1 High: a prediction resolved through CS5 `resolve()` (what `ToolRegistry.execute()` uses) never updated
  `capabilities`. *Fixed:* CS5 `on_resolve` hook, plus a read-through in `get_capability()`. See
  `open-problems/SUB4-repair-conflicts.md` for why both are there.
- [x] B1b Medium: `get_capability()` read a stored row that could be stale. *Fixed:* read-through on `updated_at`.
- [x] B3 Low: a lone-surrogate capability name raised `UnicodeEncodeError`. *Fixed:* ValueError.
- [x] B4 Low: distinct secret-shaped tags merged into one `[REDACTED:...]` capability. *Fixed:* a tag redaction would
  change, or a stored tag with a redaction mark (`[REDACTED`, U+FFFD), is untagged.
- [x] B6 Medium (design): n=1 read as ability 1.0, error 0.0. *Fixed:* readings carry `evidence` and
  `low_evidence` (< 5). Below 5 the ability is `(s+1)/(n+2)`. SUB3 must treat a low_evidence ability as unknown.
- [ ] B7 Medium (design, decided, not built): predictions are self-graded with no `call_id`. Readings must not feed
  Arena scoring or promotion until predictions carry a `call_id` (decision 0021 point 9). Strict xfail stays.
