"""LINE Messaging API adapter — reply (Phase 1), push (Phase 6), and
richer message objects (Phase 10).

`reply_text`/`push_text` are kept as they were and now delegate to the
message-object forms. Callers that only have a string should not have to
know the wire shape, and every existing call site stays correct.
"""
from __future__ import annotations

import httpx

from ..config import channel_access_token

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# LINE rejects a request carrying more than five messages, and truncates
# text at 5000 characters. Both are enforced here rather than at each call
# site so a long list reply degrades predictably instead of failing the
# whole send.
MAX_MESSAGES_PER_REQUEST = 5
MAX_TEXT_LENGTH = 5000


class LineReplyError(RuntimeError):
    pass


def text_message(text: str, quick_reply: list[dict] | None = None) -> dict:
    """A text message, optionally with quick-reply buttons.

    Quick replies are the cheapest usability win available here: they need
    no image, no provisioning and no state, they show the person what they
    can say next instead of requiring them to remember command wording, and
    they vanish once used so they never clutter the history.
    """
    message: dict = {"type": "text", "text": (text or "")[:MAX_TEXT_LENGTH]}
    if quick_reply:
        message["quickReply"] = {"items": quick_reply[:13]}  # LINE's own cap
    return message


def quick_reply_uri(label: str, url: str) -> dict:
    """A quick-reply button that opens a URL directly.

    Used for the dashboard hand-off: the alternative is a message action
    that makes the bot answer with a link, which the person then has to tap
    a second time.
    """
    return {
        "type": "action",
        "action": {"type": "uri", "label": label[:20], "uri": url},
    }


def quick_reply_item(label: str, text: str | None = None) -> dict:
    """One quick-reply button. `label` is what the person sees (max 20
    chars, enforced by LINE); `text` is what gets sent as if they typed
    it, defaulting to the label."""
    return {
        "type": "action",
        "action": {"type": "message", "label": label[:20], "text": text or label},
    }


async def _send(
    url: str, oa: str, payload: dict, client: httpx.AsyncClient | None, what: str,
) -> None:
    access_token = channel_access_token(oa)
    if not access_token:
        raise LineReplyError(f"LINE_{oa.upper()}_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(
            url, headers={"Authorization": f"Bearer {access_token}"}, json=payload,
        )
        if response.status_code >= 400:
            raise LineReplyError(
                f"LINE {what} failed: {response.status_code} {response.text[:200]}"
            )
    finally:
        if owns_client:
            await client.aclose()


async def reply_messages(
    oa: str,
    reply_token: str,
    messages: list[dict],
    client: httpx.AsyncClient | None = None,
) -> None:
    if not reply_token:
        raise LineReplyError("LINE event has no replyToken")
    if not messages:
        raise LineReplyError("reply requires at least one message")
    await _send(
        LINE_REPLY_URL, oa,
        {"replyToken": reply_token, "messages": messages[:MAX_MESSAGES_PER_REQUEST]},
        client, "reply",
    )


async def reply_text(
    oa: str,
    reply_token: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    await reply_messages(oa, reply_token, [text_message(text)], client)


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
    if not to_line_user_id:
        raise LineReplyError("push requires a target LINE user id")

    await _send(
        LINE_PUSH_URL, oa,
        {"to": to_line_user_id, "messages": [text_message(text)]},
        client, "push",
    )


async def push_messages(
    oa: str,
    to_line_user_id: str,
    messages: list[dict],
    client: httpx.AsyncClient | None = None,
) -> None:
    if not to_line_user_id:
        raise LineReplyError("push requires a target LINE user id")
    if not messages:
        raise LineReplyError("push requires at least one message")
    await _send(
        LINE_PUSH_URL, oa,
        {"to": to_line_user_id, "messages": messages[:MAX_MESSAGES_PER_REQUEST]},
        client, "push",
    )
