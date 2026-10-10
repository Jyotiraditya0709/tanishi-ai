"""Traces (node OBS1): a readable, step-by-step record of every task, linked to its events.

``with span(name, **attrs) as s:`` times the block and, when it exits, appends one JSON line to
``$TANISHI_HOME/traces/<UTC date>.jsonl``. Spans nest per context (a ContextVar), so concurrent asyncio tasks
keep separate trees. Each record holds the span's id, its parent's id, the task id from
``events.current_task()``, the model, tokens in and out, latency, tool calls and the ids of the events emitted
inside it (innermost open span only). Values known only later (tokens after a model call) go in through
``s.set(...)``. A ``tool_call`` event emitted inside a span adds its tool to the span's tool calls.

Tracing fails open: nothing here may break the traced code. Every failure is swallowed (one warning per
process, exception type only), and the body's own exception propagates unchanged. Records are redacted with
the event log's ``redact()`` and clipped before they reach disk.

``python -m tanishi.observability.trace <task_id>`` prints one task's spans as an indented tree.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tanishi.core_state import events
from tanishi.core_state.db import tanishi_home

logger = logging.getLogger(__name__)

TRACES_DIR = "traces"  # under $TANISHI_HOME
MAX_CHARS = 2000  # every string in a record is clipped to this, after redaction
MAX_DEPTH = 8  # deeper containers in attrs become a marker
MAX_ITEMS = 200  # longest list kept in a record (event ids, tool calls, attrs)
_KNOWN = ("model", "tokens_in", "tokens_out", "tool_calls")

_current: ContextVar[Span | None] = ContextVar("tanishi_current_span", default=None)
_warned = False


class Span:
    """One open span. ``set(**attrs)`` adds or replaces attributes before the span is written."""

    def __init__(self, name: Any, attrs: dict[str, Any], parent: Span | None) -> None:
        self.name = name
        self.attrs = dict(attrs)
        self.parent = parent
        self.span_id = uuid.uuid4().hex[:16]
        self.event_ids: list[int] = []
        self.event_tools: list[str] = []
        self.task_id: Any = None
        self.session_id: Any = None
        self.start_ts = datetime.now(UTC).isoformat(timespec="microseconds")
        self.t0 = time.perf_counter()

    def set(self, **attrs: Any) -> None:
        try:
            self.attrs.update(attrs)
        except Exception as exc:  # noqa: BLE001 - tracing fails open
            _warn(exc)


@contextmanager
def span(name: str, /, **attrs: Any) -> Iterator[Span]:
    """Trace the block as one span; see the module docstring. Never raises on its own account."""
    s = _open(name, attrs)
    token = _current.set(s)
    try:
        yield s
    except BaseException as exc:
        _close(s, exc)
        raise
    else:
        _close(s, None)
    finally:
        try:
            _current.reset(token)
        except Exception:  # noqa: BLE001 - exited in another context than it was entered in
            _current.set(s.parent)


def _open(name: Any, attrs: dict[str, Any]) -> Span:
    parent = _current.get()
    s = Span(name, attrs, parent)
    try:
        s.task_id, s.session_id = events.current_task()
    except Exception as exc:  # noqa: BLE001 - tracing fails open
        _warn(exc)
        if parent is not None:  # better the parent's task than none
            s.task_id, s.session_id = parent.task_id, parent.session_id
    if "task_id" in s.attrs:
        s.task_id = s.attrs.pop("task_id")
    return s


def _close(s: Span, exc: BaseException | None) -> None:
    try:
        latency_ms = (time.perf_counter() - s.t0) * 1000.0
        _write(_record(s, latency_ms, exc))
    except Exception as err:  # noqa: BLE001 - tracing fails open
        _warn(err)


def _on_event(id_: int, kind: str, payload: Any) -> None:
    """events listener: the innermost open span in this context owns the event."""
    s = _current.get()
    if s is None:
        return
    s.event_ids.append(id_)
    if kind == "tool_call" and isinstance(payload, dict) and isinstance(payload.get("tool"), str):
        s.event_tools.append(payload["tool"])


events.add_listener(_on_event)


# ------------------------------------------------------------------ records


def _record(s: Span, latency_ms: float, exc: BaseException | None) -> dict[str, Any]:
    attrs = dict(s.attrs)
    known = {k: attrs.pop(k, None) for k in _KNOWN}
    given = known["tool_calls"]
    if given is None:
        tools: list[Any] = []
    elif isinstance(given, (list, tuple)):
        tools = list(given)
    else:
        tools = [given]
    rec = {
        "span_id": s.span_id,
        "parent_id": s.parent.span_id if s.parent is not None else None,
        "task_id": s.task_id,
        "session_id": s.session_id,
        "name": s.name,
        "start_ts": s.start_ts,
        "latency_ms": round(latency_ms, 3),
        "status": "ok" if exc is None else "error",
        "error": None if exc is None else type(exc).__name__,
        "model": known["model"],
        "tokens_in": known["tokens_in"],
        "tokens_out": known["tokens_out"],
        "tool_calls": tools + s.event_tools,
        "event_ids": list(s.event_ids),
        "attrs": attrs,
    }
    return events.redact(_plain(rec, 1))


def _plain(v: Any, depth: int) -> Any:
    """`v` as plain JSON: odd objects become their repr, non-finite floats a string, strings clipped."""
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, str):
        return _clip(v)
    if depth > MAX_DEPTH:
        return "[TRUNCATED: nested too deep]"
    if isinstance(v, dict):
        return {_clip(str(k)): _plain(x, depth + 1) for k, x in list(v.items())[:MAX_ITEMS]}
    if isinstance(v, (list, tuple)):
        return [_plain(x, depth + 1) for x in list(v)[:MAX_ITEMS]]
    try:
        text = repr(v)
    except Exception:  # noqa: BLE001 - a broken __repr__ must not stop the span being written
        text = f"<{type(v).__name__}>"
    return _clip(text)


def _clip(text: str) -> str:
    # Redact before clipping, so a cut can never leave half a key behind.
    text = events.redact(text)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + f"... [{len(text) - MAX_CHARS} more chars]"


def traces_dir() -> Path:
    return tanishi_home() / TRACES_DIR


def _write(rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    d = traces_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = d / f"{datetime.now(UTC).date().isoformat()}.jsonl"
    # One write() per line on an O_APPEND fd, so concurrent writers do not interleave within a line.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8", errors="replace"))
    finally:
        os.close(fd)


def _warn(exc: BaseException) -> None:
    global _warned
    try:
        if not _warned:
            _warned = True
            logger.warning("tracing failed, the task goes on: %s", type(exc).__name__)
        else:
            logger.debug("tracing failed: %s", type(exc).__name__)
    except Exception:  # noqa: BLE001, S110 - a broken logger must not break the task
        pass


# ------------------------------------------------------------------ reading and the CLI


def load(task_id: str) -> list[dict[str, Any]]:
    """Every span recorded for `task_id`, from all trace files. Unreadable lines are skipped."""
    out: list[dict[str, Any]] = []
    d = traces_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("task_id") == task_id:
                out.append(rec)
    return out


def tree(recs: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """(depth, record) pairs, parents before children, siblings by start time. Orphans print as roots."""
    ids = {r.get("span_id") for r in recs}
    kids: dict[Any, list[dict[str, Any]]] = {}
    for r in recs:
        parent = r.get("parent_id")
        kids.setdefault(parent if parent in ids else None, []).append(r)
    for group in kids.values():
        group.sort(key=lambda r: str(r.get("start_ts", "")))
    out: list[tuple[int, dict[str, Any]]] = []
    seen: set[Any] = set()

    def walk(parent: Any, depth: int) -> None:
        for r in kids.get(parent, []):
            if id(r) in seen:
                continue
            seen.add(id(r))
            out.append((depth, r))
            if r.get("span_id") is not None:
                walk(r["span_id"], depth + 1)

    walk(None, 0)
    out.extend((0, r) for r in recs if id(r) not in seen)  # parent cycles: still show them
    return out


def format_span(depth: int, r: dict[str, Any]) -> str:
    parts = [f"{'  ' * depth}{r.get('name')}", f"{float(r.get('latency_ms') or 0):.1f} ms"]
    if r.get("status") == "error":
        parts.append(f"ERROR {r.get('error')}")
    if r.get("model") is not None:
        parts.append(f"model={r['model']}")
    if r.get("tokens_in") is not None or r.get("tokens_out") is not None:
        parts.append(f"tokens={r.get('tokens_in')}/{r.get('tokens_out')}")
    if r.get("tool_calls"):
        parts.append("tools=" + ",".join(str(t) for t in r["tool_calls"]))
    parts.append("events=" + ",".join(str(e) for e in r.get("event_ids") or []))
    if r.get("attrs"):
        parts.append(json.dumps(r["attrs"], ensure_ascii=False, sort_keys=True))
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m tanishi.observability.trace <task_id>", file=sys.stderr)
        return 2
    task_id = args[0]
    try:
        recs = load(task_id)
        if not recs:
            print(f"no trace for task {task_id}", file=sys.stderr)
            return 1
        print(f"task {task_id}: {len(recs)} spans")
        for depth, r in tree(recs):
            print(format_span(depth, r))
    except Exception as exc:  # noqa: BLE001 - a clean message, never a traceback
        print(f"could not read traces: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
