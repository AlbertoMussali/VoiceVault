from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ask_outbound import build_provider_snippet, is_sensitive_entry_tag
from app.db import get_db
from app.jobs import run_ask_summary_job
from app.models import AskQuery, AskResult, Entry, EntryTag, Tag
from app.routes.common import resolve_request_user_id
from app.search_ranking import rank_search_results
from app.settings import get_summary_generation_disabled_reason, is_summary_generation_enabled

router = APIRouter(prefix="/api/v1/ask", tags=["ask"])


class AskQueryRequest(BaseModel):
    query_text: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=8, ge=5, le=12)


class AskSummarizeRequest(BaseModel):
    snippet_ids: list[str] | None = Field(default=None, min_length=1)


def _serialize_result(result: AskResult) -> dict[str, str | int | float]:
    rank_value = float(result.rank) if isinstance(result.rank, Decimal) else float(result.rank)
    return {
        "id": str(result.id),
        "snippet_id": str(result.id),
        "entry_id": str(result.entry_id),
        "transcript_id": str(result.transcript_id),
        "snippet_text": result.snippet_text,
        "start_char": result.start_char,
        "end_char": result.end_char,
        "rank": rank_value,
        "order": result.result_order,
    }


@router.post("/query")
def create_ask_query(
    payload: AskQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str | list[dict[str, str | int | float]]]:
    user_id = resolve_request_user_id(request, db)
    query_text = payload.query_text.strip()

    ask_query = AskQuery(
        user_id=user_id,
        query_text=query_text,
        result_limit=payload.limit,
        status="done",
    )
    db.add(ask_query)
    db.flush()

    ranked = rank_search_results(db=db, user_id=user_id, query=query_text, limit=payload.limit)
    persisted_results: list[AskResult] = []
    for idx, item in enumerate(ranked, start=1):
        ask_result = AskResult(
            ask_query_id=ask_query.id,
            entry_id=item["entry_id"],
            transcript_id=item["transcript_id"],
            snippet_text=item["snippet_text"],
            start_char=item["start_char"],
            end_char=item["end_char"],
            rank=item["rank"],
            result_order=idx,
        )
        db.add(ask_result)
        persisted_results.append(ask_result)

    db.commit()

    return {
        "query_id": str(ask_query.id),
        "query_text": ask_query.query_text,
        "sources": [_serialize_result(result) for result in persisted_results],
    }


@router.post("/{query_id}/summarize")
def summarize_ask_query(
    query_id: uuid.UUID,
    payload: AskSummarizeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    summary_disabled_reason = get_summary_generation_disabled_reason()
    if not is_summary_generation_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=summary_disabled_reason)

    user_id = resolve_request_user_id(request, db)
    ask_query = db.get(AskQuery, query_id)
    if ask_query is None or ask_query.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ask query not found")

    try:
        result = run_ask_summary_job(str(query_id), payload.snippet_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return result


@router.get("/{query_id}")
def get_ask_query(
    query_id: uuid.UUID,
    request: Request,
    redact: bool = Query(default=True),
    mask: bool = Query(default=True),
    include_sensitive: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    summary_disabled_reason = get_summary_generation_disabled_reason()
    user_id = resolve_request_user_id(request, db)

    ask_query = (
        db.execute(
            select(AskQuery).where(
                AskQuery.id == query_id,
                AskQuery.user_id == user_id,
            )
        )
        .scalar_one_or_none()
    )
    if ask_query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ask query not found")

    results = (
        db.execute(
            select(AskResult)
            .where(AskResult.ask_query_id == ask_query.id)
            .order_by(AskResult.result_order.asc(), AskResult.created_at.asc())
        )
        .scalars()
        .all()
    )

    entry_ids = [result.entry_id for result in results]
    entry_by_id = {
        entry.id: entry
        for entry in (
            db.execute(select(Entry).where(Entry.id.in_(entry_ids))).scalars().all() if entry_ids else []
        )
    }

    sensitive_entries: set[uuid.UUID] = set()
    if entry_ids:
        tag_rows = db.execute(
            select(EntryTag.entry_id, Tag.normalized_name)
            .join(Tag, Tag.id == EntryTag.tag_id)
            .where(EntryTag.entry_id.in_(entry_ids))
        ).all()
        for entry_id, normalized_name in tag_rows:
            if normalized_name and is_sensitive_entry_tag(normalized_name):
                sensitive_entries.add(entry_id)

    snippets: list[dict[str, str | int | bool | uuid.UUID | None]] = []
    for result in results:
        entry = entry_by_id.get(result.entry_id)
        occurred_at = (entry.occurred_at or entry.created_at) if entry is not None else None
        is_sensitive = result.entry_id in sensitive_entries
        if is_sensitive and not include_sensitive:
            continue
        snippets.append(
            build_provider_snippet(
                result=result,
                occurred_at=occurred_at,
                is_sensitive=is_sensitive,
                redact=redact,
                mask=mask,
            )
        )

    return {
        "query_id": str(ask_query.id),
        "query_text": ask_query.query_text,
        "controls": {
            "redact": redact,
            "mask": mask,
            "include_sensitive": include_sensitive,
        },
        "snippet_count": len(snippets),
        "snippets": snippets,
        "sources": [_serialize_result(result) for result in results],
        "summary_status": ask_query.summary_status,
        "summary": ask_query.summary_json,
        "summary_error": ask_query.summary_error,
        "summary_available": summary_disabled_reason is None,
        "summary_unavailable_reason": summary_disabled_reason,
    }
