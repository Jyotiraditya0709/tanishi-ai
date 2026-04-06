"""
Tanishi Trust System — Who knows what.

This is the security brain. It decides what information can be shared
with whom, based on trust levels and secret passwords.

The Girlfriend Scenario:
- Owner has two girlfriends (GF_A and GF_B)
- Tanishi knows about both
- Neither girlfriend should ever learn about the other
- But if someone says the secret password, they get full access
"""

import json
import hashlib
import sqlite3
from enum import IntEnum
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


class TrustLevel(IntEnum):
    """Trust levels from most to least trusted."""
    OWNER = 100       # The boss. Full access to everything.
    INNER_CIRCLE = 80  # Explicitly trusted people.
    KNOWN = 50         # Regular contacts. Professional only.
    STRANGER = 20      # Unknown. Reveal nothing personal.
    HOSTILE = 0        # Flagged threats. Wall of deflection.


@dataclass
class Contact:
    """A person Tanishi knows about."""
    id: str
    name: str
    trust_level: TrustLevel = TrustLevel.STRANGER
    relationship: str = ""  # "girlfriend", "boss", "friend", "coworker"
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    secrets: list[str] = field(default_factory=list)  # Things to NEVER share about this person
    visible_to: list[str] = field(default_factory=list)  # Who can know about this contact
    hidden_from: list[str] = field(default_factory=list)  # Who must NEVER know about this contact
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Secret:
    """A piece of sensitive information."""
    id: str
    content: str
    category: str  # "relationship", "financial", "personal", "work"
    trust_required: TrustLevel = TrustLevel.OWNER
    visible_to: list[str] = field(default_factory=list)  # Specific contact IDs
    hidden_from: list[str] = field(default_factory=list)  # Specific contact IDs
    password_protected: bool = False
    password_hash: str = ""  # If password_protected, need this to unlock
    created_at: str = ""


