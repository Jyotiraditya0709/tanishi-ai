"""Morning Daily Briefing — the alfred_ / JARVIS "Good morning, sir" moment.

Assembles:
    - Current time / date
    - Weather for Jalandhar (Open-Meteo, no API key)
    - Top India news (Google News RSS, no API key)
    - Top Hacker News stories (HN API, no key)
    - Today's calendar
    - System snapshot (CPU, RAM, disk, battery)
    - Autoresearch overnight wins (reads autoresearch_results/results.tsv)

Then feeds all of it as context to TanishiBrain with a "give me my briefing" prompt
so she phrases it in her own voice and speaks it aloud.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import requests

from tanishi.proactive.proactive_speak import speak_through_brain

# Jalandhar, Punjab, India
JALANDHAR_LAT = 31.3260
JALANDHAR_LON = 75.5762

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_TSV = PROJECT_ROOT / "autoresearch_results" / "results.tsv"


# ---------- data sources ----------

def get_weather() -> dict:
    """Open-Meteo — no API key required."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": JALANDHAR_LAT,
                "longitude": JALANDHAR_LON,
                "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m,apparent_temperature",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
                "timezone": "Asia/Kolkata",
                "forecast_days": 1,
            },
            timeout=10,
        )
        d = r.json()
        cur, day = d["current"], d["daily"]
        code_map = {
            0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
            45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
            61: "light rain", 63: "rain", 65: "heavy rain",
            71: "light snow", 73: "snow", 75: "heavy snow",
            80: "showers", 81: "heavy showers", 82: "violent showers",
            95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
        }
        return {
            "condition": code_map.get(cur["weather_code"], "unknown"),
            "temp_c": cur["temperature_2m"],
            "feels_like_c": cur["apparent_temperature"],
            "humidity": cur["relative_humidity_2m"],
            "wind_kmh": cur["wind_speed_10m"],
            "high_c": day["temperature_2m_max"][0],
            "low_c": day["temperature_2m_min"][0],
            "rain_chance_pct": day["precipitation_probability_max"][0],
        }
    except Exception as e:
        return {"error": str(e)}


def get_hn_top(limit: int = 5) -> list:
    """Top Hacker News — no API key."""
    try:
        ids = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        ).json()[:limit]
        stories = []
        for sid in ids:
            try:
                s = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5
                ).json()
                if s and s.get("title"):
                    stories.append({"title": s["title"], "score": s.get("score", 0)})
            except Exception:
                continue
        return stories
    except Exception:
        return []


def get_india_news(limit: int = 5) -> list:
    """Google News RSS for India — no API key."""
    try:
        r = requests.get(
            "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 Tanishi"},
        )
        root = ET.fromstring(r.text)
        items = root.findall(".//item")[:limit]
        out = []
        for item in items:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                # Google News titles look like "Headline - Publisher" — trim publisher
                title = title_el.text
                if " - " in title:
                    title = title.rsplit(" - ", 1)[0]
                out.append(title)
        return out
    except Exception:
        return []


def get_system() -> dict:
    bat = psutil.sensors_battery()
    try:
        disk_free_gb = psutil.disk_usage("/").free / (1024 ** 3)
    except Exception:
        disk_free_gb = 0
    return {
        "cpu_pct": psutil.cpu_percent(interval=1),
        "ram_pct": psutil.virtual_memory().percent,
        "disk_free_gb": disk_free_gb,
        "battery_pct": bat.percent if bat else None,
        "battery_plugged": bat.power_plugged if bat else None,
    }


def get_calendar_today() -> list:
    try:
        from tanishi.proactive.calendar_helper import get_today_events
        events = get_today_events()
        return [{"time": e["time"], "title": e["title"]} for e in events]
    except Exception:
        return []


def get_autoresearch_wins() -> dict | None:
    """Read autoresearch_results/results.tsv and summarize last 24h of self-improvement."""
    if not RESULTS_TSV.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(RESULTS_TSV, sep="\t")
        if df.empty:
            return None
        kept = df[df.status == "keep"]
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        recent_kept = kept[kept.timestamp > cutoff] if "timestamp" in df.columns else kept
        current_score = float(df.iloc[-1].score) if "score" in df.columns else None
        baseline = float(df.iloc[0].score) if "score" in df.columns else None
        return {
            "total_experiments": int(len(df)),
            "kept_overnight": int(len(recent_kept)),
            "current_score": current_score,
            "baseline": baseline,
            "gain_pct": ((current_score - baseline) * 100) if current_score and baseline else 0,
            "top_wins": (
                recent_kept[["area", "description", "score"]].tail(5).to_dict("records")
                if len(recent_kept) and "description" in df.columns
                else []
            ),
        }
    except Exception as e:
        print(f"[briefing] autoresearch read failed: {e}")
        return None


