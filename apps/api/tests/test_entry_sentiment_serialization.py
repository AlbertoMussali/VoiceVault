from __future__ import annotations

from decimal import Decimal
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
from app.models import Entry, EntryTag, Tag, User
from app.settings import get_settings


class EntrySentimentSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entry_sentiment_serialization.db"
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

    def test_entries_include_sentiment_fields(self) -> None:
        session = get_sessionmaker()()
        try:
            user = User(email="sentiment@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()

            entry = Entry(
                user_id=user.id,
                status="ready",
                entry_type="win",
                context="work",
                sentiment_label="positive",
                sentiment_score=Decimal("0.875"),
            )
            session.add(entry)
            session.flush()

            tag = Tag(user_id=user.id, name="shipping", normalized_name="shipping")
            session.add(tag)
            session.flush()
            session.add(EntryTag(entry_id=entry.id, tag_id=tag.id))
            session.commit()

            headers = {
                "Authorization": "Bearer entry-secret-test",
                "x-user-id": str(user.id),
            }

            list_response = self.client.get("/api/v1/entries", headers=headers)
            self.assertEqual(list_response.status_code, 200)
            items = list_response.json()["entries"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["sentiment_label"], "positive")
            self.assertAlmostEqual(float(items[0]["sentiment_score"]), 0.875, places=3)

            detail_response = self.client.get(f"/api/v1/entries/{entry.id}", headers=headers)
            self.assertEqual(detail_response.status_code, 200)
            detail = detail_response.json()
            self.assertEqual(detail["sentiment_label"], "positive")
            self.assertAlmostEqual(float(detail["sentiment_score"]), 0.875, places=3)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
