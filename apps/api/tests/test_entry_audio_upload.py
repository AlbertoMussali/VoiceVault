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
from app.models import AudioAsset, Entry, User
from app.settings import get_settings


class EntryAudioUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_storage_local_root = os.environ.get("STORAGE_LOCAL_ROOT")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entry_audio_upload.db"
        os.environ["STORAGE_LOCAL_ROOT"] = f"{self.temp_dir.name}/storage"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
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
        reset_engine_cache()

    def _create_user_and_entry(self) -> uuid.UUID:
        session = get_sessionmaker()()
        try:
            user = User(email="user@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()

            entry = Entry(user_id=user.id)
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id
        finally:
            session.close()

    def test_upload_audio_persists_blob_and_metadata(self) -> None:
        entry_id = self._create_user_and_entry()
        payload = b"\x1aE\xdf\xa3webm-bytes"
        headers = {
            "content-type": "audio/webm",
            "x-audio-filename": "meeting.webm",
            "Authorization": "Bearer entry-secret-test",
        }

        response = self.client.post(f"/api/v1/entries/{entry_id}/audio", data=payload, headers=headers)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["entry_id"], str(entry_id))
        self.assertEqual(body["size_bytes"], len(payload))
        self.assertEqual(body["mime_type"], "audio/webm")
        self.assertIn("storage_key", body)
        self.assertIn("asset_id", body)

        stored_blob = pathlib.Path(self.temp_dir.name) / "storage" / body["storage_key"]
        self.assertTrue(stored_blob.exists())
        self.assertEqual(stored_blob.read_bytes(), payload)

        session = get_sessionmaker()()
        try:
            asset_id = uuid.UUID(body["asset_id"])
            audio_asset = session.get(AudioAsset, asset_id)
            self.assertIsNotNone(audio_asset)
            assert audio_asset is not None
            self.assertEqual(audio_asset.entry_id, entry_id)
            self.assertEqual(audio_asset.storage_key, body["storage_key"])
            self.assertEqual(audio_asset.size_bytes, len(payload))
            self.assertEqual(audio_asset.mime_type, "audio/webm")
        finally:
            session.close()

    def test_upload_audio_requires_existing_entry(self) -> None:
        missing = uuid.UUID("00000000-0000-0000-0000-000000000000")
        response = self.client.post(
            f"/api/v1/entries/{missing}/audio",
            data=b"abc",
            headers={"content-type": "audio/webm", "Authorization": "Bearer entry-secret-test"},
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_audio_requires_audio_content_type(self) -> None:
        entry_id = self._create_user_and_entry()
        response = self.client.post(
            f"/api/v1/entries/{entry_id}/audio",
            data=b"abc",
            headers={"content-type": "application/json", "Authorization": "Bearer entry-secret-test"},
        )
        self.assertEqual(response.status_code, 415)


if __name__ == "__main__":
    unittest.main()
