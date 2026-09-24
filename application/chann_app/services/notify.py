"""Notification delivery — Master Spec 6.8.

Order matters: the row is written FIRST, then LINE push is attempted. If push
fails, the notification still exists and still shows in the dashboard, so the
user is not silently left unaware. The reverse order would mean a LINE outage
loses the notification entirely.

The dashboard badge is not "sent" anywhere — it polls the unread count (6.8),
so writing the row is all the dashboard side needs.
"""
from __future__ import annotations

import logging
import uuid

from ..data_client import DataClient
from ..line.client import LineReplyError, image_message, push_messages, push_text, text_message

log = logging.getLogger(__name__)

# Which OA to push a given notification type through. A technician should not
# get work notifications on the customer OA they may not even have added.
TYPE_TO_OA = {
    "chat_session_new": "sales",
    "approval_pending": "sales",
    # Phase 14-B: the outcome goes back to the technician who filed it —
    # under its own type, so the dashboard can tell "please approve" from
    # "it was approved" (review E14, 6 Sep 2026).
    "approval_approved": "technician",
    "approval_rejected": "technician",
    # The two things a customer is told about their own job and may need
    # to point back at later, so they are rows and not just pushes: the
    # work being finished at check-out, and that being withdrawn when the
    # shop sends the report back (owner, 10 Sep 2026).
    "ticket_completed": "customer",
    "ticket_reopened": "customer",
    # Phase 17.5.4: the tenant owner hears about the trial deadline.
    "trial_expiring": "sales",
    "trial_expired": "sales",
    # Round 18: the paid subscription ends the same way.
    "subscription_expiring": "sales",
    "subscription_expired": "sales",
    "transfer_request": "sales",
    "sla_warning": "technician",
    "followup_due": "sales",
    "warranty_expiring": "sales",
    # Round 20V: a customer or a deal handed to a colleague — told on the
    # Sales OA, with the record's code and who handed it over.
    "record_reassigned": "sales",
    # Round 20V: the receipt goes to the customer who paid, on their OA,
    # with the link — the one push a bill makes to the person who owes it.
    "receipt_issued": "customer",
    # Round 21C: the quotation or the bill itself, handed over on request.
    "document_sent": "customer",
    # Round 21D: the owner hears a turned-away join and a plan change.
    "member_limit_reached": "sales",
    "plan_changed": "sales",
}
DEFAULT_OA = "sales"


def _entity_uuid(entity_id: str | None, *, type: str, entity_type: str | None) -> str | None:
    """`notifications.entity_id` is a UUID column. A caller that passes a
    business code (a CHN- uid, a ticket number) used to get a 422 from the
    Data Tier and the notification was silently lost — the shop was never
    told a customer had linked (review E1, 6 Sep 2026). The row matters
    more than the link: write it without the reference and say so."""
    if entity_id is None or entity_id == "":
        return None
    try:
        return str(uuid.UUID(str(entity_id)))
    except (ValueError, AttributeError, TypeError):
        log.warning(
            "notification %s carries a non-UUID entity_id %r (%s); stored without it",
            type, entity_id, entity_type,
        )
        return None


