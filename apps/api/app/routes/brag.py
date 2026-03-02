from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BragBullet, BragBulletCitation, Citation, Entry, Transcript
from app.routes.common import resolve_request_user_id

router = APIRouter(prefix="/api/v1/brag/bullets", tags=["brag"])

ALLOWED_BUCKETS = {"impact", "execution", "leadership", "collaboration", "growth"}


class BragBulletCreateRequest(BaseModel):
    bucket: str = Field(min_length=1, max_length=32)
    bullet_text: str = Field(min_length=1, max_length=4000)


class BragBulletUpdateRequest(BaseModel):
    bucket: str | None = Field(default=None, min_length=1, max_length=32)
    bullet_text: str | None = Field(default=None, min_length=1, max_length=4000)


class CitationCreateRequest(BaseModel):
    entry_id: uuid.UUID
    transcript_version: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=1)


def _normalize_bucket(bucket: str) -> str:
    normalized = bucket.strip().lower()
    if normalized not in ALLOWED_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bucket must be one of: {', '.join(sorted(ALLOWED_BUCKETS))}",
        )
    return normalized


def _serialize_brag_bullet(bullet: BragBullet) -> dict[str, str]:
    return {
        "id": str(bullet.id),
        "bucket": bullet.bucket,
        "bullet_text": bullet.bullet_text,
        "created_at": bullet.created_at.isoformat(),
        "updated_at": bullet.updated_at.isoformat(),
    }


def _serialize_citation(
    *,
    citation: Citation,
    bullet_id: uuid.UUID,
    entry_id: uuid.UUID,
) -> dict[str, str | int]:
    return {
        "id": str(citation.id),
        "bullet_id": str(bullet_id),
        "entry_id": str(entry_id),
        "transcript_id": str(citation.transcript_id),
        "transcript_version": citation.transcript_version,
        "start_char": citation.start_char,
        "end_char": citation.end_char,
        "quote_text": citation.quote_text,
        "snippet_hash": citation.snippet_hash,
        "created_at": citation.created_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_brag_bullet(
    payload: BragBulletCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = resolve_request_user_id(request, db)
    bullet = BragBullet(
        user_id=user_id,
        bucket=_normalize_bucket(payload.bucket),
        bullet_text=payload.bullet_text.strip(),
    )
    db.add(bullet)
    db.commit()
    db.refresh(bullet)
    return _serialize_brag_bullet(bullet)


@router.get("")
def list_brag_bullets(
    request: Request,
    db: Session = Depends(get_db),
    bucket: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict[str, list[dict[str, str]]]:
    user_id = resolve_request_user_id(request, db)
    statement = select(BragBullet).where(BragBullet.user_id == user_id)
    if bucket is not None:
        statement = statement.where(BragBullet.bucket == _normalize_bucket(bucket))
    bullets = db.execute(statement.order_by(BragBullet.created_at.desc())).scalars().all()
    return {"bullets": [_serialize_brag_bullet(bullet) for bullet in bullets]}


@router.get("/{bullet_id}")
def get_brag_bullet(
    bullet_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = resolve_request_user_id(request, db)
    bullet = db.get(BragBullet, bullet_id)
    if bullet is None or bullet.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brag bullet not found")
    return _serialize_brag_bullet(bullet)


@router.patch("/{bullet_id}")
def update_brag_bullet(
    bullet_id: uuid.UUID,
    payload: BragBulletUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = resolve_request_user_id(request, db)
    bullet = db.get(BragBullet, bullet_id)
    if bullet is None or bullet.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brag bullet not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided for update")

    if "bucket" in updates and updates["bucket"] is not None:
        bullet.bucket = _normalize_bucket(updates["bucket"])
    if "bullet_text" in updates and updates["bullet_text"] is not None:
        bullet.bullet_text = updates["bullet_text"].strip()

    db.commit()
    db.refresh(bullet)
    return _serialize_brag_bullet(bullet)


@router.delete("/{bullet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brag_bullet(
    bullet_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    user_id = resolve_request_user_id(request, db)
    bullet = db.get(BragBullet, bullet_id)
    if bullet is None or bullet.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brag bullet not found")
    db.delete(bullet)
    db.commit()


@router.post("/{bullet_id}/citations", status_code=status.HTTP_201_CREATED)
def create_brag_bullet_citation(
    bullet_id: uuid.UUID,
    payload: CitationCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    user_id = resolve_request_user_id(request, db)

    bullet = db.get(BragBullet, bullet_id)
    if bullet is None or bullet.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brag bullet not found")

    transcript = (
        db.execute(
            select(Transcript)
            .join(Entry, Entry.id == Transcript.entry_id)
            .where(
                Entry.id == payload.entry_id,
                Entry.user_id == user_id,
                Transcript.version == payload.transcript_version,
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript version not found for entry")

    transcript_length = len(transcript.transcript_text)
    if payload.end_char <= payload.start_char:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_char must be greater than start_char")
    if payload.end_char > transcript_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Offsets must be within transcript bounds for the selected version",
        )

    quote_text = transcript.transcript_text[payload.start_char : payload.end_char]
    citation = Citation(
        user_id=user_id,
        transcript_id=transcript.id,
        transcript_version=transcript.version,
        start_char=payload.start_char,
        end_char=payload.end_char,
        quote_text=quote_text,
        snippet_hash=hashlib.sha256(quote_text.encode("utf-8")).hexdigest(),
    )
    db.add(citation)
    db.flush()
    db.add(BragBulletCitation(bullet_id=bullet.id, citation_id=citation.id))
    db.commit()
    db.refresh(citation)
    return _serialize_citation(citation=citation, bullet_id=bullet.id, entry_id=payload.entry_id)
