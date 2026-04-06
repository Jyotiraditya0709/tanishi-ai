"""
Tanishi Auto-Memory — She learns without being told.

After every conversation, Tanishi analyzes the exchange and
automatically extracts facts, preferences, and events worth
remembering. No /remember needed.

Examples of what she auto-learns:
- "I have a meeting tomorrow at 3" → stores event
- "I prefer dark mode" → stores preference
- "My girlfriend's name is Priya" → stores relationship fact
- "I'm working on a React project" → stores project context
"""

import json
import re
from datetime import datetime
from typing import Optional

from tanishi.memory.manager import MemoryManager, MemoryEntry


# Prompt that Claude uses to extract facts from conversation
EXTRACTION_PROMPT = """Analyze this conversation exchange and extract any personal facts, preferences, events, relationships, or important information that should be remembered about the user.

USER said: {user_message}
ASSISTANT responded: {assistant_response}

Return ONLY a JSON array of facts to remember. Each fact should have:
- "content": the fact in third person ("User lives in Jalandhar")
- "category": one of "fact", "preference", "event", "relationship", "project", "habit", "goal"
- "importance": 0.0 to 1.0 (how important to remember)
- "tags": list of relevant keywords

If there are NO facts worth remembering, return an empty array: []

Rules:
- Only extract NEW information the user shared about themselves
- Don't extract generic knowledge or things the assistant said
- Preferences ("I like X", "I prefer Y") are always worth remembering
- Names, dates, locations are high importance
- Casual chat with no personal info = empty array

Return ONLY valid JSON, no markdown, no explanation."""


class AutoMemory:
    """
    Automatic fact extraction from conversations.

    After each exchange, analyzes the conversation and stores
    any new facts without the user needing to use /remember.
    """

    def __init__(self, memory: MemoryManager, claude_client=None):
        self.memory = memory
        self.claude_client = claude_client
        self.enabled = True
        self._extraction_count = 0

    async def extract_and_store(
        self,
        user_message: str,
        assistant_response: str,
        model: str = "claude-sonnet-4-20250514",
    ) -> list[MemoryEntry]:
        """
        Analyze a conversation exchange and auto-store any facts.
        Returns list of newly stored memories.
        """
        if not self.enabled or not self.claude_client:
            return []

        # Skip very short exchanges (greetings, etc.)
        if len(user_message) < 15:
            return []

        # Skip command-like messages
        if user_message.startswith("/"):
            return []

        try:
            prompt = EXTRACTION_PROMPT.format(
                user_message=user_message[:1000],
                assistant_response=assistant_response[:500],
            )

            response = self.claude_client.messages.create(
                model=model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()

            # Clean up potential markdown wrapping
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

            facts = json.loads(raw)

            if not isinstance(facts, list) or len(facts) == 0:
                return []

            stored = []
            for fact in facts:
                if not isinstance(fact, dict) or "content" not in fact:
                    continue

                # Check for duplicates
                existing = self.memory.recall(fact["content"][:50], limit=3)
                is_duplicate = any(
                    self._is_similar(fact["content"], m.content)
                    for m in existing
                )
                if is_duplicate:
                    continue

                entry_id = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self._extraction_count}"
                self._extraction_count += 1

                entry = MemoryEntry(
                    id=entry_id,
                    content=fact["content"],
                    category=fact.get("category", "fact"),
                    importance=min(fact.get("importance", 0.5), 1.0),
                    tags=fact.get("tags", []),
                    source="auto_extracted",
                )

                self.memory.remember(entry)
                stored.append(entry)

                # If it's a core fact (name, location, job), add to core memory too
                self._check_core_worthy(fact)

            return stored

        except json.JSONDecodeError:
            return []
        except Exception:
            return []

    def _is_similar(self, text1: str, text2: str) -> bool:
        """Check if two texts are similar enough to be duplicates."""
        t1 = set(text1.lower().split())
        t2 = set(text2.lower().split())
        if not t1 or not t2:
            return False
        overlap = len(t1 & t2) / max(len(t1), len(t2))
        return overlap > 0.6

    def _check_core_worthy(self, fact: dict):
        """Check if a fact should be promoted to core memory."""
        content = fact.get("content", "").lower()
        category = fact.get("category", "")

        # Patterns that indicate core-worthy facts
        core_patterns = {
            "user_location": [r"lives? in (\w[\w\s,]+)", r"from (\w[\w\s,]+)", r"based in (\w[\w\s,]+)"],
            "user_job": [r"(?:works?|working) (?:as|at|on) (.+)", r"is a (\w+ ?\w*(?:developer|engineer|designer|manager|student))"],
            "user_language": [r"speaks? (\w+)", r"(?:native|primary) language (?:is )?(\w+)"],
        }

        for key, patterns in core_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    value = match.group(1).strip().rstrip(".")
                    existing = self.memory.get_core(key)
                    if not existing:
                        self.memory.set_core(key, value)
                    break
