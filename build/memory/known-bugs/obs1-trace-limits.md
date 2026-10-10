# OBS1 v0: known limits

- [ ] **Nothing is traced yet.** `think()` and `ToolRegistry.execute()` do not open spans (the card lists `trace.py`
  only). Until someone wires them, traces exist only where code calls `span()` itself, and model/tokens are only
  what the caller passes.
- [ ] **No rotation or size cap.** One file per UTC day, forever. The CLI reads every file to find one task:
  fine at v0 scale, slow after months.
- [ ] **Spans opened outside a task have `task_id` null** and the CLI cannot show them.
- [ ] **A span that crosses threads loses its events.** Events emitted from a thread started inside a span (or from
  `run_in_executor`) go to no span, because ContextVars do not flow into plain threads.
- [ ] **A process killed mid-span writes nothing for it** (and for its open parents): records are written on exit.
- [ ] **Redaction is the event log's.** Same gaps as CS2: a password in prose with no name in front is written as is.
- [ ] **The event listener gets the raw payload.** Any future listener must redact before storing (decision 0021).
