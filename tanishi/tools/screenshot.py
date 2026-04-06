"""
Tanishi Screenshot v2 — Fixed token overflow.

Previous version sent raw base64 in the tool response (~200K tokens).
This version saves to disk and sends a compressed analysis instead.
"""

import io
import os
import base64
from datetime import datetime
from pathlib import Path

from tanishi.tools.registry import ToolDefinition


async def take_screenshot(question: str = "What's on my screen?") -> str:
    """
    Capture screenshot, save to disk, and analyze with Claude Vision.
    Returns TEXT description, not raw image data.
    """
    try:
        from PIL import ImageGrab, Image

        # Capture
        screenshot = ImageGrab.grab()

        # Resize AGGRESSIVELY — 640px wide max (keeps tokens under 50K)
        max_width = 640
        if screenshot.width > max_width:
            ratio = max_width / screenshot.width
            new_size = (max_width, int(screenshot.height * ratio))
            screenshot = screenshot.resize(new_size, Image.LANCZOS)

        # Save to disk
        screenshots_dir = Path.home() / ".tanishi" / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        save_path = screenshots_dir / filename
        screenshot.save(save_path, format="JPEG", quality=40, optimize=True)

        # Convert to base64 for Vision API (small enough now)
        buffer = io.BytesIO()
        screenshot.save(buffer, format="JPEG", quality=40, optimize=True)
        img_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        # Analyze with Claude Vision directly
        analysis = await _analyze_with_vision(img_b64, question)

        return f"Screenshot saved to {save_path}\n\nAnalysis: {analysis}"

    except ImportError:
        return "Pillow not installed. Run: pip install Pillow"
    except Exception as e:
        return f"Screenshot failed: {str(e)}"


async def _analyze_with_vision(img_b64: str, question: str) -> str:
    """Send compressed screenshot to Claude Vision."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=300,
            messages=[{
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
                    {"type": "text", "text": question},
                ],
            }],
        )
        return response.content[0].text

    except Exception as e:
        return f"Vision analysis failed: {str(e)}"


def get_screenshot_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="take_screenshot",
            description="Capture a screenshot of the user's screen, save it, and describe what's visible. Returns a text description (not raw image). Use when user asks 'what's on my screen' or 'look at my screen'.",
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "What to look for on the screen.",
                        "default": "What's on my screen?",
                    },
                },
                "required": [],
            },
            handler=take_screenshot,
            category="vision",
            risk_level="low",
        ),
    ]
