from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os


DEFAULT_DATABASE_URL = "postgresql+psycopg://voicevault:voicevault@db:5432/voicevault"
DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_API_VERSION = "0.1.0"

DEFAULT_STORAGE_BACKEND = "local"
DEFAULT_STORAGE_LOCAL_ROOT = "/tmp/voicevault-storage"

DEFAULT_AUTH_SECRET_KEY = "dev-only-change-me"
DEFAULT_JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_TTL_MINUTES = 15
DEFAULT_REFRESH_TOKEN_TTL_DAYS = 30

# Lightweight fallback for early endpoints/tests that don't exercise full JWT auth.
DEFAULT_ENTRY_AUTH_TOKEN = "dev-entry-token"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_STT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_OPENAI_SUMMARY_MODEL = "gpt-4o-mini"


def get_database_url() -> str:
    """Return database URL used by both app runtime and Alembic."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_redis_url() -> str:
    """Return Redis URL used for background queues and workers."""
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    database_url: str
    redis_url: str
    api_version: str

    storage_backend: str
    storage_local_root: str

    auth_secret_key: str
    jwt_algorithm: str
    access_token_ttl_minutes: int
    refresh_token_ttl_days: int

    entry_auth_token: str
    openai_api_key: str
    openai_base_url: str
    openai_stt_model: str
    openai_summary_model: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache application settings."""
    return Settings(
        database_url=get_database_url(),
        redis_url=get_redis_url(),
        api_version=os.getenv("API_VERSION", DEFAULT_API_VERSION),
        storage_backend=os.getenv("STORAGE_BACKEND", DEFAULT_STORAGE_BACKEND),
        storage_local_root=os.getenv("STORAGE_LOCAL_ROOT", DEFAULT_STORAGE_LOCAL_ROOT),
        auth_secret_key=os.getenv("AUTH_SECRET_KEY", DEFAULT_AUTH_SECRET_KEY),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", DEFAULT_JWT_ALGORITHM),
        access_token_ttl_minutes=int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", str(DEFAULT_ACCESS_TOKEN_TTL_MINUTES))),
        refresh_token_ttl_days=int(os.getenv("REFRESH_TOKEN_TTL_DAYS", str(DEFAULT_REFRESH_TOKEN_TTL_DAYS))),
        entry_auth_token=os.getenv("ENTRY_AUTH_TOKEN", DEFAULT_ENTRY_AUTH_TOKEN),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        openai_stt_model=os.getenv("OPENAI_STT_MODEL", DEFAULT_OPENAI_STT_MODEL),
        openai_summary_model=os.getenv("OPENAI_SUMMARY_MODEL", DEFAULT_OPENAI_SUMMARY_MODEL),
    )
