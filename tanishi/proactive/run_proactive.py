"""Tanishi Proactive Daemon — the always-on JARVIS orchestrator.

Runs THREE things together in one process:
    1. Sentinel thread (daemon)    — 60s system watchdog
    2. Briefing thread (daemon)    — fires daily at BRIEFING_HOUR:BRIEFING_MINUTE
    3. Wake word listener (main)   — blocks forever, listens for 'jarvis'

Launch:
    python -m tanishi.proactive.run_proactive

Flags:
    --no-wake       skip the wake word listener (useful if mic is taken)
    --briefing-now  fire the daily briefing immediately at startup
    --hour N        override briefing hour (default 7)
    --minute N      override briefing minute (default 0)
"""
import argparse
import threading
import time
from datetime import datetime

from tanishi.proactive.daily_briefing import speak_briefing
from tanishi.proactive.sentinel import run_sentinel
from tanishi.proactive.wake_word import run_wake_word


def briefing_scheduler(hour: int, minute: int) -> None:
    """Fires speak_briefing() once per day at the configured time."""
    print(f"[scheduler] daily briefing armed for {hour:02d}:{minute:02d}")
    last_run_date = None
    while True:
        try:
            now = datetime.now()
            if (
                now.hour == hour
                and now.minute == minute
                and last_run_date != now.date()
            ):
                print(f"[scheduler] firing daily briefing at {now.isoformat()}")
                try:
                    speak_briefing()
                except Exception as e:
                    print(f"[scheduler] briefing failed: {e}")
                last_run_date = now.date()
        except Exception as e:
            print(f"[scheduler] loop error: {e}")
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tanishi proactive mode")
    parser.add_argument("--no-wake", action="store_true", help="skip wake word listener")
    parser.add_argument("--briefing-now", action="store_true", help="fire briefing at startup")
    parser.add_argument("--hour", type=int, default=7, help="daily briefing hour (0-23)")
    parser.add_argument("--minute", type=int, default=0, help="daily briefing minute (0-59)")
    args = parser.parse_args()

    print("=" * 72)
    print("  TANISHI PROACTIVE MODE — always-on JARVIS-class assistant")
    print("=" * 72)
    print(f"  Sentinel:   every 60s  (battery / CPU / RAM / breaks / meetings)")
    print(f"  Briefing:   daily at {args.hour:02d}:{args.minute:02d}")
    print(f"  Wake word:  {'DISABLED' if args.no_wake else 'jarvis (built-in)'}")
    print("=" * 72)

    # Sentinel thread
    t_sentinel = threading.Thread(target=run_sentinel, daemon=True, name="sentinel")
    t_sentinel.start()
    print("[main] ✅ sentinel thread started")

    # Briefing scheduler thread
    t_briefing = threading.Thread(
        target=briefing_scheduler,
        args=(args.hour, args.minute),
        daemon=True,
        name="briefing",
    )
    t_briefing.start()
    print("[main] ✅ briefing scheduler started")

    # Optional immediate briefing (great for testing)
    if args.briefing_now:
        print("[main] firing immediate briefing...")
        try:
            speak_briefing()
        except Exception as e:
            print(f"[main] immediate briefing failed: {e}")

    # Wake word on main thread (pyaudio prefers main thread)
    try:
        if args.no_wake:
            print("[main] wake word disabled; keeping daemon alive")
            while True:
                time.sleep(3600)
        else:
            run_wake_word()
    except KeyboardInterrupt:
        print("\n[main] Ctrl-C received, shutting down")
    except Exception as e:
        print(f"[main] wake word crashed ({e}); keeping other threads alive")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

    print("[main] proactive mode stopped")


if __name__ == "__main__":
    main()
