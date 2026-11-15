"""Runtime settings, read from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("data"))
    ollama_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    llm_timeout_s: float = 120.0
    timezone: str = "Asia/Kolkata"
    max_steps: int = 8
    tool_timeout_s: float = 20.0
    tool_retries: int = 2
    retry_backoff_s: float = 1.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / "notes"

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Settings":
        if env_file and Path(env_file).exists():
            load_dotenv(env_file)
        return cls(
            data_dir=Path(os.getenv("AGENT_DATA_DIR", "data")),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            llm_timeout_s=_env_float("AGENT_LLM_TIMEOUT", 120.0),
            timezone=os.getenv("AGENT_TIMEZONE", "Asia/Kolkata"),
            max_steps=_env_int("AGENT_MAX_STEPS", 8),
            tool_timeout_s=_env_float("AGENT_TOOL_TIMEOUT", 20.0),
            tool_retries=_env_int("AGENT_TOOL_RETRIES", 2),
            retry_backoff_s=_env_float("AGENT_RETRY_BACKOFF", 1.0),
        )
