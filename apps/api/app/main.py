from __future__ import annotations

from fastapi import FastAPI
from fastapi.requests import Request
from starlette.responses import Response

from app.auth import authorize_bearer_token
from app.routes.entries import router as entries_router
from app.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VoiceVault API", version=settings.api_version)

    @app.middleware("http")
    async def entry_authorization_middleware(request: Request, call_next) -> Response:
        if request.url.path.startswith("/api/v1/entries"):
            unauthorized = authorize_bearer_token(request, settings.entry_auth_token)
            if unauthorized is not None:
                return unauthorized
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": settings.api_version}

    app.include_router(entries_router)

    return app


app = create_app()
