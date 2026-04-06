"""
Tanishi Voice Pipeline v3 — Real conversation.

OpenAI Whisper (hears perfectly) + Claude (thinks) + OpenAI TTS (sounds human)
With fillers, sentence-by-sentence speaking, and interrupt detection.
"""

import re
import asyncio
from typing import Optional
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

from tanishi.voice.listener import TanishiListener
from tanishi.voice.speaker import TanishiSpeaker, VoiceConfig
from tanishi.core.brain import TanishiBrain

console = Console()


@dataclass
class PipelineConfig:
    wake_word_enabled: bool = True
    show_transcript: bool = True
    show_response: bool = True
    voice_name: str = "sarcastic"
    speed: float = 1.0
    use_fillers: bool = True


class VoicePipeline:
    """Natural voice conversation pipeline."""

    def __init__(self, brain: TanishiBrain, config: Optional[PipelineConfig] = None, extra_context: str = ""):
        self.brain = brain
        self.config = config or PipelineConfig()
        self.extra_context = extra_context
        self._running = False

        self.listener = TanishiListener()
        self.listener.on_status = lambda msg: console.print(f"  [dim cyan]🎤 {msg}[/dim cyan]")
        self.listener.wake_word_enabled = self.config.wake_word_enabled

        self.speaker = TanishiSpeaker(config=VoiceConfig(speed=self.config.speed))
        self.speaker.on_status = lambda msg: console.print(f"  [dim cyan]🔊 {msg}[/dim cyan]")

        if self.config.voice_name:
            self.speaker.set_voice(self.config.voice_name)

    async def process_once(self) -> Optional[str]:
        """One cycle: listen → filler → think → speak sentence by sentence."""
        result = await self.listener.listen_once()
        if result is None or not result.text.strip():
            return None

        transcript = result.text.strip()

        if self.config.show_transcript:
            console.print(f"\n  [bold green]Heard:[/bold green] {transcript} [dim]({result.backend}, {result.duration_ms:.0f}ms)[/dim]")

        # Wake word check
        if self.listener.wake_word_enabled:
            detected, cleaned = self.listener.check_wake_word(transcript)
            if not detected:
                console.print(f"  [dim]No wake word. Say 'Hey Tanishi'.[/dim]")
                return None

            if not cleaned:
                await self.speaker.speak("Yeah?")
                result2 = await self.listener.listen_once()
                if result2 and result2.text.strip():
                    cleaned = result2.text.strip()
                    console.print(f"  [bold green]Command:[/bold green] {cleaned}")
                else:
                    await self.speaker.speak("I'll be here when you're ready.")
                    return None
            transcript = cleaned

        # Stop commands
        if transcript.lower().strip() in ("stop", "shut up", "be quiet", "enough"):
            self.speaker.stop()
            await self.speaker.speak("Fine.")
            return "stopped"

        # Filler while thinking
        if self.config.use_fillers:
            filler_task = asyncio.create_task(self.speaker.speak_filler())
            await asyncio.sleep(0.2)

        # Think
        console.print(f"\n[bold cyan]  Tanishi is thinking...[/bold cyan]")
        response = await self.brain.think(
            user_input=transcript,
            extra_context=self.extra_context,
        )

        # Wait for filler to finish
        if self.config.use_fillers:
            try:
                await filler_task
            except Exception:
                pass

        # Show response
        if self.config.show_response:
            tools_info = ""
            if response.tools_used:
                tools_info = f" | tools: {', '.join(response.tools_used)}"
            console.print(Panel(
                response.content[:500] + ("..." if len(response.content) > 500 else ""),
                title="[bold cyan]Tanishi[/bold cyan]",
                subtitle=f"[dim]{response.tokens_in}→{response.tokens_out} tokens{tools_info}[/dim]",
                border_style="cyan",
            ))

        # Speak sentence by sentence with interrupt checks
        await self._speak_with_interrupts(response.content)
        return response.content

    async def _speak_with_interrupts(self, text: str):
        """Speak sentence by sentence, check mic between each."""
        clean = self.speaker._clean_for_speech(text)
        sentences = re.split(r'(?<=[.!?])\s+', clean.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return

        # For short responses (1-3 sentences), speak all at once — sounds more natural
        if self.speaker.backend == "openai" or len(sentences) <= 3:
            await self.speaker.speak(clean)
            return

        # For longer responses, speak in chunks of 2-3 sentences
        chunks = []
        for i in range(0, len(sentences), 2):
            chunk = ' '.join(sentences[i:i+2])
            chunks.append(chunk)

        for i, chunk in enumerate(chunks):
            await self.speaker.speak(chunk)

            # Quick mic check between chunks (not after last one)
            if i < len(chunks) - 1:
                interrupted = await self._quick_mic_check(0.3)
                if interrupted:
                    console.print(f"  [dim yellow]Interrupted.[/dim yellow]")
                    await self.speaker.speak("Got it, I'll stop.")
                    break

    async def _quick_mic_check(self, duration: float = 0.3) -> bool:
        """Brief mic check for interrupts between sentences."""
        try:
            import sounddevice as sd
            import numpy as np
            detected = False

            def callback(indata, frames, time_info, status):
                nonlocal detected
                if np.sqrt(np.mean(indata ** 2)) > 0.025:
                    detected = True

            with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                              blocksize=1024, callback=callback):
                await asyncio.sleep(duration)
            return detected
        except Exception:
            return False

    async def run_loop(self):
        """Run voice mode continuously."""
        self._running = True

        mode = "OpenAI Whisper + TTS" if self.speaker.backend == "openai" else "Google + edge-tts"

        console.print(Panel(
            f"[bold]Voice mode active![/bold]\n\n"
            f"  Mode: [cyan]{mode}[/cyan]\n"
            f"  Listener: {self.listener.backend}\n"
            f"  Speaker: {self.speaker.backend} ({self.speaker.config.voice_id})\n\n"
            f"  Say [bold cyan]\"Hey Tanishi\"[/bold cyan] followed by your question.\n"
            f"  Talk between sentences to interrupt.\n"
            f"  Press [bold]Ctrl+C[/bold] to return to text mode.",
            title="[bold cyan]🎤 Voice Mode v3[/bold cyan]",
            border_style="cyan",
        ))

        try:
            while self._running:
                try:
                    await self.process_once()
                except Exception as e:
                    console.print(f"  [red]Voice error: {e}[/red]")
                    await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            console.print("\n[cyan]Tanishi:[/cyan] Back to text mode.\n")

    def stop(self):
        self._running = False
        self.speaker.stop()
