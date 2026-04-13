"""Tanishi proactive mode — always-on JARVIS-class daemon.

Modules:
    proactive_speak  — shared TTS helper that routes through TanishiBrain
    sentinel         — 60s background loop: battery, CPU, RAM, breaks, meetings
    daily_briefing   — morning mode: weather, news, calendar, system, autoresearch
    wake_word        — Picovoice Porcupine listener (default keyword 'jarvis')
    calendar_helper  — local JSON event store (Google Calendar stub)
    run_proactive    — orchestrator that runs all three together

Entry point:
    python -m tanishi.proactive.run_proactive
"""
