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


class SearchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/search_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("search-owner@example.com")

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

    def _create_entry_with_current_transcript(self, user_id: uuid.UUID, transcript_text: str) -> tuple[uuid.UUID, uuid.UUID]:
        session = get_sessionmaker()()
        try:
            entry = Entry(user_id=user_id, status="ready")
            session.add(entry)
            session.flush()

            transcript = Transcript(
                entry_id=entry.id,
                version=1,
                is_current=True,
                transcript_text=transcript_text,
                language_code="en",
                source="stt",
            )
            session.add(transcript)
            session.commit()
            session.refresh(entry)
            session.refresh(transcript)
            return entry.id, transcript.id
        finally:
            session.close()

    def test_search_returns_ranked_snippets_and_offsets(self) -> None:
        high_text = "Today I fixed blocker issues and then blocker happened again."
        low_text = "A blocker appeared once."
        high_entry_id, high_transcript_id = self._create_entry_with_current_transcript(self.user_id, high_text)
        self._create_entry_with_current_transcript(self.user_id, low_text)

        response = self.client.get("/api/v1/search", params={"q": "blocker"}, headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["query"], "blocker")
        self.assertGreaterEqual(len(body["results"]), 2)

        first = body["results"][0]
        self.assertEqual(first["entry_id"], str(high_entry_id))
        self.assertEqual(first["transcript_id"], str(high_transcript_id))
        self.assertIn("snippet_text", first)
        self.assertIn("start_char", first)
        self.assertIn("end_char", first)

        expected_start = high_text.lower().find("blocker")
        self.assertEqual(first["start_char"], expected_start)
        self.assertEqual(first["end_char"], expected_start + len("blocker"))
        self.assertEqual(high_text[first["start_char"] : first["end_char"]].lower(), "blocker")
        self.assertIn("blocker", first["snippet_text"].lower())

    def test_search_scopes_results_to_request_user(self) -> None:
        own_text = "I hit a blocker in my deployment."
        foreign_text = "blocker blocker blocker from another account"
        _, own_transcript_id = self._create_entry_with_current_transcript(self.user_id, own_text)
        other_user_id = self._create_user("search-other@example.com")
        self._create_entry_with_current_transcript(other_user_id, foreign_text)

        response = self.client.get("/api/v1/search", params={"q": "blocker"}, headers=self._auth_headers(self.user_id))
        self.assertEqual(response.status_code, 200)

        result_ids = {item["transcript_id"] for item in response.json()["results"]}
        self.assertEqual(result_ids, {str(own_transcript_id)})

    def test_search_requires_authorization(self) -> None:
        response = self.client.get("/api/v1/search", params={"q": "blocker"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})


if __name__ == "__main__":
    unittest.main()
