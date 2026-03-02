from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class MigrationVersionProbe(Base):
    """Small table used to verify migration + DB connectivity end-to-end."""

    __tablename__ = "migration_version_probe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="probe")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    audio_assets: Mapped[list["AudioAsset"]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class AudioAsset(Base):
    __tablename__ = "audio_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    entry: Mapped[Entry] = relationship(back_populates="audio_assets")
