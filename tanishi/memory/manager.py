"""
Tanishi Memory Manager — The thing that makes her HER.

Three memory layers:
1. Core Memory: Always in context (name, preferences, active goals)
2. Recall Memory: Searchable conversation history
3. Archival Memory: Long-term facts, indexed and retrievable

Inspired by Letta/MemGPT's self-managing memory architecture.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str
    content: str
    category: str  # "fact", "preference", "event", "goal", "habit", "relationship"
    importance: float = 0.5  # 0.0 to 1.0
    tags: list[str] = field(default_factory=list)
    source: str = ""  # "conversation", "observation", "user_stated"
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0


class MemoryManager:
    """
    Tanishi's memory system.

    Core memory stays in every prompt.
    Everything else is searchable.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize memory database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Core memory - always loaded
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS core_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            )
        """)

        # General memories
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'fact',
                importance REAL DEFAULT 0.5,
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT 'conversation',
                created_at TEXT,
                last_accessed TEXT,
                access_count INTEGER DEFAULT 0
            )
        """)

        # Conversation log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model_used TEXT DEFAULT '',
                timestamp TEXT NOT NULL
            )
        """)

        conn.commit()
        conn.close()

    # ============================================================
    # Core Memory — Always in context
    # ============================================================

    def set_core(self, key: str, value: str):
        """Set a core memory value (always in context)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO core_memory (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_core(self, key: str) -> Optional[str]:
        """Get a core memory value."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM core_memory WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def get_all_core(self) -> dict[str, str]:
        """Get all core memories as a dict."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM core_memory")
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result

    def build_core_context(self) -> str:
        """Build the core memory context string for the system prompt."""
        core = self.get_all_core()
        if not core:
            return "No core memories yet. Getting to know the boss."

        lines = ["## WHAT I KNOW ABOUT MY HUMAN"]
        for key, value in core.items():
            lines.append(f"- **{key}**: {value}")
        return "\n".join(lines)

    # ============================================================
    # General Memory — Searchable facts
    # ============================================================

    def remember(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a new memory."""
        entry.created_at = entry.created_at or datetime.now().isoformat()
        entry.last_accessed = entry.created_at

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO memories
            (id, content, category, importance, tags, source, created_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.id, entry.content, entry.category,
            entry.importance, json.dumps(entry.tags),
            entry.source, entry.created_at, entry.last_accessed,
            entry.access_count,
        ))
        conn.commit()
        conn.close()
        return entry

    def recall(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """Search memories by keyword (basic search — will be upgraded to vector search)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Basic keyword search (Phase 2 will add semantic/vector search via ChromaDB)
        cursor.execute("""
            SELECT * FROM memories
            WHERE content LIKE ? OR tags LIKE ?
            ORDER BY importance DESC, access_count DESC
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))

        results = []
        for row in cursor.fetchall():
            entry = MemoryEntry(
                id=row[0], content=row[1], category=row[2],
                importance=row[3], tags=json.loads(row[4]),
                source=row[5], created_at=row[6],
                last_accessed=row[7], access_count=row[8],
            )
            results.append(entry)

            # Update access stats
            cursor.execute("""
                UPDATE memories SET last_accessed = ?, access_count = access_count + 1
                WHERE id = ?
            """, (datetime.now().isoformat(), entry.id))

        conn.commit()
        conn.close()
        return results

    def get_recent_memories(self, limit: int = 10) -> list[MemoryEntry]:
        """Get most recent memories."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM memories ORDER BY created_at DESC LIMIT ?
        """, (limit,))

        results = []
        for row in cursor.fetchall():
            results.append(MemoryEntry(
                id=row[0], content=row[1], category=row[2],
                importance=row[3], tags=json.loads(row[4]),
                source=row[5], created_at=row[6],
                last_accessed=row[7], access_count=row[8],
            ))
        conn.close()
        return results

    # ============================================================
    # Conversation Log
    # ============================================================

    def log_message(self, session_id: str, role: str, content: str, model_used: str = ""):
        """Log a conversation message."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO conversations (session_id, role, content, model_used, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, role, content, model_used, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_session_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content, timestamp FROM conversations
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))
        results = [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in cursor.fetchall()]
        conn.close()
        return results

    # ============================================================
    # Memory Stats
    # ============================================================

    def get_stats(self) -> dict:
        """Get memory statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM core_memory")
        core_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM memories")
        memory_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM conversations")
        conversation_count = cursor.fetchone()[0]

        cursor.execute("SELECT category, COUNT(*) FROM memories GROUP BY category")
        by_category = {row[0]: row[1] for row in cursor.fetchall()}

        conn.close()

        return {
            "core_memories": core_count,
            "total_memories": memory_count,
            "conversation_messages": conversation_count,
            "by_category": by_category,
        }
