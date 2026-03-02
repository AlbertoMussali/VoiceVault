from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.observability import request_context_fields

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])
logger = logging.getLogger("voicevault.observability")


class FrontendErrorReportRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    stack: str | None = Field(default=None, max_length=20000)
    source: str = Field(default="window.error", max_length=128)
    level: str = Field(default="error", max_length=32)
    url: str | None = Field(default=None, max_length=2000)
    user_agent: str | None = Field(default=None, max_length=512)


@router.post("/frontend-errors", status_code=status.HTTP_202_ACCEPTED)
def report_frontend_error(payload: FrontendErrorReportRequest, request: Request) -> dict[str, str]:
    logger.error(
        "frontend_error_reported",
        extra={
            "fields": {
                **request_context_fields(request),
                "source": payload.source,
                "level": payload.level,
                "message": payload.message,
                "url": payload.url,
                "user_agent": payload.user_agent,
                "stack": payload.stack,
            }
        },
    )
    return {"status": "accepted"}
