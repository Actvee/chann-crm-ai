"""LINE Messaging API adapter — reply (Phase 1), push (Phase 6), and
richer message objects (Phase 10).

`reply_text`/`push_text` are kept as they were and now delegate to the
message-object forms. Callers that only have a string should not have to
know the wire shape, and every existing call site stays correct.
"""
from __future__ import annotations

import logging
import uuid

import httpx

log = logging.getLogger(__name__)

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
        "action": {"type": "uri", "label": _fit_label(label), "uri": url},
    }


def _fit_label(label: str, limit: int = 20) -> str:
    """A label inside LINE's 20-character button limit.

    Cut on a word boundary with an ellipsis rather than mid-word. A button
    reading "พัดลมตั้งพื้น 16 นิ้" looks like a typo, and on a list of
    near-identical products the truncated tail is often the only thing
    telling them apart — which is exactly when the button is being shown.
    """
    label = (label or "").strip()
    if len(label) <= limit:
        return label
    head = label[: limit - 1]
    space = head.rfind(" ")
    # Only break on a space when it leaves something readable. Thai does
    # not space between words, so many labels have no break at all and
    # fall back to a hard cut with the ellipsis still making it obvious.
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip() + "…"


def quick_reply_item(label: str, text: str | None = None) -> dict:
    """One quick-reply button. `label` is what the person sees (max 20
    chars, enforced by LINE); `text` is what gets sent as if they typed
    it, defaulting to the label."""
    return {
        "type": "action",
        "action": {"type": "message", "label": _fit_label(label), "text": text or label},
    }


async def _send(
    url: str, oa: str, payload: dict, client: httpx.AsyncClient | None, what: str,
) -> list[str]:
    """Send, and return the ids of the messages LINE actually created.

    The response used to be discarded, which quietly broke replying: a
    reply is bound to the message a person taps, and people tap the BOT's
    message, not their own. Without these ids the mapping could only ever
    be written against the inbound message, so "reply to this and say
    ออกเอกสาร" answered "ไม่พบข้อความต้นฉบับที่ตอบกลับ" every time.

    LINE returns sentMessages only when the request carries a retry key,
    so one is generated per call.
    """
    access_token = channel_access_token(oa)
    if not access_token:
        raise LineReplyError(f"LINE_{oa.upper()}_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                # Required for LINE to return sentMessages. Also makes the
                # send idempotent, which matters because a webhook that
                # times out is retried by LINE.
                "X-Line-Retry-Key": str(uuid.uuid4()),
            },
            json=payload,
        )
        if response.status_code >= 400:
            raise LineReplyError(
                f"LINE {what} failed: {response.status_code} {response.text[:200]}"
            )
        try:
            sent = (response.json() or {}).get("sentMessages") or []
            return [str(m.get("id")) for m in sent if m.get("id")]
        except Exception:
            # A send that succeeded but whose body could not be read is
            # still a successful send; only the reply mapping is lost.
            log.warning("LINE %s succeeded but sentMessages could not be read", what)
            return []
    finally:
        if owns_client:
            await client.aclose()


