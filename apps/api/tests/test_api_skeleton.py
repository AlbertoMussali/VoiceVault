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
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_audio_storage_root = os.environ.get("AUDIO_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["API_VERSION"] = "9.9.9-test"
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/api_skeleton.db"
        os.environ["AUDIO_STORAGE_ROOT"] = f"{self.temp_dir.name}/audio"
        get_settings.cache_clear()
        reset_engine_cache()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        if self.original_api_version is None:
            os.environ.pop("API_VERSION", None)
        else:
            os.environ["API_VERSION"] = self.original_api_version
        if self.original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_database_url
        if self.original_audio_storage_root is None:
            os.environ.pop("AUDIO_STORAGE_ROOT", None)
        else:
            os.environ["AUDIO_STORAGE_ROOT"] = self.original_audio_storage_root
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


if __name__ == "__main__":
    unittest.main()
