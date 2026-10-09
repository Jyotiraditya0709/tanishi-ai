# CS5 · the CS5 exam and five CS2 tests cannot both pass

Found by the CS5 implementer on 2026-10-09. The implementer may not change another agent's test, so this needs the
human (or the owners of the CS2 tests). The suite has **5 known failures** until this is settled.

The CS5 spec says `ToolRegistry.execute()` writes a prediction before each tool call and resolves it after. The CS5 exam
(`test_predictions.py::test_execute_prediction_is_written_before_the_tool_call_event`) requires an event whose kind
contains "predict" in the log, before `tool_call`. These CS2 tests assert that a tool call writes **only** tool events:

| Test | Assertion that breaks |
|---|---|
| `test_events.py::test_registry_execute_emits_tool_call_then_tool_result` | the whole log is exactly `tool_call`, `tool_result` |
| `test_events.py::test_registry_emits_tool_result_for_failing_tool` | `_kinds()[0] == "tool_call"` |
| `test_events_builder.py::test_tool_events_inside_a_task_carry_its_task_id_and_session` | the log between `task_start` and `task_end` is exactly the tool pair |
| `test_events_builder.py::test_tool_events_outside_a_task_have_no_task_id` | exactly two events in the log |
| `test_events_redteam.py::test_failed_tool_and_cancelled_tool_both_get_a_result_event` | `[e.kind for e in iter_events()] == ["tool_call", "tool_result"]` |

No implementation satisfies both: the CS5 exam reads the unfiltered log and needs a prediction event in it, and the CS2
tests read the unfiltered log and forbid anything but the tool pair. I built what the CS5 spec asks (the newer
spec; "the test wins" applies to the node's own exam).

**What would settle it:** in each of the five tests, filter the log to the kinds under test, e.g.
`[e for e in iter_events() if e.kind in ("tool_call", "tool_result")]` (and `task_start`/`task_end` where they are
checked). Every CS2 property they check (order, pairing, task_id, session_id, a result for every call) stays checked.
The other way out, dropping the prediction events from `execute()`, would fail the CS5 exam and lose the link between
a prediction and its task in the log.
