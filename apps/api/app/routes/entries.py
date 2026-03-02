from __future__ import annotations

from pathlib import Path
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AudioAsset, Entry
from app.storage import get_storage_backend

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


@router.get("")
def list_entries() -> dict[str, list[dict[str, str]]]:
    return {"entries": []}


@router.get("/{entry_id}")
def get_entry(entry_id: uuid.UUID) -> dict[str, str]:
    return {"entry_id": str(entry_id)}


@router.post("/{entry_id}/audio", status_code=status.HTTP_201_CREATED)
async def upload_entry_audio(
    entry_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be audio/*",
        )

    body = await request.body()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio payload is empty")

    requested_name = request.headers.get("x-audio-filename", f"entry-{entry_id}.webm")
    filename = Path(requested_name).name or f"entry-{entry_id}.webm"
    unique = uuid.uuid4()
    storage_key = f"entries/{entry_id}/audio/{unique}-{filename}"

    storage = get_storage_backend()
    storage.put(storage_key, body)

    sha256_hex = hashlib.sha256(body).hexdigest()
    audio_asset = AudioAsset(
        entry_id=entry_id,
        storage_key=storage_key,
        mime_type=content_type,
        size_bytes=len(body),
        sha256_hex=sha256_hex,
    )
    db.add(audio_asset)
    db.commit()
    db.refresh(audio_asset)

    return {
        "asset_id": str(audio_asset.id),
        "entry_id": str(entry_id),
        "storage_key": storage_key,
        "size_bytes": audio_asset.size_bytes,
        "mime_type": audio_asset.mime_type,
    }
