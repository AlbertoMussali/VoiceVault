from __future__ import annotations

from fastapi import Request
from starlette.responses import JSONResponse, Response


def _unauthorized_response() -> Response:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def authorize_bearer_token(request: Request, expected_token: str) -> Response | None:
    """
    Validate a Bearer token from Authorization header.

    Returns None when authorization succeeds, otherwise a 401 response.
    """
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or token != expected_token:
        return _unauthorized_response()
    return None
