from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    build_access_token,
    create_refresh_session,
    decode_token,
    generate_csrf_token,
    hash_password,
    hash_token,
    normalize_email,
    utcnow,
    validate_password_policy,
    verify_password,
)
from app.db import get_db
from app.models import RefreshSession, User
from app.routes.common import resolve_request_user_id
from app.settings import Settings, get_settings


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
profile_router = APIRouter(prefix="/api/v1", tags=["auth"])


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)


class TokenRequest(BaseModel):
    refresh_token: str | None = None


class AuthTokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    email: str


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )


def _set_auth_cookies(response: Response, refresh_token: str, settings: Settings) -> None:
    max_age = settings.refresh_token_ttl_days * 24 * 60 * 60
    csrf_token = generate_csrf_token()
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=max_age,
        expires=max_age,
        path="/api/v1/auth",
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=max_age,
        expires=max_age,
        path="/api/v1/auth",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_refresh_cookie_name,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/api/v1/auth",
    )
    response.delete_cookie(
        key=settings.auth_csrf_cookie_name,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/api/v1/auth",
    )


def _csrf_header_valid(request: Request, settings: Settings) -> bool:
    csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
    csrf_header = request.headers.get("X-CSRF-Token")
    if not csrf_cookie or not csrf_header:
        return False
    return secrets.compare_digest(csrf_cookie, csrf_header)


def _resolve_refresh_token(payload: TokenRequest | None, request: Request, settings: Settings) -> tuple[str, bool]:
    if payload is not None and payload.refresh_token:
        return payload.refresh_token, False
    cookie_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if cookie_token:
        return cookie_token, True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")


@router.post("/signup", response_model=AuthTokensResponse, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthTokensResponse:
    email = normalize_email(payload.email)
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered.")
    password_error = validate_password_policy(payload.password, settings)
    if password_error is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=password_error)

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    refresh_token, _ = create_refresh_session(db, user, settings)
    db.commit()
    _set_auth_cookies(response, refresh_token, settings)

    return AuthTokensResponse(
        access_token=build_access_token(user.id, settings),
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=AuthTokensResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthTokensResponse:
    email = normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise _invalid_credentials()

    refresh_token, _ = create_refresh_session(db, user, settings)
    db.commit()
    _set_auth_cookies(response, refresh_token, settings)
    return AuthTokensResponse(
        access_token=build_access_token(user.id, settings),
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=AuthTokensResponse)
def refresh(
    request: Request,
    response: Response,
    payload: TokenRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthTokensResponse:
    try:
        refresh_token, used_cookie = _resolve_refresh_token(payload, request, settings)
        if used_cookie and not _csrf_header_valid(request, settings):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")
        decoded = decode_token(refresh_token, settings, expected_type="refresh")
        user_id = uuid.UUID(str(decoded["sub"]))
        session_id = uuid.UUID(str(decoded["sid"]))
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.") from None

    session = db.get(RefreshSession, session_id)
    now = utcnow()
    if (
        session is None
        or session.user_id != user_id
        or session.token_hash != hash_token(refresh_token)
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    session.revoked_at = now
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    new_refresh_token, _ = create_refresh_session(db, user, settings)
    db.commit()
    _set_auth_cookies(response, new_refresh_token, settings)

    return AuthTokensResponse(
        access_token=build_access_token(user.id, settings),
        refresh_token=new_refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(
    request: Request,
    payload: TokenRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        refresh_token, used_cookie = _resolve_refresh_token(payload, request, settings)
        if used_cookie and not _csrf_header_valid(request, settings):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")
        decoded = decode_token(refresh_token, settings, expected_type="refresh")
        session_id = uuid.UUID(str(decoded["sid"]))
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.") from None

    session = db.get(RefreshSession, session_id)
    if session is None or session.token_hash != hash_token(refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    if session.revoked_at is None:
        session.revoked_at = utcnow()
        db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_auth_cookies(response, settings)
    return response


@profile_router.get("/me", response_model=MeResponse)
def get_me(
    request: Request,
    db: Session = Depends(get_db),
) -> MeResponse:
    user_id = resolve_request_user_id(request, db)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return MeResponse(id=str(user.id), email=user.email)
