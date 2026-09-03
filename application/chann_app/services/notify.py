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

from ..data_client import DataClient
from ..line.client import LineReplyError, push_text

log = logging.getLogger(__name__)

# Which OA to push a given notification type through. A technician should not
# get work notifications on the customer OA they may not even have added.
TYPE_TO_OA = {
    "chat_session_new": "sales",
    "approval_pending": "sales",
    # Phase 14-B: a rejected report goes back to the technician who filed it.
    "approval_rejected": "technician",
    "transfer_request": "sales",
    "sla_warning": "technician",
    "followup_due": "sales",
    "warranty_expiring": "sales",
}
DEFAULT_OA = "sales"


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
) -> dict:
    """Record, then push. Returns the stored notification either way."""
    row = await client.create_notification(
        license_id,
        target_chann_uid=target_chann_uid,
        type=type,
        message=message,
        message_en=message_en,
        entity_type=entity_type,
        entity_id=entity_id,
        delivery_line=delivery_line,
        delivery_dashboard=delivery_dashboard,
    )

    if not delivery_line:
        return row

    if not target_line_user_id:
        # Recorded but undeliverable over LINE. Worth a log line: it usually
        # means an identity was created without a LINE user ID, which should
        # not happen through the normal webhook path.
        log.warning(
            "notification %s has no target_line_user_id; dashboard only", row.get("id")
        )
        return row

    text = message_en if (language == "en" and message_en) else message
    try:
        sent_ids = await push_text(oa or TYPE_TO_OA.get(type, DEFAULT_OA), target_line_user_id, text)
    except LineReplyError as exc:
        # Deliberately swallowed: the notification is already durable, and
        # raising here would fail whatever business action triggered it —
        # a LINE hiccup must not roll back an approval or a ticket assignment.
        log.error("LINE push failed for notification %s: %s", row.get("id"), exc)
        return row

    # The pushed message is now something a person can reply to: map its
    # id to the record, exactly as the webhook does for bot replies, so
    # "reply to this and type อนุมัติ" resolves the report it names.
    if entity_type and entity_id:
        for message_id in sent_ids or []:
            try:
                await client.record_message_entity(
                    license_id, str(message_id), entity_type, str(entity_id),
                )
            except Exception:  # noqa: BLE001
                log.exception("could not map pushed message %s to %s", message_id, entity_type)
    return row