# ---------- assembly ----------

def assemble_briefing_context() -> str:
    """Gather everything into a structured text block for the brain prompt."""
    print("[briefing] gathering briefing data...")
    lines = []
    now = datetime.now()
    lines.append(f"=== CURRENT TIME ===\n{now.strftime('%A, %B %d, %Y at %I:%M %p')}")

    w = get_weather()
    if "error" not in w:
        lines.append(
            f"=== WEATHER (Jalandhar) ===\n"
            f"{w['temp_c']:.0f}°C, feels like {w['feels_like_c']:.0f}°C, {w['condition']}\n"
            f"Today: high {w['high_c']:.0f}°C, low {w['low_c']:.0f}°C, "
            f"rain chance {w['rain_chance_pct']}%\n"
            f"Humidity {w['humidity']:.0f}%, wind {w['wind_kmh']:.0f} km/h"
        )
    else:
        lines.append(f"=== WEATHER ===\nunavailable ({w['error']})")

    sys = get_system()
    bat_str = f"{sys['battery_pct']:.0f}%" if sys['battery_pct'] else "n/a"
    if sys["battery_plugged"]:
        bat_str += " (plugged in)"
    lines.append(
        f"=== SYSTEM ===\n"
        f"CPU {sys['cpu_pct']:.0f}% | RAM {sys['ram_pct']:.0f}% | "
        f"Disk free {sys['disk_free_gb']:.0f} GB | Battery {bat_str}"
    )

    cal = get_calendar_today()
    if cal:
        cal_lines = "\n".join(f"  • {e['time']} — {e['title']}" for e in cal)
        lines.append(f"=== TODAY'S CALENDAR ===\n{cal_lines}")
    else:
        lines.append("=== TODAY'S CALENDAR ===\nNothing scheduled (or calendar not connected).")

    india = get_india_news(5)
    if india:
        lines.append("=== TOP INDIA NEWS ===\n" + "\n".join(f"  • {t}" for t in india))

    hn = get_hn_top(5)
    if hn:
        lines.append("=== TOP HACKER NEWS ===\n" + "\n".join(f"  • {s['title']} ({s['score']} pts)" for s in hn))

    ar = get_autoresearch_wins()
    if ar:
        ar_block = (
            f"=== AUTORESEARCH (overnight self-improvement) ===\n"
            f"Total experiments: {ar['total_experiments']}\n"
            f"Improvements kept in last 24h: {ar['kept_overnight']}\n"
        )
        if ar["current_score"] and ar["baseline"]:
            ar_block += (
                f"Baseline score: {ar['baseline']:.4f}\n"
                f"Current score:  {ar['current_score']:.4f}\n"
                f"Gain:           {ar['gain_pct']:+.2f}%\n"
            )
        if ar["top_wins"]:
            ar_block += "Top wins:\n" + "\n".join(
                f"  • [{w['area']}] {w['description']}" for w in ar["top_wins"]
            )
        lines.append(ar_block)

    return "\n\n".join(lines)


def speak_briefing() -> None:
    """The main 'Good morning, sir' moment."""
    context = assemble_briefing_context()
    print("\n" + "=" * 72)
    print("BRIEFING CONTEXT")
    print("=" * 72)
    print(context)
    print("=" * 72 + "\n")

    prompt = (
        "Give me my morning briefing. You are Tanishi — sharp, warm, a little dry, "
        "confident like JARVIS but with your own personality. "
        "Open with a greeting ('Good morning, sir' or your own twist — don't be corny). "
        "Walk through weather, today's calendar, how my overnight self-improvement run went, "
        "and the top headlines worth knowing. "
        "Keep it tight — under 90 seconds if I read it aloud. "
        "End by asking what I want to focus on today. "
        "Do NOT just dump the data — synthesize it and tell me what actually matters."
    )

    speak_through_brain(prompt, mood="briefing", extra_context=context)


if __name__ == "__main__":
    speak_briefing()
