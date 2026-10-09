"""Proof that a tool was really called: read it from the attempt's Core State event log.

`ToolRegistry.execute()` writes a `tool_call` event and then a `tool_result` event with the same `call_id` into
$TANISHI_CORE_STATE_DB. The legacy harness faked tool calls in Ollama mode by pasting tool output into the answer text;
a verifier that only reads the answer cannot tell. These helpers read the log instead, so a tool task scores 0 unless
the registry really ran the tool and it succeeded.

Known limit: code running in the attempt can import `emit()` and write both events itself. The log is hash-chained, so
editing it afterwards is caught here, but a forged pair is not. Real separation comes with the Warden (node W1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tanishi.core_state import open_db
from tanishi.core_state.events import iter_events, verify_chain


@dataclass(frozen=True)
class ToolCall:
    tool: str
    call_id: str
    input: Any
    output: str


def real_calls(*tools: str) -> list[ToolCall]:
    """Successful, completed calls of any of `tools` (all tools if none named), oldest first.

    A call counts only if its tool_call event has a tool_result event with the same call_id and success true.
    A broken hash chain, or a log that cannot be read, yields no calls.
    """
    try:
        conn = open_db()
        try:
            ok, _ = verify_chain(conn)
        finally:
            conn.close()
        if not ok:
            return []
        started: dict[str, tuple[str, Any]] = {}
        done: list[ToolCall] = []
        for event in iter_events():
            payload = event.payload
            if not isinstance(payload, dict):
                continue
            call_id, tool = payload.get("call_id"), payload.get("tool")
            if not isinstance(call_id, str) or not isinstance(tool, str):
                continue
            if event.kind == "tool_call":
                started[call_id] = (tool, payload.get("input"))
            elif event.kind == "tool_result" and payload.get("success") is True and call_id in started:
                name, args = started[call_id]
                if name == tool and (not tools or tool in tools):
                    out = payload.get("output")
                    done.append(ToolCall(tool, call_id, args, out if isinstance(out, str) else ""))
        return done
    except Exception:  # noqa: BLE001 - an unreadable log proves nothing, so it earns nothing
        return []
