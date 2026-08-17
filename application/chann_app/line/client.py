"""Minimal LINE Messaging API adapter used by the Phase 1 reply path."""
from __future__ import annotations

import httpx

from ..config import channel_access_token

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


class LineReplyError(RuntimeError):
    pass


async def reply_text(
    oa: str,
    reply_token: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    access_token = channel_access_token(oa)
    if not access_token:
        raise LineReplyError(f"LINE_{oa.upper()}_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED")
    if not reply_token:
        raise LineReplyError("LINE event has no replyToken")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(
            LINE_REPLY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )
        if response.status_code >= 400:
            raise LineReplyError(f"LINE reply failed: {response.status_code} {response.text[:200]}")
    finally:
        if owns_client:
            await client.aclose()
