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
    Tag,
    Transcript,
    User,
)
from app.settings import get_settings
from app.storage import get_storage_backend


class EntryDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_storage_local_root = os.environ.get("STORAGE_LOCAL_ROOT")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entry_delete.db"
        os.environ["STORAGE_LOCAL_ROOT"] = f"{self.temp_dir.name}/storage"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

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

        if self.original_entry_auth_token is None:
            os.environ.pop("ENTRY_AUTH_TOKEN", None)
        else:
            os.environ["ENTRY_AUTH_TOKEN"] = self.original_entry_auth_token

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        get_storage_backend.cache_clear()
        reset_engine_cache()

    def _auth_headers(self, user_id: uuid.UUID) -> dict[str, str]:
        return {
            "Authorization": "Bearer entry-secret-test",
            "x-user-id": str(user_id),
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

    def _create_entry_graph(self, user_id: uuid.UUID) -> dict[str, uuid.UUID | str]:
        storage = get_storage_backend()
        session = get_sessionmaker()()
        try:
            entry = Entry(user_id=user_id, status="ready")
            session.add(entry)
            session.flush()

            storage_key = f"entries/{entry.id}/audio/source.webm"
            storage.put(storage_key, b"test-webm")
            audio_asset = AudioAsset(
                entry_id=entry.id,
                storage_key=storage_key,
                mime_type="audio/webm",
                size_bytes=9,
                sha256_hex="deadbeef",
            )
            session.add(audio_asset)

            transcript = Transcript(
                entry_id=entry.id,
                version=1,
                is_current=True,
                transcript_text="alpha beta gamma",
                source="stt",
                language_code="en",
            )
            session.add(transcript)
            session.flush()

            tag = Tag(user_id=user_id, name="Win", normalized_name="win")
            session.add(tag)
            session.flush()
            entry_tag = EntryTag(entry_id=entry.id, tag_id=tag.id)
            session.add(entry_tag)

            citation = Citation(
                user_id=user_id,
                transcript_id=transcript.id,
                transcript_version=1,
                start_char=6,
                end_char=10,
                quote_text="beta",
                snippet_hash="hash-beta",
            )
            session.add(citation)
            session.flush()

            bullet = BragBullet(user_id=user_id, bucket="impact", bullet_text="Delivered key outcome")
            session.add(bullet)
            session.flush()

            bullet_citation = BragBulletCitation(bullet_id=bullet.id, citation_id=citation.id)
            session.add(bullet_citation)

            ask_query = AskQuery(user_id=user_id, query_text="beta", result_limit=5, status="done")
            session.add(ask_query)
            session.flush()

            ask_result = AskResult(
                ask_query_id=ask_query.id,
                entry_id=entry.id,
                transcript_id=transcript.id,
                snippet_text="beta",
                start_char=6,
                end_char=10,
                rank=1.0,
                result_order=1,
            )
            session.add(ask_result)
            session.commit()

            return {
                "entry_id": entry.id,
                "audio_asset_id": audio_asset.id,
                "transcript_id": transcript.id,
                "entry_tag_id": entry_tag.id,
                "citation_id": citation.id,
                "bullet_citation_id": bullet_citation.id,
                "ask_result_id": ask_result.id,
                "storage_key": storage_key,
            }
        finally:
            session.close()

    def test_delete_entry_removes_blobs_rows_and_writes_audit_event(self) -> None:
        user_id = self._create_user("owner@example.com")
        created = self._create_entry_graph(user_id)
        storage_blob = pathlib.Path(self.temp_dir.name) / "storage" / str(created["storage_key"])
        self.assertTrue(storage_blob.exists())

        response = self.client.delete(f"/api/v1/entries/{created['entry_id']}", headers=self._auth_headers(user_id))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.text, "")
        self.assertFalse(storage_blob.exists())

        session = get_sessionmaker()()
        try:
            self.assertIsNone(session.get(Entry, created["entry_id"]))
            self.assertIsNone(session.get(AudioAsset, created["audio_asset_id"]))
            self.assertIsNone(session.get(Transcript, created["transcript_id"]))
            self.assertIsNone(session.get(EntryTag, created["entry_tag_id"]))
            self.assertIsNone(session.get(Citation, created["citation_id"]))
            self.assertIsNone(session.get(BragBulletCitation, created["bullet_citation_id"]))
            self.assertIsNone(session.get(AskResult, created["ask_result_id"]))

            audit_row = (
                session.query(AuditLog)
                .filter(AuditLog.event_type == "entry_deleted")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(audit_row)
            assert audit_row is not None
            self.assertEqual(audit_row.user_id, user_id)
            self.assertIsNone(audit_row.entry_id)
            self.assertEqual(audit_row.metadata_json["deleted_entry_id"], str(created["entry_id"]))
            self.assertEqual(audit_row.metadata_json["audio_asset_count"], 1)
            self.assertEqual(audit_row.metadata_json["transcript_count"], 1)
        finally:
            session.close()

    def test_delete_entry_is_scoped_to_owner(self) -> None:
        owner_id = self._create_user("owner2@example.com")
        other_id = self._create_user("other@example.com")
        created = self._create_entry_graph(owner_id)

        response = self.client.delete(f"/api/v1/entries/{created['entry_id']}", headers=self._auth_headers(other_id))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Entry not found"})

        session = get_sessionmaker()()
        try:
            self.assertIsNotNone(session.get(Entry, created["entry_id"]))
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
