"""
Tanishi Speaker v3 — Sounds like a real person.

Primary: OpenAI TTS ($0.015/1K chars) — natural, expressive, human
Fallback: edge-tts (free, robotic)

OpenAI voices: alloy, echo, fable, nova, onyx, shimmer
Each has its own personality — "onyx" is deep and confident (perfect for JARVIS),
"nova" is warm and expressive, "shimmer" is smooth.
"""

import os
import re
import asyncio
import tempfile
import random
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass


@dataclass
class VoiceConfig:
    backend: str = "auto"
    voice_id: str = ""
    speed: float = 1.0
    volume: float = 1.0


# OpenAI TTS voices — these sound HUMAN
OPENAI_VOICES = {
    "jarvis": "onyx",       # Deep, authoritative — closest to JARVIS
    "friday": "nova",       # Warm, expressive — like FRIDAY
    "sarcastic": "onyx",    # Confident, perfect for sarcasm
    "warm": "nova",         # Friendly, approachable
    "smooth": "shimmer",    # Smooth, calming
    "storyteller": "fable", # British accent, narrative
    "neutral": "alloy",     # Balanced, clean
    "deep": "echo",         # Low, resonant
}

# Edge-TTS fallback voices (free)
EDGE_VOICES = {
    "jarvis": "en-GB-RyanNeural",
    "friday": "en-US-JennyNeural",
    "indian_f": "en-IN-NeerjaNeural",
    "indian_m": "en-IN-PrabhatNeural",
    "sarcastic": "en-GB-RyanNeural",
}

DEFAULT_VOICE = "sarcastic"


