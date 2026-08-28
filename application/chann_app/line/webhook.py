"""LINE webhook handling for the three platform-level OAs.

One route per OA rather than one shared route, because the signature must be
verified against the channel secret belonging to that specific OA. A shared
route would have to guess which secret to try, and "try them all" would let a
Customer-OA message be replayed as a Technician message.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..config import OA_CHANNELS, channel_secret
from ..data_client import DataClient
from ..services.chat import (
    ChatReply,
    maybe_handle_storefront,
    greet,
    handle_chat_message,
    handle_reply,
)
from ..services.ai.intent import unavailable_reply
from ..services.registration import handle_registration, is_unregistered
from ..services.identity import resolve_context
from .client import (
    LineReplyError,
    quick_reply_item,
    quick_reply_uri,
    reply_messages,
    text_message,
)
from .signature import verify_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook/line", tags=["line"])


# A first contact ("follow" event, or an empty message) is greeted rather than
# parsed — sending "" to the intent parser would burn a model call to learn
# nothing. Anything with actual text goes through the chat engine.
def _is_reply(event: dict) -> str | None:
    """The quoted message ID, if this event is a reply to an earlier one.

    LINE exposes this as quotedMessageId on the message object. Absent for an
    ordinary message, which is the overwhelmingly common case.
    """
    quoted = (event.get("message") or {}).get("quotedMessageId")
    return quoted or None


@router.post("/{oa}")
async def handle_webhook(
    oa: str,
    request: Request,
    x_line_signature: str = Header(default=""),
):
    if oa not in OA_CHANNELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown OA")

    raw_body = await request.body()
    secret = channel_secret(oa)
    if not secret:
        # Refusing is correct: accepting unsigned webhooks in an environment
        # where the secret is merely missing would let anyone forge messages.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LINE_{oa.upper()}_CHANNEL_SECRET is REQUIRED_NOT_CONFIGURED",
        )
    if not verify_signature(secret, raw_body, x_line_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature")

    payload = await request.json()
    client = DataClient()
    replies = []
    try:
        for event in payload.get("events", []):
            if event.get("type") != "message":
                continue
            line_user_id = event.get("source", {}).get("userId")
            if not line_user_id:
                continue
            ctx = await resolve_context(client, oa, line_user_id)
            user_text = (event.get("message") or {}).get("text") or ""

            # 9.4 storefront browsing is checked before registration status
            # at all: a customer with no shop link yet, and one already
            # linked to another shop, must both be able to search products
            # and become a Lead somewhere new. Nothing about is_unregistered
            # applies to this — there is no tenant to register against until
            # a shop is actually chosen.
            #
            # The whole decision below is wrapped in a broad try/except on
            # purpose: nothing past this point had one before, which means
            # ANY unhandled exception in any handler — a bad price string,
            # a not-found record, a bug not yet caught — silently killed the
            # request before reply_text() ever ran, and the person waiting
            # in LINE got no reply at all with nothing in any log pointing
            # at why. Reported live exactly this way. A caught, logged
            # failure with a plain apology is always better than silence.
            try:
                storefront_reply = None
                if oa == "customer" and user_text.strip():
                    storefront_reply = await maybe_handle_storefront(
                        client, message=user_text, ctx=ctx, language="th",
                    )

                if storefront_reply is not None:
                    chat = storefront_reply
                elif is_unregistered(ctx):
                    # Phase 6.5: someone with no tenant gets the registration
                    # flow, not the intent parser. There is nothing to authorise
                    # against and no tenant to act in, so a model call here would
                    # spend money to reach the same dead end.
                    chat = ChatReply(
                        text=await handle_registration(
                            client, message=user_text, ctx=ctx, audience=oa
                        )
                    )
                elif not user_text.strip():
                    chat = ChatReply(text=greet(ctx))
                else:
                    quoted_id = _is_reply(event)
                    if quoted_id:
                        chat = await handle_reply(
                            client, message_id=quoted_id, reply_text=user_text, ctx=ctx
                        )
                    else:
                        chat = await handle_chat_message(
                            client, message=user_text, ctx=ctx
                        )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "unhandled error building chat reply for oa=%s chann_uid=%s: %s",
                    oa, ctx.chann_uid, exc,
                )
                chat = ChatReply(text=unavailable_reply("th"))

            try:
                await reply_messages(
                    oa,
                    event.get("replyToken", ""),
                    [text_message(
                        chat.text,
                        quick_reply=(
                            [
                                quick_reply_item(label, send)
                                for label, send in (chat.quick_replies or [])
                            ]
                            + (
                                [quick_reply_uri(*chat.quick_reply_url)]
                                if chat.quick_reply_url else []
                            )
                        ) or None,
                    )],
                )
            except LineReplyError as exc:
                log.error("LINE reply failed for oa=%s: %s", oa, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                )

            # The mapping can only be written once a message exists to map.
            # reply_text does not return the sent message ID, so the entity is
            # bound to the message the user sent — enough for a reply-to-reply
            # to resolve, and it never invents an ID we do not have.
            if chat.entity_type and chat.entity_id and ctx.license_id:
                inbound_id = (event.get("message") or {}).get("id")
                if inbound_id:
                    try:
                        await client.record_message_entity(
                            str(ctx.license_id), inbound_id,
                            chat.entity_type, chat.entity_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Losing the mapping degrades a later reply into
                        # "ไม่พบข้อความต้นฉบับ"; it must not fail the reply
                        # the user is waiting on right now.
                        log.warning("could not record message entity map: %s", exc)

            replies.append({"chann_uid": ctx.chann_uid, "text": chat.text})
    finally:
        await client.aclose()

    # LINE only needs a 200. The replies are returned so the runtime
    # acceptance checks can assert on what would have been sent.
    return {"ok": True, "oa": oa, "replies": replies}
