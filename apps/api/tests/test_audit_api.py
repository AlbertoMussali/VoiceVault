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
from app.models import AuditLog, User
from app.settings import get_settings


class AuditApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/audit_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "audit-api-entry-token"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("audit-owner@example.com")
        self.other_user_id = self._create_user("audit-other@example.com")

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

    def _auth_headers(self, user_id: uuid.UUID) -> dict[str, str]:
        return {
            "Authorization": "Bearer audit-api-entry-token",
            "x-user-id": str(user_id),
        }

    def _insert_audit_row(
        self,
        *,
        user_id: uuid.UUID,
        event_type: str,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        session = get_sessionmaker()()
        try:
            session.add(
                AuditLog(
                    user_id=user_id,
                    event_type=event_type,
                    metadata_json=metadata_json,
                )
            )
            session.commit()
        finally:
            session.close()

    def test_list_audit_events_is_scoped_and_paginated(self) -> None:
        self._insert_audit_row(user_id=self.user_id, event_type="audio_uploaded")
        self._insert_audit_row(user_id=self.other_user_id, event_type="audio_uploaded")
        self._insert_audit_row(user_id=self.user_id, event_type="transcription_called")
        self._insert_audit_row(user_id=self.user_id, event_type="llm_called")

        first_page = self.client.get(
            "/api/v1/audit?limit=2&offset=0",
            headers=self._auth_headers(self.user_id),
        )
        self.assertEqual(first_page.status_code, 200)
        first_body = first_page.json()
        self.assertEqual(first_body["pagination"], {"limit": 2, "offset": 0, "total": 3, "has_more": True})
        self.assertEqual([item["event_type"] for item in first_body["items"]], ["llm_called", "transcription_called"])
        self.assertEqual(len(first_body["items"]), 2)

        second_page = self.client.get(
            "/api/v1/audit?limit=2&offset=2",
            headers=self._auth_headers(self.user_id),
        )
        self.assertEqual(second_page.status_code, 200)
        second_body = second_page.json()
        self.assertEqual(second_body["pagination"], {"limit": 2, "offset": 2, "total": 3, "has_more": False})
        self.assertEqual([item["event_type"] for item in second_body["items"]], ["audio_uploaded"])
        self.assertEqual(len(second_body["items"]), 1)

    def test_list_audit_events_sanitizes_content_like_metadata_keys(self) -> None:
        self._insert_audit_row(
            user_id=self.user_id,
            event_type="llm_called",
            metadata_json={
                "model": "gpt-4o-mini",
                "snippet_count": 2,
                "query_text": "should not be exposed",
                "nested": {
                    "snippet_text": "should not be exposed",
                    "byte_estimate": 123,
                },
                "items": [
                    {"prompt": "hidden", "kind": "safe"},
                    {"quote_text": "hidden", "id": "x"},
                ],
            },
        )

        response = self.client.get("/api/v1/audit", headers=self._auth_headers(self.user_id))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pagination"]["total"], 1)
        self.assertEqual(len(body["items"]), 1)
        metadata = body["items"][0]["metadata"]
        self.assertEqual(metadata["model"], "gpt-4o-mini")
        self.assertEqual(metadata["snippet_count"], 2)
        self.assertNotIn("query_text", metadata)
        self.assertEqual(metadata["nested"], {"byte_estimate": 123})
        self.assertEqual(metadata["items"], [{"kind": "safe"}, {"id": "x"}])

    def test_list_audit_events_can_filter_by_event_type(self) -> None:
        self._insert_audit_row(user_id=self.user_id, event_type="audio_uploaded")
        self._insert_audit_row(user_id=self.user_id, event_type="llm_called")

        response = self.client.get(
            "/api/v1/audit?event_type=audio_uploaded",
            headers=self._auth_headers(self.user_id),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pagination"]["total"], 1)
        self.assertEqual([item["event_type"] for item in body["items"]], ["audio_uploaded"])


if __name__ == "__main__":
    unittest.main()