async def reply_messages(
    oa: str,
    reply_token: str,
    messages: list[dict],
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    if not reply_token:
        raise LineReplyError("LINE event has no replyToken")
    if not messages:
        raise LineReplyError("reply requires at least one message")
    return await _send(
        LINE_REPLY_URL, oa,
        {"replyToken": reply_token, "messages": messages[:MAX_MESSAGES_PER_REQUEST]},
        client, "reply",
    )


async def reply_text(
    oa: str,
    reply_token: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    return await reply_messages(oa, reply_token, [text_message(text)], client)


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


# ------------------------------------------------------- Flex list bubble

# LINE rejects a bubble whose JSON exceeds 10KB. Ten rows of a code, a
# label and a button sits well inside that, and ten is also about what a
# person can read without scrolling past the reply to find the next
# message — so the same number serves both limits.
MAX_FLEX_ROWS = 10

_STAGE_COLOURS = {
    "new": "#7C89A3",
    "lead": "#7C89A3",
    "draft": "#7C89A3",
    "proposed": "#3F5EA8",
    "sent": "#3F5EA8",
    "won": "#2E8B62",
    "accepted": "#2E8B62",
    "contact": "#2E8B62",
    "lost": "#B4646C",
    "rejected": "#B4646C",
}


def _flex_row(row: dict) -> dict:
    """One line of a list bubble: title, optional badge, own action button.

    The button belongs to the row rather than the bubble because a single
    "view details" quick reply has to guess which record the person meant,
    and guessing the first one is wrong more often than not.
    """
    left = [
        {
            "type": "text", "text": str(row.get("title") or "-"),
            "size": "sm", "weight": "bold", "color": "#1A2030",
            "wrap": False, "flex": 1,
        },
    ]
    if row.get("subtitle"):
        # Stage is carried by the subtitle's colour rather than by a drawn
        # rail. Two richer versions were tried first — a nested spacer box,
        # then a gradient background — and both rendered correctly but pushed
        # ten rows of Thai text past LINE's 10KB bubble limit, which fails
        # the entire send and shows the user nothing at all. Colour on text
        # that already exists costs nothing and reads just as fast.
        left.append({
            "type": "text", "text": str(row["subtitle"]),
            "size": "xs", "wrap": False, "margin": "xs",
            "color": _STAGE_COLOURS.get(str(row.get("stage") or "").lower(), "#5A6478"),
        })

    contents = [
        {"type": "box", "layout": "vertical", "contents": left, "flex": 5},
    ]
    if row.get("action_label") and row.get("action_text"):
        contents.append({
            "type": "button",
            "action": {
                "type": "message",
                "label": _fit_label(str(row["action_label"])),
                "text": str(row["action_text"]),
            },
            "style": "link",
            "height": "sm",
            "flex": 2,
        })

    box = {
        "type": "box", "layout": "horizontal", "contents": contents,
        "alignItems": "center", "paddingAll": "sm",
    }
    return box


def flex_list_message(
    *, alt_text: str, title: str, rows: list[dict],
    footer_label: str | None = None, footer_url: str | None = None,
    note: str | None = None,
) -> dict:
    """A list as a Flex bubble.

    Rows carry their own buttons and the footer carries the one persistent
    destination (the dashboard). Quick replies are then free to be what
    they are good at — what to say next — instead of doubling as
    navigation, which is what made an earlier version confusing.
    """
    body: list[dict] = [
        {
            "type": "box", "layout": "horizontal",
            "contents": [
                {
                    "type": "text", "text": title, "weight": "bold",
                    "size": "md", "color": "#1A2030", "flex": 3,
                },
            ] + ([
                {
                    "type": "text", "text": note, "size": "xs",
                    "color": "#8B93A3", "align": "end", "gravity": "center", "flex": 2,
                }
            ] if note else []),
        },
        {"type": "separator", "margin": "md", "color": "#E5E0D8"},
    ]
    for row in rows[:MAX_FLEX_ROWS]:
        body.append(_flex_row(row))
        body.append({"type": "separator", "color": "#F0ECE5"})
    if body and body[-1].get("type") == "separator":
        body.pop()

    bubble: dict = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical", "contents": body,
            "paddingAll": "md", "backgroundColor": "#FFFFFF", "spacing": "none",
        },
    }
    if footer_label and footer_url:
        bubble["footer"] = {
            "type": "box", "layout": "vertical",
            "contents": [{
                "type": "button",
                "action": {"type": "uri", "label": footer_label[:20], "uri": footer_url},
                "style": "primary", "height": "sm", "color": "#E0A422",
            }],
            "paddingAll": "md",
        }

    # alt_text is what shows in the chat list and in notifications, and is
    # the whole message for anyone using a client that cannot render Flex.
    return {"type": "flex", "altText": alt_text[:400], "contents": bubble}
