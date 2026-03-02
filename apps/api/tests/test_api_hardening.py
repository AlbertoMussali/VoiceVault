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
from app.models import Entry, User
from app.settings import get_settings


class ApiHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {name: os.environ.get(name) for name in self._env_keys()}
        self.temp_dir = tempfile.TemporaryDirectory()

        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/api_hardening.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"
        os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
        os.environ["RATE_LIMIT_REQUESTS"] = "100"
        os.environ["RATE_LIMIT_AUTH_REQUESTS"] = "100"
        os.environ["MAX_REQUEST_SIZE_BYTES"] = "2097152"
        os.environ["MAX_AUDIO_UPLOAD_SIZE_BYTES"] = "26214400"
        os.environ["CORS_ALLOWED_ORIGINS"] = "https://app.example.com"

        get_settings.cache_clear()
        reset_engine_cache()
        self.client = TestClient(create_app())
        self.client.get("/health")

    def tearDown(self) -> None:
        self.client.close()
        for name, value in self._env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    @staticmethod
    def _env_keys() -> list[str]:
        return [
            "DATABASE_URL",
            "ENTRY_AUTH_TOKEN",
            "RATE_LIMIT_WINDOW_SECONDS",
            "RATE_LIMIT_REQUESTS",
            "RATE_LIMIT_AUTH_REQUESTS",
            "MAX_REQUEST_SIZE_BYTES",
            "MAX_AUDIO_UPLOAD_SIZE_BYTES",
            "CORS_ALLOWED_ORIGINS",
        ]

    @staticmethod
    def _create_entry() -> uuid.UUID:
        session = get_sessionmaker()()
        try:
            user = User(email=f"hardening-{uuid.uuid4()}@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.flush()
            entry = Entry(user_id=user.id, status="draft")
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id
        finally:
            session.close()

    def test_request_size_limit_rejects_oversized_json_payload(self) -> None:
        os.environ["MAX_REQUEST_SIZE_BYTES"] = "20"
        get_settings.cache_clear()
        self.client.close()
        self.client = TestClient(create_app())

        response = self.client.post(
            "/api/v1/auth/login",
            content='{"email":"too-big@example.com","password":"password123"}',
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("Request body exceeds limit", response.json()["detail"])

    def test_audio_upload_uses_dedicated_size_limit(self) -> None:
        os.environ["MAX_REQUEST_SIZE_BYTES"] = "1024"
        os.environ["MAX_AUDIO_UPLOAD_SIZE_BYTES"] = "10"
        get_settings.cache_clear()
        self.client.close()
        self.client = TestClient(create_app())
        self.client.get("/health")

        entry_id = self._create_entry()
        response = self.client.post(
            f"/api/v1/entries/{entry_id}/audio",
            data=b"01234567890",
            headers={"content-type": "audio/webm", "Authorization": "Bearer entry-secret-test"},
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("Request body exceeds limit", response.json()["detail"])

    def test_rate_limit_applies_to_api_requests(self) -> None:
        os.environ["RATE_LIMIT_REQUESTS"] = "1"
        os.environ["RATE_LIMIT_AUTH_REQUESTS"] = "100"
        get_settings.cache_clear()
        self.client.close()
        self.client = TestClient(create_app())

        headers = {"Authorization": "Bearer entry-secret-test"}
        first = self.client.get("/api/v1/entries", headers=headers)
        second = self.client.get("/api/v1/entries", headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["detail"], "Rate limit exceeded. Retry later.")
        self.assertEqual(second.headers.get("x-ratelimit-limit"), "1")

    def test_rate_limit_applies_to_auth_requests_with_stricter_budget(self) -> None:
        os.environ["RATE_LIMIT_REQUESTS"] = "100"
        os.environ["RATE_LIMIT_AUTH_REQUESTS"] = "1"
        get_settings.cache_clear()
        self.client.close()
        self.client = TestClient(create_app())

        payload = {"email": "nobody@example.com", "password": "password123"}
        first = self.client.post("/api/v1/auth/login", json=payload)
        second = self.client.post("/api/v1/auth/login", json=payload)

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers.get("x-ratelimit-limit"), "1")

    def test_cors_allows_only_configured_origins(self) -> None:
        allowed = self.client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        denied = self.client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://not-allowed.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "https://app.example.com")
        self.assertEqual(denied.status_code, 400)
        self.assertIsNone(denied.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
