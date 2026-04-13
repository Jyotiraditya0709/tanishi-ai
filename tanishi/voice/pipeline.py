"""
Tanishi Voice Pipeline v4 — FAST & Engaging.

Changes from v3:
1. Voice-optimized prompt: Forces Claude to give 1-3 sentence responses
2. Streaming speech: Speaks first sentence while generating the rest
3. Continuous conversation: No wake word for 30s after first activation
4. Instant fillers: "On it" plays BEFORE Claude even starts thinking
5. Faster turn-taking: Minimal gaps between listen -> think -> speak
"""

import re
import time
import asyncio
from typing import Optional
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

from tanishi.voice.listener import TanishiListener
from tanishi.voice.speaker import TanishiSpeaker, VoiceConfig
from tanishi.core.brain import TanishiBrain

console = Console()

# Voice-specific system prompt — this is the secret sauce
VOICE_SYSTEM_PROMPT = """
CRITICAL: You are in VOICE MODE. The user is LISTENING, not reading.

Rules for voice mode:
1. MAXIMUM 2-3 sentences per response. Never more. Ever.
2. Be punchy, direct, conversational. Like talking to a friend.
3. No bullet points, no lists, no markdown, no headers, no asterisks.
4. No emojis. No action descriptions like *cracks knuckles*.
5. If the task needs tools, say what you're doing in ONE sentence, then use the tools.
6. After tools complete, summarize results in ONE sentence.
7. Sound natural. Use contractions. "I'll" not "I will". "Can't" not "cannot".
8. If you don't know, say "Not sure, let me check" then stop.
9. Match the user's energy. Short question = short answer.

Examples of GOOD voice responses:
- "Found 3 earbuds under 2000 rupees on Amazon. The boAt Airdopes 141 looks best at 1299."
- "Done. Created the GitHub repo. It's public and ready."
- "You spent 649 rupees this week, mostly on Zomato. Maybe cook tomorrow?"
- "Your screen shows VS Code with a Python error on line 42. Want me to fix it?"

Examples of BAD voice responses (NEVER do these):
- "Let me break this down for you. First, I'll need to analyze..." (TOO LONG)
- "*adjusts glasses* Well, well, well..." (NO ACTIONS)
- "Here are 5 points: 1. First... 2. Second..." (NO LISTS)
"""

# Quick filler phrases — rotated for variety
FILLERS = [
    "On it.",
    "Let me check.",
    "One sec.",
    "Looking into it.",
    "Checking now.",
    "Hmm, let me see.",
    "Got it.",
]


@dataclass
class PipelineConfig:
    wake_word_enabled: bool = True
    show_transcript: bool = True
    show_response: bool = True
    voice_name: str = "sarcastic"
    speed: float = 1.0
    use_fillers: bool = True
    # v4: Conversation window — seconds to keep listening without wake word
    conversation_timeout: float = 30.0


