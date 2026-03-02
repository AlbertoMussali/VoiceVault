from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any
import uuid

from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import AudioAsset, Entry, Transcript
from app.openai_stt import transcribe_audio_bytes
from app.settings import get_redis_url
from app.storage import get_storage_backend

DEFAULT_QUEUE_NAME = "default"


def run_stub_job(payload: str = "ok") -> dict[str, str]:
    """Minimal job used to verify worker wiring end-to-end."""
    return {"status": "ok", "payload": payload}


def run_transcription_job(entry_id: str, audio_asset_id: str | None = None) -> dict[str, Any]:
    """Transcribe an entry's audio via OpenAI STT and persist a transcript version."""
    entry_uuid = _parse_uuid(entry_id, field_name="entry_id")
    asset_uuid = _parse_uuid(audio_asset_id, field_name="audio_asset_id") if audio_asset_id else None

    session = get_sessionmaker()()
    try:
        entry = session.get(Entry, entry_uuid)
        if entry is None:
            raise ValueError(f"Entry not found: {entry_uuid}")

        audio_asset = _resolve_audio_asset(session, entry_uuid=entry_uuid, audio_asset_id=asset_uuid)
        if audio_asset is None:
            raise ValueError(f"No audio asset found for entry: {entry_uuid}")

        storage = get_storage_backend()
        audio_bytes = storage.get(audio_asset.storage_key)
        transcription = transcribe_audio_bytes(
            audio_bytes=audio_bytes,
            mime_type=audio_asset.mime_type,
            filename=os.path.basename(audio_asset.storage_key),
        )

        next_version = _next_transcript_version(session, entry_uuid)
        session.execute(update(Transcript).where(Transcript.entry_id == entry_uuid).values(is_current=False))
        transcript = Transcript(
            entry_id=entry_uuid,
            version=next_version,
            is_current=True,
            transcript_text=transcription.text,
            language_code=transcription.language_code,
            source="stt",
        )
        session.add(transcript)
        entry.status = "ready"
        session.commit()
        session.refresh(transcript)

        return {
            "status": "ok",
            "entry_id": str(entry_uuid),
            "audio_asset_id": str(audio_asset.id),
            "transcript_id": str(transcript.id),
            "version": transcript.version,
        }
    finally:
        session.close()


JOB_REGISTRY: dict[str, Callable[..., Any]] = {
    "stub.echo": run_stub_job,
    "transcription.process_entry_audio": run_transcription_job,
    "transcription.openai_stt_v1": run_transcription_job,
}


def get_redis_connection() -> Redis:
    """Create a Redis client for queue/worker operations."""
    return Redis.from_url(get_redis_url())


def get_default_queue() -> Queue:
    """Return the default RQ queue."""
    return Queue(name=DEFAULT_QUEUE_NAME, connection=get_redis_connection())


def enqueue_registered_job(job_key: str, *args: Any, **kwargs: Any) -> Job:
    """Enqueue a registered job by stable key."""
    job_func = JOB_REGISTRY.get(job_key)
    if job_func is None:
        raise KeyError(f"Unknown job key: {job_key}")
    return get_default_queue().enqueue(job_func, *args, **kwargs)


def _parse_uuid(raw_value: str, *, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw_value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc


def _resolve_audio_asset(
    session: Session,
    *,
    entry_uuid: uuid.UUID,
    audio_asset_id: uuid.UUID | None,
) -> AudioAsset | None:
    if audio_asset_id is not None:
        audio_asset = session.get(AudioAsset, audio_asset_id)
        if audio_asset is None:
            raise ValueError(f"Audio asset not found: {audio_asset_id}")
        if audio_asset.entry_id != entry_uuid:
            raise ValueError("audio_asset_id does not belong to entry_id")
        return audio_asset

    statement = (
        select(AudioAsset)
        .where(AudioAsset.entry_id == entry_uuid)
        .order_by(AudioAsset.created_at.desc())
        .limit(1)
    )
    return session.execute(statement).scalar_one_or_none()


def _next_transcript_version(session: Session, entry_uuid: uuid.UUID) -> int:
    statement = select(func.max(Transcript.version)).where(Transcript.entry_id == entry_uuid)
    max_version = session.execute(statement).scalar_one_or_none()
    if max_version is None:
        return 1
    return int(max_version) + 1
