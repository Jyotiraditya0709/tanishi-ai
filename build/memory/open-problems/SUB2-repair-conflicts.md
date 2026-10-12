# SUB2 repair round: conflicts and choices (2026-10-11)

No fix had to give way to a test: the exam (`test_loop.py`) and the red-team tests all pass unchanged. Two card items
met an exam constraint and were scoped to satisfy both, and two items rest on things outside SUB2's files. A human
should confirm each.

1. **B5 "an action names a registered tool" vs the exam's fakes.** The exam's `Tools` lists no tools
   (`get_claude_tools()` is `[]`, `tools` is `{}`) yet acts on `read_file`, `a`, `t`... Checking names against that
   empty set would turn every exam act into an answer. Choice: names are checked against `get_claude_tools()` only
   when it lists at least one tool. With the real `ToolRegistry` that is every registered tool.
2. **B6 "search by key terms, not the whole task" vs `test_recalled_memory_reaches_a_prompt`**, which asserts some
   memory query contains the whole task. Choice: query the whole task first, then each key term; merge and rank by
   how many queries found a memory. Both hold. Cost: up to 7 memory queries per recall instead of 1.
3. **Item 13: "tool: predictions keep the tool's own tag".** The registry's `tool:<name>` predictions carry no
   capability tag today, so the Self Model ignores them. The `act:` ones now carry `planning`, so the two can never
   merge, which is what the card's test checks. If tools should have their own capabilities (e.g. `tool:<name>`), that
   is a change to `tanishi/tools/registry.py` (`_predict`), outside SUB2's files. Not done.
4. **Item 10: what "recalled memory" covers.** Done for the loop's own recall phase and for a tool result with
   `personal=True` (no `ToolResult` has that field yet; Twin data does not exist yet). The dream-cycle block that
   B9 puts in the system prompt is also recalled memory. Marking the task personal whenever it is non-empty would
   route almost every flag-on task to the local model (and refuse it when Ollama is down), while the legacy path
   sends the same block to Claude. Not done: needs a human decision. My recommendation: keep the dream block out of
   the flag-on cloud prompt when the task is personal, rather than making every task personal.
