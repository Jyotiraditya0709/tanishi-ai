# SUB1 · red-team breaks

See `runs/redteam-SUB1-20261009.md`. Proofs in `tests/substrate/test_state_redteam.py`.

Fixed (SUB1 repair, 2026-10-09): B1 (secrets are now stored redacted), B4 (status must be lowercase with no
surrounding blanks), B5 and B6 (a wrong-shaped row makes `load()` raise ValueError), B8 (blank task id refused).

Still open: B2 (duplicate step ids) and B3 (empty step id). The fix conflicts with the exam's property test; see
`open-problems/SUB1-repair-conflicts.md`. Their two red-team tests fail until the human settles it.

Known gap in the B1 fix: a secret-named key (`password`, `api_key`, `token`) is redacted only when its value is text.
`{"token": 12345}` and `{"password": ["x"]}` keep their value, because redacting non-text would also hit counters
such as `{"token": 5}`. Detection is only as good as `events.redact()`'s patterns.
