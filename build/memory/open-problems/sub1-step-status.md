# SUB1 · the spec does not say what a Step.status is

`Step(id, description, status, model, result_ref)` has no list of status values, and "resumes from the last completed step"
needs one value that means completed. Also not stated: what `load()` does for an unknown task id, and whether `TaskState`
and `Step` compare with `==`.

The SUB1 exam assumes:
- completed is `"done"`, not yet started is `"pending"`; the store itself must treat status as opaque text, so any other
  string (the property test uses "running", "failed", "skipped") round-trips unchanged;
- `load()` of an unknown id raises `LookupError` (`KeyError` is fine);
- `TaskState`, `Plan` and `Step` are value types (dataclasses) so `==` compares fields.

What I would do instead: fix the vocabulary in the spec (pending, running, done, failed) and name the exception.
If the builder picks a different word for completed, change the two constants at the top of `tests/substrate/test_state.py`
and say so here; do not weaken the tests.

**Builder (2026-10-09):** adopted as written, so the exam constants are unchanged. `"done"` and `"pending"` (plus
`"running"` and `"failed"`) are constants in `tanishi/substrate/state.py`. Unknown id raises `LookupError`. The three
types are plain (non-slotted) dataclasses. See decision 0014. Still open for the human: put the vocabulary in the spec.
