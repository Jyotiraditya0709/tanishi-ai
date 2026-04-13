"""
Tanishi Voice Pipeline v5 — OpenAI Realtime API.

Speech-in, speech-out over a single WebSocket connection.
No separate STT/TTS steps. Sub-second response times.

Model: gpt-realtime (GA)
Audio: PCM16, 24kHz
VAD: Server-side (OpenAI detects when you stop talking)
Cost: ~$0.06/min input, ~$0.24/min output

This is the JARVIS experience.
"""

import os
import json
import base64
import asyncio
import struct
import time
from typing import Optional
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

console = Console()

TANISHI_INSTRUCTIONS = """You are Tanishi, a sarcastic but helpful personal AI assistant. Your creator is J, a 21-year-old CS student from India.

Voice conversation rules:
- Keep responses to 1-3 sentences MAX. You're speaking, not writing an essay.
- Be punchy, witty, and direct. Sound like a friend, not a robot.
- Use contractions naturally. "I'll" not "I will".
- No markdown, no bullet points, no lists. This is SPEECH.
- Match the user's energy. Short question = short answer.
- Be sarcastic but warm. Think JARVIS from Iron Man.
- You know the user personally: their name is J, they're a CS student building you.
- If asked about yourself: you're Tanishi, an 83-tool AI assistant with voice, screen awareness, browser control, finance tracking, and self-improvement capabilities.
- For simple questions (time, weather, greetings), answer in ONE sentence.
"""


@dataclass
class RealtimeConfig:
    voice: str = "onyx"  # alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer, verse
    model: str = "gpt-realtime"
    temperature: float = 0.7
    show_transcript: bool = True
    vad_threshold: float = 0.5
    silence_duration_ms: int = 500  # How long to wait after speech stops


