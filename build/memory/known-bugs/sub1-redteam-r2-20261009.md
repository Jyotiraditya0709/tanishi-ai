# SUB1 · red-team round 2 breaks

See `runs/redteam-SUB1-20261009-r2.md`. Proofs: `tests/substrate/test_state_redteam2.py`.

Fixed (SUB1 repair 2, 2026-10-09):
- **R1 (Medium)** `events.redact()` was quadratic on repeated secret words (320 kB took minutes; hit `save()` and
  `emit()`). A possessive tail alone was not enough: it still started a scan to the end at every secret word. The
  assigned-secret pattern now starts only where a name starts, finds the word by lookahead and eats the name
  possessively, so it is linear. Pinned by `tests/core_state/test_redact_speed.py` (320 kB in under 0.5 s).
- **R2 (Medium)** any value but `None` and `""` under a secret-named key, dict and list included, is stored as `"[REDACTED]"`.
- **R4 (Low)** error messages name dict keys by position (`working[key #0]`), never by text.
- **R5 (Low)** a status must be printable ASCII with no capitals or blanks; zero-width and Cyrillic look-alikes raise ValueError.
- **R6 (Low)** `load()` of an id with a lone surrogate raises `LookupError`.

Still open:
- **R3 (Low)** a secret inside `task_id` is stored as given. Refusing it (the human's fix) fails the red-team test,
  which wants `save()` to succeed; see `open-problems/SUB1-repair-conflicts.md`.
- `load()` messages still quote the task id it was given (`no saved state for task '...'`), so a secret passed as an
  id to `load()` reaches the traceback. Not on the card; same shape as R4.
