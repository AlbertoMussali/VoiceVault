from __future__ import annotations

from collections.abc import Callable
import json
import os
from typing import Any
import uuid

from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.entry_titles import deterministic_title_from_transcript, fallback_entry_title
from app.models import AskQuery, AskResult, AuditLog, AudioAsset, BragBullet, Entry, ExportJob, Transcript
from app.openai_summary import AskSummarySentence, generate_summary_sentences
from app.openai_stt import transcribe_audio_bytes
from app.settings import get_redis_url, get_settings
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
        settings = get_settings()
        _write_audit_event(
            entry_id=entry_uuid,
            user_id=entry.user_id,
            event_type="transcription_called",
            metadata_json={
                "model": settings.openai_stt_model,
                "bytes": len(audio_bytes),
            },
        )
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
        if not entry.title or not entry.title.strip():
            entry.title = deterministic_title_from_transcript(
                transcription.text,
                fallback=fallback_entry_title(entry_uuid),
            )
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


def run_brag_text_export_job(export_job_id: str) -> dict[str, Any]:
    """Render a deterministic text brag report and store it as an artifact."""
    export_job_uuid = _parse_uuid(export_job_id, field_name="export_job_id")

    session = get_sessionmaker()()
    try:
        export_job = session.get(ExportJob, export_job_uuid)
        if export_job is None:
            raise ValueError(f"Export job not found: {export_job_uuid}")

        export_job.status = "processing"
        export_job.error_message = None
        session.commit()

        bullets = (
            session.execute(
                select(BragBullet)
                .where(BragBullet.user_id == export_job.user_id)
                .order_by(BragBullet.created_at.asc(), BragBullet.id.asc())
            )
            .scalars()
            .all()
        )
        report_text = _build_brag_text_report(bullets)
        storage_key = f"exports/brag/{export_job.user_id}/{export_job.id}/report.txt"
        get_storage_backend().put(storage_key, report_text.encode("utf-8"))

        export_job.status = "completed"
        export_job.artifact_storage_key = storage_key
        export_job.metadata_json = {
            "bullet_count": len(bullets),
            "file_name": "voicevault-brag-report.txt",
        }
        session.commit()

        return {
            "status": "ok",
            "export_job_id": str(export_job.id),
            "artifact_storage_key": storage_key,
            "bullet_count": len(bullets),
        }
    except Exception as exc:
        session.rollback()
        export_job = session.get(ExportJob, export_job_uuid)
        if export_job is not None:
            export_job.status = "failed"
            export_job.error_message = str(exc)[:2000]
            session.commit()
        raise
    finally:
        session.close()


def run_ask_summary_job(ask_query_id: str, snippet_ids: list[str] | None = None) -> dict[str, Any]:
    """Generate an ask summary with strict per-sentence citation validation."""
    ask_query_uuid = _parse_uuid(ask_query_id, field_name="ask_query_id")
    session = get_sessionmaker()()
    try:
        ask_query = session.get(AskQuery, ask_query_uuid)
        if ask_query is None:
            raise ValueError(f"Ask query not found: {ask_query_uuid}")

        all_results = (
            session.execute(
                select(AskResult)
                .where(AskResult.ask_query_id == ask_query_uuid)
                .order_by(AskResult.result_order.asc())
            )
            .scalars()
            .all()
        )
        if not all_results:
            raise ValueError("Cannot summarize without ask sources")

        by_id = {str(result.id): result for result in all_results}
        selected_results = all_results
        if snippet_ids is not None:
            requested_ids = _normalize_requested_snippet_ids(snippet_ids)
            if not requested_ids:
                raise ValueError("snippet_ids must include at least one value")
            unknown_ids = [snippet_id for snippet_id in requested_ids if snippet_id not in by_id]
            if unknown_ids:
                raise ValueError(f"snippet_ids contain unknown values: {', '.join(unknown_ids)}")
            selected_results = [by_id[snippet_id] for snippet_id in requested_ids]

        sources = [
            {
                "snippet_id": str(result.id),
                "entry_id": str(result.entry_id),
                "transcript_id": str(result.transcript_id),
                "snippet_text": result.snippet_text,
                "start_char": result.start_char,
                "end_char": result.end_char,
            }
            for result in selected_results
        ]
        settings = get_settings()
        _write_audit_event(
            entry_id=None,
            user_id=ask_query.user_id,
            event_type="llm_called",
            metadata_json={
                "model": settings.openai_summary_model,
                "snippet_count": len(sources),
                "byte_estimate": _estimate_summary_request_bytes(query_text=ask_query.query_text, sources=sources),
            },
        )
        generated_sentences = generate_summary_sentences(
            query_text=ask_query.query_text,
            sources=sources,
        )
        validated_sentences = validate_summary_sentences(
            generated_sentences,
            allowed_snippet_ids={item["snippet_id"] for item in sources},
        )

        ask_query.summary_status = "done"
        ask_query.summary_json = {"sentences": validated_sentences}
        ask_query.summary_error = None
        session.commit()
        return {
            "query_id": str(ask_query.id),
            "summary_status": ask_query.summary_status,
            "summary": ask_query.summary_json,
        }
    except Exception as exc:
        session.rollback()
        ask_query = session.get(AskQuery, ask_query_uuid)
        if ask_query is not None:
            ask_query.summary_status = "error"
            ask_query.summary_error = str(exc)[:2000]
            session.commit()
        raise
    finally:
        session.close()


