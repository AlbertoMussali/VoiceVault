from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.settings import get_database_url


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for Alembic metadata discovery."""


engine: Engine = create_engine(get_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def check_db_connection() -> bool:
    """Lightweight connectivity probe for health checks and smoke tests."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
