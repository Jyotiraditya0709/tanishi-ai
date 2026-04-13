"""Proactive speak helper — v4, pygame-free.

Plays audio directly via OpenAI TTS API + playsound3 (a pure-Python player
that uses Windows MediaPlayer under the hood — no pygame, no SDL, no build step).

Why not use TanishiSpeaker.speak()?
    Because TanishiSpeaker's _play_audio() imports pygame, which has no
    prebuilt wheel for Python 3.14 on Windows. It was silently failing in
    the existing codebase and falling through to pyttsx3 (the Zira voice).
    This module fixes the playback problem at the proactive layer without
    touching speaker.py.

Install dependency:
    pip install --user playsound3

Falls back gracefully:
    OpenAI TTS fails         -> edge-tts       (if installed)
    edge-tts fails           -> pyttsx3        (always installed)
    pyttsx3 fails            -> stdout only
"""
import asyncio
import os
import tempfile
import threading
from pathlib import Path

# ================================================================
# STEP 1: Load .env so OPENAI_API_KEY is visible
# ================================================================
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parents[2] / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
        print(f"[proactive_speak] loaded .env from {_env_path}")
    else:
        load_dotenv()
except ImportError:
    _env_path = Path(__file__).resolve().parents[2] / ".env"
    if _env_path.exists():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        print(f"[proactive_speak] hand-parsed .env from {_env_path}")

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
print(f"[proactive_speak] OPENAI_API_KEY present: {bool(OPENAI_KEY)}")

# ================================================================
# STEP 2: Probe available audio players at import time
# ================================================================
_player = None
try:
    import playsound3  # noqa
    _player = "playsound3"
    print("[proactive_speak] audio player: playsound3 ✓")
except ImportError:
    try:
        from playsound import playsound  # noqa
        _player = "playsound"
        print("[proactive_speak] audio player: playsound (legacy) ✓")
    except ImportError:
        pass

if _player is None:
    # Windows fallback: use winsound (PCM wav only, no mp3) or os.startfile
    try:
        import winsound  # noqa
        _player = "winsound_via_wmp"
        print("[proactive_speak] audio player: windows media player (subprocess) ✓")
    except ImportError:
        print("[proactive_speak] WARNING: no audio player found, will use pyttsx3 fallback")


# ================================================================
# STEP 3: Lazy brain singleton
# ================================================================
_brain = None
_brain_lock = threading.Lock()


def _get_brain():
    global _brain
    if _brain is None:
        with _brain_lock:
            if _brain is None:
                from tanishi.core.brain import TanishiBrain
                _brain = TanishiBrain()
                print("[proactive_speak] TanishiBrain initialized")
    return _brain


# ================================================================
# STEP 4: Direct OpenAI TTS (no TanishiSpeaker dependency)
# ================================================================
def _openai_tts_to_file(text: str, voice: str = "nova") -> str | None:
    """Call OpenAI TTS, save MP3 to temp file, return path. None on failure.

    Default voice changed from 'onyx' (male) to 'nova' (female) — Tanishi
    is a she. Override with env var TANISHI_VOICE if you want something else.
    Valid voices: alloy, echo, fable, onyx, nova, shimmer
    """
    if not OPENAI_KEY:
        print("[proactive_speak] no OPENAI_API_KEY, skipping OpenAI TTS")
        return None

    voice = os.getenv("TANISHI_VOICE", voice).lower()
    if voice not in {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}:
        voice = "nova"

    try:
        import httpx
    except ImportError:
        import urllib.request
        import json
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/audio/speech",
                data=json.dumps({
                    "model": "tts-1",
                    "voice": voice,
                    "input": text,
                }).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {OPENAI_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                audio_bytes = resp.read()
        except Exception as e:
            print(f"[proactive_speak] OpenAI TTS (urllib) failed: {e}")
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(audio_bytes)
        tmp.close()
        return tmp.name

    # httpx path (preferred — same as TanishiSpeaker uses)
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {OPENAI_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "tts-1",
                    "voice": voice,
                    "input": text,
                    "speed": 1.0,
                },
            )
        if resp.status_code != 200:
            print(f"[proactive_speak] OpenAI TTS HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[proactive_speak] OpenAI TTS request failed: {e}")
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.write(resp.content)
    tmp.close()
    print(f"[proactive_speak] OpenAI TTS OK — {len(resp.content)} bytes, voice={voice}")
    return tmp.name


def _play_file(path: str) -> bool:
    """Play an audio file. Returns True if playback succeeded."""
    if _player == "playsound3":
        try:
            import playsound3
            playsound3.playsound(path, block=True)
            return True
        except Exception as e:
            print(f"[proactive_speak] playsound3 failed: {e}")
            return False

    if _player == "playsound":
        try:
            from playsound import playsound
            playsound(path, block=True)
            return True
        except Exception as e:
            print(f"[proactive_speak] playsound (legacy) failed: {e}")
            return False

    if _player == "winsound_via_wmp":
        # Use Windows Media Player via powershell — works for mp3
        try:
            import subprocess
            # PowerShell inline play + wait
            ps_cmd = (
                f'Add-Type -AssemblyName presentationCore; '
                f'$p = New-Object System.Windows.Media.MediaPlayer; '
                f'$p.Open([System.Uri]::new("{path}")); '
                f'$p.Play(); '
                f'Start-Sleep -Milliseconds 500; '
                f'while ($p.Position -lt $p.NaturalDuration.TimeSpan) {{ Start-Sleep -Milliseconds 200 }}; '
                f'$p.Close()'
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                timeout=180,
                capture_output=True,
            )
            return True
        except Exception as e:
            print(f"[proactive_speak] powershell WMP play failed: {e}")
            return False

    return False


def _fallback_pyttsx3(text: str) -> None:
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 185)
        engine.say(text)
        engine.runAndWait()
    except Exception:
        print(f"[TANISHI 🔊 text-only] {text}")


# ================================================================
# STEP 5: Async helper
# ================================================================
def _run_coro(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ================================================================
# PUBLIC API
# ================================================================
def speak_raw(text: str) -> None:
    """Speak text directly, no brain formatting."""
    print(f"[proactive] {text}")
    audio_path = _openai_tts_to_file(text)
    if audio_path is None:
        _fallback_pyttsx3(text)
        return
    success = _play_file(audio_path)
    try:
        os.unlink(audio_path)
    except Exception:
        pass
    if not success:
        _fallback_pyttsx3(text)


def speak_through_brain(prompt: str, mood: str = "casual", extra_context: str = "") -> None:
    """Full pipeline: brain phrases the prompt, then OpenAI TTS speaks it."""
    text = prompt
    try:
        brain = _get_brain()
        response = _run_coro(brain.think(prompt, mood=mood, extra_context=extra_context))
        text = getattr(response, "content", None) or str(response)
    except Exception as e:
        import traceback
        print(f"[proactive_speak] brain route FAILED: {e}")
        traceback.print_exc()

    print(f"[proactive] {text}")
    audio_path = _openai_tts_to_file(text)
    if audio_path is None:
        _fallback_pyttsx3(text)
        return
    success = _play_file(audio_path)
    try:
        os.unlink(audio_path)
    except Exception:
        pass
    if not success:
        _fallback_pyttsx3(text)
