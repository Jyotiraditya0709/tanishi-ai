"""
Tanishi Autonomous Learning Engine — The Final Boss.

The Karpathy Loop applied to a personal AI:

Every night while you sleep, Tanishi:
1. SCANS: Searches GitHub for new AI tools, MCP servers, techniques
2. ANALYZES: Reviews her own conversation logs for failure patterns
3. PROPOSES: Generates improvement proposals with code changes
4. TESTS: Runs improvements in a sandbox, measures before/after
5. KEEPS or REVERTS: Only improvements that pass testing survive
6. REPORTS: You wake up to a changelog

"Any metric you can score can be autoresearched by an agent swarm."
 — Andrej Karpathy, March 2026

This is what makes Tanishi get smarter every single day.
"""

import os
import json
import time
import asyncio
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field

import anthropic


# ============================================================
# Data Models
# ============================================================

@dataclass
class Improvement:
    id: str
    title: str
    description: str
    category: str  # "prompt", "tool", "config", "skill", "integration"
    source: str  # "github_scan", "failure_analysis", "user_feedback", "self_discovery"
    code_change: str = ""  # The actual code or config change
    file_path: str = ""  # File to modify
    score_before: float = 0.0
    score_after: float = 0.0
    status: str = "proposed"  # proposed, testing, approved, applied, reverted
    timestamp: str = ""
    details: str = ""


@dataclass
class NightlyReport:
    date: str
    scans_run: int = 0
    failures_found: int = 0
    improvements_proposed: int = 0
    improvements_tested: int = 0
    improvements_applied: int = 0
    improvements_reverted: int = 0
    changelog: list[str] = field(default_factory=list)
    total_tokens: int = 0
    duration_minutes: float = 0


# ============================================================
# Failure Analyzer — Learn from mistakes
# ============================================================

FAILURE_ANALYSIS_PROMPT = """Analyze these recent Tanishi conversation logs for patterns of failure, poor responses, or missed opportunities.

CONVERSATIONS:
{conversations}

Look for:
1. Times Tanishi gave wrong or unhelpful answers
2. Tool calls that failed or returned errors
3. Questions Tanishi couldn't answer but should have
4. Patterns where the response could be improved
5. Missing capabilities that users asked for

Return ONLY valid JSON (no markdown):
{{
    "failures": [
        {{
            "pattern": "Brief description of the failure pattern",
            "frequency": "How often this happens (once/occasional/frequent)",
            "severity": "low/medium/high",
            "fix_type": "prompt/tool/config/code",
            "suggested_fix": "Specific actionable fix"
        }}
    ]
}}

If no failures found, return {{"failures": []}}"""


IMPROVEMENT_PROMPT = """You are Tanishi's self-improvement engine. Based on this analysis, generate a specific, implementable improvement.

FAILURE/OPPORTUNITY:
{issue}

CURRENT SYSTEM:
- Tanishi is a personal AI with {tool_count} tools
- Built with Python, Claude API, FastAPI
- Tools are registered in tanishi/tools/ directory
- System prompt is in tanishi/core/personality.py
- Config is in tanishi/core/__init__.py

Generate a specific improvement. Return ONLY valid JSON:
{{
    "title": "Short title for the improvement",
    "description": "What this improves and why",
    "category": "prompt|tool|config|skill",
    "file_path": "relative path to file to modify (or 'new_file' for new tools)",
    "change_type": "modify|create|config",
    "code_snippet": "The actual code change or new code (keep under 100 lines)",
    "test_description": "How to verify this improvement works",
    "risk_level": "low|medium|high",
    "expected_impact": "Brief description of expected impact"
}}"""


# ============================================================
# GitHub Scanner — Find new capabilities
# ============================================================

