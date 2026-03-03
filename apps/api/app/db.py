from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import get_database_url


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for Alembic metadata discovery."""


def _resolve_engine_connect_args(database_url: str) -> dict[str, str]:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgresql+psycopg"):
        return {}

    existing_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "gssencmode" in existing_query:
        return {}

    return {"gssencmode": "disable"}


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = get_database_url()
    connect_args = _resolve_engine_connect_args(database_url)
    if connect_args:
        return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    return create_engine(database_url, pool_pre_ping=True)


def reset_engine_cache() -> None:
    get_engine.cache_clear()


def get_sessionmaker() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def initialize_schema() -> None:
    """Create tables from SQLAlchemy metadata (SQLite/testing convenience)."""
    Base.metadata.create_all(bind=get_engine())


def check_db_connection() -> bool:
    """Lightweight connectivity probe for health checks and smoke tests."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return True


def get_db() -> Session:
    """Yield a SQLAlchemy session for request-scoped dependencies."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
