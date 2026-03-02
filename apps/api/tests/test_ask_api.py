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

from fastapi.testclient import TestClient

from app.db import get_sessionmaker, reset_engine_cache
from app.main import create_app
from app.models import AskQuery, AskResult, AuditLog, Entry, EntryTag, Tag, Transcript, User
from app.openai_summary import AskSummarySentence
from app.settings import get_settings


class AskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/ask_api.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")
        self.user_id = self._create_user("ask-owner@example.com")

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

    def _create_entry_with_current_transcript(
        self,
        user_id: uuid.UUID,
        transcript_text: str,
        tags: list[str] | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
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

            for raw_tag in tags or []:
                normalized = raw_tag.strip().lower()
                if not normalized:
                    continue
                tag = session.query(Tag).filter(Tag.user_id == user_id, Tag.normalized_name == normalized).one_or_none()
                if tag is None:
                    tag = Tag(user_id=user_id, name=raw_tag.strip(), normalized_name=normalized)
                    session.add(tag)
                    session.flush()
                session.add(EntryTag(entry_id=entry.id, tag_id=tag.id))

            session.commit()
            session.refresh(entry)
            session.refresh(transcript)
            return entry.id, transcript.id
        finally:
            session.close()

    def test_ask_query_returns_sources_and_persists_query_and_results(self) -> None:
        self._create_entry_with_current_transcript(
            self.user_id,
            "Today I fixed blocker issues and then blocker happened again.",
        )
        self._create_entry_with_current_transcript(self.user_id, "A blocker appeared once.")

        response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertIn("query_id", body)
        self.assertEqual(body["query_text"], "blocker")
        self.assertGreaterEqual(len(body["sources"]), 1)
        self.assertLessEqual(len(body["sources"]), 5)

        session = get_sessionmaker()()
        try:
            ask_query = session.get(AskQuery, uuid.UUID(body["query_id"]))
            self.assertIsNotNone(ask_query)
            assert ask_query is not None
            self.assertEqual(ask_query.user_id, self.user_id)
            self.assertEqual(ask_query.query_text, "blocker")
            self.assertEqual(ask_query.result_limit, 5)
            self.assertEqual(ask_query.status, "done")

            stored_results = (
                session.query(AskResult)
                .filter(AskResult.ask_query_id == ask_query.id)
                .order_by(AskResult.result_order.asc())
                .all()
            )
            self.assertEqual(len(stored_results), len(body["sources"]))
            self.assertTrue(all(result.result_order >= 1 for result in stored_results))
        finally:
            session.close()

    def test_ask_query_scopes_sources_to_request_user(self) -> None:
        own_entry_id, _ = self._create_entry_with_current_transcript(self.user_id, "my blocker note")
        other_user_id = self._create_user("ask-other@example.com")
        other_entry_id, _ = self._create_entry_with_current_transcript(other_user_id, "other user blocker note")

        response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(self.user_id),
        )
        self.assertEqual(response.status_code, 200)
        entry_ids = {item["entry_id"] for item in response.json()["sources"]}
        self.assertIn(str(own_entry_id), entry_ids)
        self.assertNotIn(str(other_entry_id), entry_ids)

    def test_ask_query_requires_authorization(self) -> None:
        response = self.client.post("/api/v1/ask/query", json={"query_text": "blocker", "limit": 5})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})

    def test_ask_query_limit_must_be_between_five_and_twelve(self) -> None:
        too_low = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 4},
            headers=self._auth_headers(),
        )
        self.assertEqual(too_low.status_code, 422)

        too_high = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 13},
            headers=self._auth_headers(),
        )
        self.assertEqual(too_high.status_code, 422)

    def test_provider_payload_applies_transforms_without_mutating_stored_results(self) -> None:
        self._create_entry_with_current_transcript(
            self.user_id,
            (
                "Contact me at person@example.com or 415-555-0134. "
                "Card 1234-5678-9012-3456 and url https://example.com/path "
                "reference 9876543210 blocker."
            ),
        )

        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)
        query_id = query_response.json()["query_id"]

        payload_response = self.client.get(
            f"/api/v1/ask/{query_id}",
            params={"redact": "true", "mask": "true", "include_sensitive": "true"},
            headers=self._auth_headers(),
        )
        self.assertEqual(payload_response.status_code, 200)
        body = payload_response.json()
        self.assertGreaterEqual(body["snippet_count"], 1)
        transformed = body["snippets"][0]["snippet_text"]
        self.assertIn("[REDACTED_EMAIL]", transformed)
        self.assertIn("[REDACTED_PHONE]", transformed)
        self.assertIn("[MASKED_URL]", transformed)
        self.assertNotIn("person@example.com", transformed)
        self.assertNotIn("415-555-0134", transformed)
        self.assertNotIn("https://example.com/path", transformed)

        session = get_sessionmaker()()
        try:
            ask_result = (
                session.query(AskResult)
                .join(AskQuery, AskResult.ask_query_id == AskQuery.id)
                .filter(AskQuery.id == uuid.UUID(query_id))
                .order_by(AskResult.result_order.asc())
                .first()
            )
            self.assertIsNotNone(ask_result)
            assert ask_result is not None
            self.assertIn("person@example.com", ask_result.snippet_text)
            self.assertIn("415-555-0134", ask_result.snippet_text)
            self.assertIn("https://example.com/path", ask_result.snippet_text)
        finally:
            session.close()

    def test_provider_payload_excludes_sensitive_results_by_default(self) -> None:
        regular_entry_id, _ = self._create_entry_with_current_transcript(
            self.user_id,
            "Regular blocker note with customer update.",
            tags=["general"],
        )
        sensitive_entry_id, _ = self._create_entry_with_current_transcript(
            self.user_id,
            "Sensitive blocker note with payroll details.",
            tags=["work_sensitive"],
        )

        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)
        query_id = query_response.json()["query_id"]

        default_payload = self.client.get(f"/api/v1/ask/{query_id}", headers=self._auth_headers())
        self.assertEqual(default_payload.status_code, 200)
        default_entry_ids = {item["entry_id"] for item in default_payload.json()["snippets"]}
        self.assertIn(str(regular_entry_id), default_entry_ids)
        self.assertNotIn(str(sensitive_entry_id), default_entry_ids)

        include_sensitive_payload = self.client.get(
            f"/api/v1/ask/{query_id}",
            params={"include_sensitive": "true"},
            headers=self._auth_headers(),
        )
        self.assertEqual(include_sensitive_payload.status_code, 200)
        included_entry_ids = {item["entry_id"] for item in include_sensitive_payload.json()["snippets"]}
        self.assertIn(str(regular_entry_id), included_entry_ids)
        self.assertIn(str(sensitive_entry_id), included_entry_ids)

    def test_provider_payload_excludes_sensitive_alias_and_raw_only_by_default(self) -> None:
        regular_entry_id, _ = self._create_entry_with_current_transcript(
            self.user_id,
            "Regular blocker update with no restrictions.",
            tags=["general"],
        )
        sensitive_alias_entry_id, _ = self._create_entry_with_current_transcript(
            self.user_id,
            "Confidential blocker update for a compensation discussion.",
            tags=["work-sensitive"],
        )
        raw_only_entry_id, _ = self._create_entry_with_current_transcript(
            self.user_id,
            "Raw-only blocker update that should stay out of provider payload by default.",
            tags=["raw_only"],
        )

        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)
        query_id = query_response.json()["query_id"]

        default_payload = self.client.get(f"/api/v1/ask/{query_id}", headers=self._auth_headers())
        self.assertEqual(default_payload.status_code, 200)
        default_entry_ids = {item["entry_id"] for item in default_payload.json()["snippets"]}
        self.assertIn(str(regular_entry_id), default_entry_ids)
        self.assertNotIn(str(sensitive_alias_entry_id), default_entry_ids)
        self.assertNotIn(str(raw_only_entry_id), default_entry_ids)

        include_sensitive_payload = self.client.get(
            f"/api/v1/ask/{query_id}",
            params={"include_sensitive": "true"},
            headers=self._auth_headers(),
        )
        self.assertEqual(include_sensitive_payload.status_code, 200)
        included_entry_ids = {item["entry_id"] for item in include_sensitive_payload.json()["snippets"]}
        self.assertIn(str(regular_entry_id), included_entry_ids)
        self.assertIn(str(sensitive_alias_entry_id), included_entry_ids)
        self.assertIn(str(raw_only_entry_id), included_entry_ids)

    def test_ask_summarize_accepts_cited_sentences_for_selected_snippets(self) -> None:
        self._create_entry_with_current_transcript(self.user_id, "Blocker A happened and we mitigated it.")
        self._create_entry_with_current_transcript(self.user_id, "Blocker B was caused by flaky deploy steps.")

        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)

        query_body = query_response.json()
        query_id = query_body["query_id"]
        selected_snippet_id = query_body["sources"][0]["id"]

        with patch(
            "app.jobs.generate_summary_sentences",
            return_value=[
                AskSummarySentence(
                    text="The main blocker was deploy instability.",
                    snippet_ids=[selected_snippet_id],
                )
            ],
        ):
            summarize_response = self.client.post(
                f"/api/v1/ask/{query_id}/summarize",
                json={"snippet_ids": [selected_snippet_id]},
                headers=self._auth_headers(),
            )

        self.assertEqual(summarize_response.status_code, 200)
        summary_body = summarize_response.json()
        self.assertEqual(summary_body["summary_status"], "done")
        self.assertEqual(
            summary_body["summary"]["sentences"][0]["snippet_ids"],
            [selected_snippet_id],
        )

        fetch_response = self.client.get(f"/api/v1/ask/{query_id}", headers=self._auth_headers())
        self.assertEqual(fetch_response.status_code, 200)
        fetched = fetch_response.json()
        self.assertEqual(fetched["summary_status"], "done")
        self.assertEqual(fetched["summary"]["sentences"][0]["snippet_ids"], [selected_snippet_id])

        session = get_sessionmaker()()
        try:
            audit_row = (
                session.query(AuditLog)
                .filter(AuditLog.event_type == "llm_called")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(audit_row)
            assert audit_row is not None
            self.assertEqual(audit_row.user_id, self.user_id)
            self.assertIsNone(audit_row.entry_id)
            self.assertEqual(audit_row.metadata_json["model"], get_settings().openai_summary_model)
            self.assertEqual(audit_row.metadata_json["snippet_count"], 1)
            self.assertIsInstance(audit_row.metadata_json["byte_estimate"], int)
            self.assertGreater(audit_row.metadata_json["byte_estimate"], 0)
            self.assertNotIn("snippet_text", audit_row.metadata_json)
            self.assertNotIn("query_text", audit_row.metadata_json)
        finally:
            session.close()

    def test_ask_summarize_rejects_uncited_sentences(self) -> None:
        self._create_entry_with_current_transcript(self.user_id, "Blocker recurrence impacted release confidence.")
        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)
        query_id = query_response.json()["query_id"]

        with patch(
            "app.jobs.generate_summary_sentences",
            return_value=[AskSummarySentence(text="We had recurring blockers.", snippet_ids=[])],
        ):
            summarize_response = self.client.post(
                f"/api/v1/ask/{query_id}/summarize",
                json={},
                headers=self._auth_headers(),
            )

        self.assertEqual(summarize_response.status_code, 422)
        self.assertIn("uncited", summarize_response.json()["detail"])

    def test_ask_summarize_rejects_unknown_or_out_of_scope_snippet_ids(self) -> None:
        self._create_entry_with_current_transcript(self.user_id, "Blocker item one.")
        self._create_entry_with_current_transcript(self.user_id, "Blocker item two.")

        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)
        body = query_response.json()
        query_id = body["query_id"]
        valid_snippet_id = body["sources"][0]["id"]
        unknown_snippet_id = str(uuid.uuid4())

        invalid_request = self.client.post(
            f"/api/v1/ask/{query_id}/summarize",
            json={"snippet_ids": [unknown_snippet_id]},
            headers=self._auth_headers(),
        )
        self.assertEqual(invalid_request.status_code, 422)
        self.assertIn("unknown values", invalid_request.json()["detail"])

        with patch(
            "app.jobs.generate_summary_sentences",
            return_value=[
                AskSummarySentence(
                    text="Summary tried citing a snippet outside the allowed set.",
                    snippet_ids=[unknown_snippet_id],
                )
            ],
        ):
            invalid_model_output = self.client.post(
                f"/api/v1/ask/{query_id}/summarize",
                json={"snippet_ids": [valid_snippet_id]},
                headers=self._auth_headers(),
            )
        self.assertEqual(invalid_model_output.status_code, 422)
        self.assertIn("invalid snippet_id", invalid_model_output.json()["detail"])

    def test_ask_summarize_rejects_mixed_cited_and_freeform_output(self) -> None:
        self._create_entry_with_current_transcript(self.user_id, "Blocker from CI pipeline instability.")
        query_response = self.client.post(
            "/api/v1/ask/query",
            json={"query_text": "blocker", "limit": 5},
            headers=self._auth_headers(),
        )
        self.assertEqual(query_response.status_code, 200)
        body = query_response.json()
        query_id = body["query_id"]
        valid_snippet_id = body["sources"][0]["id"]

        with patch(
            "app.jobs.generate_summary_sentences",
            return_value=[
                AskSummarySentence(
                    text="CI instability blocked deployment and required retried runs.",
                    snippet_ids=[valid_snippet_id],
                ),
                AskSummarySentence(
                    text="This second sentence attempts freeform output with no citation.",
                    snippet_ids=["   "],
                ),
            ],
        ):
            summarize_response = self.client.post(
                f"/api/v1/ask/{query_id}/summarize",
                json={"snippet_ids": [valid_snippet_id]},
                headers=self._auth_headers(),
            )

        self.assertEqual(summarize_response.status_code, 422)
        self.assertIn("uncited", summarize_response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
