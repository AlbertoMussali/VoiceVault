from __future__ import annotations

from datetime import datetime
import re
import uuid

from app.models import AskResult

_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b")
_LONG_NUMBER_PATTERN = re.compile(r"\b\d{9,}\b")
_CARD_PATTERN = re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b")
_URL_PATTERN = re.compile(r"https?://[^\s]+", flags=re.IGNORECASE)

SENSITIVE_TAG_TOKENS = frozenset(
    {
        "sensitive",
        "work_sensitive",
        "work-sensitive",
        "confidential",
        "private",
        "raw_only",
        "raw-only",
    }
)


def apply_outbound_transforms(text: str, *, redact: bool, mask: bool) -> str:
    transformed = text
    if redact:
        transformed = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", transformed)
        transformed = _PHONE_PATTERN.sub("[REDACTED_PHONE]", transformed)
    if mask:
        transformed = _LONG_NUMBER_PATTERN.sub(lambda value: f"***{value.group(0)[-4:]}", transformed)
        transformed = _CARD_PATTERN.sub("[MASKED_CARD]", transformed)
        transformed = _URL_PATTERN.sub("[MASKED_URL]", transformed)
    return transformed


def is_sensitive_entry_tag(tag_name: str) -> bool:
    return tag_name.strip().lower() in SENSITIVE_TAG_TOKENS


def build_snippet_id(result: AskResult) -> str:
    return f"{result.entry_id}:{result.start_char}:{result.end_char}:{result.result_order}"


def to_iso_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.date().isoformat()


def build_provider_snippet(
    *,
    result: AskResult,
    occurred_at: datetime | None,
    is_sensitive: bool,
    redact: bool,
    mask: bool,
) -> dict[str, str | int | bool | uuid.UUID | None]:
    return {
        "snippet_id": build_snippet_id(result),
        "entry_id": result.entry_id,
        "transcript_id": result.transcript_id,
        "start_char": result.start_char,
        "end_char": result.end_char,
        "occurred_at": to_iso_date(occurred_at),
        "is_sensitive": is_sensitive,
        "snippet_text": apply_outbound_transforms(result.snippet_text, redact=redact, mask=mask),
    }
