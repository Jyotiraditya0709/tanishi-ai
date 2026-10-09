# 0008 · Event log format and wiring (CS2)

Date: 2026-10-09. Decided by: the CS2 builder, within the spec. Revisit before W1 (Warden) if it disagrees.

1. **Canonical row.** `hash = sha256((prev_hash + canonical).encode("utf-8"))`, where `canonical` is
   `json.dumps({id, ts, kind, actor, session_id, payload, prev_hash}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
   and `payload` is the **stored text**, not the parsed object. So every byte of every column is covered, whitespace included.
   The id is in the hash, so `emit()` picks it itself (max of `sqlite_sequence` and `MAX(id)`, plus 1) inside `BEGIN IMMEDIATE`.
   Anyone else writing to `events` must use `emit()`; a raw INSERT breaks the chain on purpose.
2. **Stored payload** is `json.dumps(redact(payload), ensure_ascii=False, allow_nan=False)`, default separators, keys in caller
   order. The exam's single-byte test relies on the `": "` separator. Any JSON value is accepted, not only dicts.
3. **ts** is `datetime.now(UTC).isoformat(timespec="microseconds")`, fixed width, so `since` compares as a string.
4. **Truncation.** `verify_chain` also fails, at `last_id + 1`, when `sqlite_sequence` is ahead of the last row.
5. **Redaction** runs inside `emit()` on every string, dict key, `kind`, `actor` and `session_id`. It replaces key shapes with
   `[REDACTED:<name>]`, the value of `api_key=...`-style assignments in text, and any string under a dict key that names a
   secret (`password`, `api_key`, `secret`, `access_token`, ...). `redact()` is public so callers can redact before they clip.
6. **Wiring fails open.** `think()` and `ToolRegistry.execute()` log a warning (exception type only, never the payload) and carry
   on if `emit()` raises. A broken log must not break the user's chat. Enforcement belongs to the Warden (W1), which may choose
   to fail closed for tool calls.
7. **What gets logged.** `task_start {task_id, input, mood}`, `task_end {task_id, success, model_used, tools_used, error}`,
   `tool_call {tool, input}`, `tool_result {tool, success, output, error, ms}`. Tool output and error are redacted, then
   clipped to 4000 chars. The user's input is logged whole (redacted), because failure analysis needs the task text.
