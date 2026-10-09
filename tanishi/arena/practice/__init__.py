"""The Practice tier: 29 tasks (9 ported from the legacy benchmark, 20 new), each a Task YAML in `tasks/`.

    from tanishi.arena.practice import load_practice, cei_eligible
    tasks = load_practice()                        # all 29, sorted by id
    scored = [t for t in tasks if cei_eligible(t)]  # what CEI may use

Style tasks keep an LLM judge, are tagged `judge:llm`, and are left out of CEI. Tasks that need a tool carry the tag
`tool`, and their verifiers read the event log, so they score 0 if the tool was not really called.
"""
from __future__ import annotations

from pathlib import Path

from tanishi.arena.task import Task, load_task

PRACTICE_DIR = Path(__file__).parent / "tasks"
JUDGE_TAG = "judge:llm"
TOOL_TAG = "tool"


def load_practice() -> list[Task]:
    """Every Practice task, sorted by id."""
    return sorted((load_task(p) for p in PRACTICE_DIR.glob("*.yaml")), key=lambda t: t.id)


def cei_eligible(task: Task) -> bool:
    """False for any task that depends on an LLM judge."""
    return JUDGE_TAG not in task.tags


def needs_tool(task: Task) -> bool:
    return TOOL_TAG in task.tags


__all__ = ["PRACTICE_DIR", "cei_eligible", "load_practice", "needs_tool"]
