from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.db import get_sessionmaker, reset_engine_cache
from app.main import create_app
from app.models import (
    AskQuery,
    AskResult,
    AuditLog,
    AudioAsset,
    BragBullet,
    BragBulletCitation,
    Citation,
    Entry,
    EntryTag,
    ExportJob,
    RefreshSession,
    Tag,
    Transcript,
    User,
)
from app.settings import get_settings
from app.storage import get_storage_backend


class AccountDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_storage_local_root = os.environ.get("STORAGE_LOCAL_ROOT")
        self.original_auth_secret_key = os.environ.get("AUTH_SECRET_KEY")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/account_delete.db"
        os.environ["STORAGE_LOCAL_ROOT"] = f"{self.temp_dir.name}/storage"
        os.environ["AUTH_SECRET_KEY"] = "account-delete-test-secret"
        os.environ["ENTRY_AUTH_TOKEN"] = "account-delete-entry-token"

        get_settings.cache_clear()
        get_storage_backend.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")

    def tearDown(self) -> None:
        self.client.close()

        if self.original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_database_url

        if self.original_storage_local_root is None:
            os.environ.pop("STORAGE_LOCAL_ROOT", None)
        else:
            os.environ["STORAGE_LOCAL_ROOT"] = self.original_storage_local_root

        if self.original_auth_secret_key is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = self.original_auth_secret_key

        if self.original_entry_auth_token is None:
            os.environ.pop("ENTRY_AUTH_TOKEN", None)
        else:
            os.environ["ENTRY_AUTH_TOKEN"] = self.original_entry_auth_token

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        get_storage_backend.cache_clear()
        reset_engine_cache()

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    def _signup_user(self, *, email: str, password: str) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": email, "password": password},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def _create_entry_with_audio(self, access_token: str) -> dict[str, str]:
        entry_response = self.client.post("/api/v1/entries", headers=self._auth_headers(access_token))
        self.assertEqual(entry_response.status_code, 201)
        entry_id = entry_response.json()["entry_id"]

        audio_response = self.client.post(
            f"/api/v1/entries/{entry_id}/audio",
            headers={
                **self._auth_headers(access_token),
                "content-type": "audio/webm",
                "x-audio-filename": "source.webm",
            },
            data=b"fake-webm-content",
        )
        self.assertEqual(audio_response.status_code, 201)
        body = audio_response.json()
        return {"entry_id": entry_id, "storage_key": body["storage_key"]}

    def _insert_export_artifact(self, *, user_id: uuid.UUID, artifact_storage_key: str) -> None:
        session = get_sessionmaker()()
        try:
            session.add(
                ExportJob(
                    user_id=user_id,
                    export_type="account_export_all_v1",
                    status="completed",
                    format="zip",
                    artifact_storage_key=artifact_storage_key,
                )
            )
            session.commit()
        finally:
            session.close()

    def _insert_related_records(self, *, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        session = get_sessionmaker()()
        try:
            transcript = Transcript(
                entry_id=entry_id,
                version=1,
                is_current=True,
                transcript_text="Account delete graph transcript",
                source="stt",
                language_code="en",
            )
            session.add(transcript)
            session.flush()

            tag = Tag(user_id=user_id, name="DeleteMe", normalized_name="deleteme")
            session.add(tag)
            session.flush()
            session.add(EntryTag(entry_id=entry_id, tag_id=tag.id))

            citation = Citation(
                user_id=user_id,
                transcript_id=transcript.id,
                transcript_version=1,
                start_char=0,
                end_char=7,
                quote_text="Account",
                snippet_hash="delete-me-hash",
            )
            session.add(citation)
            session.flush()

            bullet = BragBullet(user_id=user_id, bucket="impact", bullet_text="Account delete evidence")
            session.add(bullet)
            session.flush()
            session.add(BragBulletCitation(bullet_id=bullet.id, citation_id=citation.id))

            ask_query = AskQuery(user_id=user_id, query_text="account", result_limit=5, status="done")
            session.add(ask_query)
            session.flush()
            session.add(
                AskResult(
                    ask_query_id=ask_query.id,
                    entry_id=entry_id,
                    transcript_id=transcript.id,
                    snippet_text="Account",
                    start_char=0,
                    end_char=7,
                    rank=1.0,
                    result_order=1,
                )
            )
            session.commit()
        finally:
            session.close()

    def test_delete_account_requires_correct_password(self) -> None:
        auth_payload = self._signup_user(email="owner@example.com", password="StrongPass123")
        response = self.client.request(
            "DELETE",
            "/api/v1/account",
            headers=self._auth_headers(auth_payload["access_token"]),
            json={"password": "WrongPass123"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid password."})

    def test_delete_account_wipes_user_data_revokes_tokens_and_deletes_blobs(self) -> None:
        auth_payload = self._signup_user(email="owner2@example.com", password="StrongPass123")
        access_token = auth_payload["access_token"]
        refresh_token = auth_payload["refresh_token"]

        created = self._create_entry_with_audio(access_token)
        audio_blob = pathlib.Path(self.temp_dir.name) / "storage" / created["storage_key"]
        self.assertTrue(audio_blob.exists())

        session = get_sessionmaker()()
        try:
            user = session.query(User).filter(User.email == "owner2@example.com").one()
            user_id = user.id
        finally:
            session.close()

        self._insert_related_records(user_id=user_id, entry_id=uuid.UUID(created["entry_id"]))

        export_key = f"exports/account/{user_id}/job-1/archive.zip"
        get_storage_backend().put(export_key, b"zip-content")
        export_blob = pathlib.Path(self.temp_dir.name) / "storage" / export_key
        self.assertTrue(export_blob.exists())
        self._insert_export_artifact(user_id=user_id, artifact_storage_key=export_key)

        response = self.client.request(
            "DELETE",
            "/api/v1/account",
            headers=self._auth_headers(access_token),
            json={"password": "StrongPass123"},
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.text, "")
        self.assertFalse(audio_blob.exists())
        self.assertFalse(export_blob.exists())

        refresh_response = self.client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(refresh_response.status_code, 401)

        session = get_sessionmaker()()
        try:
            self.assertIsNone(session.get(User, user_id))
            self.assertEqual(session.query(Entry).filter(Entry.user_id == user_id).count(), 0)
            self.assertEqual(session.query(AudioAsset).join(Entry, AudioAsset.entry_id == Entry.id).count(), 0)
            self.assertEqual(session.query(Transcript).count(), 0)
            self.assertEqual(session.query(Tag).count(), 0)
            self.assertEqual(session.query(EntryTag).count(), 0)
            self.assertEqual(session.query(Citation).count(), 0)
            self.assertEqual(session.query(BragBullet).count(), 0)
            self.assertEqual(session.query(BragBulletCitation).count(), 0)
            self.assertEqual(session.query(AskQuery).count(), 0)
            self.assertEqual(session.query(AskResult).count(), 0)
            self.assertEqual(session.query(ExportJob).filter(ExportJob.user_id == user_id).count(), 0)
            self.assertEqual(session.query(RefreshSession).filter(RefreshSession.user_id == user_id).count(), 0)

            audit_row = (
                session.query(AuditLog)
                .filter(AuditLog.event_type == "account_deleted")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(audit_row)
            assert audit_row is not None
            self.assertIsNone(audit_row.user_id)
            self.assertEqual(audit_row.metadata_json["audio_blob_count"], 1)
            self.assertEqual(audit_row.metadata_json["artifact_blob_count"], 1)
            self.assertGreaterEqual(audit_row.metadata_json["revoked_session_count"], 1)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
