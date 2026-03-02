from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditLog
from app.routes.common import resolve_request_user_id

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

_BLOCKED_METADATA_KEYS = {
    "body",
    "content",
    "prompt",
    "query_text",
    "quote_text",
    "response",
    "snippet_text",
    "text",
    "transcript_text",
}


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, inner_value in value.items():
            if not isinstance(key, str):
                continue
            normalized_key = key.strip().lower()
            if normalized_key in _BLOCKED_METADATA_KEYS or normalized_key.endswith("_text"):
                continue
            sanitized[key] = _sanitize_metadata(inner_value)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    return value


def _serialize_audit_row(row: AuditLog) -> dict[str, Any]:
    created_at = row.created_at
    if isinstance(created_at, datetime):
        created_at_value = created_at.isoformat()
    else:
        created_at_value = str(created_at)

    return {
        "id": row.id,
        "event_type": row.event_type,
        "entry_id": str(row.entry_id) if isinstance(row.entry_id, uuid.UUID) else None,
        "metadata": _sanitize_metadata(row.metadata_json or {}),
        "created_at": created_at_value,
    }


@router.get("")
def list_audit_events(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None, min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user_id = resolve_request_user_id(request, db)

    filters = [AuditLog.user_id == user_id]
    if event_type is not None:
        filters.append(AuditLog.event_type == event_type.strip())

    total = db.scalar(select(func.count(AuditLog.id)).where(*filters)) or 0
    rows = (
        db.execute(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    return {
        "items": [_serialize_audit_row(row) for row in rows],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": int(total),
            "has_more": (offset + len(rows)) < int(total),
        },
    }
