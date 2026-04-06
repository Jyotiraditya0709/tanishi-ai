"""
Tanishi Configuration — All settings in one place.
Stability patch: bulletproof path handling, never crashes on missing .env.
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
    version: str = "0.5.0"

    # --- LLM Providers ---
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-20250514", alias="CLAUDE_MODEL")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="mistral:7b", alias="OLLAMA_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # --- Paths (accept both DB_PATH and TANISHI_DB_PATH) ---
    tanishi_home: Path = Field(default=Path.home() / ".tanishi")
    db_path: Optional[Path] = Field(default=None, alias="DB_PATH")
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
    default_llm: str = "claude"
    privacy_mode: bool = False
    max_conversation_history: int = 50
    auto_improve: bool = True

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def model_post_init(self, __context):
        """Set up derived paths and ensure directories exist."""
        # Also check TANISHI_DB_PATH as fallback
        if self.db_path is None:
            tanishi_db = os.getenv("TANISHI_DB_PATH", "")
            if tanishi_db:
                self.db_path = Path(tanishi_db).expanduser()
            else:
                self.db_path = self.tanishi_home / "tanishi.db"

        # Expand user paths (handle ~ on all platforms)
        self.db_path = Path(self.db_path).expanduser().resolve() if self.db_path else self.tanishi_home / "tanishi.db"

        if self.memory_path is None:
            self.memory_path = self.tanishi_home / "memory"
        if self.skills_path is None:
            self.skills_path = self.tanishi_home / "skills"
        if self.logs_path is None:
            self.logs_path = self.tanishi_home / "logs"

        # Create directories (never crash on this)
        try:
            for path in [self.tanishi_home, self.memory_path, self.skills_path, self.logs_path]:
                path.mkdir(parents=True, exist_ok=True)
            # Ensure db directory exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


# Singleton
_config = None


def get_config() -> TanishiConfig:
    """Get or create the global config instance."""
    global _config
    if _config is None:
        try:
            _config = TanishiConfig()
        except Exception as e:
            # Nuclear fallback — use all defaults, never crash
            print(f"  Warning: Config error ({e}). Using defaults.")
            _config = TanishiConfig(
                _env_file=None,  # Skip .env file
            )
    return _config
