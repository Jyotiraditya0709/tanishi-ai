"""Calendar helper — supports a simple local JSON file so the briefing and sentinel
work immediately without OAuth setup.

Drop your events into `proactive_events.json` at the project root:

[
  {"id": "1", "title": "Standup",        "start": "2026-04-08T09:00:00"},
  {"id": "2", "title": "Lunch with Mom", "start": "2026-04-08T13:00:00"},
  {"id": "3", "title": "DBMS exam",      "start": "2026-04-08T15:00:00"}
]

To wire up real Google Calendar later, replace `_load_events()` with a call
to google-api-python-client. The rest of the interface stays the same.
"""
import json
from datetime import datetime
from pathlib import Path

EVENTS_FILE = Path(__file__).resolve().parents[2] / "proactive_events.json"


def _load_events() -> list:
    if not EVENTS_FILE.exists():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[calendar] failed to load {EVENTS_FILE}: {e}")
        return []


def get_today_events() -> list:
    """Return today's events sorted by start time."""
    events = _load_events()
    today = datetime.now().date()
    out = []
    for e in events:
        try:
            start = datetime.fromisoformat(e["start"])
            if start.date() == today:
                out.append({
                    "id": str(e.get("id", start.isoformat())),
                    "title": e["title"],
                    "time": start.strftime("%I:%M %p"),
                    "start_dt": start,
                })
        except Exception:
            continue
    return sorted(out, key=lambda x: x["start_dt"])


def get_upcoming_events(within_minutes: int = 5) -> list:
    """Return events whose start is within the next N minutes."""
    events = _load_events()
    now = datetime.now()
    out = []
    for e in events:
        try:
            start = datetime.fromisoformat(e["start"])
            delta_min = (start - now).total_seconds() / 60
            if 0 <= delta_min <= within_minutes:
                out.append({
                    "id": str(e.get("id", start.isoformat())),
                    "title": e["title"],
                    "minutes_until": int(round(delta_min)),
                })
        except Exception:
            continue
    return out
