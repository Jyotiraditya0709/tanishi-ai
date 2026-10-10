# OBS1 · red-team report (2026-10-10)

Target: `tanishi/observability/trace.py` at 3aec775, plus the listener hook in `core_state/events.py`.
Proofs: `tests/observability/test_redteam_obs1.py` (assert the correct behaviour; open breaks are `xfail(strict=True)`,
confirmed to fail for the stated reason with `--runxfail`). No source file was changed.

## Breaks

| # | Sev | Break | Smallest fix |
|---|-----|-------|--------------|
| R1 | **High** | `load()` uses `str.splitlines()`. `json.dumps(ensure_ascii=False)` leaves U+2028, U+2029 and U+0085 raw, so any record containing one is cut in two and silently dropped from the CLI. Model output can contain them. | `text.split("\n")` instead of `splitlines()` |
| R5 | **High** | `_write` ignores the count `os.write` returns. A short write (disk full, signal) leaves a line with no newline; the next record is glued to it and both are unreadable. | loop until all bytes are written; start a record with `\n` if the file does not end in one |
| R6 | Medium | One attr that `json.dumps` rejects (`10**5000`, past the 4300-digit limit) drops the whole span, event links included. Any failure in `_record`/`_write` does the same. | on failure retry once with `attrs={}` so ids, latency and status survive |
| R2 | Medium | CLI prints `name`, `model`, tool names and `error` raw. A `\n` in a span name forges extra tree lines (looks like a tool call that never happened). Tool names come from the model. | print those fields through `json.dumps` or replace control characters |
| R3 | Medium | Same path: ESC sequences in a tool name reach the terminal (`\x1b[2J` clears the screen). | same fix as R2 |
| R4 | Medium | `tree()`'s `walk` recurses; a task with ~1000 nested spans prints `could not read traces: RecursionError`. | iterative walk with an explicit stack |
| R9 | Medium | A span opened before `task_scope` has `task_id` null, so the root of the task is invisible and its children print as orphan roots. This is the natural shape for a wrapper around `think()`. | when a span opens with a task and its parent has none, set the parent's `task_id` (parents are written later) |
| R10 | Medium | A span held open across a `yield` stays current in the consumer's context. The consumer's events and child spans are attributed to the generator's span (streaming model calls will hit this). | doc it and give streaming a non-contextvar `start()/finish()`; or open the span in the consumer |
| R7 | Low-Med | Caps are per container (200 items, depth 8), not per record. 800k ints in nested lists gave a 2.7 MB line and slow `load()`. | cap the serialised line (e.g. 64 KB): over it, drop `attrs` and mark the record truncated |
| R8 | Low | `event_ids` is cut at 200 with no marker: the link the card promises is lost silently. | add `event_count` and `events_truncated` |
| R12 | Low | A tool passed as `tool_calls=[...]` and the registry's own `tool_call` event are both counted, so `["search","search"]`. | de-duplicate, or document that callers must not pass what the registry emits |
| R11 | Low | `task_id=42` (int) is stored as an int; the CLI argument is a str, so `trace 42` finds nothing. | `str()` the task id at open |

## Held up (tests pass today)

- Secrets in the span name, model, attr keys, nested header values, `repr()` of an exception, bytes and tool names never reach disk (h1).
- A raising `_write` and a `BaseException` body: body error propagates unchanged, context is reset (h2, h4).
- Tracing does not touch the event hash chain (h3).
- 500 spans take well under 5 ms each (h5).
- Also tried, no break: lone surrogates, NaN/inf, broken `__repr__`, parent cycles, span exited in another context,
  exception message leaking (type only is stored), dir/file modes, concurrent appends.

## For the next node

- **Nothing in production calls `span()`** (grep: no caller outside `trace.py`). The acceptance line "a readable trace of
  every task" is only true for code that opts in; `think()` and `ToolRegistry.execute()` still need a span each. A green
  OBS1 is a green *library*, not tracing.
- Attrs are caller text. Redaction is for secrets, not personal data: a caller that puts prompt or reply text in attrs
  writes conversation text to `traces/` (CLAUDE.md forbids it in logs). Wiring should pass lengths and ids, not text.
- The Arena runs candidates under a temporary `TANISHI_HOME`; a grader span carrying expected output would sit in a
  directory the candidate can read. Graders must not put task answers in attrs.
