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
from app.models import BragBullet, BragBulletCitation, Citation, Entry, Transcript, User
from app.settings import get_settings


class BragBulletsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/brag_bullets_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("brag-owner@example.com")

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

    def _create_bullet(self, user_id: uuid.UUID, bucket: str, bullet_text: str) -> uuid.UUID:
        session = get_sessionmaker()()
        try:
            bullet = BragBullet(user_id=user_id, bucket=bucket, bullet_text=bullet_text)
            session.add(bullet)
            session.commit()
            session.refresh(bullet)
            return bullet.id
        finally:
            session.close()

    def _create_entry_with_transcript(
        self,
        user_id: uuid.UUID,
        transcript_text: str,
        *,
        version: int = 1,
        is_current: bool = True,
        entry_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        session = get_sessionmaker()()
        try:
            if entry_id is None:
                entry = Entry(user_id=user_id, status="ready")
                session.add(entry)
                session.flush()
            else:
                entry = session.get(Entry, entry_id)
                assert entry is not None

            transcript = Transcript(
                entry_id=entry.id,
                version=version,
                is_current=is_current,
                transcript_text=transcript_text,
                language_code="en",
                source="stt" if version == 1 else "user_edit",
            )
            session.add(transcript)
            session.commit()
            session.refresh(transcript)
            return entry.id, transcript.id
        finally:
            session.close()

    def test_brag_bullet_crud_and_list_by_bucket(self) -> None:
        created = self.client.post(
            "/api/v1/brag/bullets",
            json={"bucket": "Impact", "bullet_text": "Reduced incident recovery time by 30%"},
            headers=self._auth_headers(),
        )
        self.assertEqual(created.status_code, 201)
        body = created.json()
        bullet_id = body["id"]
        self.assertEqual(body["bucket"], "impact")
        self.assertEqual(body["bullet_text"], "Reduced incident recovery time by 30%")

        listed_filtered = self.client.get(
            "/api/v1/brag/bullets",
            params={"bucket": "impact"},
            headers=self._auth_headers(),
        )
        self.assertEqual(listed_filtered.status_code, 200)
        self.assertEqual(len(listed_filtered.json()["bullets"]), 1)
        self.assertEqual(listed_filtered.json()["bullets"][0]["id"], bullet_id)

        updated = self.client.patch(
            f"/api/v1/brag/bullets/{bullet_id}",
            json={"bucket": "Leadership", "bullet_text": "Led postmortem and prevention workstream"},
            headers=self._auth_headers(),
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["bucket"], "leadership")
        self.assertEqual(updated.json()["bullet_text"], "Led postmortem and prevention workstream")

        fetched = self.client.get(f"/api/v1/brag/bullets/{bullet_id}", headers=self._auth_headers())
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["id"], bullet_id)
        self.assertEqual(fetched.json()["bucket"], "leadership")

        deleted = self.client.delete(f"/api/v1/brag/bullets/{bullet_id}", headers=self._auth_headers())
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.text, "")

        gone = self.client.get(f"/api/v1/brag/bullets/{bullet_id}", headers=self._auth_headers())
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(gone.json(), {"detail": "Brag bullet not found"})

    def test_brag_bullets_are_scoped_to_request_user(self) -> None:
        own_bullet_id = self._create_bullet(self.user_id, "impact", "Own bullet")
        other_user_id = self._create_user("brag-other@example.com")
        other_bullet_id = self._create_bullet(other_user_id, "impact", "Other user's bullet")

        listed = self.client.get("/api/v1/brag/bullets", headers=self._auth_headers())
        self.assertEqual(listed.status_code, 200)
        listed_ids = {item["id"] for item in listed.json()["bullets"]}
        self.assertIn(str(own_bullet_id), listed_ids)
        self.assertNotIn(str(other_bullet_id), listed_ids)

        denied = self.client.get(f"/api/v1/brag/bullets/{other_bullet_id}", headers=self._auth_headers())
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json(), {"detail": "Brag bullet not found"})

    def test_brag_bullet_requires_authorization(self) -> None:
        response = self.client.get("/api/v1/brag/bullets")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

    def test_create_citation_validates_offsets_against_transcript_version(self) -> None:
        bullet_id = self._create_bullet(self.user_id, "impact", "Backed by transcript evidence")
        entry_id, transcript_id = self._create_entry_with_transcript(self.user_id, "alpha beta gamma")

        created = self.client.post(
            f"/api/v1/brag/bullets/{bullet_id}/citations",
            json={
                "entry_id": str(entry_id),
                "transcript_version": 1,
                "start_char": 6,
                "end_char": 10,
            },
            headers=self._auth_headers(),
        )
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertEqual(body["bullet_id"], str(bullet_id))
        self.assertEqual(body["entry_id"], str(entry_id))
        self.assertEqual(body["transcript_id"], str(transcript_id))
        self.assertEqual(body["transcript_version"], 1)
        self.assertEqual(body["start_char"], 6)
        self.assertEqual(body["end_char"], 10)
        self.assertEqual(body["quote_text"], "beta")

        out_of_bounds = self.client.post(
            f"/api/v1/brag/bullets/{bullet_id}/citations",
            json={
                "entry_id": str(entry_id),
                "transcript_version": 1,
                "start_char": 0,
                "end_char": 99,
            },
            headers=self._auth_headers(),
        )
        self.assertEqual(out_of_bounds.status_code, 400)
        self.assertEqual(
            out_of_bounds.json(),
            {"detail": "Offsets must be within transcript bounds for the selected version"},
        )

    def test_citation_remains_immutable_to_original_transcript_version(self) -> None:
        bullet_id = self._create_bullet(self.user_id, "execution", "Shipped a fix")
        entry_id, v1_transcript_id = self._create_entry_with_transcript(self.user_id, "alpha beta gamma")

        created = self.client.post(
            f"/api/v1/brag/bullets/{bullet_id}/citations",
            json={
                "entry_id": str(entry_id),
                "transcript_version": 1,
                "start_char": 6,
                "end_char": 10,
            },
            headers=self._auth_headers(),
        )
        self.assertEqual(created.status_code, 201)
        citation_id = uuid.UUID(created.json()["id"])

        self._create_entry_with_transcript(
            self.user_id,
            "replacement transcript text without that exact token",
            version=2,
            is_current=True,
            entry_id=entry_id,
        )

        session = get_sessionmaker()()
        try:
            citation = session.get(Citation, citation_id)
            assert citation is not None
            self.assertEqual(citation.transcript_id, v1_transcript_id)
            self.assertEqual(citation.transcript_version, 1)
            self.assertEqual(citation.quote_text, "beta")

            links = (
                session.query(BragBulletCitation)
                .filter(BragBulletCitation.bullet_id == bullet_id, BragBulletCitation.citation_id == citation_id)
                .all()
            )
            self.assertEqual(len(links), 1)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
