"""
Tanishi's Brain v2 — Now with hands.

The Hybrid LLM Router + Tool Use Engine.

When Tanishi needs to act (search the web, read files, run commands),
Claude returns tool_use blocks. We execute them and feed results back.
This loop continues until Claude has a final text response.
"""

import asyncio
import json
import re
import threading
import warnings
import httpx
import anthropic
from typing import AsyncGenerator, Callable, Optional
from dataclasses import dataclass, field

from tanishi.config import routing as routing_cfg
from tanishi.core import get_config
from tanishi.core.personality import get_system_prompt
from tanishi.memory.manager import MemoryManager
from tanishi.tools.registry import ToolRegistry, ToolResult


@dataclass
class Message:
    role: str
    content: str
    timestamp: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class BrainResponse:
    content: str
    model_used: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached: bool = False
    tools_used: list[str] = field(default_factory=list)


SENSITIVE_KEYWORDS = [
    "password", "secret", "private", "girlfriend", "boyfriend",
    "salary", "bank", "medical", "health", "ssn", "credit card",
    "affair", "cheat", "personal", "diary", "journal",
]


def classify_sensitivity(text: str) -> str:
    text_lower = text.lower()
    hits = sum(1 for kw in SENSITIVE_KEYWORDS if kw in text_lower)
    if hits >= 2:
        return "high"
    elif hits >= 1:
        return "medium"
    return "low"


def should_use_local(text: str, config=None) -> bool:
    if config and config.privacy_mode:
        return True
    return classify_sensitivity(text) == "high"


