# SUB3 · open red-team breaks (2026-10-10)

Details and fixes: `build/memory/runs/redteam-SUB3-20261010.md`. Tests: `tests/substrate/test_router_redteam.py` (xfail-strict).

- B2 (high): falsy non-False `personal_local_only` switches the personal-stays-local rule off.
- B4 (medium): `report_spend` leaves no event and ignores a denied `record_spend`; cap undercounts.
- B1 (medium): `route()` blocks the event loop on a slow Warden; no async entry.
- B3 (medium): `estimate_tokens` undercounts non-ASCII text.
- B5 (low): `COMPLEXITY_THRESHOLD_TOKENS` copied at import, autoresearch changes ignored.
