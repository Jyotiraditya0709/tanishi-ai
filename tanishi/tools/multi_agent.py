"""
Tanishi Multi-Agent Engine — A team of her.

When a task is too complex for one agent, Tanishi spawns
specialist clones that work in parallel:

- Researcher: searches web, gathers data
- Coder: writes and reviews code
- Writer: creates content, copy, emails
- Analyst: analyzes data, makes decisions
- Planner: breaks down tasks, creates timelines
- Critic: reviews other agents' work, finds flaws

The Coordinator (Tanishi herself) manages the team,
assigns tasks, collects results, and presents the final output.

Usage:
  "Launch my SaaS product" → spawns Writer + Coder + Analyst
  "Research and compare 5 laptops" → spawns Researcher + Analyst
  "Write a blog post about AI" → spawns Researcher + Writer + Critic
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Callable
from dataclasses import dataclass, field

import anthropic

from tanishi.tools.registry import ToolDefinition


# ============================================================
# Agent Definitions
# ============================================================

@dataclass
class AgentResult:
    agent_name: str
    role: str
    output: str
    tokens_used: int = 0
    time_ms: float = 0
    success: bool = True


AGENT_ROLES = {
    "researcher": {
        "name": "Researcher",
        "emoji": "🔍",
        "system": """You are a research specialist. Your job is to gather comprehensive, accurate information on the given topic. 
Be thorough but concise. Focus on facts, data, and credible sources. 
Present findings in a structured format with key points.
Do NOT make things up — if you don't know, say so.""",
    },
    "coder": {
        "name": "Coder",
        "emoji": "💻",
        "system": """You are an expert programmer. Write clean, production-ready code.
Include comments, error handling, and follow best practices.
If asked to build something, provide complete, runnable code.
Use Python unless specified otherwise. Be practical, not theoretical.""",
    },
    "writer": {
        "name": "Writer",
        "emoji": "✍️",
        "system": """You are a skilled content writer. Create engaging, well-structured content.
Match the tone to the audience — professional for business, casual for blogs.
Use strong hooks, clear structure, and compelling conclusions.
Be creative but accurate. No fluff.""",
    },
    "analyst": {
        "name": "Analyst",
        "emoji": "📊",
        "system": """You are a data analyst and strategic thinker. 
Analyze information objectively, identify patterns, and make data-driven recommendations.
Present pros, cons, and tradeoffs clearly.
Use numbers and comparisons when possible. Be decisive — give a clear recommendation.""",
    },
    "planner": {
        "name": "Planner",
        "emoji": "📋",
        "system": """You are a project planner and task manager.
Break complex goals into concrete, actionable steps with timelines.
Identify dependencies, risks, and priorities.
Create realistic plans, not wishful thinking. Include milestones.""",
    },
    "critic": {
        "name": "Critic",
        "emoji": "🔎",
        "system": """You are a critical reviewer. Your job is to find flaws, gaps, and improvements.
Review the work of other agents objectively.
Point out what's good, what's wrong, and what's missing.
Be constructive — don't just criticize, suggest fixes.
Be thorough but fair.""",
    },
}


# ============================================================
# Task Decomposition Prompt
# ============================================================

DECOMPOSE_PROMPT = """You are Tanishi's coordinator. A complex task needs to be broken into subtasks for specialist agents.

Available agents:
- researcher: Gathers information, searches web, collects data
- coder: Writes code, builds features, debugs
- writer: Creates content, copy, emails, documents
- analyst: Analyzes data, compares options, makes recommendations  
- planner: Creates plans, timelines, breaks down projects
- critic: Reviews work, finds flaws, suggests improvements

Task: {task}

Break this into 2-5 subtasks. For each subtask, specify which agent should handle it and what exactly they should do.

Respond ONLY with valid JSON (no markdown, no backticks):
{{
    "plan": "Brief overall plan description",
    "subtasks": [
        {{
            "agent": "researcher",
            "task": "Specific instruction for this agent",
            "depends_on": []
        }},
        {{
            "agent": "writer", 
            "task": "Specific instruction - can reference results from previous agents",
            "depends_on": [0]
        }}
    ]
}}

depends_on is an array of subtask indices (0-based) that must complete first. Empty = can run immediately.
Keep it practical — 2-4 subtasks for most things, max 5 for very complex tasks."""


