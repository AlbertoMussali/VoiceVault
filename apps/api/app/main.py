from __future__ import annotations

from fastapi import FastAPI

from app.routers.auth import router as auth_router
from app.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VoiceVault API", version=settings.api_version)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": settings.api_version}

    app.include_router(auth_router)

    return app


app = create_app()
