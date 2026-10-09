"""The six North Star numbers (node OBS2): CEI, RCR, CAR, AR, IA and Human Effort, in one place.

``compute(window_days=90)`` reads the Core State and returns, for each number, its ``value``, the ``inputs`` it
was computed from and an ``explanation``, so a 0 (or a number that cannot be computed yet) always says why.
It only reads: it never creates, migrates or writes the database.

What each number is built from (decision 0014):

- **CEI** = B x T x H x N x A x P (``cei``). B is the count of Reality-verified mastered capabilities
  (``capabilities.state = 'mastered'``). T, H, N, A and P are defined in the master plan, which is not in the repo;
  until a node wires them they show ``None`` with that reason, and CEI is 0 only when a measured factor is 0.
- **RCR** = discoveries per researcher-month (``rcr``): 8 Human Effort hours are one researcher-day and a month is
  30 researcher-days, which is what makes the card's example (3 discoveries, 10 hours) give 72. The funnel is read
  from the OBS3 experiment ledger: experiments run, experiments with enough paired seeds to judge, and discoveries
  (experiments whose gain ``is_real_gain`` calls real). Under ``MIN_RCR_HOURS`` (0.1 h) of timed effort RCR is
  ``None``: a few seconds of timer and one discovery would otherwise read as a breakthrough.
- **CAR, AR, IA** have no definition in the repo yet, so they show ``None`` and say so (open-problems/OBS2.md).
- **Human Effort** is the hours timed with ``tanishi time`` (``effort.py``) inside the window.
"""
from __future__ import annotations

import math
import numbers
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tanishi.core_state.db import open_db, resolve_path
from tanishi.observability import effort
from tanishi.observability.attribution import MIN_SEEDS, is_real_gain
from tanishi.observability.experiments import seed_scores

HOURS_PER_RESEARCHER_DAY = effort.HOURS_PER_RESEARCHER_DAY
RESEARCHER_DAYS_PER_MONTH = 30.0
MIN_RCR_HOURS = 0.1  # below this RCR is a timer artefact, not a rate (decision 0018, item 8)
MASTERED = "mastered"
CEI_FACTORS = ("B", "T", "H", "N", "A", "P")
_NOT_IN_REPO = "defined in the master plan, which is not in the repo yet; no node measures it (open-problems/OBS2.md)"


def _number(x: Any, name: str) -> float:
    """A finite real number >= 0, not a bool. Anything else is a ValueError (decision 0011, rule 12)."""
    if isinstance(x, bool) or not isinstance(x, numbers.Real):
        raise ValueError(f"{name} must be a number, got {x!r}")  # noqa: TRY004
    if not _finite(x) or x < 0:
        raise ValueError(f"{name} must be a finite number >= 0, got {x!r}")
    return x


def _finite(x: float) -> bool:
    try:
        return math.isfinite(x)
    except OverflowError:  # an int too large for a float
        return False


def _result(x: float, name: str) -> float:
    """A computed number must be finite: inf is not strict JSON and not a measurement."""
    if not _finite(x):
        raise ValueError(f"{name} overflowed: its inputs are too large to give a finite number")
    return x


def cei(b: float, t: float, h: float, n: float, a: float, p: float) -> float | None:
    """CEI = B x T x H x N x A x P. Every factor is a finite number >= 0.

    A product too large to be finite is None (not measurable), not inf and not a ValueError: the red-team test
    calls ``cei(1e200, 1e200, ...)`` without catching an error (open-problems/OBS2-repair-conflicts.md).
    """
    factors = [_number(v, name) for v, name in zip((b, t, h, n, a, p), CEI_FACTORS, strict=True)]
    product = math.prod(factors)
    return product if _finite(product) else None


def rcr(discoveries: float, hours: float) -> float:
    """Discoveries per researcher-month: discoveries / (hours / 8) x 30. Hours must be > 0."""
    d = _number(discoveries, "discoveries")
    h = _number(hours, "hours")
    if h == 0:
        raise ValueError("RCR needs Human Effort hours > 0; with no hours timed there is nothing to divide by")
    return _result(d / (h / HOURS_PER_RESEARCHER_DAY) * RESEARCHER_DAYS_PER_MONTH, "RCR")


