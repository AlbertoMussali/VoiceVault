from __future__ import annotations

from base64 import urlsafe_b64encode
import hashlib
import hmac
import secrets


_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a password with a random salt."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    encoded_salt = urlsafe_b64encode(salt).decode("utf-8")
    encoded_digest = urlsafe_b64encode(digest).decode("utf-8")
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${encoded_salt}${encoded_digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plain password against a stored PBKDF2 hash."""
    try:
        scheme, raw_iterations, encoded_salt, encoded_digest = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = encoded_salt.encode("utf-8")
        expected_digest = encoded_digest.encode("utf-8")
    except (ValueError, TypeError):
        return False

    candidate_digest = urlsafe_b64encode(
        hashlib.pbkdf2_hmac(
            _PBKDF2_ALGORITHM,
            password.encode("utf-8"),
            urlsafe_b64decode_bytes(salt),
            iterations,
        )
    )
    return hmac.compare_digest(candidate_digest, expected_digest)


def urlsafe_b64decode_bytes(raw: bytes) -> bytes:
    """Decode URL-safe base64 bytes, accepting optional missing padding."""
    padding = b"=" * (-len(raw) % 4)
    from base64 import urlsafe_b64decode

    return urlsafe_b64decode(raw + padding)


def generate_session_token() -> str:
    """Generate a high-entropy token suitable for refresh/session IDs."""
    return secrets.token_urlsafe(32)
