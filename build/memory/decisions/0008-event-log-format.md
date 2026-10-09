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

## Repair round, 2026-10-09 (decided by the human after the red team; built by the CS2 builder)

These replace items 4 and 6 above, plus the "compares as a string" part of item 3 and the "input logged whole"
part of item 7, and they extend item 5.

8. **Contiguous ids (C1).** `verify_chain` requires ids 1, 2, 3, ... with no gap and the last id equal to
   `sqlite_sequence.seq`. It returns the first missing id, or the first bad row. A deleted tail or a wiped log stays
   visible after later honest emits. If `seq` is behind the last id, it reports `seq + 1`.
9. **One snapshot (C2).** `verify_chain` reads the rows and `sqlite_sequence` inside one read transaction
   (`BEGIN` ... `ROLLBACK`; if the caller is already in a transaction, it reads inside that one).
10. **Bounded payloads (E1, E2, P1).** Before hashing, `emit()` replaces lone surrogates with U+FFFD, replaces any
    container nested deeper than `MAX_DEPTH = 32` (the payload itself is level 1) with
    `"[TRUNCATED: nested too deep]"`, and clips every string (dict keys and header fields too) to `MAX_CHARS = 4000`
    after redaction, ending in `... [N more chars]`. This applies to every event, so the user's input in
    `task_start` is clipped too. `redact()` on its own fixes surrogates and caps depth but does not clip. Only
    values that are not JSON (objects, sets, NaN) still make `emit()` raise.
11. **Tool calls fail closed.** If `ToolRegistry.execute()` cannot write `tool_call`, the tool does not run and the
    caller gets `ToolResult(success=False, error=EVENT_LOG_UNAVAILABLE)`. A failed `tool_result` write only warns,
    because the tool has already run. `think()` stays fail open and warns: a chat reply is not an action.
12. **Gap marker (E3).** Any `emit()` failure after the header checks (db down, a payload that is not JSON, a C3
    stray row) appends `{"ts", "kind"}`, never the payload, to `$TANISHI_HOME/event_gaps.jsonl` (0600). The next
    successful `emit()` claims the file by atomic rename inside its write lock and first writes
    `log_gap {count, kinds, first_ts, last_ts}` (actor `event_log`) in the same transaction. If that transaction
    fails, the lines go back to the file. If the gap file cannot be written, a warning is logged and the
    original error still propagates.
13. **Model failures (W1).** `BrainResponse.error` is set on the API and Ollama error paths, and
    `BrainResponse.failed` is also true for `model_used` ending in `(error)`. `task_end.success` is False then,
    and `task_end.error` explains why.
14. **Correlation (W2).** `think()` sets `events.task_scope(task_id, session_id)`, a ContextVar.
    `session_id` is `brain._active_session_id` when it is a str. `tool_call` and `tool_result` carry `tool`,
    `call_id` (uuid4 hex, shared by the pair) and `task_id` (None outside a task). All four kinds put
    `session_id` in the column.
15. **More redaction (R1, R2).** The new patterns cover JWTs, any value assigned to a name containing
    secret, password, passwd, token, api key, private key or credential (any length), `Authorization:` credentials,
    `Basic <base64 of user:pass>`, URL userinfo passwords, and `?…key=` / `sig=` / `auth=` URL params. Under a dict
    key that names a secret (now also token, cookie, credential(s), authorization, bearer and auth, matched as
    whole words so `max_tokens` and `author` stay), every non-null, non-empty value is replaced, whatever its type.
    Every pattern starts on a literal, so a long hostile string cannot make it backtrack quadratically. Known
    limit: a password in prose with no name in front of it ("my password is hunter2") is not caught.
16. **`since` (T1).** `iter_events(since=...)` parses `since` with `datetime.fromisoformat` (`Z`, an offset, or
    naive meaning UTC) and compares each row's ts as a UTC datetime in Python, not as a string. Rows whose ts does
    not parse are left out of a `since` query.
