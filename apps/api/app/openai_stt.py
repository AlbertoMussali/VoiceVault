from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from urllib import request
from urllib.error import HTTPError, URLError

from app.settings import get_settings


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language_code: str | None


def transcribe_audio_bytes(
    *,
    audio_bytes: bytes,
    mime_type: str,
    filename: str,
) -> TranscriptionResult:
    settings = get_settings()
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for transcription")

    url = f"{settings.openai_base_url.rstrip('/')}/audio/transcriptions"
    boundary = f"voicevault-{uuid.uuid4().hex}"

    payload = _build_multipart_payload(
        boundary=boundary,
        model=settings.openai_stt_model,
        filename=filename,
        mime_type=mime_type,
        audio_bytes=audio_bytes,
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    req = request.Request(url=url, data=payload, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=120) as response:
            body = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI STT failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI STT request failed: {exc.reason}") from exc

    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI STT returned an invalid JSON payload") from exc

    text = str(decoded.get("text", "")).strip()
    if not text:
        raise RuntimeError("OpenAI STT response did not include transcript text")
    language = decoded.get("language")
    language_code = str(language).strip() if language else None
    return TranscriptionResult(text=text, language_code=language_code)


def _build_multipart_payload(
    *,
    boundary: str,
    model: str,
    filename: str,
    mime_type: str,
    audio_bytes: bytes,
) -> bytes:
    filename_base = os.path.basename(filename) or "audio.webm"
    segments = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="model"\r\n\r\n',
        model.encode("utf-8"),
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename_base}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8"),
        audio_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(segments)
