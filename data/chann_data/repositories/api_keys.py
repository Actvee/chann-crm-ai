"""Round 21B — API keys: the credential an outside system presents.

A key is high-entropy (32 random alphanumerics behind a fixed prefix),
so SHA-256 is the right hash: it is the lookup index, and there is no
low-entropy password for argon2 to protect. The plaintext is returned
from `create` once and never stored.

The rate window lives here too, because Redis is a Data-tier concern:
one INCR per request on a per-minute bucket, expiring itself. A Redis
outage must never turn into a refused request — the limit is protection
against a runaway client, not an authority (cache.py's FALLBACK_DB rule).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import ApiKey
from .tenant_scope import TenantScope

log = logging.getLogger(__name__)

KEY_PREFIX = "chann_live_"
KEY_BODY_LEN = 32
PREFIX_DISPLAY_LEN = len(KEY_PREFIX) + 4
RATE_LIMIT_PER_MINUTE = 600
BUCKET_TTL_S = 120
TOUCH_EVERY = timedelta(seconds=60)
_ALPHABET = string.ascii_letters + string.digits


class ApiKeyNotFound(Exception):
    pass


class ApiKeyConflict(Exception):
    pass


def generate_key() -> str:
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(KEY_BODY_LEN))


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    return raw[:PREFIX_DISPLAY_LEN]


def rate_window_remaining(redis_client, key_id: str, *, now: datetime,
                          limit: int = RATE_LIMIT_PER_MINUTE) -> int:
    bucket = f"api_rl:{key_id}:{now.astimezone(timezone.utc):%Y%m%d%H%M}"
    try:
        pipe = redis_client.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, BUCKET_TTL_S)
        count, _ = pipe.execute()
    except Exception:  # noqa: BLE001 — the counter is protection, not authority
        log.warning("api rate window unavailable; not limiting", exc_info=True)
        return limit
    return limit - int(count)


class ApiKeyRepository:
    def __init__(self, session):
        self._s = session

    def create(self, scope: TenantScope, *, name: str, created_by_chann_uid: str | None) -> tuple[ApiKey, str]:
        name = " ".join((name or "").split())
        if not name:
            raise ApiKeyConflict("name is required")
        for _ in range(5):
            raw = generate_key()
            digest = hash_key(raw)
            clash = self._s.execute(select(ApiKey.id).where(ApiKey.key_hash == digest)).first()
            if clash is None:
                break
        else:  # pragma: no cover — 2^190 odds
            raise ApiKeyConflict("could not allocate a unique key")
        row = ApiKey(
            id=uuid.uuid4(), license_id=scope.license_id, name=name,
            key_prefix=display_prefix(raw), key_hash=digest,
            created_by_chann_uid=created_by_chann_uid,
        )
        self._s.add(row)
        self._s.flush()
        return row, raw

    def list_for_license(self, scope: TenantScope) -> list[ApiKey]:
        return list(self._s.execute(
            select(ApiKey).where(ApiKey.license_id == scope.license_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        ).scalars())

    def get(self, scope: TenantScope, key_id: uuid.UUID) -> ApiKey:
        row = self._s.execute(select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.license_id == scope.license_id,
        )).scalars().first()
        if row is None:
            raise ApiKeyNotFound("api key not found")
        return row

    def revoke(self, scope: TenantScope, key_id: uuid.UUID) -> ApiKey:
        row = self.get(scope, key_id)
        if row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            self._s.flush()
        return row

    def find_live_by_hash(self, key_hash: str) -> ApiKey | None:
        row = self._s.execute(select(ApiKey).where(ApiKey.key_hash == key_hash)).scalars().first()
        if row is None or row.revoked_at is not None:
            return None
        return row

    def touch(self, row: ApiKey, now: datetime) -> None:
        if row.last_used_at is None or now - row.last_used_at >= TOUCH_EVERY:
            row.last_used_at = now
            self._s.flush()