class NotificationNotDelivered(RuntimeError):
    """The strict road's failure: the row may exist, the push did not go.

    Only a caller whose business action IS the push asks for it
    (`raise_on_failure=True` — handing a document to the customer, round
    21E). Everything else keeps the swallow-and-log behaviour below."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


async def send_notification(
    client: DataClient,
    *,
    license_id: str,
    target_chann_uid: str,
    target_line_user_id: str | None,
    type: str,
    message: str,
    message_en: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    delivery_line: bool = True,
    delivery_dashboard: bool = True,
    language: str = "th",
    oa: str | None = None,
    quick_reply: list | None = None,
    images: list[str] | None = None,
    raise_on_failure: bool = False,
    ref: str | None = None,
) -> dict:
    """Record, then push. Returns the stored notification either way.

    `images` (round 20T): https links pushed as picture messages BEFORE
    the words, in one request, so the customer sees the photo and then
    the line about it — with the quick reply still on the last message.

    `quick_reply` exists so that the pushes which carry a button — the
    live-chat lines that offer "จบการสนทนา" and "คุยกับร้าน" — can come
    through HERE rather than calling push_text directly. Four of them did,
    which meant four customer-facing pushes left no notification row: they
    could not be counted, so a per-shop push quota would have read low
    (OA audit, 18 ก.ย. 2569). Routing them through this function without
    carrying the button would have been a worse trade — a customer told
    the conversation ended, with no way to reopen it.
    """
    # Phase 20 i18n: the READER's language, not the sender's. A caller that
    # supplies message_en is asking for the recipient's preference to
    # decide; one that has only one text gets no lookup, nothing to gain.
    if message_en:
        try:
            prefs = await client.get_display_preferences(target_chann_uid) or {}
            language = str(prefs.get("language") or language or "th")
        except Exception:  # noqa: BLE001
            pass
    async def record() -> dict:
        return await client.create_notification(
            license_id,
            target_chann_uid=target_chann_uid,
            type=type,
            message=message,
            message_en=message_en,
            entity_type=entity_type,
            entity_id=_entity_uuid(entity_id, type=type, entity_type=entity_type),
            delivery_line=delivery_line,
            delivery_dashboard=delivery_dashboard,
        )

    # `ref` names the record in the log (e.g. "quote Q-2026-0010"), so the
    # owner can find a push that did not go by the code he typed (21E).
    about = f" ({ref})" if ref else ""

    target_oa = oa or TYPE_TO_OA.get(type, DEFAULT_OA)
    plan_locked = False
    if target_oa == "customer" and delivery_line and license_id:
        # Round 21D (spec §3.4, R5): a shop without the Customer LINE link
        # does not push to customers. The row is still written — it is the
        # record of what the shop did — just not delivered over LINE.
        from . import entitlements

        if not await entitlements.feature_allowed(client, license_id, "feature.customer_line_link"):
            delivery_line = False
            plan_locked = True

    # The strict road (round 21E review, Important 3): the push IS the
    # business action, so LINE goes first and the row is written only once
    # LINE accepted. A refused push leaves no record of a send that never
    # happened — and a retry is never mistaken for a repeat.
    if raise_on_failure:
        if plan_locked:
            # Its own reason (final fix, Task 11): the plan switched the
            # push off — not a customer without a LINE target.
            log.info("notification%s not pushed: the plan has no Customer LINE link", about)
            raise NotificationNotDelivered("plan_locked")
        if not delivery_line or not target_line_user_id:
            log.warning("notification%s has no target_line_user_id; nothing sent", about)
            raise NotificationNotDelivered("no_line_target")
        try:
            sent_ids = await _push(
                target_oa, target_line_user_id,
                message_en if (language == "en" and message_en) else message, images, quick_reply,
            )
        except LineReplyError as exc:
            log.error("LINE push failed%s: %s", about, exc)
            raise NotificationNotDelivered("push_failed", str(exc)) from exc
        # LINE accepted: the customer HAS it. A row that cannot be written
        # now must not turn that into "ส่งไม่สำเร็จ — ลองอีกครั้ง", which
        # would push it a second time (21E re-review 2, I-1). Logged loud
        # instead; the send stands.
        try:
            row = await record()
        except Exception:  # noqa: BLE001 — the push is the fact that matters
            log.exception("LINE accepted%s but the notification row could not be written", about)
            row = {"id": None, "row_missing": True}
        await _map_pushed(client, license_id, sent_ids, entity_type, entity_id)
        return row

    row = await record()

    if not delivery_line:
        return row

    if not target_line_user_id:
        # Recorded but undeliverable over LINE. Worth a log line: it usually
        # means an identity was created without a LINE user ID, which should
        # not happen through the normal webhook path.
        log.warning(
            "notification %s%s has no target_line_user_id; dashboard only", row.get("id"), about
        )
        return row

    text = message_en if (language == "en" and message_en) else message
    try:
        sent_ids = await _push(target_oa, target_line_user_id, text, images, quick_reply)
    except LineReplyError as exc:
        # Deliberately swallowed: the notification is already durable, and
        # raising here would fail whatever business action triggered it —
        # a LINE hiccup must not roll back an approval or a ticket assignment.
        log.error("LINE push failed for notification %s%s: %s", row.get("id"), about, exc)
        return row

    await _map_pushed(client, license_id, sent_ids, entity_type, entity_id)
    return row


async def _push(oa: str, to: str, text: str, images, quick_reply) -> list:
    pictures = [image_message(u) for u in (images or [])[:4] if str(u).startswith("https://")]
    if pictures:
        return await push_messages(oa, to, [*pictures, text_message(text, quick_reply=quick_reply)])
    return await push_text(oa, to, text, quick_reply=quick_reply)


async def _map_pushed(client, license_id, sent_ids, entity_type, entity_id) -> None:
    """The pushed message is now something a person can reply to: map its
    id to the record, exactly as the webhook does for bot replies, so
    "reply to this and type อนุมัติ" resolves the report it names."""
    if not (entity_type and entity_id):
        return
    for message_id in sent_ids or []:
        try:
            await client.record_message_entity(
                license_id, str(message_id), entity_type, str(entity_id),
            )
        except Exception:  # noqa: BLE001
            log.exception("could not map pushed message %s to %s", message_id, entity_type)