def _rows(conn: sqlite3.Connection | None, sql: str, args: tuple = ()) -> list[tuple]:
    """Rows of a read, or none when there is no db or it was never migrated."""
    if conn is None:
        return []
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def _utc(ts: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _funnel(conn: sqlite3.Connection | None, since: datetime) -> dict[str, int]:
    """The research funnel over experiments with a ledger row in the window."""
    pairs: set[tuple[str, str]] = set()
    for baseline, candidate, ts in _rows(conn, "SELECT baseline, candidate, ts FROM experiments"):
        at = _utc(ts)
        if at is not None and at >= since and baseline is not None and candidate is not None:
            pairs.add((baseline, candidate))
    judged = discoveries = unreadable = 0
    for baseline, candidate in sorted(pairs):
        try:
            base, cand = seed_scores(conn, baseline, candidate)
            if len(base) < MIN_SEEDS:
                continue
            judged += 1
            if is_real_gain(base, cand).real:
                discoveries += 1
        except ValueError:  # a ledger the OBS3 rules refuse to read counts as run, never as a discovery
            unreadable += 1
    return {"experiments": len(pairs), "judged": judged, "discoveries": discoveries, "unreadable": unreadable}


def _cei_entry(conn: sqlite3.Connection | None) -> dict[str, Any]:
    mastered = _rows(conn, "SELECT COUNT(*) FROM capabilities WHERE state = ?", (MASTERED,))
    b = mastered[0][0] if mastered else 0
    inputs: dict[str, dict[str, Any]] = {
        "B": {"value": b, "source": f"capabilities with state '{MASTERED}' (Reality-verified mastery)"},
    }
    for name in CEI_FACTORS[1:]:
        inputs[name] = {"value": None, "source": _NOT_IN_REPO}
    values = [f["value"] for f in inputs.values()]
    if b == 0:
        return {"value": 0, "inputs": inputs, "unit": "index",
                "explanation": "CEI is 0 because B is 0: no Reality-verified mastery yet."}
    if any(v is None for v in values):
        missing = ", ".join(k for k, f in inputs.items() if f["value"] is None)
        return {"value": None, "inputs": inputs, "unit": "index",
                "explanation": f"CEI cannot be computed yet: {missing} are not measured by any node."}
    value = cei(*values)
    why = "CEI = B x T x H x N x A x P." if value is not None else "CEI overflowed: the product is not finite."
    return {"value": value, "inputs": inputs, "unit": "index", "explanation": why}


def _rcr_entry(funnel: dict[str, int], hours: float, days: float) -> dict[str, Any]:
    d = funnel["discoveries"]
    inputs = {"human_effort_hours": hours, "researcher_days": hours / HOURS_PER_RESEARCHER_DAY,
              "funnel": funnel, "window_days": days}
    unit = "discoveries per researcher-month (8 hours = 1 researcher-day, 30 researcher-days = 1 month)"
    if hours == 0 and d == 0:
        return {"value": 0, "inputs": inputs, "unit": unit,
                "explanation": "RCR is 0: no discoveries and no Human Effort hours timed yet."}
    if hours == 0:
        return {"value": None, "inputs": inputs, "unit": unit,
                "explanation": f"RCR is unknown: {d} discoveries but no Human Effort hours timed to divide by."}
    if hours < MIN_RCR_HOURS:
        return {"value": None, "inputs": inputs, "unit": unit,
                "explanation": (f"RCR is unknown: only {hours * 60:.1f} minutes of Human Effort timed; it needs at"
                                f" least {MIN_RCR_HOURS:g} hours to be a rate, not a timer artefact.")}
    if d == 0:
        why = ("no experiments in the ledger in this window" if funnel["experiments"] == 0
               else f"none of {funnel['experiments']} experiments showed a gain larger than measured noise")
        return {"value": 0.0, "inputs": inputs, "unit": unit, "explanation": f"RCR is 0: {why}."}
    return {"value": rcr(d, hours), "inputs": inputs, "unit": unit,
            "explanation": f"{d} discoveries in {hours:.2f} Human Effort hours."}


def _undefined_entry(name: str, conn_open: bool, days: float) -> dict[str, Any]:
    return {"value": None, "inputs": {"definition": f"{name} is {_NOT_IN_REPO}", "window_days": days,
                                      "core_state_found": conn_open},
            "unit": None, "explanation": f"{name} is not computed yet: its definition is not in the repo."}


def _effort_entry(hours: float, days: float, conn: sqlite3.Connection | None) -> dict[str, Any]:
    finished = [s for s in effort.sessions(conn) if s.stop is not None] if conn is not None else []
    inputs = {"hours": hours, "researcher_days": hours / HOURS_PER_RESEARCHER_DAY,
              "sessions_all_time": len(finished), "window_days": days, "source": "tanishi time start | stop"}
    why = (f"{hours:.2f} hours timed in the last {days:g} days." if hours
           else "Human Effort is 0: no time logged with `tanishi time` in this window.")
    return {"value": hours, "inputs": inputs, "unit": "hours", "explanation": why}


def _open_existing() -> sqlite3.Connection | None:
    path = resolve_path()
    if path != ":memory:" and not Path(path).exists():
        return None  # reading must not create the db
    return open_db(path)


def compute(window_days: float = 90) -> dict[str, dict[str, Any]]:
    """All six North Star numbers over the last ``window_days`` days, each with its inputs and an explanation."""
    days = effort.check_window(window_days)
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    conn = _open_existing()
    try:
        hours = effort.hours(days, conn=conn, now=now) if conn is not None else 0.0
        funnel = _funnel(conn, since)
        return {
            "CEI": _cei_entry(conn),
            "RCR": _rcr_entry(funnel, hours, days),
            "CAR": _undefined_entry("CAR", conn is not None, days),
            "AR": _undefined_entry("AR", conn is not None, days),
            "IA": _undefined_entry("IA", conn is not None, days),
            "human_effort": _effort_entry(hours, days, conn),
        }
    finally:
        if conn is not None:
            conn.close()


def format_report(result: dict[str, dict[str, Any]]) -> str:
    """The six numbers as plain text, one line each, with the reason under it."""
    lines = []
    for name, entry in result.items():
        value = entry["value"]
        shown = "n/a" if value is None else f"{value:.4g}"
        lines.append(f"{name:<13}{shown:>10}  {entry.get('unit') or ''}".rstrip())
        lines.append(f"{'':<13}{'':>10}  {entry['explanation']}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(format_report(compute(float(sys.argv[1]) if len(sys.argv) > 1 else 90)))
