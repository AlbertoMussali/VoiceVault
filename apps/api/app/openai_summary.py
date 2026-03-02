from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from app.settings import get_settings


@dataclass(frozen=True)
class AskSummarySentence:
    text: str
    snippet_ids: list[str]


def generate_summary_sentences(*, query_text: str, sources: list[dict[str, Any]]) -> list[AskSummarySentence]:
    settings = get_settings()
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for summary generation")

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.openai_summary_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You summarize user-provided sources. "
                    "Output JSON only with key 'sentences' as an array. "
                    "Each item must be an object with non-empty 'text' and non-empty 'snippet_ids' array. "
                    "Only use snippet IDs from provided sources."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query_text": query_text,
                        "sources": sources,
                        "output_schema": {
                            "sentences": [
                                {
                                    "text": "string",
                                    "snippet_ids": ["source-snippet-id"],
                                }
                            ]
                        },
                    }
                ),
            },
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
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI summary failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI summary request failed: {exc.reason}") from exc

    try:
        decoded = json.loads(body.decode("utf-8"))
        content = decoded["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenAI summary returned an invalid JSON payload") from exc

    sentences = parsed.get("sentences")
    if not isinstance(sentences, list):
        raise RuntimeError("OpenAI summary payload missing sentences array")

    normalized: list[AskSummarySentence] = []
    for sentence in sentences:
        if not isinstance(sentence, dict):
            raise RuntimeError("OpenAI summary sentence must be an object")
        text = str(sentence.get("text", "")).strip()
        raw_snippet_ids = sentence.get("snippet_ids")
        if not text:
            raise RuntimeError("OpenAI summary sentence text is required")
        if not isinstance(raw_snippet_ids, list):
            raise RuntimeError("OpenAI summary sentence snippet_ids must be an array")
        snippet_ids = [str(value).strip() for value in raw_snippet_ids if str(value).strip()]
        if not snippet_ids:
            raise RuntimeError("OpenAI summary sentence must include at least one snippet_id")
        normalized.append(AskSummarySentence(text=text, snippet_ids=snippet_ids))

    if not normalized:
        raise RuntimeError("OpenAI summary produced no sentences")

    return normalized
