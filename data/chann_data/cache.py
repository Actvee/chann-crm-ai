"""Cache-aside with fail-secure semantics (ADR-006, Master Spec 1.8).

The rule that matters: a cache outage must never widen anyone's access.

Two distinct failure behaviours are required, and conflating them is the bug
this module exists to prevent:

  FALLBACK_DB   the cache is an optimisation. On miss or outage, read the
                database. Safe because the database is the source of truth
                and returns the same or stricter answer.

  FAIL_CLOSED   the cache IS the authority (admin sessions). On outage there
                is no safe fallback, because "I cannot verify this session"
                must mean "log in again", never "assume it is valid".
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Callable

import redis

from .config import settings

log = logging.getLogger(__name__)


class CacheFailureMode(str, Enum):
    FALLBACK_DB = "fallback_db"
    FAIL_CLOSED = "fail_closed"


class CacheUnavailable(RuntimeError):
    """Raised only for FAIL_CLOSED objects. Callers must translate this into
    a re-authentication response, never into a permissive default."""


class Cache:
    def __init__(self, url: str | None = None):
        self._url = url or settings.redis_url
        self._client: redis.Redis | None = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def get_or_load(
        self,
        key: str,
        ttl_s: int,
        loader: Callable[[], Any],
        failure_mode: CacheFailureMode = CacheFailureMode.FALLBACK_DB,
    ) -> Any:
        try:
            raw = self.client.get(key)
            if raw is not None:
                return json.loads(raw)
        except redis.RedisError as exc:
            if failure_mode is CacheFailureMode.FAIL_CLOSED:
                log.warning("cache down, failing closed for key=%s", key)
                raise CacheUnavailable(key) from exc
            log.warning("cache down, falling back to database for key=%s", key)
            return loader()

        if failure_mode is CacheFailureMode.FAIL_CLOSED:
            # A miss on an authoritative object is not an invitation to guess.
            raise CacheUnavailable(key)

        value = loader()
        if value is not None:
            self.set(key, value, ttl_s)
        return value

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        try:
            self.client.setex(key, ttl_s, json.dumps(value, default=str))
        except redis.RedisError:
            log.warning("cache write failed for key=%s (non-fatal)", key)

    def set_required(self, key: str, value: Any, ttl_s: int) -> None:
        """Write an authoritative cache object or fail closed.

        Admin sessions are revocable only while Redis is authoritative.  A
        swallowed write would issue a JWT that can never be validated, so it
        is materially different from a best-effort cache-aside write.
        """
        try:
            self.client.setex(key, ttl_s, json.dumps(value, default=str))
        except redis.RedisError as exc:
            raise CacheUnavailable(key) from exc

    def invalidate(self, *keys: str) -> None:
        try:
            if keys:
                self.client.delete(*keys)
        except redis.RedisError:
            log.warning("cache invalidation failed for keys=%s", keys)

    def invalidate_required(self, *keys: str) -> None:
        try:
            if keys:
                self.client.delete(*keys)
        except redis.RedisError as exc:
            raise CacheUnavailable(",".join(keys)) from exc


# Key builders — centralised so invalidation can never drift from reads.
def k_identity(line_user_id: str) -> str:
    return f"chann_id:{line_user_id}"


def k_member(license_id: str, chann_uid: str) -> str:
    return f"license_member:{license_id}:{chann_uid}"


def k_permissions(license_id: str, chann_uid: str) -> str:
    return f"permissions:{license_id}:{chann_uid}"


def k_license_setting(license_id: str, setting_key: str) -> str:
    return f"license_setting:{license_id}:{setting_key}"


def k_admin_session(session_id: str) -> str:
    return f"admin_session:{session_id}"


cache = Cache()
