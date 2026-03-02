from __future__ import annotations

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

from app.db import get_sessionmaker, initialize_schema, reset_engine_cache
from app.jobs import JOB_REGISTRY, run_transcription_job
from app.models import AudioAsset, Entry, Transcript, User
from app.openai_stt import TranscriptionResult
from app.settings import get_settings
from app.storage import get_storage_backend


class TranscriptionJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_storage_local_root = os.environ.get("STORAGE_LOCAL_ROOT")
        self.original_openai_api_key = os.environ.get("OPENAI_API_KEY")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/transcription_job.db"
        os.environ["STORAGE_LOCAL_ROOT"] = f"{self.temp_dir.name}/storage"
        os.environ["OPENAI_API_KEY"] = "test-openai-key"

        get_settings.cache_clear()
        reset_engine_cache()
        get_storage_backend.cache_clear()
        initialize_schema()

    def tearDown(self) -> None:
        if self.original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_database_url

        if self.original_storage_local_root is None:
            os.environ.pop("STORAGE_LOCAL_ROOT", None)
        else:
            os.environ["STORAGE_LOCAL_ROOT"] = self.original_storage_local_root

        if self.original_openai_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.original_openai_api_key

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()
        get_storage_backend.cache_clear()

    def test_registry_contains_transcription_job(self) -> None:
        self.assertIn("transcription.openai_stt_v1", JOB_REGISTRY)
        self.assertIs(JOB_REGISTRY["transcription.openai_stt_v1"], run_transcription_job)

    def test_run_transcription_job_stores_transcript_v1_and_sets_entry_ready(self) -> None:
        entry_id, audio_asset_id = self._create_entry_with_audio()

        with patch(
            "app.jobs.transcribe_audio_bytes",
            return_value=TranscriptionResult(text="hello from stt", language_code="en"),
        ) as mocked_transcriber:
            result = run_transcription_job(str(entry_id), str(audio_asset_id))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entry_id"], str(entry_id))
        self.assertEqual(result["audio_asset_id"], str(audio_asset_id))
        self.assertEqual(result["version"], 1)

        session = get_sessionmaker()()
        try:
            entry = session.get(Entry, entry_id)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.status, "ready")

            transcripts = session.query(Transcript).filter(Transcript.entry_id == entry_id).all()
            self.assertEqual(len(transcripts), 1)
            transcript = transcripts[0]
            self.assertEqual(transcript.version, 1)
            self.assertTrue(transcript.is_current)
            self.assertEqual(transcript.transcript_text, "hello from stt")
            self.assertEqual(transcript.language_code, "en")
            self.assertEqual(transcript.source, "stt")
        finally:
            session.close()

        mocked_transcriber.assert_called_once()

    def _create_entry_with_audio(self) -> tuple[uuid.UUID, uuid.UUID]:
        storage = get_storage_backend()
        session = get_sessionmaker()()
        try:
            user = User(email="transcribe@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()

            entry = Entry(user_id=user.id, status="transcribing")
            session.add(entry)
            session.flush()

            storage_key = f"entries/{entry.id}/audio/source.webm"
            payload = b"\x1aE\xdf\xa3test-webm"
            storage.put(storage_key, payload)

            audio_asset = AudioAsset(
                entry_id=entry.id,
                storage_key=storage_key,
                mime_type="audio/webm",
                size_bytes=len(payload),
            )
            session.add(audio_asset)
            session.commit()
            return entry.id, audio_asset.id
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
