from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os


DEFAULT_DATABASE_URL = "postgresql+psycopg://voicevault:voicevault@db:5432/voicevault"
DEFAULT_API_VERSION = "0.1.0"
DEFAULT_ENTRY_AUTH_TOKEN = "dev-entry-token"


def get_database_url() -> str:
    """Return database URL used by both app runtime and Alembic."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    database_url: str
    api_version: str
    entry_auth_token: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache application settings."""
    return Settings(
        database_url=get_database_url(),
        api_version=os.getenv("API_VERSION", DEFAULT_API_VERSION),
        entry_auth_token=os.getenv("ENTRY_AUTH_TOKEN", DEFAULT_ENTRY_AUTH_TOKEN),
    )
