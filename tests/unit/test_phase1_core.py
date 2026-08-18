"""Phase 1 unit tests.

Covers the four things the Master Spec calls mandatory at this phase:
tenant isolation, cache fail-secure behaviour, LINE signature verification,
and Platform Admin authentication.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))

from chann_data.cache import Cache, CacheFailureMode, CacheUnavailable  # noqa: E402
from chann_data.repositories.tenant_scope import CrossTenantAccessDenied, TenantScope  # noqa: E402


# --------------------------------------------------------------------------
# Multi-tenant isolation (cross-cutting principle 4)
# --------------------------------------------------------------------------

class TestTenantIsolation:
    def test_scope_permits_its_own_license(self):
        lic = uuid.uuid4()
        TenantScope(license_id=lic).assert_owns(lic)  # must not raise

    def test_scope_refuses_another_license(self):
        scope = TenantScope(license_id=uuid.uuid4())
        with pytest.raises(CrossTenantAccessDenied):
            scope.assert_owns(uuid.uuid4())

    def test_scope_is_immutable(self):
        """A mutable scope could be widened mid-request by any code holding a
        reference to it, which would defeat the whole mechanism."""
        scope = TenantScope(license_id=uuid.uuid4())
        with pytest.raises(Exception):
            scope.license_id = uuid.uuid4()  # type: ignore[misc]


# --------------------------------------------------------------------------
# Cache fail-secure (ADR-006)
# --------------------------------------------------------------------------

class _BrokenRedis:
    """Stands in for a Redis instance that is down."""

    def get(self, *_a, **_kw):
        import redis
        raise redis.RedisError("simulated outage")

    def setex(self, *_a, **_kw):
        import redis
        raise redis.RedisError("simulated outage")

    def delete(self, *_a, **_kw):
        import redis
        raise redis.RedisError("simulated outage")


class _CacheWithBrokenClient(Cache):
    @property
    def client(self):
        return _BrokenRedis()


class TestCacheFailSecure:
    def test_outage_falls_back_to_database_for_optimisation_objects(self):
        cache = _CacheWithBrokenClient(url="redis://unused")
        calls = []

        def loader():
            calls.append(1)
            return {"role": "member"}

        result = cache.get_or_load("k", 60, loader, CacheFailureMode.FALLBACK_DB)
        assert result == {"role": "member"}
        assert calls, "a cache outage must fall through to the database"

    def test_outage_fails_closed_for_admin_sessions(self):
        """There is no safe default for 'I cannot verify this session'."""
        cache = _CacheWithBrokenClient(url="redis://unused")

        def loader():
            pytest.fail("FAIL_CLOSED objects must never consult a fallback loader")

        with pytest.raises(CacheUnavailable):
            cache.get_or_load("admin_session:x", 60, loader, CacheFailureMode.FAIL_CLOSED)

    def test_outage_never_widens_permission(self):
        """The property that actually matters: whatever the cache does, the
        answer is never more permissive than the database's answer."""
        cache = _CacheWithBrokenClient(url="redis://unused")
        result = cache.get_or_load("k", 60, lambda: None, CacheFailureMode.FALLBACK_DB)
        assert result is None, "a miss during an outage must not synthesise access"

    def test_authoritative_session_write_fails_closed(self):
        cache = _CacheWithBrokenClient(url="redis://unused")
        with pytest.raises(CacheUnavailable):
            cache.set_required("admin_session:x", {"admin_id": "a"}, 60)

    def test_authoritative_session_logout_fails_closed(self):
        cache = _CacheWithBrokenClient(url="redis://unused")
        with pytest.raises(CacheUnavailable):
            cache.invalidate_required("admin_session:x")


# --------------------------------------------------------------------------
# LINE webhook signature (Master Spec 1.6)
# --------------------------------------------------------------------------

from chann_app.line.signature import compute_signature, verify_signature  # noqa: E402
from chann_app.line.client import LINE_REPLY_URL, reply_text  # noqa: E402
from chann_app.config import settings as application_settings  # noqa: E402


class TestLineSignature:
    def test_valid_signature_accepted(self):
        secret, body = "s3cr3t", b'{"events":[]}'
        assert verify_signature(secret, body, compute_signature(secret, body))

    def test_tampered_body_rejected(self):
        secret = "s3cr3t"
        sig = compute_signature(secret, b'{"events":[]}')
        assert not verify_signature(secret, b'{"events":[{"type":"message"}]}', sig)

    def test_signature_from_a_different_oa_is_rejected(self):
        """Each OA has its own channel secret. Accepting a signature made with
        another OA's secret would let a Customer message be replayed as a
        Technician message."""
        body = b'{"events":[]}'
        customer_sig = compute_signature("customer-secret", body)
        assert not verify_signature("technician-secret", body, customer_sig)

    def test_missing_secret_or_signature_rejected(self):
        body = b"{}"
        assert not verify_signature("", body, "whatever")
        assert not verify_signature("secret", body, "")


