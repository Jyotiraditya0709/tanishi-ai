# OBS1 · red-team breaks (2026-10-10)

Full table with fixes: `build/memory/runs/redteam-OBS1-20261010.md`. Proofs: `tests/observability/test_redteam_obs1.py`.
All twelve fixed in the repair round (2026-10-10); every xfail mark is off and the proofs pass. How each was fixed:
decision `0023-traces.md`.

- [x] R1 High: `load()` splits on `\n` only; U+2028/U+2029/U+0085 are also escaped on disk.
- [x] R5 High: `_write` loops over short `os.write` counts.
- [x] R6 Medium: an unserialisable attr becomes `"[UNSERIALISABLE: <type>]"`; a record that still fails is written bare.
- [x] R2/R3 Medium: the CLI escapes C0, DEL, C1, U+2028 and U+2029 in every printed field.
- [x] R4 Medium: `tree()` is iterative.
- [x] R9 Medium: the span a task starts in takes the task id and prints as the root.
- [x] R10 Medium: a span held by a suspended generator is skipped for events and child spans.
- [x] R7 Low-Med: 64 KB cap per record, `truncated` marker.
- [x] R8 Low: `event_count` and `events_truncated`.
- [x] R11/R12 Low: task ids compare as strings; a tool both reported counts once.
- [ ] Spec gap, still open: nothing in production calls `span()` yet (decision 0023 point 11, next node).
- [ ] Still open: traces are readable by Arena candidates (`open-problems/OBS1-trace-leakage.md`, SEC1).
