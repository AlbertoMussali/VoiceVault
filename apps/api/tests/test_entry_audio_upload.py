from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.db import get_sessionmaker, reset_engine_cache
from app.main import create_app
from app.models import AudioAsset, Entry
from app.settings import get_settings


class EntryAudioUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_audio_storage_root = os.environ.get("AUDIO_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entry_audio_upload.db"
        os.environ["AUDIO_STORAGE_ROOT"] = f"{self.temp_dir.name}/audio"
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
        if self.original_audio_storage_root is None:
            os.environ.pop("AUDIO_STORAGE_ROOT", None)
        else:
            os.environ["AUDIO_STORAGE_ROOT"] = self.original_audio_storage_root
        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    def _create_entry(self) -> int:
        session = get_sessionmaker()()
        try:
            entry = Entry()
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id
        finally:
            session.close()

    def test_upload_audio_persists_blob_and_metadata(self) -> None:
        entry_id = self._create_entry()
        payload = b"\x1aE\xdf\xa3webm-bytes"
        headers = {
            "content-type": "audio/webm",
            "x-audio-filename": "meeting.webm",
        }

        response = self.client.post(f"/entries/{entry_id}/audio", data=payload, headers=headers)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["entry_id"], entry_id)
        self.assertEqual(body["byte_size"], len(payload))
        self.assertEqual(body["content_type"], "audio/webm")
        self.assertIn("storage_key", body)

        stored_blob = pathlib.Path(self.temp_dir.name) / "audio" / body["storage_key"]
        self.assertTrue(stored_blob.exists())
        self.assertEqual(stored_blob.read_bytes(), payload)

        session = get_sessionmaker()()
        try:
            audio_asset = session.get(AudioAsset, body["asset_id"])
            self.assertIsNotNone(audio_asset)
            assert audio_asset is not None
            self.assertEqual(audio_asset.entry_id, entry_id)
            self.assertEqual(audio_asset.original_filename, "meeting.webm")
            self.assertEqual(audio_asset.byte_size, len(payload))
            self.assertEqual(audio_asset.content_type, "audio/webm")
        finally:
            session.close()

    def test_upload_audio_requires_existing_entry(self) -> None:
        response = self.client.post(
            "/entries/999/audio",
            data=b"abc",
            headers={"content-type": "audio/webm"},
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_audio_requires_audio_content_type(self) -> None:
        entry_id = self._create_entry()
        response = self.client.post(
            f"/entries/{entry_id}/audio",
            data=b"abc",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 415)


if __name__ == "__main__":
    unittest.main()
