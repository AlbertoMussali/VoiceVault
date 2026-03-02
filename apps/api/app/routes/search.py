from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.routes.common import resolve_request_user_id
from app.search_ranking import rank_search_results

router = APIRouter(prefix="/api/v1/search", tags=["search"])

@router.get("")
def search_entries(
    request: Request,
    q: str = Query(min_length=1, max_length=512),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, str | list[dict[str, str | int | float]]]:
    user_id = resolve_request_user_id(request, db)
    ranked = rank_search_results(db=db, user_id=user_id, query=q, limit=limit)
    return {
        "query": q,
        "results": [
            {
                "entry_id": str(item["entry_id"]),
                "transcript_id": str(item["transcript_id"]),
                "snippet_text": item["snippet_text"],
                "start_char": item["start_char"],
                "end_char": item["end_char"],
                "rank": item["rank"],
            }
            for item in ranked
        ],
    }
