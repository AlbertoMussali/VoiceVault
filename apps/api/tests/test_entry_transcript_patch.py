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
from app.models import Entry, Transcript, User
from app.settings import get_settings


class EntryTranscriptPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entry_transcript_patch.db"
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

        if self.original_entry_auth_token is None:
            os.environ.pop("ENTRY_AUTH_TOKEN", None)
        else:
            os.environ["ENTRY_AUTH_TOKEN"] = self.original_entry_auth_token

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    def _create_entry_with_transcript(self) -> tuple[uuid.UUID, str]:
        session = get_sessionmaker()()
        try:
            user = User(email="editor@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()

            entry = Entry(user_id=user.id, status="ready")
            session.add(entry)
            session.flush()

            transcript = Transcript(
                entry_id=entry.id,
                version=1,
                is_current=True,
                transcript_text="original transcript",
                language_code="en",
                source="stt",
            )
            session.add(transcript)
            session.commit()
            return entry.id, transcript.transcript_text
        finally:
            session.close()

    def test_patch_entry_transcript_creates_next_version_and_preserves_previous(self) -> None:
        entry_id, original_text = self._create_entry_with_transcript()

        response = self.client.patch(
            f"/api/v1/entries/{entry_id}/transcript",
            json={"transcript_text": "edited transcript"},
            headers={"Authorization": "Bearer entry-secret-test"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["entry_id"], str(entry_id))
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["is_current"], True)
        self.assertEqual(body["language_code"], "en")
        self.assertEqual(body["source"], "user_edit")
        self.assertIn("transcript_id", body)

        session = get_sessionmaker()()
        try:
            transcripts = (
                session.query(Transcript)
                .filter(Transcript.entry_id == entry_id)
                .order_by(Transcript.version.asc())
                .all()
            )
            self.assertEqual(len(transcripts), 2)

            old_transcript = transcripts[0]
            new_transcript = transcripts[1]

            self.assertEqual(old_transcript.version, 1)
            self.assertFalse(old_transcript.is_current)
            self.assertEqual(old_transcript.transcript_text, original_text)
            self.assertEqual(old_transcript.source, "stt")

            self.assertEqual(new_transcript.version, 2)
            self.assertTrue(new_transcript.is_current)
            self.assertEqual(new_transcript.transcript_text, "edited transcript")
            self.assertEqual(new_transcript.language_code, "en")
            self.assertEqual(new_transcript.source, "user_edit")
        finally:
            session.close()

    def test_patch_entry_transcript_requires_existing_transcript(self) -> None:
        session = get_sessionmaker()()
        try:
            user = User(email="editor2@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()
            entry = Entry(user_id=user.id, status="draft")
            session.add(entry)
            session.commit()
            entry_id = entry.id
        finally:
            session.close()

        response = self.client.patch(
            f"/api/v1/entries/{entry_id}/transcript",
            json={"transcript_text": "new text"},
            headers={"Authorization": "Bearer entry-secret-test"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "error_code": "TRANSCRIPT_MISSING",
                "message": "Cannot patch transcript before initial transcription exists.",
                "error_type": "fatal",
                "retryable": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
