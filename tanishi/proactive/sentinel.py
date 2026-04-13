"""Proactive Sentinel — the thing that makes Tanishi feel alive.

Runs every 60 seconds in a background thread. Checks:
  - battery (critical <15%, low <25%)
  - CPU (spike >90%)
  - RAM (>90%)
  - continuous-use break reminder (>90 min)
  - calendar (meeting starting in next 5 min)

When a threshold trips AND its cooldown has elapsed, Tanishi speaks unprompted.
Every alert has a cooldown so she doesn't nag.

State persists to `proactive_state.json` so cooldowns survive restarts.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import psutil

from tanishi.proactive.proactive_speak import speak_through_brain

STATE_FILE = Path(__file__).resolve().parents[2] / "proactive_state.json"
CHECK_INTERVAL_SEC = 60

# ---------- thresholds (tune freely) ----------
BATTERY_CRITICAL_PCT = 15
BATTERY_LOW_PCT = 25
CPU_HIGH_PCT = 90
RAM_HIGH_PCT = 90
BREAK_INTERVAL_MIN = 90

# ---------- cooldowns in seconds ----------
COOLDOWN = {
    "battery_critical": 600,    # 10 min
    "battery_low": 1800,        # 30 min
    "cpu_high": 1200,           # 20 min
    "ram_high": 1200,           # 20 min
    "break_reminder": 3600,     # 60 min
    "meeting_5min": 300,        # 5 min per meeting
}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s.setdefault("last_alerts", {})
            s.setdefault("session_start", time.time())
            return s
        except Exception:
            pass
    return {"last_alerts": {}, "session_start": time.time()}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[sentinel] state save failed: {e}")


def _can_alert(state: dict, key: str) -> bool:
    last = state["last_alerts"].get(key, 0)
    return (time.time() - last) > COOLDOWN.get(key, 600)


def _mark_alert(state: dict, key: str) -> None:
    state["last_alerts"][key] = time.time()
    _save_state(state)


# ---------- individual checks ----------

def _check_battery(state: dict) -> None:
    bat = psutil.sensors_battery()
    if not bat:
        return
    if bat.power_plugged:
        return
    pct = bat.percent
    if pct <= BATTERY_CRITICAL_PCT and _can_alert(state, "battery_critical"):
        speak_through_brain(
            f"Battery critically low at {pct:.0f} percent. Plug in immediately or I lose you.",
            mood="urgent",
        )
        _mark_alert(state, "battery_critical")
    elif pct <= BATTERY_LOW_PCT and _can_alert(state, "battery_low"):
        speak_through_brain(
            f"Battery's at {pct:.0f} percent, sir. Grab the charger when you get a chance.",
            mood="casual",
        )
        _mark_alert(state, "battery_low")


def _check_cpu(state: dict) -> None:
    cpu = psutil.cpu_percent(interval=1)
    if cpu >= CPU_HIGH_PCT and _can_alert(state, "cpu_high"):
        # Find the top offender
        top = "unknown"
        try:
            procs = [(p.info["name"], p.info["cpu_percent"])
                     for p in psutil.process_iter(["name", "cpu_percent"])]
            procs.sort(key=lambda x: x[1] or 0, reverse=True)
            if procs:
                top = procs[0][0]
        except Exception:
            pass
        speak_through_brain(
            f"CPU pegged at {cpu:.0f} percent. Top offender looks like {top}. You might want to investigate.",
            mood="concerned",
        )
        _mark_alert(state, "cpu_high")


def _check_ram(state: dict) -> None:
    ram = psutil.virtual_memory().percent
    if ram >= RAM_HIGH_PCT and _can_alert(state, "ram_high"):
        speak_through_brain(
            f"RAM usage at {ram:.0f} percent. You're about to start swapping. Close some Chrome tabs.",
            mood="concerned",
        )
        _mark_alert(state, "ram_high")


def _check_break(state: dict) -> None:
    session_min = (time.time() - state["session_start"]) / 60
    if session_min >= BREAK_INTERVAL_MIN and _can_alert(state, "break_reminder"):
        speak_through_brain(
            f"Sir, you've been heads-down for {session_min:.0f} minutes straight. "
            f"Stand up, drink water, and look at something more than two feet away. "
            f"I'll still be here when you get back.",
            mood="caring",
        )
        _mark_alert(state, "break_reminder")
        state["session_start"] = time.time()
        _save_state(state)


def _check_calendar(state: dict) -> None:
    """5-minute meeting warning. Silently skips if no calendar wired up."""
    try:
        from tanishi.proactive.calendar_helper import get_upcoming_events
        events = get_upcoming_events(within_minutes=5)
    except Exception:
        return
    for event in events:
        key = f"meeting_{event['id']}"
        if _can_alert(state, key) or key not in state["last_alerts"]:
            speak_through_brain(
                f"Heads up — your meeting '{event['title']}' starts in {event['minutes_until']} minutes.",
                mood="alert",
            )
            state["last_alerts"][key] = time.time()
            _save_state(state)


def run_sentinel() -> None:
    """Main sentinel loop. Blocks forever. Designed to run in a background thread."""
    print(f"[sentinel] starting — interval {CHECK_INTERVAL_SEC}s, {len(COOLDOWN)} checks armed")
    state = _load_state()
    state["session_start"] = time.time()
    _save_state(state)

    while True:
        try:
            _check_battery(state)
            _check_cpu(state)
            _check_ram(state)
            _check_break(state)
            _check_calendar(state)
        except KeyboardInterrupt:
            print("[sentinel] shutdown")
            break
        except Exception as e:
            print(f"[sentinel] check cycle failed: {e}")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run_sentinel()
