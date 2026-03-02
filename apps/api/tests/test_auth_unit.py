from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import generate_session_token, hash_password, verify_password


class AuthUnitTests(unittest.TestCase):
    def test_hash_and_verify_password(self) -> None:
        password = "correct horse battery staple"
        password_hash = hash_password(password)
        self.assertTrue(verify_password(password, password_hash))

    def test_verify_password_rejects_wrong_password(self) -> None:
        password_hash = hash_password("expected-password")
        self.assertFalse(verify_password("wrong-password", password_hash))

    def test_generate_session_token_is_unique(self) -> None:
        first = generate_session_token()
        second = generate_session_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 40)
        self.assertGreaterEqual(len(second), 40)


if __name__ == "__main__":
    unittest.main()
