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

import inspect
import json
import logging
import math
import os
import sys
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from tanishi.core_state import events
from tanishi.core_state.db import tanishi_home

logger = logging.getLogger(__name__)

TRACES_DIR = "traces"  # under $TANISHI_HOME
MAX_CHARS = 2000  # every string in a record is clipped to this, after redaction
MAX_DEPTH = 8  # deeper containers in attrs become a marker
MAX_ITEMS = 200  # longest list kept in a record (event ids, tool calls, attrs)
MAX_RECORD_BYTES = 64 * 1024  # one serialised record; over it, attrs are dropped and the record is marked
_KNOWN = ("model", "tokens_in", "tokens_out", "tool_calls")
_LINE_BREAKS = (chr(0x2028), chr(0x2029), chr(0x85))  # str.splitlines() breaks on these; json.dumps keeps them

_current: ContextVar[Span | None] = ContextVar("tanishi_current_span", default=None)
_warned = False


class Span:
    """One open span. ``set(**attrs)`` adds or replaces attributes before the span is written."""

    def __init__(self, name: Any, attrs: dict[str, Any], parent: Span | None, holder: FrameType | None) -> None:
        self.name = name
        self.attrs = dict(attrs)
        self.parent = parent
        self.holder = holder  # the generator frame the span is open in, if any (see _holder)
        self.closed = False
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


def span(name: str, /, **attrs: Any) -> AbstractContextManager[Span]:
    """Trace the block as one span; see the module docstring. Never raises on its own account."""
    try:
        holder = _holder(sys._getframe(1))
    except Exception as exc:  # noqa: BLE001 - tracing fails open
        _warn(exc)
        holder = None
    return _span(name, attrs, holder)


@contextmanager
def _span(name: Any, attrs: dict[str, Any], holder: FrameType | None) -> Iterator[Span]:
    s = _open(name, attrs, holder)
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


def _open(name: Any, attrs: dict[str, Any], holder: FrameType | None) -> Span:
    try:
        parent = _innermost()
    except Exception as exc:  # noqa: BLE001 - tracing fails open
        _warn(exc)
        parent = _current.get()
    s = Span(name, attrs, parent, holder)
    try:
        s.task_id, s.session_id = events.current_task()
    except Exception as exc:  # noqa: BLE001 - tracing fails open
        _warn(exc)
        if parent is not None:  # better the parent's task than none
            s.task_id, s.session_id = parent.task_id, parent.session_id
    if "task_id" in s.attrs:
        s.task_id = s.attrs.pop("task_id")
    if s.task_id is not None and parent is not None and parent.task_id is None:
        # A task started inside an open span (a wrapper around think()): that span is the task's root. It is
        # written after its children, so it still gets the id. A span around several tasks goes to the first.
        parent.task_id, parent.session_id = s.task_id, s.session_id
    return s


# A generator driven by contextlib (a @contextmanager wrapper around span()) runs its with-block in the frame that
# entered it, so it does not hold a span across a yield. Any other generator or async generator does.
_GENERATOR_FLAGS = inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR


def _holder(frame: FrameType | None) -> FrameType | None:
    """The generator frame a span opened from `frame` stays open in across yields, or None for a plain frame."""
    while frame is not None and frame.f_code.co_flags & _GENERATOR_FLAGS:
        back = frame.f_back
        if back is None or back.f_globals.get("__name__") != "contextlib":
            return frame
        while back is not None and back.f_globals.get("__name__") == "contextlib":
            back = back.f_back
        frame = back  # a contextlib wrapper: the span belongs to whoever entered it
    return None


def _innermost() -> Span | None:
    """The span that owns what happens now: the current one, skipping spans already closed and spans held open
    by a generator that is suspended at a yield (its consumer's events and child spans are the consumer's)."""
    s = _current.get()
    running: set[int] | None = None
    while s is not None and (s.closed or s.holder is not None):
        if not s.closed:
            if running is None:
                running = set()
                f: FrameType | None = sys._getframe(1)
                while f is not None:
                    running.add(id(f))
                    f = f.f_back
            if id(s.holder) in running:
                return s
        s = s.parent
    return s


def _close(s: Span, exc: BaseException | None) -> None:
    s.closed, s.holder = True, None  # drop the frame: it holds this span in its locals
    latency_ms = (time.perf_counter() - s.t0) * 1000.0
    try:
        _write(_record(s, latency_ms, exc))
        return
    except Exception as err:  # noqa: BLE001 - tracing fails open
        _warn(err)
    try:  # once more without attrs, so ids, latency, status and event links survive
        _write(_bare(s, latency_ms, exc))
    except Exception as err:  # noqa: BLE001 - tracing fails open
        _warn(err)


def _on_event(id_: int, kind: str, payload: Any) -> None:
    """events listener: the innermost open span in this context owns the event (see _innermost)."""
    s = _innermost()
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
    # A tool both the caller passed and the registry's tool_call event reported counts once.
    left = Counter(t for t in tools if isinstance(t, str))
    for t in s.event_tools:
        if left[t] > 0:
            left[t] -= 1
        else:
            tools.append(t)
    rec = _bare(s, latency_ms, exc)
    rec.update(
        model=_safe(known["model"], 2),
        tokens_in=_safe(known["tokens_in"], 2),
        tokens_out=_safe(known["tokens_out"], 2),
        tool_calls=_safe(tools, 2),
        # One attr json.dumps rejects becomes a placeholder; the others stay.
        attrs={_clip(str(k)): _safe(v, 3) for k, v in list(attrs.items())[:MAX_ITEMS]},
    )
    return events.redact(rec)


