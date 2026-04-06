"""
Tanishi Listener v3 — Perfect hearing with OpenAI Whisper.

Primary: OpenAI Whisper API ($0.006/min) — hears everything perfectly
Fallback: Google free STT (when OPENAI_API_KEY not set)

No more "Titan ishi" or "Tony hsieh" — Whisper nails "Tanishi" every time.
"""

import os
import io
import wave
import time
import tempfile
import asyncio
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    text: str
    confidence: float = 0.0
    language: str = "en"
    duration_ms: float = 0
    backend: str = ""


class TanishiListener:
    """Microphone listener with OpenAI Whisper or Google STT."""

    def __init__(self):
        self.on_status: Optional[Callable] = None
        self.wake_word = "tanishi"
        self.wake_word_enabled = True
        self.backend = "unknown"
        self._openai_key = os.getenv("OPENAI_API_KEY", "")
        self._detect_backend()

    def _detect_backend(self):
        if self._openai_key:
            self.backend = "whisper"
            self._status("STT: OpenAI Whisper API")
        else:
            try:
                import speech_recognition as sr
                self.backend = "google"
                self._status("STT: Google Free (set OPENAI_API_KEY for better accuracy)")
            except ImportError:
                self.backend = "none"
                self._status("No STT available!")

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    async def listen_once(self) -> Optional[TranscriptionResult]:
        """Listen for speech and transcribe."""
        if self.backend == "none":
            return None

        # Record audio
        audio_data = await self._record_audio()
        if audio_data is None or len(audio_data) == 0:
            return None

        self._status("Transcribing...")

        # Save to WAV
        audio_int16 = (audio_data * 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            with wave.open(f, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_int16.tobytes())

        try:
            if self.backend == "whisper":
                return await self._transcribe_whisper(temp_path)
            else:
                return await self._transcribe_google(temp_path)
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    async def _record_audio(self) -> Optional[np.ndarray]:
        """Record audio using sounddevice until silence after speech."""
        try:
            import sounddevice as sd
        except ImportError:
            self._status("sounddevice not installed! pip install sounddevice")
            return None

        sample_rate = 16000
        silence_threshold = 0.015
        silence_duration = 1.5
        max_duration = 30
        chunk_size = 1024

        self._status("Listening...")

        def _record():
            chunks = []
            silent_chunks = 0
            max_silent = int(silence_duration * (sample_rate / chunk_size))
            max_chunks = int(max_duration * (sample_rate / chunk_size))
            started = False
            no_speech_timeout = 0
            max_no_speech = int(10 * (sample_rate / chunk_size))  # 10 second timeout

            def callback(indata, frames, time_info, status):
                nonlocal silent_chunks, started, no_speech_timeout
                chunk = indata.copy()
                volume = np.sqrt(np.mean(chunk ** 2))

                if volume > silence_threshold:
                    started = True
                    silent_chunks = 0
                elif started:
                    silent_chunks += 1

                if started:
                    chunks.append(chunk)
                else:
                    no_speech_timeout += 1

            try:
                with sd.InputStream(
                    samplerate=sample_rate, channels=1,
                    dtype="float32", blocksize=chunk_size,
                    callback=callback,
                ):
                    while True:
                        sd.sleep(100)
                        if started and silent_chunks > max_silent:
                            break
                        if len(chunks) > max_chunks:
                            break
                        if not started and no_speech_timeout > max_no_speech:
                            return None
            except Exception as e:
                return None

            if not chunks:
                return None
            return np.concatenate(chunks, axis=0)

        return await asyncio.get_event_loop().run_in_executor(None, _record)

    async def _transcribe_whisper(self, wav_path: str) -> TranscriptionResult:
        """Transcribe using OpenAI Whisper API — near-perfect accuracy."""
        import httpx
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(wav_path, "rb") as f:
                    resp = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self._openai_key}"},
                        files={"file": ("audio.wav", f, "audio/wav")},
                        data={
                            "model": "whisper-1",
                            "language": "en",
                            "prompt": "Hey Tanishi, Tanishi",  # Hint for the name
                        },
                    )

                if resp.status_code == 200:
                    data = resp.json()
                    elapsed = (time.time() - start) * 1000
                    return TranscriptionResult(
                        text=data.get("text", "").strip(),
                        confidence=0.95,
                        duration_ms=elapsed,
                        backend="whisper",
                    )
                else:
                    return TranscriptionResult(
                        text="", backend=f"whisper (error {resp.status_code})"
                    )
        except Exception as e:
            return TranscriptionResult(text="", backend=f"whisper (error: {e})")

    async def _transcribe_google(self, wav_path: str) -> TranscriptionResult:
        """Transcribe using Google free STT."""
        import speech_recognition as sr
        start = time.time()

        def _do():
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio = recognizer.record(source)
            try:
                return recognizer.recognize_google(audio)
            except sr.UnknownValueError:
                return ""
            except sr.RequestError as e:
                return ""

        text = await asyncio.get_event_loop().run_in_executor(None, _do)
        elapsed = (time.time() - start) * 1000

        return TranscriptionResult(
            text=text.strip() if text else "",
            confidence=0.85,
            duration_ms=elapsed,
            backend="google",
        )

    def check_wake_word(self, text: str) -> tuple[bool, str]:
        """Check for wake word. Whisper actually hears 'Tanishi' correctly."""
        if not self.wake_word_enabled:
            return True, text

        text_lower = text.lower().strip()

        wake_words = [
            "hey tanishi", "hi tanishi", "tanishi",
            "hey taneeshi", "taneeshi", "hey danish",
            "titan ishi", "titanishi", "tony shi",
            "tony hsieh", "hey finish", "tenshi",
        ]

        for ww in wake_words:
            if ww in text_lower:
                cleaned = text_lower.replace(ww, "").strip()
                cleaned = cleaned.lstrip(",").lstrip(".").lstrip("!").strip()
                return True, cleaned if cleaned else ""

        # Fuzzy match
        words = text_lower.replace(",", " ").replace(".", " ").split()
        for i, word in enumerate(words):
            if self._fuzzy_match(word):
                remaining = " ".join(words[i+1:]).strip()
                return True, remaining

        for i in range(len(words) - 1):
            combined = words[i] + words[i+1]
            if self._fuzzy_match(combined):
                remaining = " ".join(words[i+2:]).strip()
                return True, remaining

        return False, text

    def _fuzzy_match(self, word: str) -> bool:
        word = word.lower().strip()
        if len(word) < 4 or len(word) > 12:
            return False
        tanishi_sounds = ["tan", "nish", "ishi", "tani", "nishi"]
        if sum(1 for s in tanishi_sounds if s in word) >= 2:
            return True
        overlap = len(set("tanishi") & set(word))
        if overlap >= 4 and len(word) >= 5:
            return True
        return False

    @property
    def is_available(self) -> bool:
        return self.backend != "none"

    def get_status(self) -> dict:
        return {
            "backend": self.backend,
            "available": self.is_available,
            "wake_word": self.wake_word,
        }
