# OBS1 v0: known limits

- [ ] **Nothing is traced yet.** `think()` and `ToolRegistry.execute()` do not open spans (the card lists `trace.py`
  only). Until someone wires them, traces exist only where code calls `span()` itself, and model/tokens are only
  what the caller passes. Decision 0023 point 11 says how.
- [ ] **No rotation.** One file per UTC day, forever. The CLI reads every file to find one task: fine at v0 scale,
  slow after months. (Each record is now capped at 64 KB.)
- [ ] **Spans opened outside any task have `task_id` null** and the CLI cannot show them. A span that a task
  *starts inside* now takes that task's id (R9), but only the immediate parent, and only for the first task.
- [ ] **A span that crosses threads loses its events.** Events emitted from a thread started inside a span (or from
  `run_in_executor`) go to no span, because ContextVars do not flow into plain threads.
- [ ] **A process killed mid-span writes nothing for it** (and for its open parents): records are written on exit.
- [ ] **Redaction is the event log's.** Same gaps as CS2: a password in prose with no name in front is written as is.
  Personal text is kept out by rule only (decision 0023 point 10), not by code.
- [ ] **The event listener gets the raw payload.** Any future listener must redact before storing (decision 0023).
- [ ] **Generator spans are found by frame.** A span in a generator owns events only while that generator runs.
  Generators driven by contextlib are treated as wrappers; any other driver (pytest fixtures, a hand-written
  `send()` loop that opens spans for its generators) makes the generator a holder, so the body's events go to the
  enclosing span instead. A dropped stream's span is written as `error` / `GeneratorExit`.
- [ ] **A short write is finished, not made atomic.** Under concurrent appenders, a line split over two `write()`
  calls can interleave with another process's line. Only on disk-full or a signal; both lines are then unreadable.
- [ ] **The 64 KB cap drops attrs and tool calls only.** A container passed as `task_id` can still push a record over.
- [ ] **Traces are readable by Arena candidates** (same user, same `TANISHI_HOME`): `open-problems/OBS1-trace-leakage.md`.
