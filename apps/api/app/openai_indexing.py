from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from app.settings import get_settings

ALLOWED_ENTRY_TYPES = {"win", "blocker", "idea", "task", "learning"}
ALLOWED_CONTEXTS = {"work", "life"}
ALLOWED_SENTIMENT_LABELS = {"positive", "neutral", "negative"}
MAX_TAGS = 5
MAX_TAG_LENGTH = 64

_TAG_SANITIZE_RE = re.compile(r"[^a-z0-9_-]+")
_TAG_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class IndexingResult:
    entry_type: str
    context: str
    tags: list[str]
    sentiment_label: str
    sentiment_score: float


def classify_transcript(*, transcript_text: str) -> IndexingResult:
    settings = get_settings()
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for transcript indexing")

    normalized_text = transcript_text.strip()
    if not normalized_text:
        raise RuntimeError("Transcript text is required for indexing")

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.openai_indexing_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You classify a work journal transcript. "
                    "Output JSON only with keys: entry_type, context, tags, sentiment_label, sentiment_score. "
                    "Allowed entry_type: win|blocker|idea|task|learning. "
                    "Allowed context: work|life. "
                    "tags must be an array of short lowercase strings. "
                    "Allowed sentiment_label: positive|neutral|negative. "
                    "sentiment_score must be a number between 0 and 1."
                ),
            },
            {"role": "user", "content": json.dumps({"transcript_text": normalized_text})},
        ],
    }

    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=120) as response:
            body = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"OpenAI indexing failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI indexing request failed: {exc.reason}") from exc

    try:
        decoded = json.loads(body.decode("utf-8"))
        content = decoded["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenAI indexing returned an invalid JSON payload") from exc

    return _normalize_indexing_payload(parsed)


def estimate_indexing_request_bytes(*, transcript_text: str) -> int:
    payload = {"transcript_text": transcript_text}
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _normalize_indexing_payload(payload: Any) -> IndexingResult:
    if not isinstance(payload, dict):
        raise RuntimeError("OpenAI indexing payload must be an object")

    entry_type = str(payload.get("entry_type", "")).strip().lower()
    if entry_type not in ALLOWED_ENTRY_TYPES:
        raise RuntimeError("OpenAI indexing payload includes invalid entry_type")

    context = str(payload.get("context", "")).strip().lower()
    if context not in ALLOWED_CONTEXTS:
        raise RuntimeError("OpenAI indexing payload includes invalid context")

    sentiment_label = str(payload.get("sentiment_label", "")).strip().lower()
    if sentiment_label not in ALLOWED_SENTIMENT_LABELS:
        raise RuntimeError("OpenAI indexing payload includes invalid sentiment_label")

    raw_score = payload.get("sentiment_score")
    try:
        sentiment_score = float(raw_score)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenAI indexing payload includes invalid sentiment_score") from exc
    sentiment_score = max(0.0, min(1.0, sentiment_score))

    raw_tags = payload.get("tags")
    if not isinstance(raw_tags, list):
        raise RuntimeError("OpenAI indexing payload includes invalid tags")
    tags = _normalize_tags(raw_tags)

    return IndexingResult(
        entry_type=entry_type,
        context=context,
        tags=tags,
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
    )


def _normalize_tags(raw_tags: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_tags:
        tag = _normalize_tag(value)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
        if len(normalized) >= MAX_TAGS:
            break
    return normalized


def _normalize_tag(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        return ""
    candidate = _TAG_SPACE_RE.sub(" ", raw_value.strip().lower())
    if not candidate:
        return ""
    candidate = candidate.replace(" ", "-")
    candidate = _TAG_SANITIZE_RE.sub("", candidate).strip("-_")
    if not candidate:
        return ""
    return candidate[:MAX_TAG_LENGTH]
