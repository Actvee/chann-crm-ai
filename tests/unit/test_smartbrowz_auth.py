"""Master Spec 10.6 — the SmartBrowz OAuth token-refresh mechanism, built
ahead of real credentials being available (see smartbrowz_auth.py's module
docstring). Uses a real httpx.MockTransport for both the Data-tier cache
calls (via DataClient) and the simulated Zoho token endpoint — the whole
point of this module is the plumbing between two real HTTP calls, so a
hand-written fake would defeat the purpose the same way FakeDataClient
would (see test_data_client.py's own docstring for that exact lesson).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402
from chann_app.data_client import DataClient  # noqa: E402
from chann_app.services.smartbrowz_auth import (  # noqa: E402
    SmartBrowzAuthError,
    SmartBrowzTokenManager,
)


def _data_client_for(handler) -> DataClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://data-tier.test")
    return DataClient(base_url="http://data-tier.test", secret="test-secret", client=http_client)


class _FakeZohoCache:
    """Backs the Data-tier /chat/smartbrowz-token endpoints in-memory, so
    a test can assert on both what the token manager asked the Data tier
    to store AND what a subsequent read returns — exactly the round trip
    the real cache provides."""

    def __init__(self):
        self.stored: dict | None = None
        self.set_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/chat/smartbrowz-token"):
            import json
            self.stored = json.loads(request.content)
            self.set_calls += 1
            return httpx.Response(204)
        if request.method == "GET" and request.url.path.endswith("/chat/smartbrowz-token"):
            if self.stored is None:
                return httpx.Response(404, json={"detail": "no cached smartbrowz token"})
            return httpx.Response(200, json={
                "access_token": self.stored["access_token"],
                "api_domain": self.stored.get("api_domain"),
            })
        if request.method == "DELETE" and request.url.path.endswith("/chat/smartbrowz-token"):
            self.stored = None
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


@pytest.fixture(autouse=True)
def _smartbrowz_settings():
    """Isolates SmartBrowz config per test — this is process-global
    pydantic-settings state, so a test that clears it must not leak into
    the next one."""
    original = (
        settings.smartbrowz_client_id, settings.smartbrowz_client_secret,
        settings.smartbrowz_refresh_token, settings.smartbrowz_accounts_url,
    )
    settings.smartbrowz_client_id = "test-client-id"
    settings.smartbrowz_client_secret = "test-client-secret"
    settings.smartbrowz_refresh_token = "test-refresh-token"
    settings.smartbrowz_accounts_url = "https://accounts.zoho.test"
    yield
    (
        settings.smartbrowz_client_id, settings.smartbrowz_client_secret,
        settings.smartbrowz_refresh_token, settings.smartbrowz_accounts_url,
    ) = original


class TestSmartBrowzTokenManager:
    async def test_refreshes_when_nothing_is_cached(self):
        cache = _FakeZohoCache()
        client = _data_client_for(cache.handler)

        def zoho_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "accounts.zoho.test"
            assert "grant_type=refresh_token" in str(request.url)
            assert "refresh_token=test-refresh-token" in str(request.url)
            return httpx.Response(200, json={
                "access_token": "brand-new-token", "api_domain": "https://www.zohoapis.com",
                "token_type": "Bearer", "expires_in": 3600,
            })

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(zoho_handler))
        manager = SmartBrowzTokenManager(client, http_client=http_client)

        token = await manager.get_access_token()
        assert token == "brand-new-token"
        assert cache.set_calls == 1
        assert cache.stored["access_token"] == "brand-new-token"

    async def test_uses_the_cached_token_without_calling_zoho_again(self):
        cache = _FakeZohoCache()
        cache.stored = {"access_token": "already-cached", "api_domain": "https://www.zohoapis.com"}
        client = _data_client_for(cache.handler)

        def zoho_handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not call Zoho when a cached token exists")

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(zoho_handler))
        manager = SmartBrowzTokenManager(client, http_client=http_client)

        token = await manager.get_access_token()
        assert token == "already-cached"
        assert cache.set_calls == 0

    async def test_get_api_domain_returns_the_cached_domain(self):
        cache = _FakeZohoCache()
        cache.stored = {"access_token": "t", "api_domain": "https://www.zohoapis.eu"}
        client = _data_client_for(cache.handler)
        manager = SmartBrowzTokenManager(client, http_client=httpx.AsyncClient())

        domain = await manager.get_api_domain()
        assert domain == "https://www.zohoapis.eu"

    async def test_invalidate_clears_the_cache_so_the_next_call_refreshes(self):
        cache = _FakeZohoCache()
        cache.stored = {"access_token": "stale", "api_domain": None}
        client = _data_client_for(cache.handler)

        def zoho_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "access_token": "fresh-after-invalidate", "expires_in": 3600,
            })

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(zoho_handler))
        manager = SmartBrowzTokenManager(client, http_client=http_client)

        await manager.invalidate()
        assert cache.stored is None
        token = await manager.get_access_token()
        assert token == "fresh-after-invalidate"

    async def test_missing_credentials_raise_a_clear_error_not_a_crash(self):
        """10.6: a provider outage (or here, simply not being configured
        yet) must surface as a clear render failure, never something that
        could be mistaken for AI fabricating a document."""
        settings.smartbrowz_client_id = ""
        settings.smartbrowz_client_secret = ""
        settings.smartbrowz_refresh_token = ""
        cache = _FakeZohoCache()
        client = _data_client_for(cache.handler)
        manager = SmartBrowzTokenManager(client, http_client=httpx.AsyncClient())

        with pytest.raises(SmartBrowzAuthError, match="not configured"):
            await manager.get_access_token()

    async def test_a_non_200_from_zoho_raises_a_clear_error(self):
        cache = _FakeZohoCache()
        client = _data_client_for(cache.handler)

        def zoho_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_code"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(zoho_handler))
        manager = SmartBrowzTokenManager(client, http_client=http_client)

        with pytest.raises(SmartBrowzAuthError, match="400"):
            await manager.get_access_token()

    async def test_a_200_with_no_access_token_raises_a_clear_error(self):
        """Documented real Zoho behaviour: an expired/revoked refresh_token
        can come back as HTTP 200 with an "error" field in the body
        instead of a proper 4xx — this must not be mistaken for success."""
        cache = _FakeZohoCache()
        client = _data_client_for(cache.handler)

        def zoho_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "invalid_refresh_token"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(zoho_handler))
        manager = SmartBrowzTokenManager(client, http_client=http_client)

        with pytest.raises(SmartBrowzAuthError, match="no.*access_token|did not return"):
            await manager.get_access_token()

    async def test_cached_token_ttl_is_shorter_than_the_real_expiry(self):
        """The Data-tier cache entry must expire before the real Zoho
        token does, with a safety margin — never the other way round,
        which would mean serving an already-dead token as if it were
        still good."""
        cache = _FakeZohoCache()
        client = _data_client_for(cache.handler)

        captured_ttl = {}
        original_set = client.set_smartbrowz_token

        async def capturing_set(access_token, *, api_domain=None, ttl_seconds=3300):
            captured_ttl["value"] = ttl_seconds
            return await original_set(
                access_token, api_domain=api_domain, ttl_seconds=ttl_seconds,
            )
        client.set_smartbrowz_token = capturing_set

        def zoho_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(zoho_handler))
        manager = SmartBrowzTokenManager(client, http_client=http_client)
        await manager.get_access_token()

        assert captured_ttl["value"] < 3600
