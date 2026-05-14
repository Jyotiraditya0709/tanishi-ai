import os
import asyncio


async def summarize_clipboard() -> str:
    try:
        import pyperclip
    except Exception:
        return "pyperclip is not installed; cannot read clipboard."

    text = (pyperclip.paste() or "").strip()
    if not text:
        return "Clipboard is empty."

    prompt = (
        "Summarize this clipboard content into concise bullet points.\n"
        "Keep it actionable and short.\n\n"
        f"Clipboard:\n{text[:12000]}"
    )

    try:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "ANTHROPIC_API_KEY not set; cannot summarize clipboard."
        client = anthropic.Anthropic(api_key=api_key)
        msg = await asyncio.to_thread(
            client.messages.create,
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"Clipboard summarize failed: {e}"