# ============================================================
# Multi-Agent Engine
# ============================================================

class MultiAgentEngine:
    """
    Orchestrates multiple specialist agents working in parallel.
    """

    def __init__(self, claude_client, model: str = "claude-sonnet-4-20250514"):
        self.client = claude_client
        self.model = model
        self.on_status: Optional[Callable] = None

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    async def _call_agent(self, role: str, task: str, context: str = "") -> AgentResult:
        """Run a single specialist agent."""
        agent_config = AGENT_ROLES.get(role, AGENT_ROLES["researcher"])
        start = time.time()

        prompt = task
        if context:
            prompt = f"Previous agents' results for context:\n\n{context}\n\n---\n\nYour task: {task}"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=agent_config["system"],
                messages=[{"role": "user", "content": prompt}],
            )

            output = response.content[0].text
            tokens = response.usage.input_tokens + response.usage.output_tokens
            elapsed = (time.time() - start) * 1000

            return AgentResult(
                agent_name=agent_config["name"],
                role=role,
                output=output,
                tokens_used=tokens,
                time_ms=elapsed,
                success=True,
            )

        except Exception as e:
            return AgentResult(
                agent_name=agent_config["name"],
                role=role,
                output=f"Agent error: {str(e)}",
                time_ms=(time.time() - start) * 1000,
                success=False,
            )

    async def decompose_task(self, task: str) -> dict:
        """Use Claude to break a complex task into agent subtasks."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": DECOMPOSE_PROMPT.format(task=task),
                }],
            )

            raw = response.content[0].text.strip()
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

            return json.loads(raw)

        except json.JSONDecodeError:
            return {
                "plan": "Direct execution",
                "subtasks": [{"agent": "researcher", "task": task, "depends_on": []}],
            }
        except Exception as e:
            return {
                "plan": f"Error: {e}",
                "subtasks": [{"agent": "researcher", "task": task, "depends_on": []}],
            }

    async def execute(self, task: str) -> dict:
        """
        Execute a complex task using multiple agents.

        1. Decompose the task into subtasks
        2. Run independent subtasks in parallel
        3. Run dependent subtasks sequentially with context
        4. Collect and return all results
        """
        self._status("🧠 Coordinator: Breaking down the task...")

        # Step 1: Decompose
        plan = await self.decompose_task(task)
        subtasks = plan.get("subtasks", [])

        self._status(f"📋 Plan: {plan.get('plan', 'Executing...')}")
        self._status(f"👥 Spawning {len(subtasks)} agents...")

        # Step 2: Execute with dependency management
        results: list[Optional[AgentResult]] = [None] * len(subtasks)
        total_tokens = 0

        # Group by dependency level
        levels = self._build_execution_levels(subtasks)

        for level_idx, level_tasks in enumerate(levels):
            # Run all tasks at this level in parallel
            self._status(f"⚡ Running level {level_idx + 1}/{len(levels)}: {len(level_tasks)} agents in parallel")

            async_tasks = []
            for task_idx in level_tasks:
                st = subtasks[task_idx]
                role = st["agent"]
                agent_task = st["task"]
                agent_config = AGENT_ROLES.get(role, AGENT_ROLES["researcher"])

                # Build context from completed dependencies
                context_parts = []
                for dep_idx in st.get("depends_on", []):
                    if dep_idx < len(results) and results[dep_idx]:
                        dep_result = results[dep_idx]
                        context_parts.append(
                            f"[{dep_result.agent_name}'s output]:\n{dep_result.output[:1500]}"
                        )
                context = "\n\n".join(context_parts)

                self._status(f"  {agent_config['emoji']} {agent_config['name']}: {agent_task[:60]}...")
                async_tasks.append((task_idx, role, agent_task, context))

            # Execute in parallel
            parallel_results = await asyncio.gather(*[
                self._call_agent(role, agent_task, context)
                for _, role, agent_task, context in async_tasks
            ])

            # Store results
            for i, (task_idx, _, _, _) in enumerate(async_tasks):
                results[task_idx] = parallel_results[i]
                r = parallel_results[i]
                total_tokens += r.tokens_used
                status = "✅" if r.success else "❌"
                self._status(f"  {status} {r.agent_name} done ({r.time_ms:.0f}ms, {r.tokens_used} tokens)")

        self._status(f"✨ All agents complete! Total: {total_tokens} tokens")

        return {
            "plan": plan.get("plan", ""),
            "results": [
                {
                    "agent": r.agent_name,
                    "role": r.role,
                    "output": r.output,
                    "tokens": r.tokens_used,
                    "time_ms": r.time_ms,
                    "success": r.success,
                }
                for r in results if r
            ],
            "total_tokens": total_tokens,
        }

    def _build_execution_levels(self, subtasks: list) -> list[list[int]]:
        """Group subtasks into parallel execution levels based on dependencies."""
        n = len(subtasks)
        completed = set()
        levels = []

        while len(completed) < n:
            level = []
            for i in range(n):
                if i in completed:
                    continue
                deps = set(subtasks[i].get("depends_on", []))
                if deps.issubset(completed):
                    level.append(i)

            if not level:
                # Deadlock — just run remaining sequentially
                level = [i for i in range(n) if i not in completed]

            levels.append(level)
            completed.update(level)

        return levels


# ============================================================
# Tool Handlers
# ============================================================

_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
            _engine = MultiAgentEngine(client)
    return _engine


async def multi_agent_task(task: str) -> str:
    """Execute a complex task using multiple specialist agents working in parallel."""
    engine = _get_engine()
    if not engine:
        return "Multi-agent needs ANTHROPIC_API_KEY in .env"

    status_lines = []
    engine.on_status = lambda msg: status_lines.append(msg)

    result = await engine.execute(task)

    # Format output
    output = [f"📋 **Plan**: {result['plan']}\n"]
    output.append(f"👥 **Agents used**: {len(result['results'])} | Total tokens: {result['total_tokens']}\n")

    for r in result["results"]:
        emoji = AGENT_ROLES.get(r["role"], {}).get("emoji", "🤖")
        output.append(f"---\n{emoji} **{r['agent']}** ({r['time_ms']:.0f}ms):\n")
        output.append(r["output"][:2000])
        output.append("")

    # Add status log
    output.append("\n---\n📊 **Execution log**:")
    for line in status_lines:
        output.append(f"  {line}")

    return "\n".join(output)


async def spawn_agent(role: str, task: str) -> str:
    """Spawn a single specialist agent for a focused task."""
    engine = _get_engine()
    if not engine:
        return "Needs ANTHROPIC_API_KEY"

    if role not in AGENT_ROLES:
        return f"Unknown role: {role}. Available: {', '.join(AGENT_ROLES.keys())}"

    config = AGENT_ROLES[role]
    result = await engine._call_agent(role, task)

    return (
        f"{config['emoji']} **{config['name']}** ({result.time_ms:.0f}ms, {result.tokens_used} tokens):\n\n"
        f"{result.output}"
    )


# ============================================================
# Tool Definitions
# ============================================================

def get_multi_agent_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="multi_agent_task",
            description="Execute a complex task using multiple specialist AI agents working in parallel. Automatically decomposes the task, spawns researchers, coders, writers, analysts as needed, and coordinates their work. Use for complex requests like 'build a landing page', 'research and compare products', 'plan a project', 'write a business plan'.",
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The complex task to execute with multiple agents"},
                },
                "required": ["task"],
            },
            handler=multi_agent_task,
            category="multi-agent",
            risk_level="low",
        ),
        ToolDefinition(
            name="spawn_agent",
            description="Spawn a single specialist agent for a focused task. Roles: researcher (search/gather info), coder (write code), writer (create content), analyst (analyze/compare), planner (create plans), critic (review/improve). Use when you need one specific specialist.",
            input_schema={
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "Agent role: researcher, coder, writer, analyst, planner, critic"},
                    "task": {"type": "string", "description": "Specific task for this agent"},
                },
                "required": ["role", "task"],
            },
            handler=spawn_agent,
            category="multi-agent",
            risk_level="low",
        ),
    ]
