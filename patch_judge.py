"""Patch benchmark.py to use Ollama (Gemma) for judging instead of Claude Haiku.

Usage (from project root):
    python patch_judge.py

Idempotent — safe to re-run.
"""
from pathlib import Path
import re

FILE = Path("tanishi/autoresearch/benchmark.py")

NEW_FUNCTION = '''def judge_response(task, response):
    """Score an AI response 0.0-1.0 using Gemma (local) with Claude fallback."""
    if not response or not response.strip():
        return 0.0

    criteria = "\\n".join(f"  - {c}" for c in task.success_criteria)
    prompt = (
        "Score this AI response from 0.0 to 1.0.\\n"
        f"Task: {task.prompt}\\n"
        f"Criteria:\\n{criteria}\\n"
        f"Response: {response[:2000]}\\n"
        "Reply with ONLY a number 0.0-1.0."
    )

    import os
    import re as _re

    # --- Try Ollama first (free, local) ---
    # Note: NO "options" dict. Passing num_predict/temperature to gemma4:e4b
    # causes it to return empty strings on some Ollama builds. Gemma stops
    # naturally after emitting the score (usually 2-4 tokens).
    try:
        import requests
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        ollama_model = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
        r = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=60,
        )
        if r.status_code == 200:
            raw = r.json().get("message", {}).get("content", "").strip()
            m = _re.search(r"\\d*\\.?\\d+", raw)
            if m:
                return max(0.0, min(1.0, float(m.group())))
            print(f"[judge/ollama] no number in response: {raw!r}")
        else:
            print(f"[judge/ollama] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[judge/ollama] error: {e} -- falling back to Claude")

    # --- Fallback: Claude Haiku ---
    try:
        from anthropic import Anthropic
        client = Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        m = _re.search(r"\\d*\\.?\\d+", raw)
        return max(0.0, min(1.0, float(m.group()))) if m else 0.0
    except Exception as e:
        print(f"[judge/claude-fallback] error: {e}")
        return 0.0
'''


def main() -> int:
    if not FILE.exists():
        print(f"ERROR: {FILE} not found. Run this from the project root.")
        return 1

    src = FILE.read_text(encoding="utf-8")

    # Match from "def judge_response" up to (but not including) the next top-level def
    pattern = re.compile(
        r"def judge_response\(task, response\):.*?(?=\n(?:async )?def )",
        re.DOTALL,
    )
    if not pattern.search(src):
        print("ERROR: could not locate judge_response in the file.")
        return 2

    # Backup once
    backup = FILE.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text(src, encoding="utf-8")
        print(f"[backup] saved to {backup}")
    else:
        print(f"[backup] already exists at {backup}, not overwriting")

    # Lambda replacement to avoid \d/\. being interpreted as backrefs
    new_src = pattern.sub(lambda _m: NEW_FUNCTION.rstrip(), src, count=1)
    FILE.write_text(new_src, encoding="utf-8")

    # Verify
    written = FILE.read_text(encoding="utf-8")
    if "ollama" in written.lower() and "NO \"options\" dict" in written:
        print(f"[ok] patched {FILE}")
        print("     - options dict removed (fixes empty-string bug)")
        print("     - Ollama first, Claude Haiku fallback")
        return 0
    else:
        print("[ERROR] patch did not take. Restoring from backup.")
        FILE.write_text(src, encoding="utf-8")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
