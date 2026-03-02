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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import create_app
from app.models import User
from app.settings import get_settings


class AuthServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original_auth_secret_key = os.environ.get("AUTH_SECRET_KEY")
        cls.original_auth_cookie_secure = os.environ.get("AUTH_COOKIE_SECURE")
        os.environ["AUTH_SECRET_KEY"] = "test-secret-key"
        os.environ["AUTH_COOKIE_SECURE"] = "false"
        get_settings.cache_clear()

        cls.temp_db = tempfile.NamedTemporaryFile(suffix=".db")
        cls.engine = create_engine(f"sqlite+pysqlite:///{cls.temp_db.name}", future=True)
        cls.TestSessionLocal = sessionmaker(bind=cls.engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=cls.engine)

        cls.app = create_app(audit_session_factory=cls.TestSessionLocal)

        def override_get_db() -> Session:
            db = cls.TestSessionLocal()
            try:
                yield db
            finally:
                db.close()

        cls.app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.dependency_overrides.clear()
        cls.engine.dispose()
        cls.temp_db.close()
        if cls.original_auth_secret_key is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = cls.original_auth_secret_key
        if cls.original_auth_cookie_secure is None:
            os.environ.pop("AUTH_COOKIE_SECURE", None)
        else:
            os.environ["AUTH_COOKIE_SECURE"] = cls.original_auth_cookie_secure
        get_settings.cache_clear()

    def setUp(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

    def test_signup_creates_account_and_returns_tokens(self) -> None:
        response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "TeSt@Example.com", "password": "StrongPass123"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("access_token", payload)
        self.assertIn("refresh_token", payload)

        with self.TestSessionLocal() as db:
            user = db.query(User).filter(User.email == "test@example.com").one_or_none()
            self.assertIsNotNone(user)
            self.assertNotEqual(user.password_hash, "StrongPass123")

    def test_login_rejects_bad_password(self) -> None:
        self.client.post(
            "/api/v1/auth/signup",
            json={"email": "user@example.com", "password": "StrongPass123"},
        )

        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)

    def test_signup_rejects_weak_password(self) -> None:
        response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "weak@example.com", "password": "alllowercase12"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Password must include at least one uppercase letter.")

    def test_refresh_rotates_session(self) -> None:
        signup_response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "user@example.com", "password": "StrongPass123"},
        )
        old_refresh = signup_response.json()["refresh_token"]

        refresh_response = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh},
        )
        self.assertEqual(refresh_response.status_code, 200)
        new_refresh = refresh_response.json()["refresh_token"]
        self.assertNotEqual(old_refresh, new_refresh)

        replay_response = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh},
        )
        self.assertEqual(replay_response.status_code, 401)

    def test_logout_revokes_refresh_token(self) -> None:
        signup_response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "user@example.com", "password": "StrongPass123"},
        )
        refresh_token = signup_response.json()["refresh_token"]

        logout_response = self.client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )
        self.assertEqual(logout_response.status_code, 204)

        refresh_response = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_signup_sets_refresh_and_csrf_cookies(self) -> None:
        response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "cookies@example.com", "password": "StrongPass123"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("vv_refresh_token", response.cookies)
        self.assertIn("vv_csrf_token", response.cookies)

    def test_refresh_with_cookie_requires_csrf_header(self) -> None:
        signup_response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "cookie-refresh@example.com", "password": "StrongPass123"},
        )
        csrf_token = signup_response.cookies.get("vv_csrf_token")

        missing_csrf = self.client.post("/api/v1/auth/refresh")
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(missing_csrf.json()["detail"], "CSRF validation failed.")

        with_csrf = self.client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(with_csrf.status_code, 200)
        self.assertIn("refresh_token", with_csrf.json())

    def test_logout_with_cookie_requires_csrf_header(self) -> None:
        signup_response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "cookie-logout@example.com", "password": "StrongPass123"},
        )
        csrf_token = signup_response.cookies.get("vv_csrf_token")

        missing_csrf = self.client.post("/api/v1/auth/logout")
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(missing_csrf.json()["detail"], "CSRF validation failed.")

        with_csrf = self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(with_csrf.status_code, 204)

    def test_me_returns_authenticated_user(self) -> None:
        signup_response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "profile@example.com", "password": "StrongPass123"},
        )
        access_token = signup_response.json()["access_token"]

        response = self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("id", payload)
        self.assertEqual(payload["email"], "profile@example.com")


if __name__ == "__main__":
    unittest.main()
