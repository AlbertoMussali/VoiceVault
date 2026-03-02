from __future__ import annotations

from pathlib import Path
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from jwt import InvalidTokenError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.auth import decode_token
from app.db import get_db
from app.jobs import enqueue_registered_job
from app.errors import ApiContractError, ErrorType
from app.models import AuditLog, AudioAsset, Entry, Transcript, User
from app.settings import get_settings
from app.storage import get_storage_backend

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


class TranscriptPatchRequest(BaseModel):
    transcript_text: str = Field(min_length=1)
    language_code: str | None = Field(default=None, max_length=16)
    source: str = Field(default="user_edit", min_length=1, max_length=32)


def _extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _resolve_request_user_id(request: Request, db: Session) -> uuid.UUID:
    settings = get_settings()
    token = _extract_bearer_token(request)

    if token == settings.entry_auth_token:
        requested_user_id = request.headers.get("x-user-id")
        if requested_user_id is not None:
            try:
                user_id = uuid.UUID(requested_user_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="x-user-id must be a valid UUID",
                ) from exc

            if db.get(User, user_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            return user_id

        first_user_id = db.query(User.id).order_by(User.created_at.asc()).limit(1).scalar()
        if first_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No users available for static entry token; provide x-user-id",
            )
        return first_user_id

    try:
        payload = decode_token(token, settings, expected_type="access")
        user_id = uuid.UUID(str(payload["sub"]))
    except (InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if db.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


@router.post("", status_code=status.HTTP_201_CREATED)
def create_entry(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = _resolve_request_user_id(request, db)
    entry = Entry(user_id=user_id, status="draft")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"entry_id": str(entry.id), "status": entry.status}


@router.get("")
def list_entries() -> dict[str, list[dict[str, str]]]:
    return {"entries": []}


@router.get("/{entry_id}")
def get_entry(entry_id: uuid.UUID) -> dict[str, str]:
    return {"entry_id": str(entry_id)}


@router.patch("/{entry_id}/transcript")
def patch_entry_transcript(
    entry_id: uuid.UUID,
    payload: TranscriptPatchRequest,
    db: Session = Depends(get_db),
) -> dict[str, str | int | bool | None]:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise ApiContractError(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="ENTRY_NOT_FOUND",
            message="Entry not found.",
            error_type=ErrorType.FATAL,
        )

    max_version = db.execute(select(func.max(Transcript.version)).where(Transcript.entry_id == entry_id)).scalar_one_or_none()
    if max_version is None:
        raise ApiContractError(
            status_code=status.HTTP_409_CONFLICT,
            error_code="TRANSCRIPT_MISSING",
            message="Cannot patch transcript before initial transcription exists.",
            error_type=ErrorType.FATAL,
        )

    current_transcript = (
        db.execute(
            select(Transcript)
            .where(Transcript.entry_id == entry_id, Transcript.is_current.is_(True))
            .order_by(Transcript.version.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    next_version = int(max_version) + 1

    db.execute(update(Transcript).where(Transcript.entry_id == entry_id).values(is_current=False))
    new_transcript = Transcript(
        entry_id=entry_id,
        version=next_version,
        is_current=True,
        transcript_text=payload.transcript_text,
        language_code=payload.language_code if payload.language_code is not None else (current_transcript.language_code if current_transcript else None),
        source=payload.source,
    )
    db.add(new_transcript)
    db.commit()
    db.refresh(new_transcript)

    return {
        "entry_id": str(entry_id),
        "transcript_id": str(new_transcript.id),
        "version": new_transcript.version,
        "is_current": new_transcript.is_current,
        "language_code": new_transcript.language_code,
        "source": new_transcript.source,
    }


@router.post("/{entry_id}/audio", status_code=status.HTTP_201_CREATED)
async def upload_entry_audio(
    entry_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise ApiContractError(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="ENTRY_NOT_FOUND",
            message="Entry not found.",
            error_type=ErrorType.FATAL,
        )

    request_content_type = request.headers.get("content-type", "")
    requested_name = request.headers.get("x-audio-filename", f"entry-{entry_id}.webm")
    filename = Path(requested_name).name or f"entry-{entry_id}.webm"

    mime_type = request_content_type
    body = b""

    if request_content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("audio") or form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiContractError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="AUDIO_MISSING_MULTIPART_FILE",
                message="Missing multipart audio file.",
                error_type=ErrorType.FATAL,
            )
        body = await upload.read()
        if upload.filename:
            filename = Path(upload.filename).name or filename
        mime_type = upload.content_type or ""
    else:
        if not request_content_type.startswith("audio/"):
            raise ApiContractError(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                error_code="AUDIO_UNSUPPORTED_MEDIA_TYPE",
                message="Content-Type must be audio/* or multipart/form-data.",
                error_type=ErrorType.FATAL,
            )
        body = await request.body()

    if not mime_type.startswith("audio/"):
        raise ApiContractError(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            error_code="AUDIO_UNSUPPORTED_MEDIA_TYPE",
            message="Audio file content type must be audio/*.",
            error_type=ErrorType.FATAL,
        )

    if not body:
        raise ApiContractError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="AUDIO_EMPTY_PAYLOAD",
            message="Audio payload is empty.",
            error_type=ErrorType.FATAL,
        )

    unique = uuid.uuid4()
    storage_key = f"entries/{entry_id}/audio/{unique}-{filename}"

    storage = get_storage_backend()
    try:
        storage.put(storage_key, body)
    except OSError as exc:
        raise ApiContractError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="AUDIO_STORAGE_UNAVAILABLE",
            message="Audio storage is temporarily unavailable. Retry this upload.",
            error_type=ErrorType.TRANSIENT,
        ) from exc

    sha256_hex = hashlib.sha256(body).hexdigest()
    audio_asset = AudioAsset(
        entry_id=entry_id,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=len(body),
        sha256_hex=sha256_hex,
    )
    db.add(audio_asset)
    entry.status = "transcribing"
    db.add(
        AuditLog(
            user_id=entry.user_id,
            entry_id=entry_id,
            event_type="audio_uploaded",
            metadata_json={
                "bytes": len(body),
                "mime_type": mime_type,
            },
        )
    )

    try:
        db.flush()
        job = enqueue_registered_job(
            "transcription.process_entry_audio",
            entry_id=str(entry_id),
            audio_asset_id=str(audio_asset.id),
        )
        db.commit()
        db.refresh(audio_asset)
    except (SQLAlchemyError, Exception) as exc:
        db.rollback()
        try:
            storage.delete(storage_key)
        except OSError:
            pass
        raise ApiContractError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="AUDIO_ENQUEUE_FAILED",
            message="Audio accepted but failed to enqueue transcription job. Retry this upload.",
            error_type=ErrorType.TRANSIENT,
        ) from exc

    return {
        "asset_id": str(audio_asset.id),
        "entry_id": str(entry_id),
        "status": entry.status,
        "job_id": str(job.id),
        "storage_key": storage_key,
        "size_bytes": audio_asset.size_bytes,
        "mime_type": audio_asset.mime_type,
    }
