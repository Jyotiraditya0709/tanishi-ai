# OBS1 repair round · conflicts and changes outside the card (2026-10-10)

No fix conflicted with an exam or red-team assertion. One change was needed outside `trace.py`:

## `tests/conftest.py`: suite isolation did not survive `monkeypatch.undo()`

- **What was wrong.** The autouse fixture `_temporary_tanishi_home` set `HOME` and `TANISHI_CORE_STATE_DB` through the
  test's own `monkeypatch`. A test that calls `monkeypatch.undo()` (the R5 proof does, to drop its fake `os.write`)
  also undid the isolation, so everything after that line ran against the **real** `~/.tanishi` and the real Core
  State db. The R5 proof then read the real `~/.tanishi/traces` and failed even with the fix in place.
  `tests/core_state/test_goals_impl.py` and `tests/core_state/test_predictions_redteam.py` call `undo()` too.
- **What I did.** The fixture now uses its own `pytest.MonkeyPatch.context()`, so a test's `undo()` only undoes that
  test's patches. No assertion changed; the full suite passes.
- **Why it is not a quiet rewrite.** It is the repo's isolation rule (CLAUDE.md: tests never touch the real
  `~/.tanishi/`), not exam logic. Whoever owns the conftest should confirm.
