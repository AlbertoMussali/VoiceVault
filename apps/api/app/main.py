from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import JSONResponse
from starlette.responses import Response

from app.audit import AuditLoggingMiddleware
from app.auth import authorize_entries_request
from app.db import get_sessionmaker, initialize_schema
from app.demo_seed import seed_demo_account_data
from app.errors import ApiContractError
from app.observability import configure_logging, report_backend_exception, request_context_fields
from app.routers.auth import profile_router, router as auth_router
from app.routes.account import router as account_router
from app.routes.audit import router as audit_router
from app.routes.ask import router as ask_router
from app.routes.brag import router as brag_router
from app.routes.brag_export import router as brag_export_router
from app.routes.entries import router as entries_router
from app.routes.exports import router as exports_router
from app.routes.observability import router as observability_router
from app.routes.search import router as search_router
from app.routes.tags import router as tags_router
from app.security import RateLimitMiddleware, RequestSizeLimitMiddleware
from app.settings import get_settings

request_logger = logging.getLogger("voicevault.request")


def create_app(audit_session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    configure_logging()
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        # TestClient usage may skip startup lifespan hooks unless entered as a
        # context manager, so initialize sqlite schemas eagerly.
        initialize_schema()
        if settings.demo_seed_enabled:
            with get_sessionmaker()() as session:
                seed_demo_account_data(session, settings)
    app = FastAPI(title="VoiceVault API", version=settings.api_version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Audio-Filename", "X-CSRF-Token"],
    )
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_request_size_bytes=settings.max_request_size_bytes,
        max_audio_upload_size_bytes=settings.max_audio_upload_size_bytes,
    )
    app.add_middleware(
        RateLimitMiddleware,
        window_seconds=settings.rate_limit_window_seconds,
        api_limit=settings.rate_limit_requests,
        auth_limit=settings.rate_limit_auth_requests,
    )
    app.add_middleware(AuditLoggingMiddleware, audit_session_factory=audit_session_factory)

    @app.on_event("startup")
    def startup() -> None:
        if settings.demo_seed_enabled and not settings.database_url.startswith("sqlite"):
            with get_sessionmaker()() as session:
                seed_demo_account_data(session, settings)

    @app.middleware("http")
    async def entry_authorization_middleware(request: Request, call_next) -> Response:
        # Let CORS preflight requests flow through without auth checks.
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path.startswith("/api/v1/entries"):
            unauthorized = authorize_entries_request(request, settings)
            if unauthorized is not None:
                return unauthorized
        return await call_next(request)

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next) -> Response:
        start_time = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            status_code = response.status_code if response is not None else 500
            request_logger.info(
                "http_request_completed",
                extra={
                    "fields": {
                        **request_context_fields(request),
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    }
                },
            )

    @app.exception_handler(ApiContractError)
    async def api_contract_error_handler(_: Request, exc: ApiContractError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        report_backend_exception(logging.getLogger("voicevault.backend"), request, exc)
        return JSONResponse(
            status_code=500,
            content={"error_code": "INTERNAL_SERVER_ERROR", "message": "Internal server error."},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": settings.api_version}

    app.include_router(auth_router)
    app.include_router(profile_router)
    app.include_router(account_router)
    app.include_router(audit_router)
    app.include_router(entries_router)
    app.include_router(search_router)
    app.include_router(ask_router)
    app.include_router(tags_router)
    app.include_router(brag_router)
    app.include_router(brag_export_router)
    app.include_router(exports_router)
    app.include_router(observability_router)

    return app


app = create_app()
