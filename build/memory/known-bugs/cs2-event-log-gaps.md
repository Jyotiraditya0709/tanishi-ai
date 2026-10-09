# CS2 · event log gaps not fixed (2026-10-09, updated after the repair round)

For the red team and the nodes that follow. None of these breaks an acceptance line; each is a limit of the spec.

- [ ] **No anchor.** The chain is tamper-evident only against someone who does not recompute. With file access, an attacker can
  drop the triggers, edit row k, rehash rows k..N and fix `sqlite_sequence`, and `verify_chain` passes. The contiguity check
  (C1) only closes the cheap version: delete and let the next emit paper over it. Fix: the Warden (W1) holds the latest
  hash (or a signed checkpoint, or an HMAC key outside the db). Needs W1.
- [ ] **One unhashed row stops logging (C3).** See `cs2-redteam-20261009.md` and `open-problems/CS2.md`.
- [ ] **Redaction is pattern-based.** A key with no known prefix, in free text with no secret-like name before it, is written
  as is. So is a password in prose ("my password is hunter2").
- [ ] **`emit()` is synchronous** and is called from async `think()` / `execute()`. Under write contention it can block the event
  loop for up to the 5 s busy timeout.
- [ ] **`emit()` opens the db and runs `migrate()` on every call.** Correct and simple; costs a few file reads per event. Cache a
  connection per (thread, path) if profiling says it matters. It also stats `$TANISHI_HOME/event_gaps.jsonl` once per call.
- [x] ~~Tool input is not clipped~~. Fixed: every string in every payload is clipped to 4000 chars after redaction.
- [ ] **`stream_think()` is not wired directly;** it calls `think()`, so it gets task events, but nothing records the stream itself.
- [ ] **ts can go backwards** (NTP step). `since` now compares each row as a UTC datetime, so no row is skipped, but ts order
  and id order can disagree. Order by id.
- [ ] **Gap marker limits.** If `$TANISHI_HOME` itself is unwritable, a failed write leaves no gap line (only a warning). A
  process killed between commit and deleting its `.claim` file leaves the file behind; its gaps were already logged.
- [ ] **Fail closed means a broken log stops every tool.** That is the decision, but nothing yet tells the user why beyond the
  tool's error text (`EVENT_LOG_UNAVAILABLE`). Worth surfacing in the UI later.
