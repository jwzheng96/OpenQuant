"""JWT + bcrypt helpers.

Uses `bcrypt` directly rather than `passlib` — passlib's compat shim
breaks against bcrypt 5+ (the removed `__about__` attribute, plus the
new 72-byte hard limit). Pre-truncating at 72 bytes matches what
bcrypt-the-spec actually does (only the first 72 bytes are hashed).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings


BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12       # ~0.25s on Apple M-series


def _prep(plain: str) -> bytes:
    """bcrypt only hashes first 72 bytes; truncate UTF-8 safely."""
    b = plain.encode("utf-8")
    return b[:BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prep(plain), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(plain), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------- #
# JWT                                                                           #
# ---------------------------------------------------------------------------- #


def create_access_token(
    *,
    user_id: UUID | str,
    username: str,
    role: str,
    extra: dict[str, Any] | None = None,
) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_ttl_minutes)).timestamp()),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    """Returns claims dict or None if invalid / expired."""
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError:
        return None
