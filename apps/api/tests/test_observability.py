from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.db import reset_engine_cache
from app.main import create_app
from app.observability import JsonLogFormatter
from app.settings import get_settings


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "ENTRY_AUTH_TOKEN": os.environ.get("ENTRY_AUTH_TOKEN"),
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/observability.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"
        get_settings.cache_clear()
        reset_engine_cache()
        app = create_app()
        test_router = APIRouter()

        @test_router.get("/__tests__/raise")
        def raise_error() -> None:
            raise RuntimeError("boom")

        app.include_router(test_router)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.get("/health")

    def tearDown(self) -> None:
        self.client.close()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    def test_frontend_error_reporting_endpoint_accepts_payload(self) -> None:
        response = self.client.post(
            "/api/v1/observability/frontend-errors",
            json={
                "message": "Cannot read property 'x' of undefined",
                "stack": "TypeError: ...",
                "source": "window.error",
                "level": "error",
                "url": "http://localhost:5173/app",
                "user_agent": "Mozilla/5.0",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "accepted"})

    def test_unhandled_exception_returns_sanitized_error_payload(self) -> None:
        response = self.client.get("/__tests__/raise")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"error_code": "INTERNAL_SERVER_ERROR", "message": "Internal server error."},
        )

    def test_json_log_formatter_emits_structured_payload(self) -> None:
        formatter = JsonLogFormatter()
        record = logging.makeLogRecord(
            {
                "name": "voicevault.test",
                "levelname": "INFO",
                "msg": "hello",
                "fields": {"request_id": "abc", "path": "/health"},
            }
        )

        parsed = json.loads(formatter.format(record))
        self.assertEqual(parsed["logger"], "voicevault.test")
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["message"], "hello")
        self.assertEqual(parsed["request_id"], "abc")
        self.assertEqual(parsed["path"], "/health")
        self.assertIn("timestamp", parsed)


if __name__ == "__main__":
    unittest.main()
