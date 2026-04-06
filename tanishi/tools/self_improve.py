"""
Tanishi Self-Improvement Engine — She evolves.

This is the Karpathy Loop applied to a personal AI:
1. Scan the internet for new AI tools, techniques, and improvements
2. Evaluate if they'd benefit Tanishi
3. Propose the upgrade to the user
4. If approved, integrate the improvement
5. Repeat

Also includes:
- GitHub trending scanner
- Skill auto-discovery
- Configuration optimization
"""

import json
import httpx
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tanishi.tools.registry import ToolDefinition


@dataclass
class ImprovementProposal:
    """A proposed improvement for Tanishi."""
    id: str
    title: str
    description: str
    source_url: str = ""
    category: str = ""  # "tool", "skill", "config", "integration", "model"
    impact: str = "medium"  # "low", "medium", "high"
    effort: str = "medium"  # "low", "medium", "high"
    status: str = "proposed"  # "proposed", "approved", "rejected", "implemented"
    proposed_at: str = ""
    details: str = ""


class SelfImproveEngine:
    """
    Tanishi's self-improvement system.

    Scans for improvements and proposes them.
    Never acts without permission.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.proposals_file = data_dir / "improvement_proposals.json"
        self.proposals: list[ImprovementProposal] = []
        self._load_proposals()

    def _load_proposals(self):
        """Load existing proposals."""
        if self.proposals_file.exists():
            try:
                data = json.loads(self.proposals_file.read_text())
                self.proposals = [ImprovementProposal(**p) for p in data]
            except Exception:
                self.proposals = []

    def _save_proposals(self):
        """Save proposals to disk."""
        self.proposals_file.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "id": p.id, "title": p.title, "description": p.description,
                "source_url": p.source_url, "category": p.category,
                "impact": p.impact, "effort": p.effort, "status": p.status,
                "proposed_at": p.proposed_at, "details": p.details,
            }
            for p in self.proposals
        ]
        self.proposals_file.write_text(json.dumps(data, indent=2))

    def add_proposal(self, proposal: ImprovementProposal) -> ImprovementProposal:
        """Add a new improvement proposal."""
        proposal.proposed_at = datetime.now().isoformat()
        self.proposals.append(proposal)
        self._save_proposals()
        return proposal

    def approve_proposal(self, proposal_id: str) -> Optional[ImprovementProposal]:
        """Mark a proposal as approved."""
        for p in self.proposals:
            if p.id == proposal_id:
                p.status = "approved"
                self._save_proposals()
                return p
        return None

    def reject_proposal(self, proposal_id: str) -> Optional[ImprovementProposal]:
        """Mark a proposal as rejected."""
        for p in self.proposals:
            if p.id == proposal_id:
                p.status = "rejected"
                self._save_proposals()
                return p
        return None

    def get_pending(self) -> list[ImprovementProposal]:
        """Get all pending proposals."""
        return [p for p in self.proposals if p.status == "proposed"]

    def get_all(self) -> list[ImprovementProposal]:
        """Get all proposals."""
        return self.proposals


# ============================================================
# GitHub Scanner — Find trending AI tools
# ============================================================

async def scan_github_trending(topic: str = "ai-agent", language: str = "", max_results: int = 5) -> str:
    """
    Scan GitHub for trending repositories related to a topic.
    Uses GitHub's search API (no auth needed for basic queries).
    """
    try:
        query_parts = [topic]
        if language:
            query_parts.append(f"language:{language}")

        query = " ".join(query_parts)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": f"{query} pushed:>2025-01-01",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": max_results,
                },
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Tanishi-AI-Assistant",
                },
            )

            if resp.status_code == 403:
                return "GitHub API rate limit hit. I'm too popular for my own good. Try again in a minute."

            data = resp.json()

        repos = data.get("items", [])
        if not repos:
            return f"No trending repos found for '{topic}'. The AI world is sleeping."

        results = [f"Trending GitHub repos for '{topic}':\n"]
        for i, repo in enumerate(repos, 1):
            results.append(f"{i}. **{repo['full_name']}** ({repo['stargazers_count']:,} stars)")
            results.append(f"   {repo.get('description', 'No description')[:120]}")
            results.append(f"   URL: {repo['html_url']}")
            results.append(f"   Language: {repo.get('language', 'N/A')} | Updated: {repo['updated_at'][:10]}")
            results.append("")

        return "\n".join(results)

    except Exception as e:
        return f"GitHub scan failed: {str(e)}"


async def scan_for_improvements(focus: str = "personal AI assistant tools") -> str:
    """
    Comprehensive scan for potential Tanishi improvements.
    Searches GitHub for new tools, frameworks, and techniques.
    """
    searches = [
        ("AI agent framework", "python"),
        ("personal AI assistant", "python"),
        ("MCP server", ""),
        ("self-improving AI", ""),
        (focus, ""),
    ]

    all_results = [f"Self-Improvement Scan Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    all_results.append(f"Focus: {focus}\n")

    for topic, lang in searches:
        result = await scan_github_trending(topic, lang, max_results=3)
        all_results.append(result)
        all_results.append("---\n")

    all_results.append(
        "\nI've found these projects. Want me to analyze any of them in detail "
        "and propose specific improvements I could integrate?"
    )

    return "\n".join(all_results)


# ============================================================
# Tool Definitions
# ============================================================

def get_self_improve_tools() -> list[ToolDefinition]:
    """Return self-improvement tool definitions."""
    return [
        ToolDefinition(
            name="scan_github_trending",
            description="Search GitHub for trending repositories on a topic. Use this to find new AI tools, frameworks, MCP servers, or techniques that could improve Tanishi's capabilities.",
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Search topic (e.g., 'AI agent framework', 'MCP server', 'voice AI').",
                        "default": "ai-agent",
                    },
                    "language": {
                        "type": "string",
                        "description": "Filter by programming language (e.g., 'python', 'rust', 'typescript').",
                        "default": "",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results.",
                        "default": 5,
                    },
                },
                "required": [],
            },
            handler=scan_github_trending,
            category="self-improvement",
            risk_level="low",
        ),
        ToolDefinition(
            name="scan_for_improvements",
            description="Run a comprehensive self-improvement scan. Searches multiple GitHub topics for new AI tools, frameworks, and techniques that could enhance Tanishi. Use when the user asks Tanishi to find ways to improve herself.",
            input_schema={
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "description": "Specific focus area for the scan.",
                        "default": "personal AI assistant tools",
                    },
                },
                "required": [],
            },
            handler=scan_for_improvements,
            category="self-improvement",
            risk_level="low",
        ),
    ]
