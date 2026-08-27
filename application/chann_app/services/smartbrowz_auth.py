"""SmartBrowz OAuth access-token management (Master Spec 10.6).

Zoho's OAuth pattern here: the access_token is short-lived (Zoho's own
docs say one hour) and used as a bearer credential on every SmartBrowz
REST call; the refresh_token does not expire and is exchanged for a new
access_token whenever the cached one is gone or close to expiring. There
is exactly one long-lived secret to keep safe (the refresh_token) — the
client_id/client_secret pair identifies this application, not any one
tenant, matching that SmartBrowz itself is one shared Catalyst project
serving every tenant, not a credential each company brings its own copy
of (see cache.k_smartbrowz_token in the Data tier for the same point made
about the cache key).

What this module does NOT do: call SmartBrowz's actual PDF-generation
endpoints. That adapter (HTML -> PDF via a published template version,
per 10.4/10.6) is separate, later work — this module only solves "how do
we always have a valid bearer token to put on that future call," so it
can be built, wired up, and its own tests written the moment real
SmartBrowz credentials exist, without also having to get token refresh
right at the same time.

Scope note: Zoho's own docs describe entering a scope string like
"ZohoCatalyst.<module>.<operation>" (confirmed pattern for other Catalyst
modules, e.g. "ZohoCatalyst.tables.rows.CREATE") when generating a Self
Client grant token, and the exact scope names available for a given
module are shown in the Catalyst Console's own scope picker rather than
published as a fixed list — so the precise SmartBrowz scope string isn't
hardcoded anywhere here. When generating the grant token in the Catalyst
API Console, choose whatever scope the console shows for "generate
PDF/screenshot" and "manage templates" (the two SmartBrowz capabilities
this project needs), and put the resulting refresh_token in
SMARTBROWZ_REFRESH_TOKEN — nothing in this module needs to know the
scope string itself, since scope is fixed to whatever the refresh_token
was originally granted for and is never passed again on refresh.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings
from ..data_client import DataClient

log = logging.getLogger(__name__)

# Refresh this many seconds before the cached token's real expiry, so a
# request in flight never races a token that's about to die mid-call.
# Zoho's docs give ~3600s validity; the Data-tier cache itself is also
# stored with a shorter TTL than that (see set_smartbrowz_token's default)
# for the same reason, so this is a second, independent safety margin.
REFRESH_SKEW_S = 120


class SmartBrowzAuthError(RuntimeError):
    """Raised when a usable access token cannot be obtained.

    Callers (the eventual SmartBrowz render adapter) must surface this as
    a clear render failure to whoever asked for a PDF — 10.6 is explicit
    that a provider outage must never cause AI or the runtime path to
    fabricate a document instead.
    """


class SmartBrowzTokenManager:
    """Get a valid SmartBrowz access token, refreshing through Zoho only
    when the Data-tier cache doesn't already have a good one.

    Deliberately stateless itself (no token held on the instance between
    calls) — the source of truth is the Data-tier cache
    (`k_smartbrowz_token`), shared across every Application-tier instance,
    not this object's own memory. See that cache key's docstring for why.
    """

    def __init__(self, client: DataClient, http_client: httpx.AsyncClient | None = None):
        self._client = client
        self._http = http_client or httpx.AsyncClient(timeout=15.0)

    async def get_access_token(self) -> str:
        cached = await self._client.get_smartbrowz_token()
        if cached is not None:
            return cached["access_token"]
        return await self._refresh()

    async def get_api_domain(self) -> str | None:
        """The datacenter-specific API host Zoho returns alongside the
        access token (e.g. https://www.zohoapis.com) — SmartBrowz REST
        calls must go to this domain, not a hardcoded one, since it
        depends on which datacenter the Catalyst project actually lives
        in. Forces a refresh if nothing is cached yet, since api_domain
        is only ever learned from a real token response."""
        cached = await self._client.get_smartbrowz_token()
        if cached is not None and cached.get("api_domain"):
            return cached["api_domain"]
        await self._refresh()
        cached = await self._client.get_smartbrowz_token()
        return cached.get("api_domain") if cached else None

    async def invalidate(self) -> None:
        """Forces the next get_access_token() to refresh rather than
        trust the cache — for a caller that just got a 401 from SmartBrowz
        despite a cached token looking unexpired (clock skew, a token
        revoked out-of-band in the Catalyst console, etc.)."""
        await self._client.clear_smartbrowz_token()

    async def _refresh(self) -> str:
        if not (
            settings.smartbrowz_client_id
            and settings.smartbrowz_client_secret
            and settings.smartbrowz_refresh_token
        ):
            raise SmartBrowzAuthError(
                "SmartBrowz OAuth is not configured — SMARTBROWZ_CLIENT_ID, "
                "SMARTBROWZ_CLIENT_SECRET, and SMARTBROWZ_REFRESH_TOKEN are "
                "REQUIRED_BY_PHASE_10 and are not all set"
            )
        try:
            resp = await self._http.post(
                f"{settings.smartbrowz_accounts_url}/oauth/v2/token",
                params={
                    "grant_type": "refresh_token",
                    "client_id": settings.smartbrowz_client_id,
                    "client_secret": settings.smartbrowz_client_secret,
                    "refresh_token": settings.smartbrowz_refresh_token,
                },
            )
        except httpx.HTTPError as exc:
            raise SmartBrowzAuthError(f"SmartBrowz token refresh request failed: {exc}") from exc

        if resp.status_code != 200:
            raise SmartBrowzAuthError(
                f"SmartBrowz token refresh failed: HTTP {resp.status_code} {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise SmartBrowzAuthError(
                f"SmartBrowz token refresh returned a non-JSON body: {resp.text[:300]}"
            ) from exc

        # Zoho can return HTTP 200 with an "error" field in the body
        # instead of a proper 4xx (documented Zoho OAuth behaviour) — an
        # expired/revoked refresh_token looks exactly like this, and it
        # must not be mistaken for a usable response.
        if "access_token" not in data:
            log.error("SmartBrowz token refresh returned no access_token: %s", data)
            raise SmartBrowzAuthError(
                f"SmartBrowz token refresh did not return an access_token: {data}"
            )

        access_token = data["access_token"]
        api_domain = data.get("api_domain")
        expires_in = int(data.get("expires_in", 3600))
        # Cache for less than the token's real lifetime (REFRESH_SKEW_S
        # margin), so the Data tier's own TTL expiry and this module's
        # in-flight-request safety margin agree with each other rather
        # than one silently outliving the other.
        await self._client.set_smartbrowz_token(
            access_token, api_domain=api_domain,
            ttl_seconds=max(60, expires_in - REFRESH_SKEW_S),
        )
        return access_token