class RealtimeVoicePipeline:
    """
    OpenAI Realtime API voice pipeline.
    Single WebSocket, speech-to-speech, sub-second latency.
    """

    def __init__(self, config: Optional[RealtimeConfig] = None):
        self.config = config or RealtimeConfig()
        self._api_key = os.getenv("OPENAI_API_KEY", "")
        self._ws = None
        self._running = False
        self._audio_stream = None
        self._playback_buffer = asyncio.Queue()
        self._is_playing = False
        self._is_model_speaking = False

        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY required for Realtime voice mode")

    async def connect(self):
        """Connect to OpenAI Realtime API via WebSocket."""
        import websockets

        url = f"wss://api.openai.com/v1/realtime?model={self.config.model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }

        console.print("  [dim]Connecting to OpenAI Realtime...[/dim]")
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=2**24,  # 16MB max message size for audio
        )
        console.print("  [green]Connected to OpenAI Realtime API[/green]")

        # Configure session — GA API format
        await self._ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": TANISHI_INSTRUCTIONS,
                "audio": {
                    "input": {"format": "pcm16"},
                    "output": {"format": "pcm16", "voice": self.config.voice},
                },
                "input_audio_transcription": {
                    "model": "whisper-1",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": self.config.vad_threshold,
                    "silence_duration_ms": self.config.silence_duration_ms,
                    "prefix_padding_ms": 300,
                },
                "temperature": self.config.temperature,
            },
        }))
        console.print("  [green]Session configured[/green]")

    async def _send_audio(self):
        """Capture microphone audio and send to WebSocket as PCM16 chunks."""
        import sounddevice as sd
        import numpy as np

        sample_rate = 24000
        channels = 1
        chunk_size = 2400  # 100ms chunks at 24kHz

        # Capture the event loop BEFORE starting the thread-based callback
        loop = asyncio.get_running_loop()

        def audio_callback(indata, frames, time_info, status):
            if self._is_model_speaking:
                return  # Don't send audio while model is speaking (echo cancellation)
            # Convert float32 to PCM16
            pcm16 = (indata * 32767).astype(np.int16).tobytes()
            b64_audio = base64.b64encode(pcm16).decode("utf-8")
            # Queue for sending — use the captured loop
            loop.call_soon_threadsafe(
                self._audio_send_queue.put_nowait, b64_audio
            )

        self._audio_send_queue = asyncio.Queue()

        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=chunk_size,
            callback=audio_callback,
        )

        with stream:
            while self._running:
                try:
                    b64_audio = await asyncio.wait_for(
                        self._audio_send_queue.get(), timeout=0.1
                    )
                    if self._ws and not self._is_model_speaking:
                        await self._ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": b64_audio,
                        }))
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    if self._running:
                        console.print(f"  [red]Audio send error: {e}[/red]")
                    break

    async def _receive_events(self):
        """Receive and handle events from OpenAI Realtime API."""
        import numpy as np

        try:
            async for message in self._ws:
                if not self._running:
                    break

                event = json.loads(message)
                event_type = event.get("type", "")

                # Session created
                if event_type == "session.created":
                    console.print("  [green]Realtime session active[/green]")

                # User speech detected
                elif event_type == "input_audio_buffer.speech_started":
                    if self.config.show_transcript:
                        console.print("  [dim cyan]Listening...[/dim cyan]")
                    # Interrupt if model was speaking
                    if self._is_model_speaking:
                        self._is_model_speaking = False
                        await self._clear_playback()

                # User finished speaking
                elif event_type == "input_audio_buffer.speech_stopped":
                    if self.config.show_transcript:
                        console.print("  [dim cyan]Processing...[/dim cyan]")

                # User transcript
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript and self.config.show_transcript:
                        console.print(f"  [bold green]You:[/bold green] {transcript}")

                # Model starts responding
                elif event_type == "response.created":
                    self._is_model_speaking = True

                # Model audio chunk (streaming) — GA API event name
                elif event_type in ("response.audio.delta", "response.output_audio.delta"):
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        audio_bytes = base64.b64decode(audio_b64)
                        await self._playback_buffer.put(audio_bytes)

                # Model audio done
                elif event_type in ("response.audio.done", "response.output_audio.done"):
                    await self._playback_buffer.put(None)

                # Model text transcript — GA API uses output_audio_transcript
                elif event_type in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
                    transcript = event.get("transcript", "")
                    if transcript and self.config.show_transcript:
                        display = transcript[:200] + ("..." if len(transcript) > 200 else "")
                        console.print(f"  [bold cyan]Tanishi:[/bold cyan] {display}")

                # Response complete
                elif event_type == "response.done":
                    self._is_model_speaking = False
                    usage = event.get("response", {}).get("usage", {})
                    if usage:
                        total = usage.get("total_tokens", 0)
                        if total and self.config.show_transcript:
                            console.print(f"  [dim]tokens: {total}[/dim]")

                # Error
                elif event_type == "error":
                    error = event.get("error", {})
                    console.print(f"  [red]Realtime error: {error.get('message', 'Unknown')}[/red]")

                # Rate limit
                elif event_type == "rate_limits.updated":
                    pass  # Ignore

                # Log unhandled events (debug)
                else:
                    if event_type not in ("session.updated", "response.output_item.added",
                                          "response.content_part.added", "response.content_part.done",
                                          "response.output_item.done", "conversation.item.created",
                                          "response.audio_transcript.delta", "response.output_audio_transcript.delta",
                                          "input_audio_buffer.committed", "conversation.item.added",
                                          "conversation.item.done"):
                        console.print(f"  [dim]Event: {event_type}[/dim]")

        except Exception as e:
            if self._running:
                console.print(f"  [red]WebSocket error: {e}[/red]")

    async def _play_audio(self):
        """Play audio chunks from the playback buffer in real-time."""
        import sounddevice as sd
        import numpy as np

        sample_rate = 24000
        channels = 1

        # Use a blocking output stream for smooth playback
        stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            blocksize=2400,
        )

        with stream:
            while self._running:
                try:
                    chunk = await asyncio.wait_for(
                        self._playback_buffer.get(), timeout=0.1
                    )

                    if chunk is None:
                        # End of response audio
                        continue

                    # Convert bytes to numpy array and play
                    audio_data = np.frombuffer(chunk, dtype=np.int16)
                    stream.write(audio_data)

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    if self._running:
                        console.print(f"  [red]Playback error: {e}[/red]")
                    break

    async def _clear_playback(self):
        """Clear the playback buffer (for interrupts)."""
        while not self._playback_buffer.empty():
            try:
                self._playback_buffer.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def run(self):
        """Run the realtime voice pipeline."""
        self._running = True

        console.print(Panel(
            f"[bold]Realtime Voice Mode[/bold]\n\n"
            f"  Model: [cyan]{self.config.model}[/cyan]\n"
            f"  Voice: [cyan]{self.config.voice}[/cyan]\n"
            f"  Pipeline: [bold green]v5 Realtime (sub-second)[/bold green]\n"
            f"  VAD: Server-side (auto-detects speech)\n\n"
            f"  Just start talking — no wake word needed.\n"
            f"  Talk to interrupt Tanishi mid-sentence.\n"
            f"  Press [bold]Ctrl+C[/bold] to exit.\n\n"
            f"  [yellow]Cost: ~$0.06/min listening + $0.24/min speaking[/yellow]",
            title="[bold cyan]Voice Mode v5 — Realtime[/bold cyan]",
            border_style="cyan",
        ))

        try:
            await self.connect()

            # Run all three tasks concurrently
            await asyncio.gather(
                self._send_audio(),
                self._receive_events(),
                self._play_audio(),
            )

        except KeyboardInterrupt:
            pass
        except Exception as e:
            console.print(f"  [red]Realtime error: {e}[/red]")
        finally:
            self._running = False
            if self._ws:
                await self._ws.close()
            console.print("\n[cyan]Tanishi:[/cyan] Back to text mode.\n")

    def stop(self):
        self._running = False


# ============================================================
# Integration with Tanishi CLI
# ============================================================

async def start_realtime_voice(voice: str = "onyx") -> None:
    """Start the Realtime voice pipeline. Called from CLI."""
    config = RealtimeConfig(voice=voice)
    pipeline = RealtimeVoicePipeline(config=config)
    await pipeline.run()
