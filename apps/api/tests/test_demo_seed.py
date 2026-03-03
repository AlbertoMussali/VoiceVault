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
from sqlalchemy import select

from app.db import get_sessionmaker, reset_engine_cache
from app.main import create_app
from app.models import Entry, Transcript, User
from app.settings import get_settings


class DemoSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_demo_seed_enabled = os.environ.get("DEMO_SEED_ENABLED")
        self.original_demo_seed_days = os.environ.get("DEMO_SEED_DAYS")
        self.original_demo_seed_email = os.environ.get("DEMO_SEED_EMAIL")
        self.original_demo_seed_password = os.environ.get("DEMO_SEED_PASSWORD")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/demo_seed.db"
        os.environ["DEMO_SEED_ENABLED"] = "true"
        os.environ["DEMO_SEED_DAYS"] = "30"
        os.environ["DEMO_SEED_EMAIL"] = "demo@demo"
        os.environ["DEMO_SEED_PASSWORD"] = "demo"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")

    def tearDown(self) -> None:
        self.client.close()

        def _restore(name: str, value: str | None) -> None:
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

        _restore("DATABASE_URL", self.original_database_url)
        _restore("DEMO_SEED_ENABLED", self.original_demo_seed_enabled)
        _restore("DEMO_SEED_DAYS", self.original_demo_seed_days)
        _restore("DEMO_SEED_EMAIL", self.original_demo_seed_email)
        _restore("DEMO_SEED_PASSWORD", self.original_demo_seed_password)
        _restore("ENTRY_AUTH_TOKEN", self.original_entry_auth_token)

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    def test_demo_seed_creates_user_daily_entries_and_transcripts(self) -> None:
        session = get_sessionmaker()()
        try:
            user = session.scalar(select(User).where(User.email == "demo@demo"))
            self.assertIsNotNone(user)
            assert user is not None

            entries = (
                session.execute(select(Entry).where(Entry.user_id == user.id))
                .scalars()
                .all()
            )
            self.assertEqual(len(entries), 30)
            self.assertTrue(all(entry.context == "work" for entry in entries))
            self.assertTrue(all(entry.status == "ready" for entry in entries))

            transcripts = (
                session.execute(
                    select(Transcript)
                    .join(Entry, Transcript.entry_id == Entry.id)
                    .where(Entry.user_id == user.id)
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(transcripts), 30)
            self.assertTrue(all(transcript.is_current for transcript in transcripts))
            self.assertTrue(all(transcript.source == "demo_seed" for transcript in transcripts))
            self.assertTrue(all(len(transcript.transcript_text.strip()) > 40 for transcript in transcripts))
        finally:
            session.close()

    def test_demo_account_can_login_and_query_seeded_entries(self) -> None:
        login_response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "demo@demo", "password": "demo"},
        )
        self.assertEqual(login_response.status_code, 200)
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        entries_response = self.client.get("/api/v1/entries", headers=headers)
        self.assertEqual(entries_response.status_code, 200)
        entries = entries_response.json()["entries"]
        self.assertEqual(len(entries), 30)

        entry_id = entries[0]["entry_id"]
        detail_response = self.client.get(f"/api/v1/entries/{entry_id}", headers=headers)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("transcript", detail_response.json())

        search_response = self.client.get("/api/v1/search", params={"q": "incident"}, headers=headers)
        self.assertEqual(search_response.status_code, 200)
        self.assertGreater(len(search_response.json()["results"]), 0)


if __name__ == "__main__":
    unittest.main()
