# 0021 · Trace format and the event listener hook (OBS1)

Date: 2026-10-10. Decided by: the OBS1 builder, within the spec. (Numbered 0021 because three branches already
claim 0020.)

1. **Where.** One JSON line per span, written when the span exits, to `$TANISHI_HOME/traces/<UTC date>.jsonl`
   (dir 0700, files 0600, one `os.write` per line on an `O_APPEND` fd). Children are written before their parents.
   Readers must rebuild the tree from `parent_id`; `trace.tree()` does that.
2. **Record.** `span_id` (16 hex), `parent_id` (null for a root), `task_id`, `session_id` (from
   `events.current_task()`, or a `task_id=` attr, which wins), `name`, `start_ts`, `latency_ms`, `status`
   (`ok`|`error`), `error` (exception type only, never the message), `model`, `tokens_in`, `tokens_out`,
   `tool_calls` (list), `event_ids` (list of ints), `attrs` (everything else). Unset fields are null, not missing.
3. **Feeding values in.** `with span(name, **attrs) as s:` and `s.set(...)` for values known later (tokens after the
   model call). `tool_calls` given as attrs come first, then the tool of every `tool_call` event emitted inside.
4. **Event links.** `events.add_listener(fn)` / `remove_listener(fn)` in `tanishi/core_state/events.py`. `emit()`
   calls each listener after the commit with `(id, kind, payload)` (the payload as the caller gave it, **not
   redacted**: a listener must redact before it stores anything). A listener that raises is logged and ignored.
   The innermost open span in the emitting context owns the id. Only the id `emit()` returns is linked; a `log_gap`
   row written in the same transaction is not.
5. **Redaction.** Every string is redacted with `events.redact`, then clipped to 2000 chars; lists and dicts are cut
   at 200 items and depth 8; non-JSON values become their `repr` (redacted too). Then the whole record goes through
   `events.redact` once more so values under secret-named keys go.
6. **Fail open.** Nothing in `trace.py` raises into the traced code. The first failure in a process logs one warning
   (exception type only); later ones log at debug.
