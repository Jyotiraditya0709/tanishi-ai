# OBS1 · what the spec leaves open (written by the tester, from the spec alone)

The card gives `with span(name, **attrs)`, `python -m tanishi.observability.trace <task_id>` and two acceptance lines.
`tests/observability/test_trace.py` pins only what the card and the CS2 API pin. If the implementer disagrees with an
item, say so in `build/memory/open-problems/OBS1-exam-conflicts.md` and the human decides; do not edit the tests.

- **A1 · Where.** Records go under `$TANISHI_HOME/traces/` (default `~/.tanishi/traces/`), any `*.jsonl` file name,
  one JSON object per line, one line per span, written when the span exits.
- **A2 · Field names.** Read through alias lists (also accepted under a nested `attrs` dict): name (`name`),
  id (`span_id`|`id`), parent (`parent_id`|`parent`), task (`task_id`), model (`model`), tokens
  (`tokens_in`/`tokens_out`, or `input_tokens`/`output_tokens`), tool calls (`tool_calls`|`tools`), events
  (`event_ids`|`events`), latency (`latency_ms`|`duration_ms`, or seconds under `latency`|`duration`).
- **A3 · How attrs arrive.** `span("x", model=..., tokens_in=..., tokens_out=..., tool_calls=[...])` records them
  verbatim. Other attrs are kept somewhere in the record.
- **A4 · Event ids are automatic.** Ids returned by `events.emit()` while the span is open (in this context) land in
  that span's `event_ids`, and only in the innermost open span. A span with none has `[]`.
- **A5 · Tool calls from events.** A `tool_call` event emitted inside a span makes that tool's name appear in the
  span's tool calls (or the count 1).
- **A6 · Task id.** Taken from `events.current_task()` (set by `task_scope`). Root spans have a null parent.
  Nesting is per context (ContextVar), so asyncio tasks keep separate trees.
- **A7 · Fail open.** Any failure writing or computing a trace (unwritable dir, `TANISHI_HOME` a file, odd attrs,
  `current_task` raising) is swallowed. The body's own exception propagates unchanged and the span is still recorded.
- **A8 · Secrets.** Attrs go through redaction before they hit disk (CLAUDE.md: no secrets in logs).
- **A9 · CLI.** `python -m tanishi.observability.trace <task_id>` exits 0 for a known task, prints that task's spans
  only, parents before children, children indented deeper, event ids visible. An unknown task or an empty traces dir
  may exit non-zero or print nothing, but must not raise a traceback.

## Not covered, and why
- Wiring into `think()` / `ToolRegistry.execute()`: the card's file list is `trace.py` only.
- Token counts derived automatically from a model response: the card does not say where they come from.
- File rotation, size caps and concurrent multi-process writers: not in the acceptance lines.