class VoicePipeline:
    """Fast, engaging voice conversation pipeline."""

    def __init__(self, brain: TanishiBrain, config: Optional[PipelineConfig] = None, extra_context: str = ""):
        self.brain = brain
        self.config = config or PipelineConfig()
        self.extra_context = extra_context
        self._running = False
        self._filler_index = 0

        # v4: Track last interaction time for continuous conversation
        self._last_interaction = 0.0
        self._in_conversation = False

        self.listener = TanishiListener()
        self.listener.on_status = lambda msg: console.print(f"  [dim cyan]{msg}[/dim cyan]")
        self.listener.wake_word_enabled = self.config.wake_word_enabled

        self.speaker = TanishiSpeaker(config=VoiceConfig(speed=self.config.speed))
        self.speaker.on_status = lambda msg: console.print(f"  [dim cyan]{msg}[/dim cyan]")

        if self.config.voice_name:
            self.speaker.set_voice(self.config.voice_name)

        # v4: Pre-cache flag
        self._fillers_cached = False

    def _get_filler(self) -> str:
        """Get next filler phrase, rotating through the list."""
        filler = FILLERS[self._filler_index % len(FILLERS)]
        self._filler_index += 1
        return filler

    def _is_in_conversation_window(self) -> bool:
        """Check if we're still within the conversation window (no wake word needed)."""
        if not self._in_conversation:
            return False
        elapsed = time.time() - self._last_interaction
        return elapsed < self.config.conversation_timeout

    async def process_once(self) -> Optional[str]:
        """One cycle: listen -> filler -> think -> speak. FAST."""
        start_time = time.time()

        result = await self.listener.listen_once()
        if result is None or not result.text.strip():
            return None

        transcript = result.text.strip()

        if self.config.show_transcript:
            console.print(f"\n  [bold green]Heard:[/bold green] {transcript} [dim]({result.backend}, {result.duration_ms:.0f}ms)[/dim]")

        # Wake word check — SKIP if we're in active conversation
        if self.listener.wake_word_enabled and not self._is_in_conversation_window():
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
        else:
            # In conversation window — strip wake word if present
            detected, cleaned = self.listener.check_wake_word(transcript)
            if detected and cleaned:
                transcript = cleaned

        # Mark conversation as active
        self._in_conversation = True
        self._last_interaction = time.time()

        # Stop commands
        stop_words = ("stop", "shut up", "be quiet", "enough", "nevermind", "never mind", "goodbye", "bye")
        if transcript.lower().strip() in stop_words:
            self.speaker.stop()
            self._in_conversation = False
            await self.speaker.speak("Later, boss.")
            return "stopped"

        # INSTANT filler — play from cache, not API
        filler_task = None
        if self.config.use_fillers:
            filler_task = asyncio.create_task(self.speaker.speak_cached_filler())

        # Think — with voice-optimized prompt
        think_start = time.time()
        voice_context = VOICE_SYSTEM_PROMPT + "\n" + self.extra_context

        response = await self.brain.think(
            user_input=transcript,
            extra_context=voice_context,
        )
        think_time = time.time() - think_start

        # Wait for filler to finish (if still playing)
        if filler_task:
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
                response.content[:300] + ("..." if len(response.content) > 300 else ""),
                title="[bold cyan]Tanishi[/bold cyan]",
                subtitle=f"[dim]{response.tokens_in}->{response.tokens_out} tokens | think: {think_time:.1f}s{tools_info}[/dim]",
                border_style="cyan",
            ))

        # Speak with streaming approach — first sentence fast
        await self._speak_streaming(response.content)

        total_time = time.time() - start_time
        console.print(f"  [dim]Total turn: {total_time:.1f}s[/dim]")

        # Update conversation timestamp
        self._last_interaction = time.time()
        return response.content

    async def _speak_streaming(self, text: str):
        """Speak response with streaming — first sentence ASAP, rest follows."""
        clean = self.speaker._clean_for_speech(text)
        if not clean.strip():
            return

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', clean.strip())
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 3]

        if not sentences:
            return

        # Speak first sentence IMMEDIATELY
        await self.speaker.speak(sentences[0])

        # Speak remaining sentences with interrupt checks
        for sentence in sentences[1:]:
            interrupted = await self._quick_mic_check(0.2)
            if interrupted:
                console.print(f"  [dim yellow]Interrupted.[/dim yellow]")
                break
            await self.speaker.speak(sentence)

    async def _quick_mic_check(self, duration: float = 0.2) -> bool:
        """Brief mic check for interrupts between sentences."""
        try:
            import sounddevice as sd
            import numpy as np
            detected = False

            def callback(indata, frames, time_info, status):
                nonlocal detected
                if np.sqrt(np.mean(indata ** 2)) > 0.03:
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

        # v4: Pre-cache filler audio on first run
        if not self._fillers_cached:
            console.print("  [dim]Caching voice fillers for instant playback...[/dim]")
            await self.speaker.precache_fillers()
            self._fillers_cached = True

        mode = "OpenAI Whisper + TTS" if self.speaker.backend == "openai" else "Google + edge-tts"

        console.print(Panel(
            f"[bold]Voice mode active![/bold]\n\n"
            f"  Mode: [cyan]{mode}[/cyan]\n"
            f"  Listener: {self.listener.backend}\n"
            f"  Speaker: {self.speaker.backend} ({self.speaker.config.voice_id})\n"
            f"  Pipeline: [bold green]v4 Fast[/bold green]\n\n"
            f"  Say [bold cyan]\"Hey Tanishi\"[/bold cyan] to start.\n"
            f"  After activation, keep talking for 30s without wake word.\n"
            f"  Talk between sentences to interrupt.\n"
            f"  Press [bold]Ctrl+C[/bold] to return to text mode.",
            title="[bold cyan]Voice Mode v4[/bold cyan]",
            border_style="cyan",
        ))

        try:
            while self._running:
                try:
                    await self.process_once()
                except asyncio.CancelledError:
                    break
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    err_msg = str(e)
                    if "CancelledError" in err_msg or "cancelled" in err_msg.lower():
                        break
                    console.print(f"  [red]Voice error: {e}[/red]")
                    await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            console.print("\n[cyan]Tanishi:[/cyan] Back to text mode.\n")

    def stop(self):
        self._running = False
        self.speaker.stop()