class TanishiSpeaker:
    """Text-to-speech that sounds like a real human conversation."""

    # Pre-cached filler phrases for instant playback
    FILLER_PHRASES = ["On it.", "One sec.", "Let me check.", "Got it.", "Checking now.", "Hmm let me see."]

    def __init__(self, config: Optional[VoiceConfig] = None):
        self.config = config or VoiceConfig()
        self.backend = self.config.backend
        self._openai_key = os.getenv("OPENAI_API_KEY", "")
        self._is_speaking = False
        self._stop_requested = False
        self.on_status: Optional[Callable] = None

        # v4: Persistent pygame mixer (don't reinit every time)
        self._pygame_initialized = False

        # v4: Filler audio cache
        self._filler_cache_dir = Path.home() / ".tanishi" / "voice_cache"
        self._filler_cache_dir.mkdir(parents=True, exist_ok=True)
        self._filler_files: dict[str, str] = {}

        self._detect_backend()

    def _detect_backend(self):
        if self.backend in ("auto", "openai") and self._openai_key:
            self.backend = "openai"
            if not self.config.voice_id:
                self.config.voice_id = OPENAI_VOICES.get(DEFAULT_VOICE, "onyx")
            self._status(f"TTS: OpenAI ({self.config.voice_id}) — human quality")
            return

        try:
            import edge_tts
            self.backend = "edge-tts"
            if not self.config.voice_id:
                self.config.voice_id = EDGE_VOICES.get(DEFAULT_VOICE, "en-GB-RyanNeural")
            self._status(f"TTS: edge-tts ({self.config.voice_id}) — set OPENAI_API_KEY for human voice")
            return
        except ImportError:
            pass

        try:
            import pyttsx3
            self.backend = "pyttsx3"
            self._pyttsx_engine = pyttsx3.init()
            self._status("TTS: pyttsx3 (basic)")
            return
        except Exception:
            pass

        self.backend = "none"
        self._status("No TTS available!")

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    async def precache_fillers(self):
        """Pre-generate filler audio files for instant playback. Call once at startup."""
        if self.backend != "openai" or not self._openai_key:
            return

        import httpx

        valid_openai_ids = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
        voice_id = self.config.voice_id if self.config.voice_id in valid_openai_ids else "onyx"

        for phrase in self.FILLER_PHRASES:
            cache_key = f"{voice_id}_{phrase.replace(' ', '_').replace('.', '')}"
            cache_path = self._filler_cache_dir / f"{cache_key}.mp3"

            if cache_path.exists():
                self._filler_files[phrase] = str(cache_path)
                continue

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/audio/speech",
                        headers={
                            "Authorization": f"Bearer {self._openai_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "tts-1",
                            "voice": voice_id,
                            "input": phrase,
                            "speed": self.config.speed,
                        },
                    )
                    if resp.status_code == 200:
                        cache_path.write_bytes(resp.content)
                        self._filler_files[phrase] = str(cache_path)
            except Exception:
                pass

        if self._filler_files:
            self._status(f"Voice: cached {len(self._filler_files)} fillers for instant playback")

    async def speak_cached_filler(self) -> bool:
        """Play a random pre-cached filler instantly. Returns True if played."""
        if not self._filler_files:
            return False

        phrase = random.choice(list(self._filler_files.keys()))
        file_path = self._filler_files[phrase]

        if os.path.exists(file_path):
            await self._play_audio(file_path)
            return True
        return False

    def stop(self):
        self._stop_requested = True
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    async def speak(self, text: str):
        if self.backend == "none" or not text.strip():
            return
        self._is_speaking = True
        self._stop_requested = False
        try:
            clean = self._clean_for_speech(text)
            if not clean.strip():
                return
            if self.backend == "openai":
                await self._speak_openai(clean)
            elif self.backend == "edge-tts":
                await self._speak_edge_tts(clean)
            elif self.backend == "pyttsx3":
                await self._speak_pyttsx3(clean)
        finally:
            self._is_speaking = False
            self._stop_requested = False

    async def _speak_openai(self, text: str):
        """Speak using OpenAI TTS — sounds like a real person."""
        import httpx

        # SAFETY: Only send valid OpenAI voice IDs — never edge-tts IDs
        valid_openai_ids = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
        voice_id = self.config.voice_id
        if voice_id not in valid_openai_ids:
            voice_id = "onyx"  # Safe default

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            temp_path = f.name

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self._openai_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "tts-1",
                        "voice": voice_id,
                        "input": text,
                        "speed": self.config.speed,
                    },
                )

                if resp.status_code == 200:
                    with open(temp_path, "wb") as f:
                        f.write(resp.content)
                    if not self._stop_requested:
                        await self._play_audio(temp_path)
                else:
                    self._status(f"OpenAI TTS error: {resp.status_code}")
                    # Fallback to edge-tts
                    await self._speak_edge_tts(text)

        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    async def _speak_edge_tts(self, text: str):
        """Fallback: edge-tts (free, less natural)."""
        try:
            import edge_tts
        except ImportError:
            return

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            temp_path = f.name

        try:
            voice = self.config.voice_id
            # If using OpenAI voice name, map to edge-tts
            if voice in OPENAI_VOICES.values():
                voice = EDGE_VOICES.get(DEFAULT_VOICE, "en-GB-RyanNeural")

            communicate = edge_tts.Communicate(text, voice, rate="+5%")
            await communicate.save(temp_path)

            if not self._stop_requested:
                await self._play_audio(temp_path)
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    async def _speak_pyttsx3(self, text: str):
        def _do():
            self._pyttsx_engine.say(text)
            self._pyttsx_engine.runAndWait()
        await asyncio.get_event_loop().run_in_executor(None, _do)

    async def _play_audio(self, file_path: str):
        """Play audio with interrupt support. Keeps pygame mixer alive for speed."""
        try:
            import pygame
            if not self._pygame_initialized:
                pygame.mixer.init()
                self._pygame_initialized = True
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.set_volume(self.config.volume)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self._stop_requested:
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.05)
            return
        except ImportError:
            pass
        except Exception:
            # If pygame fails, try reinitializing
            self._pygame_initialized = False
            pass

        # Fallback: playsound
        try:
            from playsound import playsound
            await asyncio.get_event_loop().run_in_executor(None, playsound, file_path)
            return
        except ImportError:
            pass

        # Fallback: OS command
        import platform
        if platform.system() == "Windows":
            cmd = f'powershell -c "Add-Type -AssemblyName PresentationCore; $p = New-Object System.Windows.Media.MediaPlayer; $p.Open(\'{file_path}\'); $p.Play(); Start-Sleep -Seconds ([math]::Ceiling($p.NaturalDuration.TimeSpan.TotalSeconds + 1))"'
            process = await asyncio.create_subprocess_shell(cmd)
            await process.wait()

    def _clean_for_speech(self, text: str) -> str:
        """Clean text for natural speech — remove markdown, actions, emojis."""
        # Remove *action text* entirely
        text = re.sub(r'\*[^*]+\*', ' ', text)
        # Remove markdown
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'#{1,6}\s*', '', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'^\s*[-•]\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s*', '', text, flags=re.MULTILINE)
        # Remove emojis
        text = re.sub(r'[🔥🎯🧠💡⚡🎬🏢✅❌🔧💻📱🎤🔊]', '', text)
        # Clean whitespace
        text = re.sub(r'\n\s*\n', '. ', text)
        text = re.sub(r'\n', ', ', text)
        text = re.sub(r'\s+', ' ', text)
        # Truncate
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        if len(sentences) > 10:
            text = ' '.join(sentences[:8])
            text += ". There's more on screen."
        return text.strip()

    async def speak_filler(self):
        """Quick filler while thinking."""
        fillers = ["Hmm, let me think.", "One sec.", "On it.", "Let me check."]
        await self.speak(random.choice(fillers))

    def set_voice(self, voice_name: str):
        """Change voice preset."""
        voice_name = voice_name.lower()
        if self.backend == "openai" and voice_name in OPENAI_VOICES:
            self.config.voice_id = OPENAI_VOICES[voice_name]
            self._status(f"Voice: {voice_name} ({self.config.voice_id})")
        elif voice_name in EDGE_VOICES:
            self.config.voice_id = EDGE_VOICES[voice_name]
            self._status(f"Voice: {voice_name} ({self.config.voice_id})")
        elif voice_name in OPENAI_VOICES:
            self.config.voice_id = OPENAI_VOICES[voice_name]
            self._status(f"Voice: {voice_name} ({self.config.voice_id})")
        else:
            all_voices = {**OPENAI_VOICES, **EDGE_VOICES}
            self._status(f"Unknown: {voice_name}. Options: {', '.join(all_voices.keys())}")

    @property
    def is_available(self) -> bool:
        return self.backend != "none"

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def get_status(self) -> dict:
        return {
            "backend": self.backend,
            "voice": self.config.voice_id,
            "speed": self.config.speed,
        }

    @staticmethod
    def list_voices() -> dict:
        return {**OPENAI_VOICES, **EDGE_VOICES}
