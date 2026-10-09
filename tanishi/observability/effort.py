"""The Human Effort timer (node OBS2): how many hours the human spent on Tanishi.

``tanishi time start`` and ``tanishi time stop`` append ``effort_start`` and ``effort_stop`` events (actor
``human``) to the Core State event log, so the timer survives across processes and its history is hash-chained
like every other action. Nothing else is stored.

Hours are read back by replaying those events in id order: a start while the timer already runs and a stop
while it is idle are ignored, so a doubled verb (or two terminals racing) can never count time twice or count
time that was not timed. Only finished sessions count; a running one is reported apart.
"""
from __future__ import annotations

import math
import numbers
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tanishi.core_state import events
from tanishi.core_state.db import open_db, resolve_path

START = "effort_start"
STOP = "effort_stop"
ACTOR = "human"
HOURS_PER_RESEARCHER_DAY = 8.0


@dataclass(frozen=True)
class Session:
    start: datetime
    stop: datetime | None  # None while the timer runs


def _utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _read_events(conn: sqlite3.Connection | None) -> list[tuple[str, str]]:
    """(kind, ts) of every timer event, in id order. An empty or unmigrated Core State has none."""
    own = None
    if conn is None:
        path = resolve_path()
        if path != ":memory:" and not Path(path).exists():
            return []  # reading must not create the db
        conn = own = open_db(path)
    try:
        return conn.execute("SELECT kind, ts FROM events WHERE kind IN (?, ?) AND actor = ? ORDER BY id",
                            (START, STOP, ACTOR)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        if own is not None:
            own.close()


def sessions(conn: sqlite3.Connection | None = None) -> list[Session]:
    """Every timed session, oldest first; the last one has ``stop=None`` if the timer is running."""
    out: list[Session] = []
    running: datetime | None = None
    for kind, ts in _read_events(conn):
        try:
            at = _utc(ts)
        except (TypeError, ValueError):
            continue  # a row whose time does not parse cannot be measured
        if kind == START and running is None:
            running = at
        elif kind == STOP and running is not None:
            out.append(Session(running, max(at, running)))  # a clock stepping back counts 0, never negative
            running = None
    if running is not None:
        out.append(Session(running, None))
    return out


def _check_window(window_days: object) -> float | None:
    if window_days is None:
        return None
    if isinstance(window_days, bool) or not isinstance(window_days, numbers.Real):
        raise ValueError(f"window_days must be a positive number, got {window_days!r}")  # noqa: TRY004
    if not math.isfinite(window_days) or window_days <= 0:
        raise ValueError(f"window_days must be a positive number, got {window_days!r}")
    return float(window_days)


def hours(window_days: float | None = None, conn: sqlite3.Connection | None = None,
          now: datetime | None = None) -> float:
    """Hours in finished sessions; with ``window_days``, only the part inside the last ``window_days`` days."""
    days = _check_window(window_days)
    now = now or datetime.now(UTC)
    since = None if days is None else now - timedelta(days=days)
    total = 0.0
    for s in sessions(conn):
        if s.stop is None:
            continue
        start = s.start if since is None else max(s.start, since)
        if s.stop > start:
            total += (s.stop - start).total_seconds()
    return total / 3600


def running_since(conn: sqlite3.Connection | None = None) -> datetime | None:
    found = sessions(conn)
    return found[-1].start if found and found[-1].stop is None else None


def start() -> bool:
    """Start the timer. Returns False, writing nothing, if it is already running."""
    if running_since() is not None:
        return False
    events.emit(START, {}, actor=ACTOR)
    return True


def stop() -> float | None:
    """Stop the timer and return the hours of the session it closed, or None if it was not running."""
    began = running_since()
    if began is None:
        return None
    events.emit(STOP, {}, actor=ACTOR)
    closed = sessions()[-1]
    return 0.0 if closed.stop is None else (closed.stop - closed.start).total_seconds() / 3600


def report(window_days: float | None = None) -> dict:
    """Human Effort so far: finished hours (all time, or in the window), sessions and whether it runs now."""
    days = _check_window(window_days)
    found = sessions()
    now = datetime.now(UTC)
    began = found[-1].start if found and found[-1].stop is None else None
    total = hours(days, now=now)
    return {
        "hours": total,
        "researcher_days": total / HOURS_PER_RESEARCHER_DAY,
        "sessions": sum(1 for s in found if s.stop is not None),
        "window_days": days,
        "running": began is not None,
        "running_since": None if began is None else began.isoformat(),
        "running_hours": None if began is None else max(0.0, (now - began).total_seconds() / 3600),
        "note": "no Human Effort timed yet; run `tanishi time start`" if total == 0 and began is None else "",
    }


def _format(r: dict) -> str:
    scope = "all time" if r["window_days"] is None else f"last {r['window_days']:g} days"
    lines = [(f"Human Effort ({scope}): {r['hours']:.2f} hours in {r['sessions']} session(s)"
              f" = {r['researcher_days']:.2f} researcher-days")]
    if r["running"]:
        lines.append(f"Timer running since {r['running_since']} ({r['running_hours']:.2f} hours so far)")
    if r["note"]:
        lines.append(r["note"])
    return "\n".join(lines)


def cli(argv: list[str]) -> int:
    """``tanishi time start | stop | report [days]``. Returns the exit code."""
    usage = "usage: tanishi time start | stop | report [window_days]"
    if not argv or argv[0] not in ("start", "stop", "report"):
        print(usage, file=sys.stderr)
        return 2
    verb, rest = argv[0], argv[1:]
    try:
        if verb == "start":
            print("Timer started." if start() else "Timer is already running.")
        elif verb == "stop":
            closed = stop()
            print("Timer was not running." if closed is None else f"Timer stopped: {closed:.2f} hours.")
        else:
            if len(rest) > 1:
                print(usage, file=sys.stderr)
                return 2
            window = float(rest[0]) if rest else None
            print(_format(report(window)))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"tanishi time {verb}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli(sys.argv[1:]))
