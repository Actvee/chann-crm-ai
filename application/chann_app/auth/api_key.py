"""Round 21B — the outside system's credential, turned into the same
principal the LIFF routes use.

`Authorization: Bearer chann_live_…` → SHA-256 → one Data call that
finds the key, counts it against its minute and stamps last_used_at →
a TenantPrincipal with audience "api". Everything after that is the
existing permission and tenant machinery; nothing here is bespoke.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status

from ..data_client import DataClient
from ..routers_admin import get_data_client
from ..services.authorization import TenantPrincipal, build_principal, refuse_if_suspended

KEY_PREFIX = "chann_live_"
BEARER = "Bearer "


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _error(code: int, name: str, message: str, headers: dict | None = None) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": name, "message": message}, headers=headers)


async def api_principal(
    request: Request,
    authorization: str = Header(default=""),
    client: DataClient = Depends(get_data_client),
) -> TenantPrincipal:
    raw = authorization[len(BEARER):].strip() if authorization.startswith(BEARER) else ""
    if not raw.startswith(KEY_PREFIX) or len(raw) != len(KEY_PREFIX) + 32:
        raise _error(status.HTTP_401_UNAUTHORIZED, "missing_or_malformed_key",
                     "Send the shop's API key as: Authorization: Bearer chann_live_…")
    # Validate the key body contains only ASCII alphanumeric characters
    body = raw[len(KEY_PREFIX):]
    if not re.fullmatch(r"[A-Za-z0-9]{32}", body):
        raise _error(status.HTTP_401_UNAUTHORIZED, "missing_or_malformed_key",
                     "Send the shop's API key as: Authorization: Bearer chann_live_…")
    found = await client.resolve_api_key(hash_api_key(raw))
    if found is None:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unknown_or_revoked_key",
                     "This key is not known or has been revoked. Make a new one on the dashboard.")
    limit, remaining = int(found.get("limit") or 0), int(found.get("remaining") or 0)
    if remaining < 0:
        wait = 60 - datetime.now(timezone.utc).second or 60
        # Stamped BEFORE the raise: the ext sub-app's middleware reads
        # request.state to emit X-RateLimit-*, and the one reply that most
        # needs to say how big the window is was the only one without the
        # headers (round 21B review I2).
        request.state.rate_limit = (limit, 0)
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited",
                     f"More than {limit} requests this minute. Try again in {wait} s.",
                     headers={"Retry-After": str(wait)})
    license_status = str(found.get("license_status") or "active")
    refuse_if_suspended(license_status, request.method)
    request.state.rate_limit = (limit, max(remaining, 0))
    key = found["key"]
    # round 21B fix: the ext API's /me names the key that called it (its
    # own id, the label the owner gave it, and its prefix) — not just the
    # id that was already inside chann_uid.
    request.state.api_key = {"id": key["id"], "name": key.get("name"), "key_prefix": key.get("key_prefix")}
    principal = build_principal(
        license_id=str(key["license_id"]), chann_uid=f"api:{key['id']}", role="api", is_owner=False,
        role_keys=found.get("permission_keys") or (), audience="api",
        license_status=license_status, plan_payload=found.get("plan"),
    )
    # Round 21D (spec §5.5): the external API is an Enterprise feature.
    # AFTER the key checks — a bad key is 401 whatever the plan.
    principal.require_feature("feature.external_api")
    return principal
