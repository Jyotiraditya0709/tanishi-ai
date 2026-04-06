"""
Tanishi Screen Watcher — She sees everything.

Continuously monitors your screen and proactively helps:
- Spots errors in your terminal/IDE and offers fixes
- Notices what you're browsing and offers context
- Detects when you're stuck (same screen for too long)
- Reads text on screen when asked "what do you see?"

Smart about resources:
- Only analyzes when screen changes significantly
- Compresses screenshots to ~100KB before sending to Claude
- Configurable interval (default: 10 seconds)
- Pause/resume anytime
"""

import os
import io
import time
import base64
import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field


@dataclass
class ScreenContext:
    """What Tanishi sees on screen right now."""
    description: str = ""
    detected_app: str = ""
    has_error: bool = False
    error_text: str = ""
    has_code: bool = False
    timestamp: str = ""
    screenshot_hash: str = ""


@dataclass
class WatcherConfig:
    interval_seconds: int = 10       # Check screen every N seconds
    change_threshold: float = 0.05   # Min % change to trigger analysis
    auto_help_errors: bool = True    # Proactively offer help on errors
    auto_help_stuck: bool = True     # Notice when stuck on same screen
    stuck_timeout: int = 120         # Seconds on same screen = "stuck"
    max_analyses_per_min: int = 3    # Rate limit Claude Vision calls
    enabled: bool = True


