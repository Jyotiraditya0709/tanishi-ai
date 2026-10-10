# 0023 · Traces: format, event links and the rules for callers (OBS1)

Date: 2026-10-10. Decided by: the OBS1 builder within the spec (points 1-6), revised in the red-team repair round
(R1-R12, human decisions, points 7-11). Was `0021-trace-format.md`; renumbered because other branches claim 0020-0022.

1. **Where.** One JSON line per span, written when the span exits, to `$TANISHI_HOME/traces/<UTC date>.jsonl`
   (dir 0700, files 0600, an `O_APPEND` fd per line). `_write` loops until every byte is written, so a short
   `os.write` never leaves a line without its `\n` (R5). Children are written before their parents. Readers rebuild
   the tree from `parent_id`; `trace.tree()` does that with an explicit stack, so any depth prints (R4).
2. **Record.** `span_id` (16 hex), `parent_id` (null for a root), `task_id`, `session_id` (from
   `events.current_task()`, or a `task_id=` attr, which wins), `name`, `start_ts`, `latency_ms`, `status`
   (`ok`|`error`), `error` (exception type only, never the message), `model`, `tokens_in`, `tokens_out`,
   `tool_calls` (list), `event_ids` (first 200), `event_count` (all of them), `events_truncated` (how many were cut,
   R8), `attrs` (everything else), `truncated` (null, or `{"bytes", "limit", "dropped"}`, see 5). Unset fields are
   null, not missing.
3. **Feeding values in.** `with span(name, **attrs) as s:` and `s.set(...)` for values known later (tokens after the
   model call). `tool_calls` given as attrs come first; the tool of every `tool_call` event emitted inside is added
   unless a given entry of the same name is still unmatched, so a tool reported by both the caller and the
   registry counts once, and two real calls still count twice (R12).
4. **Event links.** `events.add_listener(fn)` / `remove_listener(fn)` in `tanishi/core_state/events.py`. `emit()`
   calls each listener after the commit with `(id, kind, payload)` (the payload as the caller gave it, **not
   redacted**: a listener must redact before it stores anything). A listener that raises is logged and ignored.
   The innermost *live* span owns the id (see 7). Only the id `emit()` returns is linked; a `log_gap` row written in
   the same transaction is not.
5. **Redaction and size.** Every string is redacted with `events.redact`, then clipped to 2000 chars; lists and
   dicts are cut at 200 items and depth 8; non-JSON values become their `repr` (redacted too). A value that
   `json.dumps` still rejects (an int past 4300 digits) becomes `"[UNSERIALISABLE: <type>]"`; the rest of the span
   is kept (R6). If the record still cannot be built, a bare one (ids, latency, status, event links, no attrs) is
   written instead. The whole record goes through `events.redact` once more. A serialised record over 64 KB
   (`MAX_RECORD_BYTES`) loses its `attrs`, then its `tool_calls`, and says so in `truncated` (R7). U+2028, U+2029
   and U+0085 are written as `\uXXXX` escapes and `load()` splits on `\n` only (R1).
6. **Fail open.** Nothing in `trace.py` raises into the traced code. The first failure in a process logs one warning
   (exception type only); later ones log at debug.
7. **Generators and streaming (R10).** A span opened in a generator or async generator frame stays the
   ContextVar's value while the generator is suspended at a `yield`, because a generator runs in its consumer's
   context. So each span remembers that frame (its *holder*), and when an event arrives or a child span opens, a
   span whose holder is not on the running stack is skipped: the consumer's own work goes to the consumer's span.
   A generator driven by `contextlib` (a `@contextmanager` wrapper around `span()`) is not a holder; the frame that
   entered the wrapper is used instead. Closed spans are skipped the same way. Rules for streaming code:
   - Prefer to open the span in the **consumer**, around the whole loop over the stream, and `s.set(tokens_out=...)`
     when the stream ends. That span then owns everything the stream does.
   - A span inside the generator is fine, but it only owns what runs *inside* the generator between resumptions,
     and it is written when the generator finishes or is closed. A stream that is dropped without `close()` is
     written whenever it is garbage-collected, with status `error` / `GeneratorExit`.
   - Generators driven by something other than contextlib (pytest fixtures, custom drivers) count as holders.
8. **A task started inside an open span (R9).** When a span opens with a task id and its parent span has none, the
   parent takes that task and session id (it is written later than the child, so its record carries them). The CLI
   then prints it as the task's root. Only the immediate parent is linked; a span around several tasks goes to the
   first one. `load()` compares task ids as strings, so `task_id=42` is found by `trace 42` (R11).
9. **CLI output (R2, R3).** Every printed field (name, model, tokens, tools, event ids, attrs, error, the task id in
   the header) goes through `printable()`, which writes C0, DEL, C1, U+2028 and U+2029 as escapes. One span is one
   terminal line, and model-chosen names cannot move the cursor or forge lines.
10. **Traces carry ids and lengths, never prompts or conversation text.** This is a rule for every caller of
    `span()` and `s.set()`, not something `trace.py` can enforce: redaction removes secrets, not personal data.
    Pass `prompt_chars=len(prompt)`, message ids, belief ids, tool names; never the prompt, the reply, a tool's
    arguments or output, or a grader's expected answer. Personal data stays in the Core State and the local model.
11. **Wiring is the next node's job.** Nothing in production calls `span()` yet. The next node opens
    `span("think", model=...)` in `TanishiBrain.think()` inside its `task_scope` (and sets tokens after the
    response), and one `span("tool", ...)` per call in `ToolRegistry.execute()`, passing ids and lengths only
    (rule 10). Tool calls then link through the registry's `tool_call` event without being counted twice.
