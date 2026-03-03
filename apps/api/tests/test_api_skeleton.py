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

from app.db import reset_engine_cache
from app.main import create_app
from app.settings import get_settings


class ApiSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_version = os.environ.get("API_VERSION")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["API_VERSION"] = "9.9.9-test"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/api_skeleton.db"
        get_settings.cache_clear()
        reset_engine_cache()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        if self.original_api_version is None:
            os.environ.pop("API_VERSION", None)
        else:
            os.environ["API_VERSION"] = self.original_api_version
        if self.original_entry_auth_token is None:
            os.environ.pop("ENTRY_AUTH_TOKEN", None)
        else:
            os.environ["ENTRY_AUTH_TOKEN"] = self.original_entry_auth_token
        if self.original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_database_url
        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_version_endpoint_uses_settings_loader(self) -> None:
        response = self.client.get("/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"version": "9.9.9-test"})

    def test_entry_routes_require_authorization_header(self) -> None:
        list_response = self.client.get("/api/v1/entries")
        detail_response = self.client.get("/api/v1/entries/00000000-0000-0000-0000-000000000000")

        self.assertEqual(list_response.status_code, 401)
        self.assertEqual(detail_response.status_code, 401)
        self.assertEqual(list_response.json(), {"detail": "Unauthorized"})
        self.assertEqual(detail_response.json(), {"detail": "Unauthorized"})

    def test_entry_routes_accept_valid_bearer_token(self) -> None:
        headers = {"Authorization": "Bearer entry-secret-test"}
        list_response = self.client.get("/api/v1/entries", headers=headers)
        detail_response = self.client.get(
            "/api/v1/entries/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json(), {"entries": []})
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json(), {"entry_id": "00000000-0000-0000-0000-000000000000"})

    def test_entry_routes_support_access_tokens_for_authenticated_users(self) -> None:
        signup = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "skeleton-user@example.com", "password": "StrongPass123"},
        )
        self.assertEqual(signup.status_code, 201)
        access_token = signup.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        list_response = self.client.get("/api/v1/entries", headers=headers)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json(), {"entries": []})


if __name__ == "__main__":
    unittest.main()
