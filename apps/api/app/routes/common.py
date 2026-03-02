from __future__ import annotations

import uuid

from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.auth import decode_token
from app.models import User
from app.settings import get_settings


def extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def resolve_request_user_id(request: Request, db: Session) -> uuid.UUID:
    settings = get_settings()
    token = extract_bearer_token(request)

    if token == settings.entry_auth_token:
        requested_user_id = request.headers.get("x-user-id")
        if requested_user_id is not None:
            try:
                user_id = uuid.UUID(requested_user_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="x-user-id must be a valid UUID",
                ) from exc

            if db.get(User, user_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            return user_id

        first_user_id = db.query(User.id).order_by(User.created_at.asc()).limit(1).scalar()
        if first_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No users available for static entry token; provide x-user-id",
            )
        return first_user_id

    try:
        payload = decode_token(token, settings, expected_type="access")
        user_id = uuid.UUID(str(payload["sub"]))
    except (InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if db.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id