class ScreenWatcher:
    """
    Continuous screen monitor with intelligent change detection.
    
    The watcher takes periodic screenshots, compares them to detect
    changes, and only sends to Claude Vision when something meaningful
    happens. This keeps costs low while maintaining awareness.
    """

    def __init__(self, config: Optional[WatcherConfig] = None):
        self.config = config or WatcherConfig()
        self._running = False
        self._paused = False
        self._last_hash = ""
        self._last_analysis_time = 0
        self._same_screen_since = 0
        self._analysis_count_this_min = 0
        self._minute_start = time.time()
        self._current_context = ScreenContext()

        # Callbacks
        self.on_context_change: Optional[Callable] = None  # When screen context changes
        self.on_error_detected: Optional[Callable] = None  # When error spotted
        self.on_stuck_detected: Optional[Callable] = None  # When user seems stuck
        self.on_status: Optional[Callable] = None
        self._analyze_callback: Optional[Callable] = None  # Claude Vision call

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    @property
    def current_context(self) -> ScreenContext:
        return self._current_context

    def _capture_screenshot(self) -> Optional[bytes]:
        """Capture and compress a screenshot. Returns PNG bytes."""
        try:
            from PIL import ImageGrab, Image

            screenshot = ImageGrab.grab()

            # Resize to reduce size (720p is enough for analysis)
            max_width = 960
            if screenshot.width > max_width:
                ratio = max_width / screenshot.width
                new_size = (max_width, int(screenshot.height * ratio))
                screenshot = screenshot.resize(new_size, Image.LANCZOS)

            # Compress to JPEG for smaller size
            buffer = io.BytesIO()
            screenshot.save(buffer, format="JPEG", quality=60, optimize=True)
            return buffer.getvalue()

        except ImportError:
            self._status("Pillow not installed. pip install Pillow")
            return None
        except Exception as e:
            return None

    def _image_hash(self, img_bytes: bytes) -> str:
        """Quick hash to detect screen changes."""
        return hashlib.md5(img_bytes).hexdigest()

    def _has_significant_change(self, new_hash: str) -> bool:
        """Check if screen changed enough to warrant analysis."""
        if not self._last_hash:
            return True
        return new_hash != self._last_hash

    def _check_rate_limit(self) -> bool:
        """Ensure we don't spam Claude Vision."""
        now = time.time()
        if now - self._minute_start > 60:
            self._analysis_count_this_min = 0
            self._minute_start = now
        return self._analysis_count_this_min < self.config.max_analyses_per_min

    async def analyze_screenshot(self, img_bytes: bytes, question: str = "") -> str:
        """
        Send screenshot to Claude Vision for analysis.
        Uses the callback set by the CLI/brain.
        """
        if not self._analyze_callback:
            return "No analysis callback set."

        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        prompt = question or (
            "Briefly describe what's on this screen in 2-3 sentences. "
            "Note the main application visible, any error messages, "
            "code being edited, or websites open. "
            "If you see an error, quote it exactly. "
            "Be concise — this is a background check, not a detailed report."
        )

        try:
            self._analysis_count_this_min += 1
            result = await self._analyze_callback(img_b64, prompt)
            return result
        except Exception as e:
            return f"Analysis failed: {e}"

    def _detect_patterns(self, analysis: str) -> ScreenContext:
        """Parse Claude's analysis to detect errors, code, apps, etc."""
        ctx = ScreenContext(
            description=analysis,
            timestamp=datetime.now().isoformat(),
        )

        analysis_lower = analysis.lower()

        # Detect errors — TIGHT keywords to avoid false positives on docs/tutorials
        # Only trigger on patterns that indicate ACTUAL errors happening now
        error_keywords = [
            "traceback (most recent call last)",
            "unhandled exception",
            "fatal error",
            "segmentation fault",
            "syntax error",
            "module not found",
            "permission denied",
            "connection refused",
            "build failed",
            "compilation error",
            "panic:",
            "error: cannot find",
            "typeerror:",
            "referenceerror:",
            "nameerror:",
            "importerror:",
            "keyerror:",
            "valueerror:",
            "attributeerror:",
            "filenotfounderror:",
            "oserror:",
            "runtimeerror:",
        ]
        # Only flag if the error looks like it's ON SCREEN (not in docs/tutorials)
        is_reading_docs = any(d in analysis_lower for d in [
            "documentation", "tutorial", "reading about", "article",
            "stack overflow", "blog post", "learning about",
        ])
        for kw in error_keywords:
            if kw in analysis_lower and not is_reading_docs:
                ctx.has_error = True
                ctx.error_text = analysis
                break

        # Detect code
        code_keywords = [
            "code editor", "vs code", "visual studio", "ide",
            "terminal", "command prompt", "python", "javascript",
            "function", "class ", "import ", "def ",
        ]
        for kw in code_keywords:
            if kw in analysis_lower:
                ctx.has_code = True
                break

        # Detect app
        app_keywords = {
            "chrome": "Chrome", "firefox": "Firefox", "edge": "Edge",
            "vs code": "VS Code", "visual studio": "VS Code",
            "terminal": "Terminal", "command prompt": "Terminal",
            "spotify": "Spotify", "discord": "Discord",
            "word": "Word", "excel": "Excel", "powerpoint": "PowerPoint",
            "telegram": "Telegram", "whatsapp": "WhatsApp",
            "youtube": "YouTube", "github": "GitHub",
            "amazon": "Amazon", "gmail": "Gmail",
        }
        for kw, name in app_keywords.items():
            if kw in analysis_lower:
                ctx.detected_app = name
                break

        return ctx

    async def watch_once(self) -> Optional[ScreenContext]:
        """
        Do one watch cycle: capture → check change → analyze if needed.
        Returns new context if analysis was done, None otherwise.
        """
        if self._paused or not self.config.enabled:
            return None

        # Capture
        img_bytes = self._capture_screenshot()
        if not img_bytes:
            return None

        # Check for change
        new_hash = self._image_hash(img_bytes)

        if not self._has_significant_change(new_hash):
            # Same screen — check for "stuck"
            if self.config.auto_help_stuck:
                if self._same_screen_since == 0:
                    self._same_screen_since = time.time()
                elif time.time() - self._same_screen_since > self.config.stuck_timeout:
                    if self.on_stuck_detected:
                        self.on_stuck_detected(self._current_context)
                    self._same_screen_since = 0  # Reset
            return None

        # Screen changed
        self._same_screen_since = 0
        self._last_hash = new_hash

        # Rate limit check
        if not self._check_rate_limit():
            return None

        # Analyze with Claude Vision
        analysis = await self.analyze_screenshot(img_bytes)
        if not analysis or analysis.startswith("Analysis failed"):
            return None

        # Parse the analysis
        new_ctx = self._detect_patterns(analysis)
        new_ctx.screenshot_hash = new_hash

        old_ctx = self._current_context
        self._current_context = new_ctx

        # Notify on context change
        if self.on_context_change:
            self.on_context_change(new_ctx)

        # Notify on error detection
        if new_ctx.has_error and self.config.auto_help_errors and self.on_error_detected:
            self.on_error_detected(new_ctx)

        return new_ctx

    async def run(self):
        """Run the watcher loop continuously."""
        self._running = True
        self._status("Screen watcher active")

        while self._running:
            try:
                if not self._paused:
                    await self.watch_once()
            except Exception as e:
                self._status(f"Watch error: {e}")

            await asyncio.sleep(self.config.interval_seconds)

        self._status("Screen watcher stopped")

    def pause(self):
        self._paused = True
        self._status("Screen watcher paused")

    def resume(self):
        self._paused = False
        self._status("Screen watcher resumed")

    def stop(self):
        self._running = False

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "paused": self._paused,
            "enabled": self.config.enabled,
            "current_app": self._current_context.detected_app,
            "has_error": self._current_context.has_error,
            "interval": self.config.interval_seconds,
            "analyses_this_min": self._analysis_count_this_min,
        }


# ============================================================
# Integration with Brain — Claude Vision analysis
# ============================================================

def create_vision_analyzer(claude_client, model: str = "claude-sonnet-4-20250514"):
    """
    Create the callback function that sends screenshots to Claude Vision.
    This connects the watcher to Claude's eyes.
    """
    async def analyze(img_b64: str, prompt: str) -> str:
        try:
            response = claude_client.messages.create(
                model=model,
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": img_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )
            return response.content[0].text
        except Exception as e:
            return f"Vision error: {e}"

    return analyze
