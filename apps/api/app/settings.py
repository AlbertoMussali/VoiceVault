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
DEFAULT_AUTH_COOKIE_SECURE = True
DEFAULT_AUTH_COOKIE_SAMESITE = "strict"
DEFAULT_AUTH_REFRESH_COOKIE_NAME = "vv_refresh_token"
DEFAULT_AUTH_CSRF_COOKIE_NAME = "vv_csrf_token"
DEFAULT_PASSWORD_MIN_LENGTH = 12
DEFAULT_PASSWORD_REQUIRE_UPPERCASE = True
DEFAULT_PASSWORD_REQUIRE_LOWERCASE = True
DEFAULT_PASSWORD_REQUIRE_DIGIT = True
DEFAULT_PASSWORD_REQUIRE_SPECIAL = False

# Lightweight fallback for early endpoints/tests that don't exercise full JWT auth.
DEFAULT_ENTRY_AUTH_TOKEN = "dev-entry-token"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_STT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_OPENAI_SUMMARY_MODEL = "gpt-4o-mini"
DEFAULT_REQUIRE_ZERO_RETENTION = False
DEFAULT_PROVIDER_ZERO_RETENTION_APPROVED = False
DEFAULT_MAX_REQUEST_SIZE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_AUDIO_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_RATE_LIMIT_REQUESTS = 120
DEFAULT_RATE_LIMIT_AUTH_REQUESTS = 20
DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


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
    auth_cookie_secure: bool
    auth_cookie_samesite: str
    auth_refresh_cookie_name: str
    auth_csrf_cookie_name: str
    password_min_length: int
    password_require_uppercase: bool
    password_require_lowercase: bool
    password_require_digit: bool
    password_require_special: bool

    entry_auth_token: str
    openai_api_key: str
    openai_base_url: str
    openai_stt_model: str
    openai_summary_model: str
    require_zero_retention: bool
    provider_zero_retention_approved: bool
    max_request_size_bytes: int
    max_audio_upload_size_bytes: int
    rate_limit_window_seconds: int
    rate_limit_requests: int
    rate_limit_auth_requests: int
    cors_allowed_origins: tuple[str, ...]


def _read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _read_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return values if values else default


def _read_cookie_samesite_env(name: str, default: str) -> str:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"lax", "strict", "none"}:
        return normalized
    return default


def is_summary_generation_enabled(settings: Settings | None = None) -> bool:
    current = settings or get_settings()
    if not current.require_zero_retention:
        return True
    return current.provider_zero_retention_approved


def get_summary_generation_disabled_reason(settings: Settings | None = None) -> str | None:
    current = settings or get_settings()
    if is_summary_generation_enabled(current):
        return None
    return "Summary generation is disabled because zero-retention provider approval is required."


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
        auth_cookie_secure=_read_bool_env("AUTH_COOKIE_SECURE", DEFAULT_AUTH_COOKIE_SECURE),
        auth_cookie_samesite=_read_cookie_samesite_env("AUTH_COOKIE_SAMESITE", DEFAULT_AUTH_COOKIE_SAMESITE),
        auth_refresh_cookie_name=os.getenv("AUTH_REFRESH_COOKIE_NAME", DEFAULT_AUTH_REFRESH_COOKIE_NAME),
        auth_csrf_cookie_name=os.getenv("AUTH_CSRF_COOKIE_NAME", DEFAULT_AUTH_CSRF_COOKIE_NAME),
        password_min_length=_read_positive_int_env("PASSWORD_MIN_LENGTH", DEFAULT_PASSWORD_MIN_LENGTH),
        password_require_uppercase=_read_bool_env(
            "PASSWORD_REQUIRE_UPPERCASE",
            DEFAULT_PASSWORD_REQUIRE_UPPERCASE,
        ),
        password_require_lowercase=_read_bool_env(
            "PASSWORD_REQUIRE_LOWERCASE",
            DEFAULT_PASSWORD_REQUIRE_LOWERCASE,
        ),
        password_require_digit=_read_bool_env("PASSWORD_REQUIRE_DIGIT", DEFAULT_PASSWORD_REQUIRE_DIGIT),
        password_require_special=_read_bool_env("PASSWORD_REQUIRE_SPECIAL", DEFAULT_PASSWORD_REQUIRE_SPECIAL),
        entry_auth_token=os.getenv("ENTRY_AUTH_TOKEN", DEFAULT_ENTRY_AUTH_TOKEN),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        openai_stt_model=os.getenv("OPENAI_STT_MODEL", DEFAULT_OPENAI_STT_MODEL),
        openai_summary_model=os.getenv("OPENAI_SUMMARY_MODEL", DEFAULT_OPENAI_SUMMARY_MODEL),
        require_zero_retention=_read_bool_env("REQUIRE_ZERO_RETENTION", DEFAULT_REQUIRE_ZERO_RETENTION),
        provider_zero_retention_approved=_read_bool_env(
            "PROVIDER_ZERO_RETENTION_APPROVED",
            DEFAULT_PROVIDER_ZERO_RETENTION_APPROVED,
        ),
        max_request_size_bytes=_read_positive_int_env("MAX_REQUEST_SIZE_BYTES", DEFAULT_MAX_REQUEST_SIZE_BYTES),
        max_audio_upload_size_bytes=_read_positive_int_env(
            "MAX_AUDIO_UPLOAD_SIZE_BYTES",
            DEFAULT_MAX_AUDIO_UPLOAD_SIZE_BYTES,
        ),
        rate_limit_window_seconds=_read_positive_int_env(
            "RATE_LIMIT_WINDOW_SECONDS",
            DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
        ),
        rate_limit_requests=_read_positive_int_env("RATE_LIMIT_REQUESTS", DEFAULT_RATE_LIMIT_REQUESTS),
        rate_limit_auth_requests=_read_positive_int_env(
            "RATE_LIMIT_AUTH_REQUESTS",
            DEFAULT_RATE_LIMIT_AUTH_REQUESTS,
        ),
        cors_allowed_origins=_read_csv_env("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ALLOWED_ORIGINS),
    )
