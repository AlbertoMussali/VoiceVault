from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import io
import json
import logging
import os
from typing import Any
import uuid
import zipfile

from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.entry_titles import deterministic_title_from_transcript, fallback_entry_title
from app.models import (
    AskQuery,
    AskResult,
    AuditLog,
    AudioAsset,
    BragBullet,
    BragBulletCitation,
    Citation,
    Entry,
    EntryTag,
    ExportJob,
    Tag,
    Transcript,
)
from app.openai_indexing import classify_transcript, estimate_indexing_request_bytes
from app.openai_summary import AskSummarySentence, generate_summary_sentences
from app.openai_stt import transcribe_audio_bytes
from app.settings import get_redis_url, get_settings
from app.storage import get_storage_backend

DEFAULT_QUEUE_NAME = "default"
job_logger = logging.getLogger("voicevault.jobs")


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

        _apply_transcript_indexing(session=session, entry=entry, transcript_text=transcription.text)
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


def _apply_transcript_indexing(*, session: Session, entry: Entry, transcript_text: str) -> None:
    settings = get_settings()
    session.add(
        AuditLog(
            user_id=entry.user_id,
            entry_id=entry.id,
            event_type="llm_called",
            metadata_json={
                "model": settings.openai_indexing_model,
                "task": "transcript_indexing",
                "byte_estimate": estimate_indexing_request_bytes(transcript_text=transcript_text),
            },
        )
    )

    try:
        indexing = classify_transcript(transcript_text=transcript_text)
    except RuntimeError:
        job_logger.warning(
            "transcript_indexing_failed",
            extra={"fields": {"entry_id": str(entry.id)}},
            exc_info=True,
        )
        return

    entry.entry_type = indexing.entry_type
    entry.context = indexing.context
    entry.sentiment_label = indexing.sentiment_label
    entry.sentiment_score = indexing.sentiment_score
    _upsert_entry_tags(
        session=session,
        user_id=entry.user_id,
        entry_id=entry.id,
        tag_names=indexing.tags,
    )


def _upsert_entry_tags(
    *,
    session: Session,
    user_id: uuid.UUID,
    entry_id: uuid.UUID,
    tag_names: list[str],
) -> None:
    normalized_names = [name.strip().lower() for name in tag_names if name and name.strip()]
    if not normalized_names:
        return

    existing_tags = (
        session.execute(
            select(Tag).where(
                Tag.user_id == user_id,
                Tag.normalized_name.in_(normalized_names),
            )
        )
        .scalars()
        .all()
    )
    tags_by_name = {tag.normalized_name: tag for tag in existing_tags}

    for normalized_name in normalized_names:
        if normalized_name in tags_by_name:
            continue
        tag = Tag(
            user_id=user_id,
            name=normalized_name,
            normalized_name=normalized_name,
        )
        session.add(tag)
        session.flush()
        tags_by_name[normalized_name] = tag

    existing_links = (
        session.execute(select(EntryTag.tag_id).where(EntryTag.entry_id == entry_id))
        .scalars()
        .all()
    )
    linked_tag_ids = set(existing_links)

    for normalized_name in normalized_names:
        tag = tags_by_name.get(normalized_name)
        if tag is None or tag.id in linked_tag_ids:
            continue
        session.add(EntryTag(entry_id=entry_id, tag_id=tag.id))
        linked_tag_ids.add(tag.id)


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


