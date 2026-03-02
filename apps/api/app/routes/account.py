from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import utcnow, verify_password
from app.db import get_db
from app.errors import ApiContractError, ErrorType
from app.models import AuditLog, AudioAsset, Entry, ExportJob, RefreshSession, User
from app.routes.common import resolve_request_user_id
from app.storage import get_storage_backend
from app.storage.base import StorageNotFoundError

router = APIRouter(prefix="/api/v1/account", tags=["account"])


class AccountDeleteRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_account(
    payload: AccountDeleteRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    user_id = resolve_request_user_id(request, db)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password.")

    audio_storage_keys = (
        db.execute(
            select(AudioAsset.storage_key)
            .join(Entry, AudioAsset.entry_id == Entry.id)
            .where(Entry.user_id == user_id)
        )
        .scalars()
        .all()
    )
    export_storage_keys = (
        db.execute(
            select(ExportJob.artifact_storage_key).where(
                ExportJob.user_id == user_id,
                ExportJob.artifact_storage_key.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    blob_storage_keys = [*audio_storage_keys, *(key for key in export_storage_keys if key)]

    storage = get_storage_backend()
    for storage_key in blob_storage_keys:
        try:
            storage.delete(storage_key)
        except StorageNotFoundError:
            continue
        except OSError as exc:
            raise ApiContractError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error_code="ACCOUNT_DELETE_STORAGE_UNAVAILABLE",
                message="Account deletion could not remove stored blobs. Retry deletion.",
                error_type=ErrorType.TRANSIENT,
            ) from exc

    try:
        now = utcnow()
        revocation_result = db.execute(
            update(RefreshSession)
            .where(
                RefreshSession.user_id == user_id,
                RefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        revoked_session_count = int(revocation_result.rowcount or 0)

        db.delete(user)
        db.add(
            AuditLog(
                user_id=None,
                entry_id=None,
                event_type="account_deleted",
                metadata_json={
                    "audio_blob_count": len(audio_storage_keys),
                    "artifact_blob_count": len(export_storage_keys),
                    "revoked_session_count": revoked_session_count,
                },
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise ApiContractError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="ACCOUNT_DELETE_FAILED",
            message="Account deletion failed due to temporary database issues. Retry deletion.",
            error_type=ErrorType.TRANSIENT,
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