JOB_REGISTRY: dict[str, Callable[..., Any]] = {
    "stub.echo": run_stub_job,
    "transcription.process_entry_audio": run_transcription_job,
    "transcription.openai_stt_v1": run_transcription_job,
    "brag.export_text_v1": run_brag_text_export_job,
    "ask.summary_v1": run_ask_summary_job,
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


def _write_audit_event(
    *,
    entry_id: uuid.UUID | None,
    user_id: uuid.UUID,
    event_type: str,
    metadata_json: dict[str, Any],
) -> None:
    with get_sessionmaker()() as audit_session:
        audit_session.add(
            AuditLog(
                user_id=user_id,
                entry_id=entry_id,
                event_type=event_type,
                metadata_json=metadata_json,
            )
        )
        audit_session.commit()


def _estimate_summary_request_bytes(*, query_text: str, sources: list[dict[str, Any]]) -> int:
    payload = {
        "query_text": query_text,
        "sources": sources,
    }
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _build_brag_text_report(bullets: list[BragBullet]) -> str:
    bucket_titles = {
        "impact": "Impact",
        "execution": "Execution",
        "leadership": "Leadership",
        "collaboration": "Collaboration",
        "growth": "Growth",
    }
    bucket_order = ["impact", "execution", "leadership", "collaboration", "growth"]

    lines = [
        "VoiceVault Brag Report",
        "",
        "Dated Quotes",
        "",
    ]

    if not bullets:
        lines.append("No brag bullets available.")
        return "\n".join(lines) + "\n"

    grouped: dict[str, list[BragBullet]] = {bucket: [] for bucket in bucket_order}
    for bullet in bullets:
        grouped.setdefault(bullet.bucket, []).append(bullet)

    for bucket in bucket_order:
        bucket_bullets = grouped.get(bucket, [])
        if not bucket_bullets:
            continue
        lines.append(f"{bucket_titles[bucket]}")
        for bullet in bucket_bullets:
            quote = " ".join(segment.strip() for segment in bullet.bullet_text.splitlines()).strip()
            lines.append(f'- [{bullet.created_at.date().isoformat()}] "{quote}"')
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _normalize_requested_snippet_ids(snippet_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in snippet_ids:
        snippet_id = str(raw).strip()
        if not snippet_id:
            continue
        if snippet_id in seen:
            continue
        seen.add(snippet_id)
        normalized.append(snippet_id)
    return normalized


def validate_summary_sentences(
    sentences: list[AskSummarySentence],
    *,
    allowed_snippet_ids: set[str],
) -> list[dict[str, Any]]:
    """Validate generated summary structure and enforce citation boundaries."""
    validated: list[dict[str, Any]] = []
    for sentence in sentences:
        text = sentence.text.strip()
        if not text:
            raise ValueError("Summary sentence text cannot be empty")

        raw_ids = [snippet_id.strip() for snippet_id in sentence.snippet_ids if snippet_id.strip()]
        if not raw_ids:
            raise ValueError("Summary sentence is uncited")

        deduped_ids: list[str] = []
        seen: set[str] = set()
        for snippet_id in raw_ids:
            if snippet_id not in allowed_snippet_ids:
                raise ValueError(f"Summary sentence uses invalid snippet_id: {snippet_id}")
            if snippet_id in seen:
                continue
            seen.add(snippet_id)
            deduped_ids.append(snippet_id)

        if not deduped_ids:
            raise ValueError("Summary sentence is uncited")

        validated.append({"text": text, "snippet_ids": deduped_ids})

    if not validated:
        raise ValueError("Summary must include at least one sentence")
    return validated