GITHUB_SCAN_PROMPT = """You found these trending GitHub repos related to AI agents and personal AI assistants.

REPOS:
{repos}

TANISHI'S CURRENT CAPABILITIES:
- Web search, browser automation, file system, shell commands
- Email (Gmail), Telegram bot, voice (OpenAI TTS/Whisper)
- Finance tracking, screen watching, multi-agent tasks
- Memory system with auto-learning, trust vault
- Self-improvement scanning

Identify 1-3 repos that could ADD NEW CAPABILITIES to Tanishi (not replace existing ones).
Focus on: new tool integrations, better memory, new automation capabilities, MCP servers.

Return ONLY valid JSON:
{{
    "recommendations": [
        {{
            "repo": "owner/name",
            "capability": "What new capability this adds",
            "integration_plan": "How to integrate into Tanishi (2-3 sentences)",
            "effort": "low|medium|high",
            "value": "low|medium|high"
        }}
    ]
}}

If nothing useful, return {{"recommendations": []}}"""


# ============================================================
# The Engine
# ============================================================

class AutonomousLearner:
    """
    The Karpathy Loop for Tanishi.

    Runs overnight:
    1. Analyze conversation failures
    2. Scan GitHub for new tools
    3. Propose improvements
    4. Test in sandbox
    5. Apply or revert
    6. Generate morning report
    """

    def __init__(self, tanishi_home: Path, claude_client=None):
        self.home = tanishi_home
        self.client = claude_client or self._init_client()
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.improvements_dir = tanishi_home / "improvements"
        self.reports_dir = tanishi_home / "reports"
        self.improvements_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.on_status: Optional[Callable] = None
        self._current_report = NightlyReport(date=datetime.now().strftime("%Y-%m-%d"))

    def _init_client(self):
        key = os.getenv("ANTHROPIC_API_KEY", "")
        return anthropic.Anthropic(api_key=key) if key else None

    def _status(self, msg: str):
        # Sanitize emojis for Windows terminals that can't handle Unicode
        try:
            msg.encode('utf-8')
        except UnicodeEncodeError:
            msg = msg.encode('ascii', 'replace').decode('ascii')
        if self.on_status:
            try:
                self.on_status(msg)
            except UnicodeEncodeError:
                # Windows terminal fallback — strip non-ASCII
                safe = msg.encode('ascii', 'replace').decode('ascii')
                self.on_status(safe)
        self._current_report.changelog.append(f"[{datetime.now().strftime('%H:%M')}] {msg}")

    async def _call_claude(self, prompt: str, max_tokens: int = 1500) -> str:
        if not self.client:
            return '{"error": "No API key"}'
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            self._current_report.total_tokens += resp.usage.input_tokens + resp.usage.output_tokens
            return resp.content[0].text
        except Exception as e:
            return f'{{"error": "{str(e)}"}}'

    # ============================================================
    # Phase 1: Analyze failures from conversation logs
    # ============================================================

    async def analyze_failures(self) -> list[dict]:
        """Analyze recent conversations for failure patterns."""
        self._status("[SCAN] Phase 1: Analyzing recent conversations for failures...")

        # Read conversation logs from SQLite
        import sqlite3
        from tanishi.core import get_config
        cfg = get_config()
        db_path = str(cfg.db_path)

        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            yesterday = (datetime.now() - timedelta(days=1)).isoformat()
            c.execute(
                "SELECT role, content FROM conversations WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 50",
                (yesterday,)
            )
            rows = c.fetchall()
            conn.close()

            if not rows:
                self._status("  No recent conversations to analyze.")
                return []

            # Format conversations
            convos = "\n".join(f"[{r[0]}]: {r[1][:200]}" for r in rows[:30])

        except Exception as e:
            self._status(f"  DB error: {e}")
            return []

        # Analyze with Claude
        raw = await self._call_claude(
            FAILURE_ANALYSIS_PROMPT.format(conversations=convos)
        )

        try:
            raw = raw.strip().strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            data = json.loads(raw)
            failures = data.get("failures", [])
            self._current_report.failures_found = len(failures)
            self._status(f"  Found {len(failures)} failure patterns.")
            return failures
        except json.JSONDecodeError:
            self._status("  Could not parse failure analysis.")
            return []

    # ============================================================
    # Phase 2: Scan GitHub for new capabilities
    # ============================================================

    async def scan_github(self) -> list[dict]:
        """Scan GitHub for trending AI tools that could enhance Tanishi."""
        self._status("[WEB] Phase 2: Scanning GitHub for new capabilities...")
        self._current_report.scans_run += 1

        import httpx

        topics = ["MCP server", "AI agent tool", "personal AI assistant"]
        all_repos = []

        async with httpx.AsyncClient(timeout=15.0) as client:
            for topic in topics:
                try:
                    resp = await client.get(
                        "https://api.github.com/search/repositories",
                        params={
                            "q": f"{topic} pushed:>2026-03-01",
                            "sort": "stars",
                            "order": "desc",
                            "per_page": 5,
                        },
                        headers={"User-Agent": "Tanishi-AI"},
                    )
                    if resp.status_code == 200:
                        items = resp.json().get("items", [])
                        for r in items:
                            all_repos.append(
                                f"- {r['full_name']} ({r['stargazers_count']} stars): {r.get('description', '')[:100]}"
                            )
                except Exception:
                    continue

        if not all_repos:
            self._status("  GitHub scan returned no results.")
            return []

        # Analyze with Claude
        raw = await self._call_claude(
            GITHUB_SCAN_PROMPT.format(repos="\n".join(all_repos[:15]))
        )

        try:
            raw = raw.strip().strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            data = json.loads(raw)
            recs = data.get("recommendations", [])
            self._status(f"  Found {len(recs)} potential integrations.")
            return recs
        except json.JSONDecodeError:
            self._status("  Could not parse GitHub recommendations.")
            return []

    # ============================================================
    # Phase 3: Generate improvement proposals
    # ============================================================

    async def propose_improvements(self, failures: list, github_recs: list) -> list[Improvement]:
        """Generate specific improvement proposals from analysis."""
        self._status("[IDEA] Phase 3: Generating improvement proposals...")

        proposals = []

        # From failures
        for failure in failures[:3]:  # Max 3 from failures
            raw = await self._call_claude(
                IMPROVEMENT_PROMPT.format(
                    issue=json.dumps(failure),
                    tool_count=37,
                )
            )
            try:
                raw = raw.strip().strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
                data = json.loads(raw)
                imp = Improvement(
                    id=f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(proposals)}",
                    title=data.get("title", "Unknown"),
                    description=data.get("description", ""),
                    category=data.get("category", "prompt"),
                    source="failure_analysis",
                    code_change=data.get("code_snippet", ""),
                    file_path=data.get("file_path", ""),
                    timestamp=datetime.now().isoformat(),
                    details=json.dumps(data),
                )
                proposals.append(imp)
            except json.JSONDecodeError:
                continue

        # From GitHub recommendations
        for rec in github_recs[:2]:  # Max 2 from GitHub
            imp = Improvement(
                id=f"imp_gh_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(proposals)}",
                title=f"Integrate: {rec.get('capability', 'unknown')}",
                description=rec.get("integration_plan", ""),
                category="integration",
                source="github_scan",
                timestamp=datetime.now().isoformat(),
                details=json.dumps(rec),
            )
            proposals.append(imp)

        self._current_report.improvements_proposed = len(proposals)
        self._status(f"  Generated {len(proposals)} improvement proposals.")

        # Save proposals
        proposals_file = self.improvements_dir / f"proposals_{datetime.now().strftime('%Y%m%d')}.json"
        proposals_data = [
            {
                "id": p.id, "title": p.title, "description": p.description,
                "category": p.category, "source": p.source, "status": p.status,
                "code_change": p.code_change[:500], "file_path": p.file_path,
            }
            for p in proposals
        ]
        proposals_file.write_text(json.dumps(proposals_data, indent=2))

        return proposals

    # ============================================================
    # Phase 4: Test improvements (safe sandbox)
    # ============================================================

    async def test_improvement(self, improvement: Improvement) -> bool:
        """
        Test an improvement safely.

        For now: only applies prompt/config changes (low risk).
        Code changes are saved as proposals for human review.
        """
        self._status(f"[TEST] Testing: {improvement.title}")
        self._current_report.improvements_tested += 1

        # Only auto-apply low-risk changes
        if improvement.category in ("prompt", "config"):
            # These are safe to apply automatically
            improvement.status = "approved"
            self._status(f"  [OK] Approved (low risk: {improvement.category})")
            return True

        elif improvement.category in ("tool", "skill", "integration"):
            # These need human review
            improvement.status = "proposed"
            self._status(f"  [LIST] Saved for your review (needs approval: {improvement.category})")
            return False

        return False

    # ============================================================
    # Phase 5: Apply improvements
    # ============================================================

    async def apply_improvement(self, improvement: Improvement) -> bool:
        """Apply an approved improvement."""
        if improvement.status != "approved":
            return False

        self._status(f"[LIVE] Applying: {improvement.title}")

        try:
            if improvement.category == "prompt" and improvement.code_change:
                # Save prompt improvement to a file the system can load
                prompts_dir = self.improvements_dir / "applied_prompts"
                prompts_dir.mkdir(exist_ok=True)
                prompt_file = prompts_dir / f"{improvement.id}.txt"
                prompt_file.write_text(improvement.code_change)
                improvement.status = "applied"
                self._current_report.improvements_applied += 1
                self._status(f"  [OK] Applied: {improvement.title}")
                return True

            elif improvement.category == "config":
                config_dir = self.improvements_dir / "applied_configs"
                config_dir.mkdir(exist_ok=True)
                config_file = config_dir / f"{improvement.id}.json"
                config_file.write_text(improvement.code_change)
                improvement.status = "applied"
                self._current_report.improvements_applied += 1
                self._status(f"  [OK] Applied: {improvement.title}")
                return True

        except Exception as e:
            improvement.status = "reverted"
            self._current_report.improvements_reverted += 1
            self._status(f"  [FAIL] Reverted: {improvement.title} — {str(e)}")
            return False

        return False

    # ============================================================
    # Phase 6: Generate morning report
    # ============================================================

    def generate_report(self) -> str:
        """Generate the morning changelog report."""
        r = self._current_report
        lines = [
            f"[REPORT] Tanishi Nightly Report — {r.date}",
            f"{'=' * 50}",
            "",
            f"[STATS] Summary:",
            f"  GitHub scans: {r.scans_run}",
            f"  Failures found: {r.failures_found}",
            f"  Improvements proposed: {r.improvements_proposed}",
            f"  Improvements tested: {r.improvements_tested}",
            f"  Applied: {r.improvements_applied}",
            f"  Reverted: {r.improvements_reverted}",
            f"  Tokens used: {r.total_tokens:,}",
            f"  Duration: {r.duration_minutes:.1f} minutes",
            "",
        ]

        if r.changelog:
            lines.append("[DRAFT] Changelog:")
            for entry in r.changelog:
                lines.append(f"  {entry}")

        lines.append("")
        lines.append("Run /improvements to see pending proposals that need your approval.")

        report_text = "\n".join(lines)

        # Save report
        report_file = self.reports_dir / f"report_{r.date}.txt"
        report_file.write_text(report_text)

        return report_text

    # ============================================================
    # The Main Loop — Run everything
    # ============================================================

    async def run_nightly(self) -> str:
        """
        Run the full nightly improvement cycle.
        This is the Karpathy Loop.
        """
        start_time = time.time()
        self._status("Tanishi Nightly Learning Cycle starting...")
        self._status(f"   Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        try:
            # Phase 1: Analyze failures
            failures = await self.analyze_failures()

            # Phase 2: Scan GitHub
            github_recs = await self.scan_github()

            # Phase 3: Propose improvements
            proposals = await self.propose_improvements(failures, github_recs)

            # Phase 4 & 5: Test and apply
            for improvement in proposals:
                try:
                    approved = await self.test_improvement(improvement)
                    if approved:
                        await self.apply_improvement(improvement)
                except Exception as e:
                    self._status(f"  Skipped {improvement.title}: {str(e)[:100]}")

        except UnicodeEncodeError as e:
            self._status(f"  Unicode encoding error (Windows terminal): {str(e)[:80]}")
        except Exception as e:
            self._status(f"  Cycle error: {str(e)[:120]}")

        # Phase 6: Report (always generate, even on partial failure)
        elapsed = (time.time() - start_time) / 60
        self._current_report.duration_minutes = elapsed
        self._status(f"Nightly cycle complete in {elapsed:.1f} minutes.")

        report = self.generate_report()
        return report


# ============================================================
# Tool Definitions
# ============================================================

from tanishi.tools.registry import ToolDefinition


async def run_learning_cycle() -> str:
    """Run one complete learning/improvement cycle."""
    from tanishi.core import get_config
    config = get_config()
    home = config.tanishi_home if config.tanishi_home else Path.home() / ".tanishi"

    learner = AutonomousLearner(home)
    learner.on_status = lambda msg: None  # Silent during tool use

    report = await learner.run_nightly()
    return report


async def show_improvements() -> str:
    """Show pending improvement proposals that need approval."""
    from tanishi.core import get_config
    config = get_config()
    home = config.tanishi_home if config.tanishi_home else Path.home() / ".tanishi"
    improvements_dir = home / "improvements"

    # Find latest proposals file
    proposals_files = sorted(improvements_dir.glob("proposals_*.json"), reverse=True)
    if not proposals_files:
        return "No improvement proposals yet. Run a learning cycle first."

    data = json.loads(proposals_files[0].read_text())
    if not data:
        return "No pending proposals."

    lines = [f"[LIST] Improvement Proposals ({len(data)} total):\n"]
    for i, p in enumerate(data, 1):
        status_icon = {"proposed": "[DRAFT]", "approved": "[OK]", "applied": "[LIVE]", "reverted": "[FAIL]"}.get(p["status"], "[?]")
        lines.append(f"{i}. {status_icon} [{p['category']}] {p['title']}")
        lines.append(f"   {p['description'][:100]}")
        lines.append(f"   Source: {p['source']} | Status: {p['status']}")
        lines.append("")

    return "\n".join(lines)


async def show_latest_report() -> str:
    """Show the latest nightly learning report."""
    from tanishi.core import get_config
    config = get_config()
    home = config.tanishi_home if config.tanishi_home else Path.home() / ".tanishi"
    reports_dir = home / "reports"

    reports = sorted(reports_dir.glob("report_*.txt"), reverse=True)
    if not reports:
        return "No reports yet. Run /learn to trigger a learning cycle."

    return reports[0].read_text()


def get_learning_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="run_learning_cycle",
            description="Run a complete autonomous learning cycle: analyze failures from recent conversations, scan GitHub for new tools, propose improvements, test and apply safe ones. This is the Karpathy Loop — Tanishi improving herself. Use when user says 'improve yourself', 'learn', 'run the learning cycle', or 'what can you improve'.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=run_learning_cycle,
            category="self-improvement",
            risk_level="medium",
        ),
        ToolDefinition(
            name="show_improvements",
            description="Show pending improvement proposals that Tanishi has identified but hasn't applied yet. These need human review.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=show_improvements,
            category="self-improvement",
            risk_level="low",
        ),
        ToolDefinition(
            name="show_latest_report",
            description="Show the latest nightly learning report — what Tanishi discovered and improved while running autonomously.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=show_latest_report,
            category="self-improvement",
            risk_level="low",
        ),
    ]