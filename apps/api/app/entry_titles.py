from __future__ import annotations

import uuid

DEFAULT_ENTRY_TITLE_PREFIX = "Entry"
MAX_TRANSCRIPT_TITLE_LENGTH = 80


def fallback_entry_title(entry_id: uuid.UUID) -> str:
    return f"{DEFAULT_ENTRY_TITLE_PREFIX} {str(entry_id)[:8]}"


def deterministic_title_from_transcript(transcript_text: str, *, fallback: str) -> str:
    normalized = " ".join(transcript_text.split())
    if not normalized:
        return fallback

    if len(normalized) <= MAX_TRANSCRIPT_TITLE_LENGTH:
        return normalized

    truncated = normalized[:MAX_TRANSCRIPT_TITLE_LENGTH].rstrip()
    last_space = truncated.rfind(" ")
    if last_space >= MAX_TRANSCRIPT_TITLE_LENGTH // 2:
        truncated = truncated[:last_space]

    return f"{truncated}..."