class TestLineReply:
    @pytest.mark.asyncio
    async def test_reply_uses_the_access_token_for_the_arrival_oa(self, monkeypatch):
        monkeypatch.setattr(application_settings, "line_customer_channel_access_token", "customer-token")

        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == LINE_REPLY_URL
            assert request.headers["Authorization"] == "Bearer customer-token"
            assert b'"replyToken":"reply-1"' in request.content
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await reply_text("customer", "reply-1", "hello", client)

    @pytest.mark.asyncio
    async def test_reply_refuses_a_missing_access_token(self, monkeypatch):
        monkeypatch.setattr(application_settings, "line_sales_channel_access_token", "")
        with pytest.raises(Exception, match="REQUIRED_NOT_CONFIGURED"):
            await reply_text("sales", "reply-1", "hello")


# --------------------------------------------------------------------------
# Platform Admin authentication (ADR-005)
# --------------------------------------------------------------------------

from chann_app.auth.platform_admin import hash_password, verify_password  # noqa: E402
from chann_app.auth.platform_admin import decode_token  # noqa: E402
from chann_app.routers_admin import LoginIn, platform_login  # noqa: E402
from chann_app.auth.liff import LiffTokenInvalid, verify_id_token  # noqa: E402


class TestPlatformAdminAuth:
    def test_correct_password_verifies(self):
        assert verify_password(hash_password("correct horse battery"), "correct horse battery")

    def test_wrong_password_rejected(self):
        assert not verify_password(hash_password("correct horse battery"), "wrong")

    def test_hash_is_argon2_and_salted(self):
        h1, h2 = hash_password("same"), hash_password("same")
        assert h1.startswith("$argon2")
        assert h1 != h2, "identical passwords must not produce identical hashes"

    def test_plaintext_never_appears_in_the_hash(self):
        assert "hunter2" not in hash_password("hunter2")

    @pytest.mark.asyncio
    async def test_login_issues_a_revocable_session_jwt(self, monkeypatch):
        monkeypatch.setattr(application_settings, "jwt_secret", "unit-test-jwt-secret")

        class FakeDataClient:
            created = None

            async def authenticate_platform_admin(self, username, password):
                if (username, password) == ("admin", "correct"):
                    return {"admin_id": str(uuid.uuid4()), "username": "admin"}
                return None

            async def create_platform_admin_session(self, session_id, admin_id, ttl_s):
                self.created = (session_id, admin_id, ttl_s)

        client = FakeDataClient()
        result = await platform_login(LoginIn(username="admin", password="correct"), client)
        claims = decode_token(result.access_token)
        assert claims["scope"] == "platform.admin.access"
        assert "platform.admin.break_glass" in claims["permissions"]
        assert claims["jti"] == client.created[0]

    @pytest.mark.asyncio
    async def test_login_rejects_a_wrong_password(self):
        from fastapi import HTTPException

        class FakeDataClient:
            async def authenticate_platform_admin(self, _username, _password):
                return None

        with pytest.raises(HTTPException) as exc:
            await platform_login(LoginIn(username="admin", password="wrong"), FakeDataClient())
        assert exc.value.status_code == 401


class TestLiffAuthentication:
    @pytest.mark.asyncio
    async def test_missing_or_malformed_token_is_rejected(self):
        with pytest.raises(LiffTokenInvalid):
            await verify_id_token("", "customer")
        with pytest.raises(LiffTokenInvalid):
            await verify_id_token("not-a-jwt", "customer")

    @pytest.mark.asyncio
    async def test_line_verified_token_is_accepted(self, monkeypatch):
        monkeypatch.setattr(application_settings, "line_login_channel_id", "1234567890")

        async def handler(request: httpx.Request) -> httpx.Response:
            form = parse_qs(request.content.decode())
            assert form["id_token"] == ["a.b.c"]
            assert form["client_id"] == ["1234567890"]
            return httpx.Response(200, json={"sub": "U123", "name": "Test"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            claims = await verify_id_token("a.b.c", "customer", client)
        assert claims["sub"] == "U123"

    @pytest.mark.asyncio
    async def test_line_rejected_token_is_rejected(self, monkeypatch):
        monkeypatch.setattr(application_settings, "line_login_channel_id", "1234567890")

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(LiffTokenInvalid):
                await verify_id_token("a.b.c", "customer", client)


# --------------------------------------------------------------------------
# PDF seam (ADR-021)
# --------------------------------------------------------------------------

from chann_app.services.pdf.base import NullPdfRenderer, PdfOptions  # noqa: E402


class TestPdfSeam:
    @pytest.mark.asyncio
    async def test_null_renderer_fails_loudly(self):
        """Returning an empty PDF would let a blank quotation reach a customer."""
        with pytest.raises(NotImplementedError):
            await NullPdfRenderer().render("<html></html>", PdfOptions(), "key")
