# SUB1 · red-team round 2 breaks (all open)

See `runs/redteam-SUB1-20261009-r2.md`. Proofs: `tests/substrate/test_state_redteam2.py` (7 failing tests on purpose).

- **R1 (Medium)** `events.redact()` is quadratic on repeated `password`/`secret`/`token` words: 40 kB takes 2 s, 320 kB over
  two minutes. Hits `save()` and `emit()`. Fix is in `core_state/events.py`: possessive `[\w-]*+` in `_ASSIGNED_SECRETS[0]`.
- **R2 (Medium)** Dict or list under a secret-named key (`{"password": {...}}`) is stored in plaintext; only text is hidden.
- **R3 (Low)** `task_id` is not checked for secrets. **R4 (Low)** error messages quote dict keys, which may be secrets.
- **R5 (Low)** Zero-width or Cyrillic look-alikes of `done` pass the status check and cause a re-run.
- **R6 (Low)** `load()` of an id with a lone surrogate raises `UnicodeEncodeError`, not `LookupError`.
