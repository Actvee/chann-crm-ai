"""Platform Admin authentication — username/password (ADR-005, path 2).

argon2 for hashing, short-lived JWT for the session. This account can perform
break-glass operations across every tenant, so it is the most sensitive
credential in the system; the bootstrap password rules in
database/scripts/seed_reference.py exist for that reason.
"""
from __future__ import annotations

import datetime as dt
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from ..config import settings

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(password_hash: str, plain: str) -> bool:
    try:
        _hasher.verify(password_hash, plain)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def issue_token(admin_id: str, username: str, session_id: str | None = None) -> str:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is REQUIRED_NOT_CONFIGURED")
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": admin_id,
        "username": username,
        "scope": "platform.admin.access",
        "permissions": ["platform.admin.access", "platform.admin.break_glass"],
        "jti": session_id or str(uuid.uuid4()),
        "iat": now,
        "exp": now + dt.timedelta(seconds=settings.jwt_ttl_s),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is REQUIRED_NOT_CONFIGURED")
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
