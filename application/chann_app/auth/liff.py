"""LIFF ID Token verification (ADR-005, path 1).

Phase 1 ships the verification boundary and its failure modes. The live call
to LINE's verify endpoint is wired here but is skipped when no channel ID is
configured, so DEV can run before LINE credentials exist — the tests assert
that a missing or malformed token is rejected either way.
"""
from __future__ import annotations

import httpx

from ..config import settings

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


class LiffTokenInvalid(Exception):
    pass


def _channel_id_for(audience: str) -> str:
    if audience not in {"customer", "sales", "technician"}:
        return ""
    return settings.line_login_channel_id


async def verify_id_token(id_token: str, audience: str,
                          client: httpx.AsyncClient | None = None) -> dict:
    if not id_token or id_token.count(".") != 2:
        raise LiffTokenInvalid("malformed id token")

    channel_id = _channel_id_for(audience)
    if not channel_id:
        raise LiffTokenInvalid("LINE Login channel id is REQUIRED_NOT_CONFIGURED")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.post(
            LINE_VERIFY_URL,
            data={"id_token": id_token, "client_id": channel_id},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise LiffTokenInvalid(f"line rejected token: {resp.status_code}")
        claims = resp.json()
        if "sub" not in claims:
            raise LiffTokenInvalid("token has no subject")
        return claims
    finally:
        if owns_client:
            await client.aclose()
