"""
When to distill a session into a procedural skill (Ollama), and extraction prompt.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

SIDE_EFFECT_TOOLS = frozenset(
    {
        "send_email",
        "write_file",
        "run_command",
        "log_expense",
        "kill_process",
        "set_clipboard",
        "control_system",
        "fill_form",
        "click_element",
        "spawn_agent",
        "multi_agent_task",
        "browse_url",
    }
)


def should_extract_skill(conversation_history: list[dict], tools_used: list[str]) -> bool:
    if len(set(tools_used)) >= 2:
        return True
    n_assistant = sum(1 for m in conversation_history if m.get("role") == "assistant")
    if n_assistant >= 3:
        return True
    if any(t in SIDE_EFFECT_TOOLS for t in tools_used):
        return True
    return False


def _ollama_json(prompt: str) -> Optional[str]:
    try:
        import httpx
        from tanishi.core import get_config

        cfg = get_config()
        base = os.getenv("OLLAMA_BASE_URL", cfg.ollama_base_url).rstrip("/")
        model = os.getenv("OLLAMA_MODEL", cfg.ollama_model)
        with httpx.Client(timeout=90.0) as client:
            r = client.post(
                f"{base}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
        if r.status_code != 200:
            return None
        return r.json().get("message", {}).get("content", "").strip() or None
    except Exception:
        return None


def _parse_skill_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        if not data.get("title"):
            return None
        return data
    except json.JSONDecodeError:
        return None


def _offline_skill_from_usage(
    conversation_history: list[dict], tools_used: list[str]
) -> dict:
    """
    Template skill when Ollama is unreachable or returns bad JSON (same idea as
    autoresearch reflections template fallback).
    """
    distinct = list(dict.fromkeys(str(t) for t in tools_used if t))
    if len(distinct) >= 2:
        title = f"{distinct[0]} + {distinct[1]} workflow"
    elif len(distinct) == 1:
        title = f"{distinct[0]} workflow"
    else:
        title = "conversation workflow"

    first_user = ""
    for m in conversation_history:
        if m.get("role") == "user" and m.get("content"):
            c = m["content"]
            first_user = c if isinstance(c, str) else str(c)
            break
    words = first_user.strip().split()[:5]
    phrase = " ".join(words) if words else "user request"

    trigger_patterns: list[str] = [phrase]
    trigger_patterns.extend(distinct)
    trigger_patterns = list(dict.fromkeys(p for p in trigger_patterns if p))

    if distinct:
        procedure = "\n".join(f"{i + 1}. Called {name}" for i, name in enumerate(distinct))
    else:
        procedure = "1. Assist the user based on the conversation."

    return {
        "title": title,
        "trigger_patterns": trigger_patterns,
        "procedure": procedure,
        "tools_used": distinct,
    }


def extract_skill(conversation_history: list[dict], tools_used: list[str]) -> Optional[dict]:
    """Ask Ollama for a skill JSON; fall back to a template doc if Ollama/JSON fails."""
    tail = conversation_history[-10:]
    conv_lines = []
    for m in tail[-5:]:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content)[:4000]
        else:
            content = str(content)[:4000]
        conv_lines.append(f"{role}: {content}")
    conv_block = "\n".join(conv_lines)

    prompt = (
        "You are analyzing a successful AI assistant interaction.\n\n"
        f"Conversation:\n{conv_block}\n\n"
        f"Tools used: {tools_used}\n\n"
        "Extract a reusable skill document. Respond in JSON only:\n"
        "{\n"
        '  "title": "short descriptive title",\n'
        '  "trigger_patterns": ["3-5 short phrases that would trigger this same workflow"],\n'
        '  "procedure": "numbered step-by-step of what was done",\n'
        '  "tools_used": ["list of tool names"]\n'
        "}\n"
    )

    raw = _ollama_json(prompt)
    data = _parse_skill_json(raw or "")
    if data is None:
        simpler = (
            "Reply with ONLY compact JSON (no markdown): "
            '{"title":"...","trigger_patterns":["..."],"procedure":"...","tools_used":[]}\n'
            f"Tools: {tools_used}\nLast user message excerpt:\n"
            f"{conv_block[-1500:]}"
        )
        raw2 = _ollama_json(simpler)
        data = _parse_skill_json(raw2 or "")

    if data is None:
        data = _offline_skill_from_usage(conversation_history, tools_used)

    example_input = ""
    example_output = ""
    for m in conversation_history:
        if m.get("role") == "user" and m.get("content"):
            c = m["content"]
            example_input = c if isinstance(c, str) else str(c)[:500]
            break
    for m in reversed(conversation_history):
        if m.get("role") == "assistant" and m.get("content"):
            c = m["content"]
            example_output = (c if isinstance(c, str) else str(c))[:2000]
            break

    now = datetime.now(timezone.utc).isoformat()
    tools = data.get("tools_used")
    if not isinstance(tools, list):
        tools = list(tools_used)
    patterns = data.get("trigger_patterns")
    if not isinstance(patterns, list):
        patterns = []

    return {
        "title": str(data.get("title", "")).strip(),
        "trigger_patterns": [str(p) for p in patterns if p],
        "procedure": str(data.get("procedure", "")).strip(),
        "tools_used": [str(t) for t in tools if t],
        "example_input": example_input[:2000],
        "example_output": example_output,
        "times_used": 1,
        "avg_satisfaction": None,
        "created_at": now,
        "last_used": now,
    }
