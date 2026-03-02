from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import UTC, datetime
from typing import Any

from fastapi import Request


class JsonLogFormatter(logging.Formatter):
    """Emit logs as newline-delimited JSON for operator tooling."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        extra_fields = getattr(record, "fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging() -> None:
    """Configure process logging with JSON output."""
    root_logger = logging.getLogger()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def request_context_fields(request: Request) -> dict[str, str]:
    request_id = request.headers.get("x-request-id")
    if not request_id:
        request_id = "generated-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
    }


def report_backend_exception(logger: logging.Logger, request: Request, exc: Exception) -> None:
    logger.error(
        "backend_unhandled_exception",
        extra={
            "fields": {
                **request_context_fields(request),
                "exception_type": exc.__class__.__name__,
                "stacktrace": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            }
        },
    )
