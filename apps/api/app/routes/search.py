from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Entry, Transcript
from app.routes.common import resolve_request_user_id

router = APIRouter(prefix="/api/v1/search", tags=["search"])

_TERM_PATTERN = re.compile(r"[A-Za-z0-9']+")
_SNIPPET_CONTEXT_CHARS = 64
_MAX_CANDIDATES = 200


def _normalize_terms(query: str) -> list[str]:
    return list(dict.fromkeys(term.lower() for term in _TERM_PATTERN.findall(query)))


def _first_match_offsets(text: str, phrase: str, terms: list[str]) -> tuple[int, int] | None:
    lowered = text.lower()
    if phrase:
        phrase_index = lowered.find(phrase)
        if phrase_index >= 0:
            return phrase_index, phrase_index + len(phrase)

    for term in terms:
        term_index = lowered.find(term)
        if term_index >= 0:
            return term_index, term_index + len(term)
    return None


def _score_text(text: str, phrase: str, terms: list[str]) -> float:
    lowered = text.lower()
    phrase_hits = lowered.count(phrase) if phrase else 0
    term_hits = sum(lowered.count(term) for term in terms)
    distinct_hits = sum(1 for term in terms if term in lowered)
    return float((phrase_hits * 100) + (distinct_hits * 10) + term_hits)


def _build_snippet(text: str, start_char: int, end_char: int) -> str:
    snippet_start = max(0, start_char - _SNIPPET_CONTEXT_CHARS)
    snippet_end = min(len(text), end_char + _SNIPPET_CONTEXT_CHARS)
    snippet = text[snippet_start:snippet_end].strip()
    if snippet_start > 0:
        snippet = f"...{snippet}"
    if snippet_end < len(text):
        snippet = f"{snippet}..."
    return snippet


@router.get("")
def search_entries(
    request: Request,
    q: str = Query(min_length=1, max_length=512),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, str | list[dict[str, str | int | float]]]:
    user_id = resolve_request_user_id(request, db)
    phrase = q.strip().lower()
    terms = _normalize_terms(q)
    filters = [Transcript.transcript_text.ilike(f"%{phrase}%")] if phrase else []
    filters.extend(Transcript.transcript_text.ilike(f"%{term}%") for term in terms)
    if not filters:
        return {"query": q, "results": []}

    rows = (
        db.execute(
            select(Transcript.id, Transcript.entry_id, Transcript.transcript_text)
            .join(Entry, Entry.id == Transcript.entry_id)
            .where(
                Entry.user_id == user_id,
                Transcript.is_current.is_(True),
                or_(*filters),
            )
            .limit(_MAX_CANDIDATES)
        )
        .all()
    )

    ranked: list[dict[str, str | int | float]] = []
    for transcript_id, entry_id, transcript_text in rows:
        offsets = _first_match_offsets(transcript_text, phrase, terms)
        if offsets is None:
            continue
        start_char, end_char = offsets
        score = _score_text(transcript_text, phrase, terms)
        ranked.append(
            {
                "entry_id": str(entry_id),
                "transcript_id": str(transcript_id),
                "snippet_text": _build_snippet(transcript_text, start_char, end_char),
                "start_char": start_char,
                "end_char": end_char,
                "rank": score,
            }
        )

    ranked.sort(key=lambda item: (-float(item["rank"]), int(item["start_char"]), str(item["transcript_id"])))
    return {"query": q, "results": ranked[:limit]}
