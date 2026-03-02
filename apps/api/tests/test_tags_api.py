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
from app.models import Entry, EntryTag, Tag, User
from app.settings import get_settings


class TagsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/tags_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("tags@example.com")

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

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer entry-secret-test",
            "x-user-id": str(self.user_id),
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

    def _create_entry(self) -> uuid.UUID:
        session = get_sessionmaker()()
        try:
            entry = Entry(user_id=self.user_id, status="ready")
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id
        finally:
            session.close()

    def test_create_tag_normalizes_and_prevents_duplicate_normalized_name(self) -> None:
        response = self.client.post("/api/v1/tags", json={"name": "  Work   Win "}, headers=self._auth_headers())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {
                "id": response.json()["id"],
                "name": "Work Win",
                "normalized_name": "work win",
            },
        )

        duplicate = self.client.post("/api/v1/tags", json={"name": "work win"}, headers=self._auth_headers())
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json(), {"detail": "Tag already exists"})

    def test_autocomplete_returns_prefix_matches_using_normalized_text(self) -> None:
        for name in ["Work", "Workout", "Wins", "Life"]:
            created = self.client.post("/api/v1/tags", json={"name": name}, headers=self._auth_headers())
            self.assertEqual(created.status_code, 201)

        response = self.client.get("/api/v1/tags/autocomplete", params={"query": "wo"}, headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()["tags"]], ["Work", "Workout"])

    def test_entry_tag_links_persist_and_can_be_replaced(self) -> None:
        first = self.client.post("/api/v1/tags", json={"name": "Blocker"}, headers=self._auth_headers()).json()
        second = self.client.post("/api/v1/tags", json={"name": "Context"}, headers=self._auth_headers()).json()
        third = self.client.post("/api/v1/tags", json={"name": "Win"}, headers=self._auth_headers()).json()
        entry_id = self._create_entry()

        update = self.client.put(
            f"/api/v1/entries/{entry_id}/tags",
            json={"tag_ids": [first["id"], second["id"]]},
            headers=self._auth_headers(),
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual([tag["name"] for tag in update.json()["tags"]], ["Blocker", "Context"])

        replace = self.client.put(
            f"/api/v1/entries/{entry_id}/tags",
            json={"tag_ids": [third["id"]]},
            headers=self._auth_headers(),
        )
        self.assertEqual(replace.status_code, 200)
        self.assertEqual([tag["name"] for tag in replace.json()["tags"]], ["Win"])

        listed = self.client.get(f"/api/v1/entries/{entry_id}/tags", headers=self._auth_headers())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([tag["name"] for tag in listed.json()["tags"]], ["Win"])

        session = get_sessionmaker()()
        try:
            linked_tags = session.query(EntryTag).filter(EntryTag.entry_id == entry_id).all()
            self.assertEqual(len(linked_tags), 1)
            persisted_tag = session.get(Tag, linked_tags[0].tag_id)
            self.assertIsNotNone(persisted_tag)
            assert persisted_tag is not None
            self.assertEqual(persisted_tag.name, "Win")
        finally:
            session.close()

    def test_entry_tag_links_ignore_duplicate_tag_ids(self) -> None:
        tag = self.client.post("/api/v1/tags", json={"name": "Weekly"}, headers=self._auth_headers()).json()
        entry_id = self._create_entry()

        response = self.client.put(
            f"/api/v1/entries/{entry_id}/tags",
            json={"tag_ids": [tag["id"], tag["id"], tag["id"]]},
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()["tags"]], ["Weekly"])

        session = get_sessionmaker()()
        try:
            linked_tags = session.query(EntryTag).filter(EntryTag.entry_id == entry_id).all()
            self.assertEqual(len(linked_tags), 1)
        finally:
            session.close()

    def test_entry_tag_update_rejects_other_user_tags_without_mutating_existing_links(self) -> None:
        own_tag = self.client.post("/api/v1/tags", json={"name": "Own"}, headers=self._auth_headers()).json()
        entry_id = self._create_entry()

        initial = self.client.put(
            f"/api/v1/entries/{entry_id}/tags",
            json={"tag_ids": [own_tag["id"]]},
            headers=self._auth_headers(),
        )
        self.assertEqual(initial.status_code, 200)

        other_user_id = self._create_user("other-tags@example.com")
        session = get_sessionmaker()()
        try:
            foreign_tag = Tag(user_id=other_user_id, name="Foreign", normalized_name="foreign")
            session.add(foreign_tag)
            session.commit()
            session.refresh(foreign_tag)
            foreign_tag_id = str(foreign_tag.id)
        finally:
            session.close()

        rejected = self.client.put(
            f"/api/v1/entries/{entry_id}/tags",
            json={"tag_ids": [foreign_tag_id]},
            headers=self._auth_headers(),
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json(), {"detail": "One or more tags do not exist"})

        listed = self.client.get(f"/api/v1/entries/{entry_id}/tags", headers=self._auth_headers())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([tag["name"] for tag in listed.json()["tags"]], ["Own"])

        session = get_sessionmaker()()
        try:
            linked_tags = session.query(EntryTag).filter(EntryTag.entry_id == entry_id).all()
            self.assertEqual(len(linked_tags), 1)
            persisted_tag = session.get(Tag, linked_tags[0].tag_id)
            self.assertIsNotNone(persisted_tag)
            assert persisted_tag is not None
            self.assertEqual(persisted_tag.name, "Own")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
