from __future__ import annotations

from datetime import datetime, timezone
import os
import pathlib
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.db import get_sessionmaker, reset_engine_cache
from app.jobs import run_brag_text_export_job
from app.main import create_app
from app.models import BragBullet, ExportJob, User
from app.settings import get_settings
from app.storage import get_storage_backend


class BragExportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")
        self.original_storage_local_root = os.environ.get("STORAGE_LOCAL_ROOT")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/brag_export_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"
        os.environ["STORAGE_LOCAL_ROOT"] = f"{self.temp_dir.name}/storage"

        get_settings.cache_clear()
        reset_engine_cache()
        get_storage_backend.cache_clear()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("brag-export-owner@example.com")

    def tearDown(self) -> None:
        self.client.close()

        if self.original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_database_url

        if self.original_entry_auth_token is None:
            os.environ.pop("ENTRY_AUTH_TOKEN", None)
        else:
            os.environ["ENTRY_AUTH_TOKEN"] = self.original_entry_auth_token

        if self.original_storage_local_root is None:
            os.environ.pop("STORAGE_LOCAL_ROOT", None)
        else:
            os.environ["STORAGE_LOCAL_ROOT"] = self.original_storage_local_root

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()
        get_storage_backend.cache_clear()

    def _auth_headers(self, user_id: uuid.UUID | None = None) -> dict[str, str]:
        return {
            "Authorization": "Bearer entry-secret-test",
            "x-user-id": str(user_id or self.user_id),
        }

    def _create_user(self, email: str) -> uuid.UUID:
        session = get_sessionmaker()()
        try:
            user = User(email=email, password_hash="not-a-real-hash")
            session.add(user)
            session.commit()
            session.refresh(user)
            return user.id
        finally:
            session.close()

    def _create_bullet(
        self,
        user_id: uuid.UUID,
        bucket: str,
        text: str,
        created_at: datetime,
    ) -> None:
        session = get_sessionmaker()()
        try:
            session.add(
                BragBullet(
                    user_id=user_id,
                    bucket=bucket,
                    bullet_text=text,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.commit()
        finally:
            session.close()

    def test_brag_export_job_produces_downloadable_dated_quotes_report(self) -> None:
        self._create_bullet(
            self.user_id,
            "impact",
            "Shipped reliability fix with measurable uptime gains",
            datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        )
        self._create_bullet(
            self.user_id,
            "leadership",
            "Mentored two teammates through incident review",
            datetime(2026, 2, 8, 15, 30, tzinfo=timezone.utc),
        )

        with patch("app.routes.brag_export.enqueue_registered_job") as enqueue_mock:
            enqueue_mock.return_value = type("JobStub", (), {"id": "rq-brag-123"})()
            created = self.client.post("/api/v1/brag/export", headers=self._auth_headers())

        self.assertEqual(created.status_code, 202)
        created_body = created.json()
        export_job_id = created_body["id"]
        self.assertEqual(created_body["status"], "queued")

        result = run_brag_text_export_job(export_job_id)
        self.assertEqual(result["status"], "ok")

        status_response = self.client.get(f"/api/v1/brag/export/{export_job_id}", headers=self._auth_headers())
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()
        self.assertEqual(status_body["status"], "completed")
        self.assertEqual(status_body["download_url"], f"/api/v1/brag/export/{export_job_id}/download")

        download = self.client.get(f"/api/v1/brag/export/{export_job_id}/download", headers=self._auth_headers())
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn("attachment; filename=\"voicevault-brag-report.txt\"", download.headers["content-disposition"])
        report_text = download.content.decode("utf-8")
        self.assertIn("VoiceVault Brag Report", report_text)
        self.assertIn('[2026-01-05] "Shipped reliability fix with measurable uptime gains"', report_text)
        self.assertIn('[2026-02-08] "Mentored two teammates through incident review"', report_text)

    def test_brag_export_download_requires_completed_job(self) -> None:
        session = get_sessionmaker()()
        try:
            export_job = ExportJob(
                user_id=self.user_id,
                export_type="brag_text_v1",
                status="queued",
                format="txt",
            )
            session.add(export_job)
            session.commit()
            session.refresh(export_job)
            export_job_id = export_job.id
        finally:
            session.close()

        response = self.client.get(f"/api/v1/brag/export/{export_job_id}/download", headers=self._auth_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "Export artifact is not ready"})

    def test_brag_export_jobs_are_scoped_to_request_user(self) -> None:
        other_user_id = self._create_user("other-user@example.com")

        session = get_sessionmaker()()
        try:
            other_job = ExportJob(
                user_id=other_user_id,
                export_type="brag_text_v1",
                status="queued",
                format="txt",
            )
            session.add(other_job)
            session.commit()
            session.refresh(other_job)
            other_job_id = other_job.id
        finally:
            session.close()

        denied = self.client.get(f"/api/v1/brag/export/{other_job_id}", headers=self._auth_headers())
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json(), {"detail": "Export job not found"})


if __name__ == "__main__":
    unittest.main()
