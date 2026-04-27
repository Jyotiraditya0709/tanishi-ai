"""
Dream Memory (nanobot-style two-stage compaction).

Stage 1: nightly extraction from recent conversations.
Stage 2: weekly consolidation into compact core knowledge.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from tanishi.core import get_config


class DreamCycle:
    def __init__(self, memory_manager=None, config=None):
        self.config = config or get_config()
        self.memory_manager = memory_manager
        self.memory_dir = Path(self.config.tanishi_home) / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.dream_log_path = self.memory_dir / "dream_log.jsonl"
        self.core_knowledge_path = self.memory_dir / "core_knowledge.json"
        self.db_path = (
            getattr(memory_manager, "db_path", None)
            or getattr(self.config, "db_path", None)
        )

    def _load_recent_conversations(self, hours_back: int) -> list[dict]:
        if not self.db_path:
            return []
        cutoff = (datetime.now() - timedelta(hours=hours_back)).isoformat()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role, content, timestamp
            FROM conversations
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (cutoff,),
        )
        rows = [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in cur.fetchall()]
        conn.close()
        return rows

    @staticmethod
    def _chunk_turns(messages: list[dict], turns_per_chunk: int = 10) -> list[list[dict]]:
        if not messages:
            return []
        chunks = []
        for i in range(0, len(messages), turns_per_chunk):
            chunks.append(messages[i : i + turns_per_chunk])
        return chunks

    @staticmethod
    def _extract_json_payload(raw: str):
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
            text = text.replace("json", "", 1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _ollama_json(self, prompt: str) -> Optional[str]:
        try:
            import requests

            base = os.getenv("OLLAMA_BASE_URL", self.config.ollama_base_url).rstrip("/")
            model = os.getenv("OLLAMA_MODEL", self.config.ollama_model)
            r = requests.post(
                f"{base}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=90,
            )
            if r.status_code != 200:
                return None
            return r.json().get("message", {}).get("content", "").strip() or None
        except Exception:
            return None

    def run_extraction(self, hours_back: int = 24) -> list[dict]:
        """Stage 1 — extract memories from recent conversation chunks."""
        rows = self._load_recent_conversations(hours_back=hours_back)
        if not rows:
            print("[dream] no recent conversations; skipping extraction")
            return []

        chunks = self._chunk_turns(rows, turns_per_chunk=10)
        extracted_all: list[dict] = []

        for chunk in chunks:
            conv_text = "\n".join(
                f"{m.get('role', '?')}: {str(m.get('content', ''))[:1500]}" for m in chunk
            )
            prompt = (
                "You are analyzing conversations between an AI assistant and her user. "
                "Extract important information as JSON array.\n\n"
                f"Conversation:\n{conv_text}\n\n"
                "For each important item, output:\n"
                "{\n"
                '  "type": "fact|preference|pattern|event|emotion|decision",\n'
                '  "content": "what was learned",\n'
                '  "importance": "high|medium|low",\n'
                '  "category": "personal|work|health|learning|schedule|system",\n'
                '  "expires": null or "ISO date if time-bound"\n'
                "}\n\n"
                "Only extract genuinely useful information. Skip small talk.\n"
                "Respond with a JSON array only."
            )
            raw = self._ollama_json(prompt)
            payload = self._extract_json_payload(raw or "")
            if not isinstance(payload, list):
                print("[dream] extraction chunk skipped (ollama/json issue)")
                continue

            for item in payload:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", "")).strip()
                if not content:
                    continue
                rec = {
                    "type": str(item.get("type", "fact")),
                    "content": content,
                    "importance": str(item.get("importance", "medium")),
                    "category": str(item.get("category", "personal")),
                    "expires": item.get("expires"),
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "source_hours": hours_back,
                }
                extracted_all.append(rec)

                with open(self.dream_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

                # Optional DB persistence into existing memories table.
                if self.memory_manager is not None:
                    try:
                        from tanishi.memory.manager import MemoryEntry

                        importance_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
                        entry = MemoryEntry(
                            id=f"dream_{int(datetime.now().timestamp()*1000)}",
                            content=rec["content"],
                            category=rec["type"] if rec["type"] else "fact",
                            importance=importance_map.get(rec["importance"].lower(), 0.5),
                            tags=[rec["category"], "dream"],
                            source="dream_extraction",
                        )
                        self.memory_manager.remember(entry)
                    except Exception:
                        pass

        return extracted_all

    def run_consolidation(self) -> dict:
        """Stage 2 — consolidate dream log into compact core knowledge."""
        if not self.dream_log_path.exists():
            return {}
        entries = []
        for line in self.dream_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not entries:
            return {}

        # Consolidate recent week by default.
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        week_entries = []
        for e in entries:
            ts = str(e.get("extracted_at", ""))
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= week_ago:
                    week_entries.append(e)
            except Exception:
                week_entries.append(e)
        source_entries = week_entries or entries

        prompt = (
            "You are consolidating an AI assistant's memory.\n"
            f"Given these {len(source_entries)} memory entries from the past week, produce "
            "a compact summary.\n\n"
            f"Entries:\n{json.dumps(source_entries, ensure_ascii=False)[:20000]}\n\n"
            "Output a JSON object:\n"
            "{\n"
            '  "core_facts": ["list of established facts about the user"],\n'
            '  "preferences": ["list of known preferences"],\n'
            '  "active_patterns": ["behavioral patterns still relevant"],\n'
            '  "upcoming_events": ["time-bound items not yet expired"],\n'
            '  "deprecated": ["items that are outdated or contradicted"]\n'
            "}"
        )
        raw = self._ollama_json(prompt)
        data = self._extract_json_payload(raw or "")
        if not isinstance(data, dict):
            # deterministic fallback if ollama unavailable
            data = {
                "core_facts": [e["content"] for e in source_entries if e.get("type") == "fact"][:20],
                "preferences": [e["content"] for e in source_entries if e.get("type") == "preference"][:20],
                "active_patterns": [e["content"] for e in source_entries if e.get("type") == "pattern"][:20],
                "upcoming_events": [e["content"] for e in source_entries if e.get("type") == "event"][:20],
                "deprecated": [],
            }

        self.core_knowledge_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data

    def get_dream_context(self, max_tokens: int = 500) -> str:
        """Load compact dream memory context quickly (no LLM calls)."""
        bullets: list[str] = []

        if self.core_knowledge_path.exists():
            try:
                core = json.loads(self.core_knowledge_path.read_text(encoding="utf-8"))
                if isinstance(core, dict):
                    for key in (
                        "core_facts",
                        "preferences",
                        "active_patterns",
                        "upcoming_events",
                    ):
                        for item in core.get(key, [])[:8]:
                            if isinstance(item, str) and item.strip():
                                bullets.append(item.strip())
            except Exception:
                pass

        if self.dream_log_path.exists():
            lines = self.dream_log_path.read_text(encoding="utf-8").splitlines()
            for line in lines[-10:]:
                try:
                    rec = json.loads(line)
                    c = str(rec.get("content", "")).strip()
                    if c:
                        bullets.append(c)
                except Exception:
                    continue

        # de-duplicate while preserving order
        deduped = list(dict.fromkeys(bullets))
        if not deduped:
            return ""

        lines = ["THINGS I REMEMBER ABOUT YOU:"]
        for b in deduped:
            lines.append(f"- {b}")
        out = "\n".join(lines)

        # rough token budget: 1 token ~= 4 chars
        max_chars = max_tokens * 4
        return out[:max_chars]