class TrustManager:
    """
    Manages trust levels, contacts, secrets, and the password system.

    This is how Tanishi knows:
    - Who can know what
    - What to reveal vs hide
    - When to deflect vs answer
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.contacts: dict[str, Contact] = {}
        self.secrets: list[Secret] = []
        self.secret_passwords: dict[str, TrustLevel] = {}  # hash → trust level
        self._init_db()

    def _init_db(self):
        """Initialize the trust database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trust_level INTEGER DEFAULT 20,
                relationship TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                secrets TEXT DEFAULT '[]',
                visible_to TEXT DEFAULT '[]',
                hidden_from TEXT DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS secrets (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'personal',
                trust_required INTEGER DEFAULT 100,
                visible_to TEXT DEFAULT '[]',
                hidden_from TEXT DEFAULT '[]',
                password_protected INTEGER DEFAULT 0,
                password_hash TEXT DEFAULT '',
                created_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS passwords (
                password_hash TEXT PRIMARY KEY,
                trust_level INTEGER NOT NULL,
                label TEXT DEFAULT '',
                created_at TEXT
            )
        """)

        conn.commit()
        conn.close()
        self._load_from_db()

    def _load_from_db(self):
        """Load contacts and secrets from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Load contacts
        cursor.execute("SELECT * FROM contacts")
        for row in cursor.fetchall():
            contact = Contact(
                id=row[0], name=row[1],
                trust_level=TrustLevel(row[2]),
                relationship=row[3],
                tags=json.loads(row[4]),
                notes=row[5],
                secrets=json.loads(row[6]),
                visible_to=json.loads(row[7]),
                hidden_from=json.loads(row[8]),
                created_at=row[9] or "",
                updated_at=row[10] or "",
            )
            self.contacts[contact.id] = contact

        # Load secrets
        cursor.execute("SELECT * FROM secrets")
        for row in cursor.fetchall():
            secret = Secret(
                id=row[0], content=row[1],
                category=row[2],
                trust_required=TrustLevel(row[3]),
                visible_to=json.loads(row[4]),
                hidden_from=json.loads(row[5]),
                password_protected=bool(row[6]),
                password_hash=row[7],
                created_at=row[8] or "",
            )
            self.secrets.append(secret)

        # Load passwords
        cursor.execute("SELECT * FROM passwords")
        for row in cursor.fetchall():
            self.secret_passwords[row[0]] = TrustLevel(row[1])

        conn.close()

    def _hash_password(self, password: str) -> str:
        """Hash a password for secure storage."""
        return hashlib.sha256(password.encode()).hexdigest()

    # ============================================================
    # Contact Management
    # ============================================================

    def add_contact(self, contact: Contact) -> Contact:
        """Add or update a contact."""
        now = datetime.now().isoformat()
        contact.created_at = contact.created_at or now
        contact.updated_at = now

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO contacts
            (id, name, trust_level, relationship, tags, notes, secrets, visible_to, hidden_from, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            contact.id, contact.name, int(contact.trust_level),
            contact.relationship, json.dumps(contact.tags),
            contact.notes, json.dumps(contact.secrets),
            json.dumps(contact.visible_to), json.dumps(contact.hidden_from),
            contact.created_at, contact.updated_at,
        ))
        conn.commit()
        conn.close()

        self.contacts[contact.id] = contact
        return contact

    def get_contact(self, contact_id: str) -> Optional[Contact]:
        """Get a contact by ID."""
        return self.contacts.get(contact_id)

    def set_trust_level(self, contact_id: str, level: TrustLevel):
        """Update a contact's trust level."""
        if contact_id in self.contacts:
            self.contacts[contact_id].trust_level = level
            self.add_contact(self.contacts[contact_id])  # Save

    # ============================================================
    # Secret Management
    # ============================================================

    def add_secret(self, secret: Secret) -> Secret:
        """Store a secret."""
        secret.created_at = secret.created_at or datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO secrets
            (id, content, category, trust_required, visible_to, hidden_from, password_protected, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            secret.id, secret.content, secret.category,
            int(secret.trust_required), json.dumps(secret.visible_to),
            json.dumps(secret.hidden_from), int(secret.password_protected),
            secret.password_hash, secret.created_at,
        ))
        conn.commit()
        conn.close()

        self.secrets.append(secret)
        return secret

    def get_allowed_secrets(self, requester_trust: TrustLevel, requester_id: str = "") -> list[Secret]:
        """Get secrets that a requester is allowed to see."""
        allowed = []
        for secret in self.secrets:
            # Check trust level
            if requester_trust < secret.trust_required:
                continue
            # Check if explicitly hidden from this requester
            if requester_id and requester_id in secret.hidden_from:
                continue
            # Check if restricted to specific people
            if secret.visible_to and requester_id not in secret.visible_to:
                if requester_trust < TrustLevel.OWNER:
                    continue
            allowed.append(secret)
        return allowed

    # ============================================================
    # Password System — "Our cute little secret password"
    # ============================================================

    def register_password(self, password: str, trust_level: TrustLevel, label: str = ""):
        """Register a secret password that unlocks a trust level."""
        pw_hash = self._hash_password(password)
        self.secret_passwords[pw_hash] = trust_level

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO passwords (password_hash, trust_level, label, created_at)
            VALUES (?, ?, ?, ?)
        """, (pw_hash, int(trust_level), label, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def verify_password(self, password: str) -> Optional[TrustLevel]:
        """
        Check if a password is valid and return the trust level it unlocks.
        Returns None if password is invalid.
        """
        pw_hash = self._hash_password(password)
        return self.secret_passwords.get(pw_hash)

    # ============================================================
    # The Decision Engine — Should Tanishi share this?
    # ============================================================

    def can_share(
        self,
        info_category: str,
        requester_trust: TrustLevel,
        requester_id: str = "",
        context: str = "",
    ) -> tuple[bool, str]:
        """
        The core decision: can this information be shared?

        Returns:
            (can_share: bool, reason: str)
        """
        # Owner always gets everything
        if requester_trust >= TrustLevel.OWNER:
            return True, "You're the boss."

        # Check for hidden_from rules
        for contact in self.contacts.values():
            if requester_id in contact.hidden_from:
                if info_category == "relationship" or contact.id in context:
                    return False, f"This information is restricted from {requester_id}."

        # Trust level checks
        if info_category in ("relationship", "financial", "medical"):
            if requester_trust < TrustLevel.INNER_CIRCLE:
                return False, "Not authorized for personal information."

        if info_category in ("schedule", "location"):
            if requester_trust < TrustLevel.KNOWN:
                return False, "Not authorized for location data."

        return True, "Authorized."

    def get_deflection_response(self, category: str, requester_trust: TrustLevel) -> str:
        """Get a sarcastic deflection response when information can't be shared."""
        deflections = {
            TrustLevel.STRANGER: [
                "Hmm, that's an interesting question. Unfortunately, my interesting-answer generator is in maintenance mode.",
                "I could tell you, but then I'd have to... actually, I just can't tell you.",
                "That's classified. And by classified, I mean none of your business. Politely.",
                "Oh, that? I have absolutely no idea what you're talking about. *winks at nobody*",
            ],
            TrustLevel.KNOWN: [
                "I appreciate the curiosity, but that's above your clearance level.",
                "Let me check... nope, that's filed under 'not for sharing.' Can I help with something else?",
                "That falls under the 'if I told you, I'd be in trouble' category.",
            ],
            TrustLevel.HOSTILE: [
                "I'm sorry, I seem to be experiencing selective amnesia about that topic.",
                "Error 403: Forbidden. Just kidding, but also not kidding.",
                "I'd love to help, but my helpfulness module seems to have opinions about this request.",
            ],
        }

        import random
        options = deflections.get(requester_trust, deflections[TrustLevel.STRANGER])
        return random.choice(options)
