from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.db import get_sessionmaker, reset_engine_cache
from app.jobs import run_account_export_all_job
from app.main import create_app
from app.models import AudioAsset, BragBullet, BragBulletCitation, Citation, Entry, EntryTag, ExportJob, Tag, Transcript, User
from app.settings import get_settings
from app.storage import get_storage_backend


class AccountExportsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")
        self.original_storage_local_root = os.environ.get("STORAGE_LOCAL_ROOT")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/exports_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"
        os.environ["STORAGE_LOCAL_ROOT"] = f"{self.temp_dir.name}/storage"

        get_settings.cache_clear()
        reset_engine_cache()
        get_storage_backend.cache_clear()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("export-owner@example.com")

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

    def _seed_export_data(self, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        session = get_sessionmaker()()
        try:
            entry = Entry(
                user_id=user_id,
                title="Sprint demo recap",
                status="ready",
                entry_type="win",
                context="work",
                created_at=datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
            )
            session.add(entry)
            session.flush()

            transcript_v1 = Transcript(
                entry_id=entry.id,
                version=1,
                is_current=False,
                transcript_text="Initial transcript text.",
                language_code="en",
                source="stt",
                created_at=datetime(2026, 2, 20, 10, 5, tzinfo=timezone.utc),
            )
            transcript_v2 = Transcript(
                entry_id=entry.id,
                version=2,
                is_current=True,
                transcript_text="Edited transcript text with more detail.",
                language_code="en",
                source="user_edit",
                created_at=datetime(2026, 2, 20, 10, 15, tzinfo=timezone.utc),
            )
            session.add_all([transcript_v1, transcript_v2])

            tag = Tag(
                user_id=user_id,
                name="Launch",
                normalized_name="launch",
                created_at=datetime(2026, 2, 20, 10, 1, tzinfo=timezone.utc),
            )
            session.add(tag)
            session.flush()
            session.add(EntryTag(entry_id=entry.id, tag_id=tag.id, created_at=datetime(2026, 2, 20, 10, 2, tzinfo=timezone.utc)))

            audio_asset = AudioAsset(
                entry_id=entry.id,
                storage_key=f"entries/{entry.id}/audio/original.webm",
                mime_type="audio/webm",
                size_bytes=13,
                sha256_hex="abc123",
                created_at=datetime(2026, 2, 20, 10, 4, tzinfo=timezone.utc),
            )
            session.add(audio_asset)

            citation = Citation(
                user_id=user_id,
                transcript_id=transcript_v2.id,
                transcript_version=2,
                start_char=0,
                end_char=6,
                quote_text="Edited",
                snippet_hash="deadbeef",
                created_at=datetime(2026, 2, 20, 10, 30, tzinfo=timezone.utc),
            )
            session.add(citation)
            session.flush()

            bullet = BragBullet(
                user_id=user_id,
                bucket="impact",
                bullet_text="Delivered demo with clear customer value",
                created_at=datetime(2026, 2, 20, 10, 35, tzinfo=timezone.utc),
                updated_at=datetime(2026, 2, 20, 10, 35, tzinfo=timezone.utc),
            )
            session.add(bullet)
            session.flush()

            session.add(BragBulletCitation(bullet_id=bullet.id, citation_id=citation.id))
            session.commit()

            get_storage_backend().put(audio_asset.storage_key, b"fake-webm-data")
            return entry.id, audio_asset.id
        finally:
            session.close()

    def test_account_export_job_produces_zip_with_json_and_audio(self) -> None:
        entry_id, audio_asset_id = self._seed_export_data(self.user_id)

        with patch("app.routes.exports.enqueue_registered_job") as enqueue_mock:
            enqueue_mock.return_value = type("JobStub", (), {"id": "rq-export-123"})()
            created = self.client.post("/api/v1/exports", headers=self._auth_headers())

        self.assertEqual(created.status_code, 202)
        export_job_id = created.json()["id"]

        result = run_account_export_all_job(export_job_id)
        self.assertEqual(result["status"], "ok")

        status_response = self.client.get(f"/api/v1/exports/{export_job_id}", headers=self._auth_headers())
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["status"], "completed")
        self.assertEqual(status_response.json()["download_url"], f"/api/v1/exports/{export_job_id}/download")

        download = self.client.get(f"/api/v1/exports/{export_job_id}/download", headers=self._auth_headers())
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "application/zip")
        self.assertIn("attachment; filename=\"voicevault-export-all.zip\"", download.headers["content-disposition"])

        with zipfile.ZipFile(io.BytesIO(download.content), mode="r") as archive:
            members = set(archive.namelist())
            expected_audio_member = f"audio/{entry_id}/{audio_asset_id}-original.webm"
            self.assertIn("export.json", members)
            self.assertIn(expected_audio_member, members)

            exported = json.loads(archive.read("export.json").decode("utf-8"))
            self.assertEqual(exported["schema_version"], "account_export_v1")
            self.assertEqual(exported["user_id"], str(self.user_id))
            self.assertEqual(len(exported["entries"]), 1)
            self.assertEqual(exported["entries"][0]["id"], str(entry_id))
            self.assertEqual([item["version"] for item in exported["entries"][0]["transcripts"]], [1, 2])
            self.assertEqual(exported["entries"][0]["tags"], [exported["tags"][0]["id"]])
            self.assertEqual(exported["brag"]["bullets"][0]["bucket"], "impact")
            self.assertEqual(len(exported["brag"]["bullets"][0]["citations"]), 1)
            self.assertEqual(archive.read(expected_audio_member), b"fake-webm-data")

    def test_export_download_requires_completed_job(self) -> None:
        session = get_sessionmaker()()
        try:
            export_job = ExportJob(
                user_id=self.user_id,
                export_type="account_export_all_v1",
                status="queued",
                format="zip",
            )
            session.add(export_job)
            session.commit()
            session.refresh(export_job)
            export_job_id = export_job.id
        finally:
            session.close()

        response = self.client.get(f"/api/v1/exports/{export_job_id}/download", headers=self._auth_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "Export artifact is not ready"})

    def test_export_jobs_are_scoped_to_request_user(self) -> None:
        other_user_id = self._create_user("other-export-user@example.com")

        session = get_sessionmaker()()
        try:
            export_job = ExportJob(
                user_id=other_user_id,
                export_type="account_export_all_v1",
                status="queued",
                format="zip",
            )
            session.add(export_job)
            session.commit()
            session.refresh(export_job)
            export_job_id = export_job.id
        finally:
            session.close()

        denied = self.client.get(f"/api/v1/exports/{export_job_id}", headers=self._auth_headers())
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json(), {"detail": "Export job not found"})

    def test_account_export_includes_only_request_user_content(self) -> None:
        owner_entry_id, _ = self._seed_export_data(self.user_id)
        other_user_id = self._create_user("other-owner@example.com")
        other_entry_id, _ = self._seed_export_data(other_user_id)

        with patch("app.routes.exports.enqueue_registered_job") as enqueue_mock:
            enqueue_mock.return_value = type("JobStub", (), {"id": "rq-export-124"})()
            created = self.client.post("/api/v1/exports", headers=self._auth_headers())

        self.assertEqual(created.status_code, 202)
        export_job_id = created.json()["id"]
        result = run_account_export_all_job(export_job_id)
        self.assertEqual(result["status"], "ok")

        download = self.client.get(f"/api/v1/exports/{export_job_id}/download", headers=self._auth_headers())
        self.assertEqual(download.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(download.content), mode="r") as archive:
            exported = json.loads(archive.read("export.json").decode("utf-8"))
            exported_entry_ids = [entry["id"] for entry in exported["entries"]]
            self.assertEqual(exported_entry_ids, [str(owner_entry_id)])
            self.assertNotIn(str(other_entry_id), exported_entry_ids)
            self.assertEqual(len(exported["tags"]), 1)
            self.assertEqual(len(exported["brag"]["bullets"]), 1)


if __name__ == "__main__":
    unittest.main()
