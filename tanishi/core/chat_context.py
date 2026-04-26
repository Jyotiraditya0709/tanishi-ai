"""
Extra context passed into TanishiBrain.think — keep CLI, HTTP /chat, and /ws aligned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tanishi.memory.manager import MemoryManager
    from tanishi.tools.registry import ToolRegistry


def chat_extra_context(memory: Optional["MemoryManager"], tool_registry: Optional["ToolRegistry"]) -> str:
    """Core memory + tool nudge (same string the CLI has always used)."""
    core = memory.build_core_context() if memory else ""
    n = len(tool_registry.tools) if tool_registry else 0
    tool_context = (
        f"\n\nYou have {n} tools available. Use them proactively when they'd help answer the question — "
        "especially web_search for current information and scan_github_trending for finding new tools."
    )
    return core + tool_context
