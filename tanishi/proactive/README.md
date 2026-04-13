# Tanishi Proactive Mode

Makes Tanishi act like JARVIS — always on, speaks unprompted when it matters,
wakes up when you say her name, and delivers a full briefing every morning.

## What's in here

| File | What it does |
|---|---|
| `run_proactive.py` | Entrypoint — runs all three subsystems together |
| `sentinel.py` | 60s background loop: battery, CPU, RAM, breaks, meetings |
| `daily_briefing.py` | Morning briefing: weather + news + calendar + system + autoresearch wins |
| `wake_word.py` | Picovoice Porcupine listener (default keyword: `jarvis`) |
| `proactive_speak.py` | Shared TTS helper — routes unprompted speech through TanishiBrain |
| `calendar_helper.py` | Local JSON event store (Google Calendar stub) |

## Dependencies

```bash
pip install psutil pvporcupine pyaudio requests pandas SpeechRecognition pyttsx3
```

Most you already have. `pvporcupine` + `pyaudio` are the new ones for the wake word.

## One-time setup (for wake word only)

1. Go to https://console.picovoice.ai/ and sign up (free).
2. Copy your Access Key.
3. Add to `.env`:
   ```
   PICOVOICE_ACCESS_KEY=ac_your_key_here
   ```
4. (Optional) Train a custom "Tanishi" wake word in the Picovoice console,
   download the `.ppn` file, and set:
   ```
   TANISHI_KEYWORD_PATH=C:/Users/jmish/...path.../tanishi_en_windows.ppn
   ```

Without a Picovoice key, the sentinel and briefing still run — only the
wake word listener is skipped.

## Test each piece individually first

```bash
# 1. Test daily briefing (runs once, speaks, exits)
python -m tanishi.proactive.daily_briefing

# 2. Test sentinel for one cycle (Ctrl-C to stop)
python -m tanishi.proactive.sentinel

# 3. Test wake word
python -m tanishi.proactive.wake_word
```

## Run the full daemon

```bash
# Normal run — fires briefing tomorrow at 7:00 AM
python -m tanishi.proactive.run_proactive

# Skip wake word (if mic is taken or Picovoice not set up)
python -m tanishi.proactive.run_proactive --no-wake

# Fire a briefing immediately at startup (for testing)
python -m tanishi.proactive.run_proactive --briefing-now

# Custom briefing time
python -m tanishi.proactive.run_proactive --hour 8 --minute 30
```

## Adding calendar events

Create `proactive_events.json` at the project root:

```json
[
  {"id": "1", "title": "Standup",        "start": "2026-04-08T09:00:00"},
  {"id": "2", "title": "Lunch with Mom", "start": "2026-04-08T13:00:00"},
  {"id": "3", "title": "DBMS lecture",   "start": "2026-04-08T15:00:00"}
]
```

Sentinel will warn you 5 minutes before each event. Briefing will include
today's events in the morning.

## Tuning thresholds

Edit the constants at the top of `sentinel.py`:

```python
BATTERY_CRITICAL_PCT = 15
BATTERY_LOW_PCT = 25
CPU_HIGH_PCT = 90
RAM_HIGH_PCT = 90
BREAK_INTERVAL_MIN = 90
```

And cooldowns in the same file — `COOLDOWN` dict — if Tanishi nags too
much or too little.
