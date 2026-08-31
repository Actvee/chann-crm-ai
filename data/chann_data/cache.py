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


def k_pending_intent(chann_uid: str, oa: str) -> str:
    """Phase 6's missing piece: an in-progress slot-filling conversation.

    Deliberately Redis, not Postgres — this is conversational scratch state
    with a short TTL, not business data that needs an audit trail. If Redis
    is down the safe degrade is "ask fresh" (today's behaviour), never
    "assume the old answer" — the opposite of ADR-006's permission-cache
    rule, but the same underlying principle: an outage must never let stale
    state override what the user just said.

    Keyed by (chann_uid, oa), not chann_uid alone: chann_identities is
    global (one row per LINE user), and LINE issues the SAME user ID to the
    same physical account across every OA under one provider — the normal
    setup when one company runs all three official accounts. Without the OA
    in the key, an in-progress "create customer" conversation on the Sales
    OA could get merged with an unrelated message the same person later
    sends to the Customer OA.
    """
    return f"pending_intent:{chann_uid}:{oa}"


def k_last_customer_ref(chann_uid: str, oa: str) -> str:
    """Phase 9 — "which customer was this conversation just about?"

    Reported live: "บันทึกสมชายเป็น Contact แล้ว" followed immediately by
    "สร้างดีล" with no name at all — a completely natural way to talk, and
    the chat engine had no notion of "the customer we were just discussing"
    at all. Deliberately a SEPARATE key from pending_intent, not the same
    one reused: pending_intent is cleared the instant an action completes
    (see handle_chat_message), which is exactly the moment this needs to
    start existing. Conflating the two would mean the reference vanishes
    at the one point it's needed.

    Same (chann_uid, oa) scoping and same Redis-not-Postgres reasoning as
    pending_intent above.
    """
    return f"last_customer_ref:{chann_uid}:{oa}"


def k_last_entity_ref(chann_uid: str, oa: str) -> str:
    """Phase 6 follow-up — "which record was this conversation just
    looking at?"

    The same gap k_last_customer_ref closed for deal creation, generalised:
    reported live as "ข้อมูลลูกค้า ..." followed immediately by "นัดประชุม
    พรุ่งนี้ตอน 9 โมงเช้า" with no code at all. Forcing a code every time a
    person has just been looking straight at the record in question reads
    as the system not noticing what it just showed.

    Deliberately separate from k_last_customer_ref rather than replacing
    it: that key is customer-specific and feeds the deal-creation flow
    with fields the general form below does not carry (a bare display
    name, not a typed code). This one is generic across customer/deal/
    quote and is read by notes and reminders.

    Same (chann_uid, oa) scoping and same Redis-not-Postgres reasoning as
    pending_intent.
    """
    return f"last_entity_ref:{chann_uid}:{oa}"


def k_smartbrowz_token() -> str:
    """Phase 10 — the cached SmartBrowz OAuth access token.

    Global, not per-tenant: this is one platform-level Zoho Catalyst
    project credential shared across every tenant, not something each
    company brings its own copy of. Cached here (Redis, via the Data
    tier) rather than in Application-tier process memory because
    Application-tier Cloud Run instances are stateless and recycle
    independently — an in-memory cache there would refresh redundantly on
    every cold start and across every concurrent instance, wasting calls
    against Zoho's own refresh-rate limit (documented as 10 access tokens
    per refresh token per 10 minutes). A shared cache means every
    Application-tier instance sees the same still-valid token.
    """
    return "smartbrowz:access_token"


cache = Cache()