def run_account_export_all_job(export_job_id: str) -> dict[str, Any]:
    """Build a full account export zip with JSON + transcripts + tags + brag + audio blobs."""
    export_job_uuid = _parse_uuid(export_job_id, field_name="export_job_id")
    session = get_sessionmaker()()
    try:
        export_job = session.get(ExportJob, export_job_uuid)
        if export_job is None:
            raise ValueError(f"Export job not found: {export_job_uuid}")

        export_job.status = "processing"
        export_job.error_message = None
        session.commit()

        payload, audio_assets = _build_account_export_payload(session=session, user_id=export_job.user_id)
        archive_bytes = _build_account_export_archive(payload=payload, audio_assets=audio_assets)

        storage_key = f"exports/account/{export_job.user_id}/{export_job.id}/voicevault-export-all.zip"
        get_storage_backend().put(storage_key, archive_bytes)

        export_job.status = "completed"
        export_job.artifact_storage_key = storage_key
        export_job.metadata_json = {
            "entry_count": len(payload["entries"]),
            "audio_asset_count": len(audio_assets),
            "tag_count": len(payload["tags"]),
            "brag_bullet_count": len(payload["brag"]["bullets"]),
            "file_name": "voicevault-export-all.zip",
        }
        session.commit()

        return {
            "status": "ok",
            "export_job_id": str(export_job.id),
            "artifact_storage_key": storage_key,
            "entry_count": len(payload["entries"]),
            "audio_asset_count": len(audio_assets),
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
    "account.export_all_v1": run_account_export_all_job,
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


def _build_account_export_payload(*, session: Session, user_id: uuid.UUID) -> tuple[dict[str, Any], list[AudioAsset]]:
    entries = (
        session.execute(
            select(Entry)
            .where(Entry.user_id == user_id)
            .order_by(Entry.created_at.asc(), Entry.id.asc())
        )
        .scalars()
        .all()
    )
    entry_ids = [entry.id for entry in entries]

    transcripts = (
        session.execute(
            select(Transcript)
            .join(Entry, Entry.id == Transcript.entry_id)
            .where(Entry.user_id == user_id)
            .order_by(Transcript.entry_id.asc(), Transcript.version.asc())
        )
        .scalars()
        .all()
    )
    transcripts_by_entry: dict[uuid.UUID, list[Transcript]] = {}
    for transcript in transcripts:
        transcripts_by_entry.setdefault(transcript.entry_id, []).append(transcript)

    audio_assets = (
        session.execute(
            select(AudioAsset)
            .join(Entry, Entry.id == AudioAsset.entry_id)
            .where(Entry.user_id == user_id)
            .order_by(AudioAsset.entry_id.asc(), AudioAsset.created_at.asc(), AudioAsset.id.asc())
        )
        .scalars()
        .all()
    )
    audio_by_entry: dict[uuid.UUID, list[AudioAsset]] = {}
    for asset in audio_assets:
        audio_by_entry.setdefault(asset.entry_id, []).append(asset)

    tags = (
        session.execute(
            select(Tag)
            .where(Tag.user_id == user_id)
            .order_by(Tag.name.asc(), Tag.id.asc())
        )
        .scalars()
        .all()
    )
    tags_by_id = {tag.id: tag for tag in tags}
    entry_tag_pairs = (
        session.execute(
            select(EntryTag)
            .where(EntryTag.entry_id.in_(entry_ids))
            .order_by(EntryTag.entry_id.asc(), EntryTag.created_at.asc(), EntryTag.id.asc())
        )
        .scalars()
        .all()
        if entry_ids
        else []
    )
    tag_ids_by_entry: dict[uuid.UUID, list[str]] = {}
    for pair in entry_tag_pairs:
        if pair.tag_id not in tags_by_id:
            continue
        tag_ids_by_entry.setdefault(pair.entry_id, []).append(str(pair.tag_id))

    brag_bullets = (
        session.execute(
            select(BragBullet)
            .where(BragBullet.user_id == user_id)
            .order_by(BragBullet.created_at.asc(), BragBullet.id.asc())
        )
        .scalars()
        .all()
    )
    citations = (
        session.execute(
            select(Citation)
            .where(Citation.user_id == user_id)
            .order_by(Citation.created_at.asc(), Citation.id.asc())
        )
        .scalars()
        .all()
    )
    citation_by_id = {citation.id: citation for citation in citations}
    transcript_entry_id_by_id = {transcript.id: transcript.entry_id for transcript in transcripts}
    links = (
        session.execute(
            select(BragBulletCitation)
            .where(BragBulletCitation.bullet_id.in_([bullet.id for bullet in brag_bullets]))
            .order_by(BragBulletCitation.created_at.asc(), BragBulletCitation.id.asc())
        )
        .scalars()
        .all()
        if brag_bullets
        else []
    )
    citations_by_bullet: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for link in links:
        citation = citation_by_id.get(link.citation_id)
        if citation is None:
            continue
        entry_id = transcript_entry_id_by_id.get(citation.transcript_id)
        citations_by_bullet.setdefault(link.bullet_id, []).append(
            {
                "id": str(citation.id),
                "entry_id": str(entry_id) if entry_id else None,
                "transcript_id": str(citation.transcript_id),
                "transcript_version": citation.transcript_version,
                "start_char": citation.start_char,
                "end_char": citation.end_char,
                "quote_text": citation.quote_text,
                "snippet_hash": citation.snippet_hash,
                "created_at": _isoformat(citation.created_at),
            }
        )

    payload_entries = []
    for entry in entries:
        payload_entries.append(
            {
                "id": str(entry.id),
                "title": entry.title,
                "status": entry.status,
                "entry_type": entry.entry_type,
                "context": entry.context,
                "occurred_at": _isoformat(entry.occurred_at),
                "created_at": _isoformat(entry.created_at),
                "updated_at": _isoformat(entry.updated_at),
                "tags": tag_ids_by_entry.get(entry.id, []),
                "transcripts": [
                    {
                        "id": str(transcript.id),
                        "version": transcript.version,
                        "is_current": transcript.is_current,
                        "language_code": transcript.language_code,
                        "source": transcript.source,
                        "created_at": _isoformat(transcript.created_at),
                        "transcript_text": transcript.transcript_text,
                    }
                    for transcript in transcripts_by_entry.get(entry.id, [])
                ],
                "audio_assets": [
                    {
                        "id": str(asset.id),
                        "storage_key": asset.storage_key,
                        "mime_type": asset.mime_type,
                        "size_bytes": asset.size_bytes,
                        "duration_seconds": str(asset.duration_seconds) if asset.duration_seconds is not None else None,
                        "sha256_hex": asset.sha256_hex,
                        "created_at": _isoformat(asset.created_at),
                    }
                    for asset in audio_by_entry.get(entry.id, [])
                ],
            }
        )

    payload = {
        "schema_version": "account_export_v1",
        "user_id": str(user_id),
        "exported_at": _isoformat(datetime.now(timezone.utc)),
        "entries": payload_entries,
        "tags": [
            {
                "id": str(tag.id),
                "name": tag.name,
                "normalized_name": tag.normalized_name,
                "created_at": _isoformat(tag.created_at),
            }
            for tag in tags
        ],
        "brag": {
            "bullets": [
                {
                    "id": str(bullet.id),
                    "bucket": bullet.bucket,
                    "bullet_text": bullet.bullet_text,
                    "created_at": _isoformat(bullet.created_at),
                    "updated_at": _isoformat(bullet.updated_at),
                    "citations": citations_by_bullet.get(bullet.id, []),
                }
                for bullet in brag_bullets
            ]
        },
    }
    return payload, audio_assets


def _build_account_export_archive(*, payload: dict[str, Any], audio_assets: list[AudioAsset]) -> bytes:
    storage = get_storage_backend()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export.json", json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))

        for asset in audio_assets:
            original_name = os.path.basename(asset.storage_key) or f"{asset.id}.bin"
            archive_path = f"audio/{asset.entry_id}/{asset.id}-{original_name}"
            archive.writestr(archive_path, storage.get(asset.storage_key))
    return buffer.getvalue()


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


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
