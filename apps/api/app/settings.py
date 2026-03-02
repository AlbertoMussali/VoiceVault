from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os


DEFAULT_DATABASE_URL = "postgresql+psycopg://voicevault:voicevault@db:5432/voicevault"
DEFAULT_API_VERSION = "0.1.0"
DEFAULT_AUDIO_STORAGE_ROOT = "/tmp/voicevault-audio"


def get_database_url() -> str:
    """Return database URL used by both app runtime and Alembic."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    database_url: str
    api_version: str
    audio_storage_root: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache application settings."""
    return Settings(
        database_url=get_database_url(),
        api_version=os.getenv("API_VERSION", DEFAULT_API_VERSION),
        audio_storage_root=os.getenv("AUDIO_STORAGE_ROOT", DEFAULT_AUDIO_STORAGE_ROOT),
    )
