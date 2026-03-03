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
from app.models import AuditLog, Entry, User
from app.settings import get_settings


class EntryArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entry_archive.db"
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

    def _create_user_and_entry(self) -> tuple[uuid.UUID, uuid.UUID]:
        session = get_sessionmaker()()
        try:
            user = User(email="archive@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()
            entry = Entry(user_id=user.id, status="ready")
            session.add(entry)
            session.commit()
            return user.id, entry.id
        finally:
            session.close()

    def test_archive_entry_updates_status_and_writes_audit_row(self) -> None:
        user_id, entry_id = self._create_user_and_entry()
        response = self.client.post(
            f"/api/v1/entries/{entry_id}/archive",
            headers={
                "Authorization": "Bearer entry-secret-test",
                "x-user-id": str(user_id),
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["entry_id"], str(entry_id))
        self.assertEqual(body["status"], "archived")

        session = get_sessionmaker()()
        try:
            entry = session.get(Entry, entry_id)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.status, "archived")
            audit_row = (
                session.query(AuditLog)
                .filter(AuditLog.entry_id == entry_id, AuditLog.event_type == "entry_archived")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(audit_row)
            assert audit_row is not None
            self.assertEqual(audit_row.user_id, user_id)
            self.assertEqual(audit_row.metadata_json, {"previous_status": "ready"})
        finally:
            session.close()

    def test_archive_entry_requires_owned_entry(self) -> None:
        user_id, _ = self._create_user_and_entry()
        missing = uuid.UUID("00000000-0000-0000-0000-000000000000")
        response = self.client.post(
            f"/api/v1/entries/{missing}/archive",
            headers={
                "Authorization": "Bearer entry-secret-test",
                "x-user-id": str(user_id),
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Entry not found"})


if __name__ == "__main__":
    unittest.main()
