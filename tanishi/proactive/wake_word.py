"""Wake word listener using Picovoice Porcupine + PvRecorder.

No PyAudio dependency — PvRecorder is Picovoice's official audio library and ships
with prebuilt wheels for all Python versions (including 3.14). Install with:
    pip install pvporcupine pvrecorder SpeechRecognition

Default keyword: 'jarvis' — built into Porcupine, no training required.

To use a custom 'tanishi' keyword:
  1. Sign up free at https://console.picovoice.ai/
  2. Train a wake word model for 'Tanishi' (~5 min in browser)
  3. Download the .ppn file
  4. Set env var: TANISHI_KEYWORD_PATH=C:/path/to/tanishi_en_windows.ppn
"""
import io
import os
import struct
import wave
from pathlib import Path

PICOVOICE_KEY = os.getenv("PICOVOICE_ACCESS_KEY", "")
CUSTOM_KEYWORD_PATH = os.getenv("TANISHI_KEYWORD_PATH", "")


def _record_utterance(recorder, porcupine, max_seconds: int = 6) -> bytes:
    """Capture max_seconds of audio by reading frames from the already-running recorder.
    Returns raw 16-bit PCM bytes."""
    target_frames = (max_seconds * porcupine.sample_rate) // porcupine.frame_length
    samples = []
    for _ in range(target_frames):
        samples.extend(recorder.read())  # list[int16]
    return struct.pack("<" + "h" * len(samples), *samples)


def _pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container so SpeechRecognition can parse it."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _on_wake_default(recorder, porcupine) -> None:
    """Default handler: acknowledge, record 6s, transcribe, route through brain."""
    from tanishi.proactive.proactive_speak import speak_raw, speak_through_brain

    speak_raw("Yes, sir?")

    try:
        import speech_recognition as sr
    except ImportError:
        print("[wake] speech_recognition not installed. Run: pip install SpeechRecognition")
        return

    print("[wake] recording 6 seconds of command...")
    try:
        pcm_bytes = _record_utterance(recorder, porcupine, max_seconds=6)
    except Exception as e:
        print(f"[wake] recording failed: {e}")
        return

    wav_bytes = _pcm_to_wav_bytes(pcm_bytes, porcupine.sample_rate)

    r = sr.Recognizer()
    try:
        with sr.AudioFile(io.BytesIO(wav_bytes)) as source:
            audio = r.record(source)
        text = r.recognize_google(audio)
        print(f"[wake] heard: {text!r}")
    except sr.UnknownValueError:
        speak_raw("Didn't catch that, sir.")
        return
    except sr.RequestError as e:
        speak_raw(f"My speech service is down. {e}")
        return
    except Exception as e:
        print(f"[wake] STT failed: {e}")
        return

    speak_through_brain(text, mood="casual")


def run_wake_word(callback=None, keyword: str = "jarvis") -> None:
    """Block forever on wake word detection. Runs callback each time the word is heard.

    Designed to run on the main thread (audio I/O prefers it).
    """
    import time

    if not PICOVOICE_KEY:
        print("[wake] PICOVOICE_ACCESS_KEY not set — wake word disabled.")
        print("[wake] Get a free key at https://console.picovoice.ai/")
        print("[wake] Then add to your .env:  PICOVOICE_ACCESS_KEY=ac_your_key_here")
        while True:
            time.sleep(3600)

    try:
        import pvporcupine
        from pvrecorder import PvRecorder
    except ImportError as e:
        print(f"[wake] missing deps ({e}). install: pip install pvporcupine pvrecorder")
        while True:
            time.sleep(3600)

    # Build Porcupine engine
    try:
        if CUSTOM_KEYWORD_PATH and Path(CUSTOM_KEYWORD_PATH).exists():
            porcupine = pvporcupine.create(
                access_key=PICOVOICE_KEY, keyword_paths=[CUSTOM_KEYWORD_PATH]
            )
            print(f"[wake] using custom keyword from {CUSTOM_KEYWORD_PATH}")
        else:
            porcupine = pvporcupine.create(access_key=PICOVOICE_KEY, keywords=[keyword])
            print(f"[wake] using built-in keyword: '{keyword}'")
    except Exception as e:
        print(f"[wake] Porcupine init failed: {e}")
        return

    # List available mics for debugging, then open the default one
    try:
        devices = PvRecorder.get_available_devices()
        print(f"[wake] available mics: {devices}")
    except Exception:
        pass

    recorder = PvRecorder(device_index=-1, frame_length=porcupine.frame_length)
    try:
        recorder.start()
        print(f"[wake] armed. Say '{keyword}' to activate Tanishi.")
        while True:
            pcm = recorder.read()  # list of int16 samples
            if porcupine.process(pcm) >= 0:
                print(f"[wake] *** detected '{keyword}' ***")
                try:
                    if callback is None:
                        _on_wake_default(recorder, porcupine)
                    else:
                        callback()
                except Exception as e:
                    print(f"[wake] callback failed: {e}")
    except KeyboardInterrupt:
        print("\n[wake] shutdown")
    finally:
        try:
            recorder.stop()
            recorder.delete()
        except Exception:
            pass
        porcupine.delete()


if __name__ == "__main__":
    run_wake_word()
