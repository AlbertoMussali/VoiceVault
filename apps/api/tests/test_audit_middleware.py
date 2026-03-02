from __future__ import annotations

import pathlib
import sys
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Base
from app.main import create_app
from app.models import AuditLog


class AuditMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.app: FastAPI = create_app(audit_session_factory=self.session_factory)

        @self.app.post("/api/v1/auth/login")
        def auth_login() -> dict[str, str]:
            return {"status": "ok"}

        @self.app.post("/api/v1/entries")
        def create_entry() -> dict[str, str]:
            return {"status": "ok"}

        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_logs_auth_requests_without_content(self) -> None:
        response = self.client.post("/api/v1/auth/login", headers={"x-user-id": "42"})
        self.assertEqual(response.status_code, 200)

        with self.session_factory() as session:
            rows = session.execute(select(AuditLog)).scalars().all()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_type, "auth_event")
        self.assertEqual(rows[0].method, "POST")
        self.assertEqual(rows[0].path, "/api/v1/auth/login")
        self.assertEqual(rows[0].status_code, 200)
        self.assertEqual(rows[0].user_id, 42)
        self.assertFalse(hasattr(rows[0], "content"))

    def test_logs_entry_requests(self) -> None:
        response = self.client.post("/api/v1/entries")
        self.assertEqual(response.status_code, 200)

        with self.session_factory() as session:
            rows = session.execute(select(AuditLog)).scalars().all()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_type, "entry_event")
        self.assertEqual(rows[0].path, "/api/v1/entries")

    def test_ignores_non_target_routes(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

        with self.session_factory() as session:
            rows = session.execute(select(AuditLog)).scalars().all()

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
