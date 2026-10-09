# SUB1 · red-team breaks

See `runs/redteam-SUB1-20261009.md`. Proofs in `tests/substrate/test_state_redteam.py`.

Fixed (SUB1 repair, 2026-10-09): B1 (secrets are now stored redacted), B4 (status must be lowercase with no
surrounding blanks), B5 and B6 (a wrong-shaped row makes `load()` raise ValueError), B8 (blank task id refused).

Fixed (SUB1 repair 2, 2026-10-09): B2 (duplicate step ids) and B3 (empty or blank step id) raise ValueError, after the
human ruled step ids unique and non-empty and the exam's generator was changed to match.

The B1 gap (only text hidden under a secret-named key) is closed by round-2 R2: any value but `None` and `""` is hidden.
Detection is still only as good as `events.redact()`'s patterns.