class TanishiBrain:
    """
    The central intelligence of Project Tanishi.

    Now with tool use: Claude can decide to search the web,
    read files, run commands, and more.
    """

    def __init__(self, tool_registry: Optional[ToolRegistry] = None):
        self.config = get_config()
        try:
            self.memory_manager = MemoryManager(self.config.db_path)
        except Exception:
            self.memory_manager = None
        self.conversation_history: list[Message] = []
        self.claude_client = None
        self.ollama_available = False
        self.tool_registry = tool_registry or ToolRegistry()
        if tool_registry is None:
            self._register_default_tools()
        self.max_tool_loops = 10
        self._tool_status_callback: Optional[Callable] = None
        self._init_clients()
        if not self.tool_registry.tools:
            warnings.warn(
                "TanishiBrain started with no tools registered; tool-use requests will fail.",
                RuntimeWarning,
                stacklevel=2,
            )

    def set_tool_status_callback(self, callback: Callable):
        """Set callback for tool execution status (for CLI display)."""
        self._tool_status_callback = callback

    def _register_default_tools(self):
        """Provide baseline tools when brain is used standalone."""
        try:
            from tanishi.tools.system_tools import get_system_tools
            wanted = {"get_datetime", "get_system_info"}
            for tool in get_system_tools():
                if tool.name in wanted:
                    self.tool_registry.register(tool)
        except Exception:
            pass

    def _init_clients(self):
        if self.config.anthropic_api_key:
            self.claude_client = anthropic.Anthropic(
                api_key=self.config.anthropic_api_key
            )
        try:
            resp = httpx.get(f"{self.config.ollama_base_url}/api/tags", timeout=0.5)
            self.ollama_available = resp.status_code == 200
        except Exception:
            self.ollama_available = False

    def get_status(self) -> dict:
        tool_count = len(self.tool_registry.tools) if self.tool_registry else 0
        return {
            "claude": "online" if self.claude_client else "no api key",
            "ollama": "online" if self.ollama_available else "offline",
            "default_llm": self.config.default_llm,
            "privacy_mode": self.config.privacy_mode,
            "history_length": len(self.conversation_history),
            "tools": tool_count,
        }

    def _select_model(self, user_input: str) -> str:
        if self._needs_realtime_tools(user_input) and self.claude_client:
            return "claude"
        if self.config.privacy_mode and self.ollama_available:
            return "ollama"
        if should_use_local(user_input, self.config) and self.ollama_available:
            return "ollama"
        if (
            routing_cfg.LOCAL_FIRST
            and self.ollama_available
            and self._approx_prompt_tokens(user_input) < routing_cfg.COMPLEXITY_THRESHOLD_TOKENS
        ):
            return "ollama"
        if self.config.default_llm == "ollama" and self.ollama_available:
            return "ollama"
        if self.config.default_llm == "claude" and self.claude_client:
            return "claude"
        if self.config.default_llm == "auto":
            if len(user_input) > 200 or "?" in user_input:
                if self.claude_client:
                    return "claude"
            if self.ollama_available:
                return "ollama"
        if self.claude_client:
            return "claude"
        if self.ollama_available:
            return "ollama"
        raise RuntimeError("No LLM available!")

    @staticmethod
    def _approx_prompt_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    @staticmethod
    def _needs_realtime_tools(user_input: str) -> bool:
        patterns = (
            r"\b(time|clock|date|today|now|current)\b",
            r"\b(cpu|ram|memory|battery|disk|system|process)\b",
            r"\b(weather|temperature)\b",
        )
        text = user_input.lower()
        return any(re.search(pattern, text) for pattern in patterns)

    def _claude_model_for_input(self, user_input: str) -> str:
        if self._approx_prompt_tokens(user_input) >= routing_cfg.COMPLEXITY_THRESHOLD_TOKENS:
            return routing_cfg.COMPLEX_QUERY_MODEL
        return routing_cfg.SIMPLE_QUERY_MODEL

    def _build_messages(self, user_input: str) -> list[dict]:
        messages = []
        max_history = self.config.max_conversation_history
        for msg in self.conversation_history[-max_history:]:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_input})
        return messages

    async def think(
        self,
        user_input: str,
        mood: str = "casual",
        style: str = "",
        extra_context: str = "",
    ) -> BrainResponse:
        """
        Process a thought with full tool use support.

        The agentic loop:
        1. Send message + tools to Claude
        2. If Claude returns tool_use -> execute tool -> send result back
        3. Repeat until Claude returns a text response
        """
        model = self._select_model(user_input)

        skill_block = ""
        try:
            from tanishi.skills.skill_store import find_matching_skills, format_skills_for_context

            matches = find_matching_skills(user_input, top_k=3)
            if matches:
                skill_block = format_skills_for_context(matches)
                print(f"[skills] found {len(matches)} matching skill(s) for this query")
        except Exception:
            pass

        merged_extra = extra_context.strip()
        if skill_block:
            merged_extra = (
                (merged_extra + "\n\n" + skill_block).strip() if merged_extra else skill_block
            )

        try:
            from tanishi.memory.dream import DreamCycle

            dream = DreamCycle(self.memory_manager, self.config)
            dream_ctx = dream.get_dream_context(max_tokens=500)
            if dream_ctx:
                n_mem = sum(1 for ln in dream_ctx.splitlines() if ln.strip().startswith("- "))
                print(f"[dream] injected {n_mem} memories into context")
                merged_extra = (
                    (dream_ctx + "\n\n" + merged_extra).strip() if merged_extra else dream_ctx
                )
        except Exception:
            pass

        system_prompt = get_system_prompt(
            current_mode=mood,
            style=style,
            extra_context=merged_extra,
        )
        messages = self._build_messages(user_input)

        if model == "claude":
            response = await self._think_claude_with_tools(
                system_prompt, messages, user_input=user_input
            )
        else:
            response = await self._think_ollama(system_prompt, messages)

        self.conversation_history.append(Message(role="user", content=user_input))
        self.conversation_history.append(Message(role="assistant", content=response.content))

        if self._response_ok_for_skill_learning(response):
            self._schedule_skill_extraction(response)

        return response

    @staticmethod
    def _response_ok_for_skill_learning(response: BrainResponse) -> bool:
        text = (response.content or "").strip()
        if not text:
            return False
        mu = response.model_used or ""
        if mu.startswith("claude (error)") or mu.startswith("ollama (error)"):
            return False
        return True

    def _schedule_skill_extraction(self, response: BrainResponse) -> None:
        hist_snapshot = [{"role": m.role, "content": m.content} for m in self.conversation_history]
        tools_used = list(response.tools_used or [])

        def run() -> None:
            try:
                from tanishi.skills.skill_extractor import extract_skill, should_extract_skill
                from tanishi.skills.skill_store import save_skill

                if not should_extract_skill(hist_snapshot, tools_used):
                    return
                extracted = extract_skill(hist_snapshot, tools_used)
                if extracted:
                    save_skill(extracted)
                    print(f"[skills] extracted new skill: '{extracted.get('title', '')}'")
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    async def _think_claude_with_tools(
        self, system_prompt: str, messages: list[dict], user_input: str
    ) -> BrainResponse:
        """Claude with full agentic tool use loop."""
        tools = self.tool_registry.get_claude_tools()
        total_in = 0
        total_out = 0
        tools_used = []
        claude_model = self._claude_model_for_input(user_input)

        try:
            for loop_count in range(self.max_tool_loops):
                api_kwargs = {
                    "model": claude_model,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                }
                if tools:
                    api_kwargs["tools"] = tools

                response = await asyncio.to_thread(
                    self.claude_client.messages.create, **api_kwargs
                )
                total_in += response.usage.input_tokens
                total_out += response.usage.output_tokens

                if response.stop_reason == "tool_use":
                    assistant_content = response.content
                    tool_results = []

                    for block in response.content:
                        if block.type == "tool_use":
                            tool_name = block.name
                            tool_input = block.input
                            tool_use_id = block.id

                            if self._tool_status_callback:
                                self._tool_status_callback("using", tool_name, tool_input)

                            result = await self.tool_registry.execute(tool_name, tool_input)
                            tools_used.append(tool_name)

                            if self._tool_status_callback:
                                self._tool_status_callback(
                                    "done", tool_name,
                                    {"success": result.success, "ms": result.execution_time_ms}
                                )

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": result.output if result.success else f"Error: {result.error}",
                            })

                    messages.append({"role": "assistant", "content": assistant_content})
                    messages.append({"role": "user", "content": tool_results})

                else:
                    # Final text response
                    text_parts = []
                    for block in response.content:
                        if hasattr(block, "text"):
                            text_parts.append(block.text)

                    final_text = "\n".join(text_parts) or "I thought about it... and got nothing."

                    return BrainResponse(
                        content=final_text,
                        model_used=f"claude ({claude_model})",
                        tokens_in=total_in,
                        tokens_out=total_out,
                        tools_used=tools_used,
                    )

            return BrainResponse(
                content="Hit my tool loop limit. Let me just answer directly.",
                model_used=f"claude ({claude_model})",
                tokens_in=total_in,
                tokens_out=total_out,
                tools_used=tools_used,
            )

        except anthropic.APIError as e:
            return BrainResponse(
                content=f"*sighs* Claude's having a moment. Error: {e.message}",
                model_used="claude (error)",
            )

    async def _think_ollama(self, system_prompt: str, messages: list[dict]) -> BrainResponse:
        try:
            ollama_messages = [{"role": "system", "content": system_prompt}] + messages
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.config.ollama_base_url}/api/chat",
                    json={
                        "model": self.config.ollama_model,
                        "messages": ollama_messages,
                        "stream": False,
                    },
                )
                data = response.json()
                return BrainResponse(
                    content=data.get("message", {}).get("content", "...I got nothing."),
                    model_used=f"ollama ({self.config.ollama_model})",
                    tokens_in=data.get("prompt_eval_count", 0),
                    tokens_out=data.get("eval_count", 0),
                )
        except Exception as e:
            return BrainResponse(
                content=f"Local brain is offline. Error: {str(e)}",
                model_used="ollama (error)",
            )

    async def stream_think(self, user_input: str, mood: str = "casual", extra_context: str = "") -> AsyncGenerator[str, None]:
        response = await self.think(user_input, mood=mood, extra_context=extra_context)
        yield response.content

    def clear_history(self):
        self.conversation_history.clear()

    def get_history_summary(self) -> str:
        if not self.conversation_history:
            return "No conversation history yet."
        return f"{len(self.conversation_history)} messages in this session."
