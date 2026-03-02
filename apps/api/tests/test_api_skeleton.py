from __future__ import annotations

import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None  # type: ignore[assignment]

from app.settings import get_settings

if TestClient is not None:
    from app.main import create_app


@unittest.skipIf(TestClient is None, "fastapi is not installed in this test environment")
class ApiSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_version = os.environ.get("API_VERSION")
        os.environ["API_VERSION"] = "9.9.9-test"
        get_settings.cache_clear()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        if self.original_api_version is None:
            os.environ.pop("API_VERSION", None)
        else:
            os.environ["API_VERSION"] = self.original_api_version
        get_settings.cache_clear()

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
