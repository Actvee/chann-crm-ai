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
from ..services import live_chat
from ..services.chat import (
    ChatReply,
    maybe_handle_storefront,
    greet,
    handle_chat_message,
    handle_incoming_image,
    handle_incoming_location,
    handle_reply,
)
from ..services.ai.intent import unavailable_reply
from ..services.registration import first_contact, handle_registration, is_unregistered
from ..services.identity import resolve_context
from .client import (
    LineReplyError,
    flex_list_message,
    image_message,
    push_messages,
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


async def _language_of(client: DataClient, chann_uid: str) -> str:
    """"th" unless the person chose otherwise — and, as a side effect,
    the date format and time zone they chose are set for this request
    (16.3), so every date the reply prints is in their shape. A failed
    read is Thai, not an error: the preference is a courtesy, the reply
    is the point."""
    from ..services.thai_datetime import set_display_prefs

    try:
        prefs = dict(await client.get_display_preferences(chann_uid) or {})
    except Exception:  # noqa: BLE001
        set_display_prefs({})
        return "th"
    language = str(prefs.get("language") or "th").lower()
    language = language if language in ("th", "en") else "th"
    set_display_prefs({**prefs, "language": language})
    return language


async def _record_entities(client: DataClient, owed: dict, sent_ids: list[str]) -> None:
    """Map the message ids just sent onto the record they are about.

    Both the message we sent AND the one the person sent.

    The bot's message is the one that matters: people reply to the answer
    they are looking at, not to their own question. Only the inbound id
    used to be recorded — because the send discarded LINE's response — so
    replying to a bot message always answered "ไม่พบข้อความต้นฉบับที่ตอบกลับ",
    which is what made the whole reply-to feature unusable.

    The inbound id is kept too: it costs one row and makes a
    reply-to-your-own-message resolve as well.
    """
    license_id = str(owed.get("license_id") or "")
    entity_type = owed.get("entity_type")
    entity_id = owed.get("entity_id")
    if not (license_id and entity_type and entity_id):
        return
    for message_id in [*(sent_ids or []), owed.get("inbound_message_id")]:
        if not message_id:
            continue
        try:
            await client.record_message_entity(
                license_id, str(message_id), str(entity_type), str(entity_id),
            )
        except Exception as exc:  # noqa: BLE001
            # Losing the mapping degrades a later reply into
            # "ไม่พบข้อความต้นฉบับ"; it must not fail the reply the user is
            # waiting on right now.
            log.warning("could not record message entity map: %s", exc)


async def _deliver_owed_reply(
    client: DataClient, *, oa: str, event: dict, event_id: str, owed: dict,
) -> None:
    """Send an answer whose business effect already happened.

    This is the half of T01 that the old code had no way to reach: the
    handler had run, the ticket was filed, and then LINE refused the send.
    The event was already recorded as seen, so the redelivery was thanked
    and dropped, and the person who asked never heard anything.

    The reply token is tried first — a redelivery often arrives while it
    is still good, and a reply is free where a push is billed — and when
    it is gone, which is the ordinary case for a retry because reply
    tokens are short-lived, the same messages go out as a push to the same
    person. LINE's own guidance is explicit that redelivery is neither
    ordered nor guaranteed
    (https://developers.line.biz/en/docs/messaging-api/receiving-messages/),
    so if both routes fail the event stays `handled`: the answer is still
    owed, the row still says so, and the work itself is already visible on
    the record the handler wrote.
    """
    messages = list(owed.get("messages") or [])
    if not messages:
        await client.finish_webhook_event(event_id, "done")
        return

    sent_ids: list[str] = []
    delivered = False
    try:
        sent_ids = await reply_messages(oa, str(event.get("replyToken") or ""), messages)
        delivered = True
    except LineReplyError as exc:
        log.warning(
            "the reply token for redelivered LINE event %s is no longer "
            "usable (%s); pushing the answer instead", event_id, exc,
        )
        to = str(owed.get("to") or "")
        if to:
            try:
                sent_ids = await push_messages(oa, to, messages)
                delivered = True
            except LineReplyError as push_exc:
                log.error(
                    "the answer owed for LINE event %s could not be pushed "
                    "either: %s", event_id, push_exc,
                )

    if not delivered:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the answer for this event could not be delivered",
        )

    await _record_entities(client, owed, sent_ids)
    await client.finish_webhook_event(event_id, "done")


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
            event_type = event.get("type")
            # `follow` is the moment someone adds the OA. It used to be
            # dropped with every other non-message event, so adding any
            # of the three OAs produced silence — the first thing a new
            # customer or technician saw was nothing (3 Sep). It carries
            # a replyToken like a message does.
            if event_type not in ("message", "follow"):
                continue
            line_user_id = event.get("source", {}).get("userId")
            if not line_user_id:
                continue
            # A redelivery (LINE retries when the reply took too long) must
            # not file a second ticket or note: the event is claimed once
            # and the repeat dropped (review, 6 Sep 2026).
            #
            # Claimed, not merely recorded (review v3, T01). The old line
            # here wrote "seen" BEFORE the identity lookup and before the
            # reply, so a first delivery that failed left the event looking
            # finished: LINE's redelivery got a 200 and the message was
            # either never processed at all, or processed and never
            # answered. The claim below distinguishes the two halves — see
            # the Data tier's `record_webhook_event` for the four answers
            # and `finish_webhook_event` for what closes them.
            event_id = str(event.get("webhookEventId") or "")
            claim = {"state": "new", "reply": None}
            if event_id:
                claim = await client.claim_webhook_event(event_id, oa)
                if claim["state"] in ("duplicate", "in_progress"):
                    log.info(
                        "dropping redelivered LINE event %s on %s (%s)",
                        event_id, oa, claim["state"],
                    )
                    continue

            if claim["state"] == "reply_pending":
                # The handler already ran for this event and the answer
                # never left the building. Deliver it; do not run anything
                # that writes, because that write already happened.
                owed = claim.get("reply") or {}
                await _deliver_owed_reply(
                    client, oa=oa, event=event, event_id=event_id, owed=owed,
                )
                replies.append({
                    "chann_uid": str(owed.get("chann_uid") or ""),
                    "text": str(owed.get("text") or ""),
                })
                continue

            try:
                ctx = await resolve_context(client, oa, line_user_id)
                user_text = (event.get("message") or {}).get("text") or ""
                # Phase 16.3: the person's own language, on every OA. Stored
                # against the identity, so a customer who chose English at
                # one shop reads English at all of them.
                language = await _language_of(client, ctx.chann_uid)

                if event_type == "follow":
                    try:
                        text, quick = first_contact(oa, ctx, language)
                        chat = ChatReply(text=text, quick_replies=quick)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("could not build a welcome for oa=%s: %s", oa, exc)
                        chat = ChatReply(text=greet(ctx, language))
                    welcome = [text_message(chat.text, quick_reply=[
                        quick_reply_item(label, send) for label, send in chat.quick_replies
                    ] or None)]
                    # A follow has no business effect to protect, but the
                    # greeting is still something the person is owed, so
                    # the event carries it before the send is attempted.
                    # A welcome that never went out then stays visible as a
                    # `handled` row instead of vanishing (T01: "the work is
                    # visible — a push, or the record itself").
                    if event_id:
                        await client.finish_webhook_event(event_id, "handled", {
                            "oa": oa, "to": line_user_id, "messages": welcome,
                            "chann_uid": ctx.chann_uid, "text": chat.text,
                        })
                    try:
                        await reply_messages(oa, event.get("replyToken", ""), welcome)
                    except LineReplyError as exc:
                        log.error("LINE welcome failed for oa=%s: %s", oa, exc)
                    else:
                        if event_id:
                            await client.finish_webhook_event(event_id, "done")
                    replies.append({"chann_uid": ctx.chann_uid, "text": chat.text})
                    continue

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
                    message_type = str((event.get("message") or {}).get("type") or "text")
                    if message_type == "image" and not is_unregistered(ctx):
                        # 13.1: a picture is evidence on the job — stored now,
                        # while LINE still serves the bytes.
                        chat = await handle_incoming_image(
                            client, ctx=ctx, oa=oa,
                            message_id=str((event.get("message") or {}).get("id") or ""),
                            language=language,
                        )
                    elif message_type == "location" and not is_unregistered(ctx):
                        # A shared location on the technician OA is the check-in
                        # with coordinates (owner, 4 Sep: "เช็คอินบันทึกตำแหน่งไว้ด้วยไหม").
                        _loc = event.get("message") or {}
                        chat = await handle_incoming_location(
                            client, ctx=ctx, oa=oa,
                            latitude=float(_loc.get("latitude") or 0), longitude=float(_loc.get("longitude") or 0),
                            language=language,
                        )
                    elif oa == "customer" and user_text.strip():
                        storefront_reply = await maybe_handle_storefront(
                            client, message=user_text, ctx=ctx, language=language,
                        )

                    if message_type in ("image", "location") and not is_unregistered(ctx):
                        pass
                    elif storefront_reply is not None:
                        chat = storefront_reply
                    elif is_unregistered(ctx):
                        # Phase 6.5: someone with no tenant gets the registration
                        # flow, not the intent parser. There is nothing to authorise
                        # against and no tenant to act in, so a model call here would
                        # spend money to reach the same dead end.
                        registered = await handle_registration(
                            client, message=user_text, ctx=ctx, audience=oa, language=language,
                        )
                        # Text, or the report flow's own reply when linking
                        # also filed a fault the person typed earlier.
                        chat = (
                            registered if isinstance(registered, ChatReply)
                            else ChatReply(text=str(registered))
                        )
                    elif not user_text.strip():
                        chat = ChatReply(text=greet(ctx, language))
                    else:
                        quoted_id = _is_reply(event)
                        if quoted_id:
                            chat = await handle_reply(
                                client, message_id=quoted_id, reply_text=user_text, ctx=ctx,
                                language=language,
                            )
                        else:
                            chat = await handle_chat_message(
                                client, message=user_text, ctx=ctx, language=language,
                            )
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "unhandled error building chat reply for oa=%s chann_uid=%s: %s",
                        oa, ctx.chann_uid, exc,
                    )
                    chat = ChatReply(text=unavailable_reply(language))

                if not ((chat.text or "").strip() or chat.list_card or chat.images):
                    # An empty reply is a deliberate silence (a line into a live
                    # conversation); LINE gets no message, the log keeps a row.
                    replies.append({"chann_uid": ctx.chann_uid, "text": ""})
                    # Nothing is owed, so the event is finished here.
                    if event_id:
                        await client.finish_webhook_event(event_id, "done")
                    if oa in ("customer", "sales"):
                        try:
                            await live_chat.sweep(client)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("chat sweep from the webhook failed: %s", exc)
                    continue

                try:
                    quick_reply_items = (
                        [
                            quick_reply_item(label, send)
                            for label, send in (chat.quick_replies or [])
                        ]
                        + (
                            # Only when there is no card: the card carries its
                            # own footer button to the same place, and offering
                            # it twice in one reply is clutter.
                            [quick_reply_uri(*chat.quick_reply_url)]
                            if chat.quick_reply_url and not chat.list_card else []
                        )
                    ) or None

                    if chat.list_card:
                        message = flex_list_message(
                            # The plain text is the alt text, so the chat list
                            # preview and any client that cannot render Flex
                            # still get the full answer.
                            alt_text=chat.text,
                            oa=oa,
                            **chat.list_card,
                        )
                        if quick_reply_items:
                            message["quickReply"] = {"items": quick_reply_items}
                    else:
                        message = text_message(chat.text, quick_reply=quick_reply_items)

                    # Pictures first, then the words (LINE shows them in
                    # order; a reply carries at most five messages).
                    pictures = [image_message(url) for url in (chat.images or [])[:4] if url.startswith("https://")]
                    outbound = [*pictures, message]
                except LineReplyError as exc:
                    log.error("LINE reply failed for oa=%s: %s", oa, exc)
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=str(exc),
                    )

                # The seam T01 is about. Everything above has run: the
                # ticket is filed, the appointment moved, the note written.
                # Recording that BEFORE the send is what makes the two
                # halves independent — a redelivery after this line never
                # repeats the write, and never loses the answer either,
                # because the answer is in the row.
                owed = {
                    "oa": oa, "to": line_user_id, "messages": outbound,
                    "chann_uid": ctx.chann_uid, "text": chat.text,
                    "license_id": str(ctx.license_id or ""),
                    "entity_type": chat.entity_type, "entity_id": chat.entity_id,
                    "inbound_message_id": (event.get("message") or {}).get("id"),
                }
                if event_id:
                    await client.finish_webhook_event(event_id, "handled", owed)

                try:
                    sent_ids = await reply_messages(
                        oa, event.get("replyToken", ""), outbound
                    )
                except LineReplyError as exc:
                    # The event stays `handled`: the work happened once and
                    # the answer is still owed. LINE gets a 503 so it
                    # redelivers, and the redelivery takes the
                    # `reply_pending` path — reply token if it still works,
                    # push if it does not.
                    log.error("LINE reply failed for oa=%s: %s", oa, exc)
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=str(exc),
                    )

                # The message-entity map (see `_record_entities`), and then
                # the event is finished: the work happened and the person
                # has the answer.
                await _record_entities(client, owed, sent_ids or [])
                if event_id:
                    await client.finish_webhook_event(event_id, "done")

                replies.append({"chann_uid": ctx.chann_uid, "text": chat.text})
                # Phase 15 clock: every message on the two OAs that hold
                # conversations ticks the SLA/timeout sweep, so a quiet hour
                # closes a conversation even when nobody opens the dashboard.
                if oa in ("customer", "sales"):
                    try:
                        await live_chat.sweep(client)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("chat sweep from the webhook failed: %s", exc)
            except HTTPException:
                # A refusal this handler raised on purpose — the 503 from a
                # reply LINE would not take. The event's own state already
                # says what happened to it (`handled` when the business
                # effect landed), so it must NOT be released here: doing
                # that would let the redelivery repeat the write.
                raise
            except Exception:
                # Nothing was completed. Release the claim so LINE's next
                # redelivery is a retry rather than a duplicate, then let
                # the 500 out so LINE knows to send it again at all. This
                # is the first half of T01: identity lookup failing used to
                # leave the event marked seen, and the redelivery was
                # acknowledged with the message never processed.
                if event_id:
                    await client.finish_webhook_event(event_id, "failed")
                raise
    finally:
        await client.aclose()

    # LINE only needs a 200. The replies are returned so the runtime
    # acceptance checks can assert on what would have been sent.
    return {"ok": True, "oa": oa, "replies": replies}
