"""
Tanishi Configuration — All settings in one place.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class TanishiConfig(BaseSettings):
    """Central configuration for Project Tanishi."""

    # --- Identity ---
    name: str = "Tanishi"
    version: str = "0.1.0"

    # --- LLM Providers ---
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-20250514", alias="CLAUDE_MODEL")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="mistral:7b", alias="OLLAMA_MODEL")

    # --- Paths ---
    tanishi_home: Path = Field(default=Path.home() / ".tanishi")
    db_path: Optional[Path] = Field(default=None)
    memory_path: Optional[Path] = Field(default=None)
    skills_path: Optional[Path] = Field(default=None)
    logs_path: Optional[Path] = Field(default=None)

    # --- Security ---
    master_password: str = Field(default="", alias="TANISHI_MASTER_PASSWORD")
    encryption_key: str = Field(default="", alias="TANISHI_ENCRYPTION_KEY")

    # --- Server ---
    host: str = Field(default="0.0.0.0", alias="TANISHI_HOST")
    port: int = Field(default=8888, alias="TANISHI_PORT")

    # --- Behavior ---
    default_llm: str = "claude"  # "claude" | "ollama" | "auto"
    privacy_mode: bool = False  # True = force all local
    max_conversation_history: int = 50
    auto_improve: bool = True

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def model_post_init(self, __context):
        """Set up derived paths and ensure directories exist."""
        if self.db_path is None:
            self.db_path = self.tanishi_home / "tanishi.db"
        if self.memory_path is None:
            self.memory_path = self.tanishi_home / "memory"
        if self.skills_path is None:
            self.skills_path = self.tanishi_home / "skills"
        if self.logs_path is None:
            self.logs_path = self.tanishi_home / "logs"

        # Create directories
        for path in [self.tanishi_home, self.memory_path, self.skills_path, self.logs_path]:
            path.mkdir(parents=True, exist_ok=True)


# Singleton
_config = None


def get_config() -> TanishiConfig:
    """Get or create the global config instance."""
    global _config
    if _config is None:
        _config = TanishiConfig()
    return _config
