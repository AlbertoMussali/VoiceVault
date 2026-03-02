from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Tag
from app.routes.common import resolve_request_user_id

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])

_WHITESPACE_RE = re.compile(r"\s+")


class TagUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


def normalize_tag_name(name: str) -> tuple[str, str]:
    display_name = _WHITESPACE_RE.sub(" ", name.strip())
    if not display_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag name must not be empty")
    return display_name, display_name.lower()


def serialize_tag(tag: Tag) -> dict[str, str]:
    return {"id": str(tag.id), "name": tag.name, "normalized_name": tag.normalized_name}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagUpsertRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = resolve_request_user_id(request, db)
    name, normalized_name = normalize_tag_name(payload.name)
    tag = Tag(user_id=user_id, name=name, normalized_name=normalized_name)
    db.add(tag)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tag already exists") from exc
    db.refresh(tag)
    return serialize_tag(tag)


@router.get("")
def list_tags(
    request: Request,
    db: Session = Depends(get_db),
    query: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, list[dict[str, str]]]:
    user_id = resolve_request_user_id(request, db)
    statement = select(Tag).where(Tag.user_id == user_id)

    if query is not None:
        _, normalized_query = normalize_tag_name(query)
        statement = statement.where(Tag.normalized_name.contains(normalized_query))

    tags = db.execute(statement.order_by(Tag.name.asc()).limit(limit)).scalars().all()
    return {"tags": [serialize_tag(tag) for tag in tags]}


@router.get("/autocomplete")
def autocomplete_tags(
    request: Request,
    db: Session = Depends(get_db),
    query: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=10, ge=1, le=25),
) -> dict[str, list[dict[str, str]]]:
    user_id = resolve_request_user_id(request, db)
    _, normalized_query = normalize_tag_name(query)
    tags = (
        db.execute(
            select(Tag)
            .where(Tag.user_id == user_id, Tag.normalized_name.startswith(normalized_query))
            .order_by(Tag.name.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {"tags": [serialize_tag(tag) for tag in tags]}


@router.get("/{tag_id}")
def get_tag(
    tag_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = resolve_request_user_id(request, db)
    tag = db.get(Tag, tag_id)
    if tag is None or tag.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    return serialize_tag(tag)


@router.patch("/{tag_id}")
def update_tag(
    tag_id: uuid.UUID,
    payload: TagUpsertRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = resolve_request_user_id(request, db)
    tag = db.get(Tag, tag_id)
    if tag is None or tag.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    name, normalized_name = normalize_tag_name(payload.name)
    tag.name = name
    tag.normalized_name = normalized_name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tag already exists") from exc
    db.refresh(tag)
    return serialize_tag(tag)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_tag(
    tag_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    user_id = resolve_request_user_id(request, db)
    tag = db.get(Tag, tag_id)
    if tag is None or tag.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    db.delete(tag)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
