from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.jobs import enqueue_registered_job
from app.models import ExportJob
from app.routes.common import resolve_request_user_id
from app.storage import get_storage_backend
from app.storage.base import StorageNotFoundError

router = APIRouter(prefix="/api/v1/brag/export", tags=["brag"])


def _serialize_export_job(job: ExportJob) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(job.id),
        "status": job.status,
        "export_type": job.export_type,
        "format": job.format,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
    if job.metadata_json:
        payload["metadata"] = job.metadata_json
    if job.error_message:
        payload["error_message"] = job.error_message
    if job.status == "completed" and job.artifact_storage_key:
        payload["download_url"] = f"/api/v1/brag/export/{job.id}/download"
    return payload


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_brag_export_job(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = resolve_request_user_id(request, db)
    export_job = ExportJob(
        user_id=user_id,
        export_type="brag_text_v1",
        status="queued",
        format="txt",
    )
    db.add(export_job)
    db.commit()
    db.refresh(export_job)

    try:
        rq_job = enqueue_registered_job("brag.export_text_v1", str(export_job.id))
        export_job.metadata_json = {"rq_job_id": str(rq_job.id)}
        db.commit()
        db.refresh(export_job)
    except Exception:
        export_job.status = "failed"
        export_job.error_message = "Failed to enqueue export job"
        db.commit()
        db.refresh(export_job)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue export job",
        )

    return _serialize_export_job(export_job)


@router.get("/{export_job_id}")
def get_brag_export_job(
    export_job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = resolve_request_user_id(request, db)
    export_job = db.get(ExportJob, export_job_id)
    if export_job is None or export_job.user_id != user_id or export_job.export_type != "brag_text_v1":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    return _serialize_export_job(export_job)


@router.get("/{export_job_id}/download")
def download_brag_export_job(
    export_job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    user_id = resolve_request_user_id(request, db)
    export_job = db.get(ExportJob, export_job_id)
    if export_job is None or export_job.user_id != user_id or export_job.export_type != "brag_text_v1":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    if export_job.status != "completed" or not export_job.artifact_storage_key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Export artifact is not ready")

    try:
        report_bytes = get_storage_backend().get(export_job.artifact_storage_key)
    except StorageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export artifact not found") from exc

    return Response(
        content=report_bytes,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="voicevault-brag-report.txt"'},
    )
