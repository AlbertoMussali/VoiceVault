from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_sessionmaker, initialize_schema
from app.models import AudioAsset, Entry
from app.settings import get_settings
from app.storage import LocalBlobStorage


def get_db_session() -> Session:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VoiceVault API", version=settings.api_version)
    storage = LocalBlobStorage(settings.audio_storage_root)

    @app.on_event("startup")
    def startup() -> None:
        initialize_schema()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": settings.api_version}

    @app.post("/entries/{entry_id}/audio", status_code=status.HTTP_201_CREATED)
    async def upload_entry_audio(
        entry_id: int,
        request: Request,
        db: Session = Depends(get_db_session),
    ) -> dict[str, int | str]:
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
        storage_key = storage.store_entry_audio(entry_id=entry_id, content=body, filename=filename)

        audio_asset = AudioAsset(
            entry_id=entry_id,
            storage_key=storage_key,
            original_filename=filename,
            content_type=content_type,
            byte_size=len(body),
        )
        db.add(audio_asset)
        db.commit()
        db.refresh(audio_asset)

        return {
            "asset_id": audio_asset.id,
            "entry_id": entry_id,
            "storage_key": storage_key,
            "byte_size": audio_asset.byte_size,
            "content_type": audio_asset.content_type,
        }

    return app


app = create_app()
