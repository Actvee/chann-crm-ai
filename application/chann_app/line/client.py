"""Minimal LINE Messaging API adapter — reply (Phase 1) and push (Phase 6)."""
from __future__ import annotations

import httpx

from ..config import channel_access_token

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


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


async def push_text(
    oa: str,
    to_line_user_id: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Unsolicited push — Master Spec 6.8.

    Separate from reply_text because push has no replyToken and is billed and
    rate-limited differently by LINE; a reply token is also single-use and
    expires, so it cannot serve a notification raised minutes later.
    """
    access_token = channel_access_token(oa)
    if not access_token:
        raise LineReplyError(f"LINE_{oa.upper()}_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED")
    if not to_line_user_id:
        raise LineReplyError("push requires a target LINE user id")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"to": to_line_user_id, "messages": [{"type": "text", "text": text}]},
        )
        if response.status_code >= 400:
            raise LineReplyError(f"LINE push failed: {response.status_code} {response.text[:200]}")
    finally:
        if owns_client:
            await client.aclose()
