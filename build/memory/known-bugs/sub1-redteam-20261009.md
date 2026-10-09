# SUB1 · red-team breaks, not yet fixed

See `runs/redteam-SUB1-20261009.md`. Proofs fail on purpose in `tests/substrate/test_state_redteam.py`.
B1 (secrets stored in plaintext) is the one to fix first. B2-B6 and B8 are validation gaps in `state.py`.
B4 (status case) needs a decision before it is fixed: 0014 says statuses are opaque.
