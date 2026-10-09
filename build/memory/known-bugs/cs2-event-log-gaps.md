# CS2 · event log gaps not fixed (2026-10-09)

For the red team and the nodes that follow. None of these breaks an acceptance line; each is a limit of the spec.

- [ ] **No anchor.** The chain is tamper-evident only against someone who does not recompute. With file access, an attacker can
  drop the triggers, edit row k, rehash rows k..N and fix `sqlite_sequence`, and `verify_chain` passes. Fix: an external anchor
  (periodically write the head hash somewhere the attacker cannot reach, e.g. the Warden or a signed checkpoint), or an HMAC with
  a key outside the db. Needs a spec decision.
- [ ] **Redaction is pattern-based.** A key with no known prefix, in free text with no `api_key=`-style name, is written as is.
- [ ] **`emit()` is synchronous** and is called from async `think()` / `execute()`. Under write contention it can block the event
  loop for up to the 5 s busy timeout.
- [ ] **`emit()` opens the db and runs `migrate()` on every call.** Correct and simple; costs a few file reads per event. Cache a
  connection per (thread, path) if profiling says it matters.
- [ ] **Tool input is not clipped** (output is). A `write_file` call with a large body lands in the log whole.
- [ ] **`stream_think()` is not wired**; only `think()` is, as the spec says. Streaming chats write no task events yet.
- [ ] **ts can go backwards.** ts is read under the write lock, but the wall clock can step back (NTP). `iter_events` orders by id
  and `since` is a plain string compare, so a since-query can skip a row whose ts is earlier than the row before it.
