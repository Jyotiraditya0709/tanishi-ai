"""The guard client: the ONLY thing that talks to the Warden.

ToolRegistry.execute() calls guard.check() before running any tool. The client sends the
request to the Warden's Unix socket and returns its Decision. Any problem at all — no
socket, timeout, a malformed reply, the Warden down — returns a DENY. The guard fails
closed: if we cannot be told it is allowed, it is not allowed.

This module is a protected path: agents may not edit it.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass

SOCKET_PATH = os.path.expanduser(os.environ.get("TANISHI_WARDEN_SOCK", "~/.tanishi/warden.sock"))
TIMEOUT_S = float(os.environ.get("TANISHI_WARDEN_TIMEOUT", "5"))
MAX_REPLY_BYTES = 64 * 1024

_VALID = ("allow", "ask", "deny")


@dataclass(frozen=True)
class Decision:
    decision: str      # "allow" | "ask" | "deny"
    reason: str
    tier: int = 0

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def needs_approval(self) -> bool:
        return self.decision == "ask"


def _deny(reason: str) -> Decision:
    return Decision("deny", reason, 0)


def check(tool_name: str, args: dict, actor: str = "tanishi", session_id: str | None = None) -> Decision:
    """Ask the Warden about one tool call. Fails closed (deny) on any error."""
    request = json.dumps(
        {"tool": tool_name, "args": args or {}, "actor": actor, "session_id": session_id or ""},
        ensure_ascii=False,
    ) + "\n"

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT_S)
            sock.connect(SOCKET_PATH)
            sock.sendall(request.encode("utf-8"))
            buf = b""
            while b"\n" not in buf and len(buf) < MAX_REPLY_BYTES:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except (OSError, socket.timeout) as e:
        return _deny(f"warden unreachable ({type(e).__name__}); failing closed")

    if not buf:
        return _deny("warden sent no reply; failing closed")
    try:
        reply = json.loads(buf.decode("utf-8").splitlines()[0])
        decision = reply.get("decision")
        if decision not in _VALID:
            return _deny("warden reply had no valid decision; failing closed")
        return Decision(decision, str(reply.get("reason", "")), int(reply.get("tier", 0)))
    except (json.JSONDecodeError, UnicodeDecodeError, IndexError, AttributeError, TypeError, ValueError):
        return _deny("warden reply unreadable; failing closed")


def check_spend(cost_estimate: float, actor: str = "tanishi") -> Decision:
    """Ask the Warden whether a paid model call fits under today's spend cap. Deny = over cap."""
    return check("paid_model", {"cost_estimate": cost_estimate}, actor=actor)


def record_spend(actual_cost: float, actor: str = "tanishi") -> Decision:
    """Tell the Warden the real cost of a paid call just made, so the daily total stays current."""
    return check("paid_model", {"actual_cost": actual_cost}, actor=actor)
