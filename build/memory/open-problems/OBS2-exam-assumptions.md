# OBS2 · what the spec leaves open (written by the tester, from the spec alone)

The OBS2 card gives `compute(window_days=90) -> dict`, `tanishi time start | stop | report` and three acceptance lines.
The master plan is not in the repo, so B, T, H, N, A, P and the funnel are not defined anywhere the tester can read.
`tests/observability/test_north_star.py` therefore pins only what the card pins: the CEI product, the RCR anchor
(3 discoveries, 10 hours gives 72), and "each number shows its inputs". If the implementer disagrees with an item,
say so in `build/memory/open-problems/OBS2-exam-conflicts.md` and the human decides; do not edit the tests.

- **A1 · Where compute() reads.** `compute(window_days=90)` opens the Core State db named by `TANISHI_CORE_STATE_DB`
  (as every other CS/OBS module does) and the exam migrates it first. It does not create or migrate a missing db in the exam.
- **A2 · Result shape.** A JSON-serialisable dict. Each of the six numbers is a top-level key, matched ignoring case and
  punctuation (`CEI`, `rcr`, `human_effort`, `Human Effort` all match `humaneffort`). Each entry is a dict with `value`
  and a non-empty `inputs`. Where the sentence explaining a zero lives is free (any string field of 3+ words).
- **A3 · Pure helpers.** `north_star.cei(b, t, h, n, a, p)` and `north_star.rcr(discoveries, hours)` are public, because
  "unit tests cover the worked example" needs a callable for it. Names and positional order are assumptions.
- **A4 · RCR scale.** The card gives one point (3, 10 gives 72). The exam assumes RCR is proportional to discoveries and
  inversely proportional to hours, so (3, 8) gives 90 and (3, 16) gives 45. If the real formula has another shape
  (for example funnel stages with different weights) that is a spec question, not a test to bend.
- **A5 · Units.** Human Effort is in hours. The timer's `report()` returns something with a number under a key containing
  "hour", and `compute()["human_effort"]["value"]` equals it. RCR's `inputs` also show the hours used.
- **A6 · Timer edge cases.** `effort.start()`, `effort.stop()`, `effort.report()` mirror the CLI verbs. `stop` without `start`
  may raise or be ignored but must add no time. A second `start` while running may raise or be ignored but must not
  double count. State survives across processes (the CLI is one process per verb).
- **A7 · Zero hours.** `rcr(n, 0)` may raise `ValueError` or return None, 0 or inf; never `ZeroDivisionError`.
- **A8 · Bad numbers.** NaN, inf, negative, bool, str and None raise `ValueError` in `cei`, `rcr` and (for the window)
  `compute`, following decision 0011 rule 12. `window_days` must be a positive number.
- **A9 · CLI.** `python -m tanishi.cli time start|stop|report` from the repo root works, exits 0, prints something on
  `report`, and does not start the chat loop. `tanishi.cli.main` has no subcommands today, so the implementer must add it.

## Not covered, and why

- **Values computed from real Core State rows.** The card does not say which tables feed B, T, H, N, A, P, the funnel, CAR,
  AR or IA, so the exam cannot seed rows and expect a number. Only the empty case ("a 0 is explained") and the timer-fed
  Human Effort are checked from the db. A follow-up exam is needed once the master plan's definitions are written down
  (suggest the human adds them to `build/memory/decisions/`).
- **`window_days` filtering.** Without a known storage format for timer sessions, the exam cannot back-date one.
- **Rendering "in one place".** Only that the dict can be shown as JSON and the CLI prints a report.