def _bare(s: Span, latency_ms: float, exc: BaseException | None) -> dict[str, Any]:
    """The record without caller attrs: what is still written when the full record cannot be."""
    n = len(s.event_ids)
    return {
        "span_id": s.span_id,
        "parent_id": s.parent.span_id if s.parent is not None else None,
        "task_id": _safe(s.task_id, 2),
        "session_id": _safe(s.session_id, 2),
        "name": _safe(s.name, 2),
        "start_ts": s.start_ts,
        "latency_ms": round(latency_ms, 3),
        "status": "ok" if exc is None else "error",
        "error": None if exc is None else type(exc).__name__,
        "model": None,
        "tokens_in": None,
        "tokens_out": None,
        "tool_calls": [],
        "event_ids": s.event_ids[:MAX_ITEMS],
        "event_count": n,  # all ids linked inside the span; event_ids keeps the first MAX_ITEMS
        "events_truncated": max(0, n - MAX_ITEMS),
        "attrs": {},
        "truncated": None,  # set when the record was over MAX_RECORD_BYTES, see _encode
    }


def _safe(v: Any, depth: int) -> Any:
    """`_plain(v)`, or a placeholder string when the result would still not serialise (an int past 4300 digits)."""
    try:
        p = _plain(v, depth)
        json.dumps(p, allow_nan=False)
        return p
    except Exception:  # noqa: BLE001 - one bad value must not cost the span
        return f"[UNSERIALISABLE: {_clip(type(v).__name__)}]"


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
    data = _encode(rec)
    d = traces_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = d / f"{datetime.now(UTC).date().isoformat()}.jsonl"
    # One O_APPEND fd per line, so concurrent writers do not interleave within a line in the usual case (one
    # write() call). A short write (disk full, signal) is finished with more calls, never left without its "\n".
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("trace write made no progress")
            view = view[n:]
    finally:
        os.close(fd)


def _encode(rec: dict[str, Any]) -> bytes:
    """The record as one UTF-8 line of at most MAX_RECORD_BYTES: over it, attrs and then tool calls are dropped,
    and ``truncated`` says what went and how big the record was."""
    data = _line(rec).encode("utf-8", errors="replace")
    if len(data) <= MAX_RECORD_BYTES:
        return data
    size, dropped = len(data), []
    for key, empty in (("attrs", {}), ("tool_calls", [])):
        rec = {**rec, key: empty, "truncated": {"bytes": size, "limit": MAX_RECORD_BYTES, "dropped": [*dropped, key]}}
        dropped.append(key)
        data = _line(rec).encode("utf-8", errors="replace")
        if len(data) <= MAX_RECORD_BYTES:
            break
    # What is left (ids, name, model, task and session ids, each clipped to MAX_CHARS) is normally a few KB; only a
    # container passed as task_id could still push it over, and that line is written as is.
    return data


def _line(rec: dict[str, Any]) -> str:
    """One JSON line. U+2028, U+2029 and U+0085 are escaped too, so readers that use splitlines() stay safe."""
    text = json.dumps(rec, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for ch in _LINE_BREAKS:  # outside "\n" these occur only inside JSON strings, where \uXXXX is valid
        text = text.replace(ch, f"\\u{ord(ch):04x}")
    return text + "\n"


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
            # "\n" only: splitlines() would also cut at U+2028, U+2029 and U+0085 inside a value.
            lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            # As strings: the CLI's argument is a str, but a caller may have passed task_id=42.
            got = rec.get("task_id") if isinstance(rec, dict) else None
            if got is not None and str(got) == str(task_id):
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
    stack = [(0, r) for r in reversed(kids.get(None, []))]  # explicit stack: any depth, no RecursionError
    while stack:
        depth, r = stack.pop()
        if id(r) in seen:
            continue
        seen.add(id(r))
        out.append((depth, r))
        if r.get("span_id") is not None:
            stack.extend((depth + 1, c) for c in reversed(kids.get(r["span_id"], [])))
    out.extend((0, r) for r in recs if id(r) not in seen)  # parent cycles: still show them
    return out


# C0, DEL, C1 and the Unicode line and paragraph separators. Names, models and tool names can come from the model,
# so none of these may reach the terminal raw: a newline would forge tree lines, ESC would drive the terminal.
_UNPRINTABLE = {c: f"\\x{c:02x}" for c in (*range(0x20), *range(0x7F, 0xA0))}
_UNPRINTABLE.update({ord("\n"): "\\n", ord("\r"): "\\r", ord("\t"): "\\t", 0x2028: "\\u2028", 0x2029: "\\u2029"})


def printable(text: str) -> str:
    """`text` with every control character written as an escape, so one record prints as one plain line."""
    return text.translate(_UNPRINTABLE)


def format_span(depth: int, r: dict[str, Any]) -> str:
    return printable(_format_span(depth, r))


def _format_span(depth: int, r: dict[str, Any]) -> str:
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
            print(f"no trace for task {printable(task_id)}", file=sys.stderr)
            return 1
        print(f"task {printable(task_id)}: {len(recs)} spans")
        for depth, r in tree(recs):
            print(format_span(depth, r))
    except Exception as exc:  # noqa: BLE001 - a clean message, never a traceback
        print(f"could not read traces: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
