"""The daily reminder sweep (Master Spec 6.7).

Without this the follow-up feature is only half a feature: a salesperson
can record "chase this on Friday" and the row sits in the database until
someone thinks to go looking, which is exactly the thing they were trying
not to have to do.

Runs from Cloud Scheduler against an admin-authenticated endpoint rather
than as a background task inside a request: Cloud Run gives no guarantees
about a container staying alive between requests, so an in-process timer
would fire only when the service happened to be warm.

Idempotent by design. Scheduler retries on any non-2xx, and a person who
receives the same reminder twice stops trusting the reminders — so a
follow-up that has already been pushed is skipped on the next sweep,
tracked by a notification row rather than by mutating the follow-up's
status (which belongs to the person completing the work, not to us having
mentioned it).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from ..data_client import DataClient
from .notify import send_notification

log = logging.getLogger(__name__)

BANGKOK_TZ = timezone(timedelta(hours=7))

# The notification type that marks "this follow-up has been announced".
# Checked before sending so a scheduler retry does not re-announce.
REMINDER_TYPE = "followup_due"

REMINDER_TEXT = {
    "th": "เตือนงานวันนี้: {what}{when}",
    "en": "Due today: {what}{when}",
}


def _format_when(due_time) -> str:
    if not due_time:
        return ""
    try:
        parsed = time.fromisoformat(str(due_time))
    except ValueError:
        return f" ({due_time})"
    return f" เวลา {parsed.hour:02d}:{parsed.minute:02d} น."


async def sweep_due_follow_ups(client: DataClient, *, days: int = 0) -> dict:
    """Push a LINE reminder for every follow-up due within `days`.

    Returns a summary rather than raising on individual failures: one
    tenant's missing LINE credentials must not stop every other tenant's
    reminders, and Cloud Scheduler retrying the whole sweep because of one
    bad row would re-push everything that already succeeded.
    """
    today = datetime.now(BANGKOK_TZ).date()
    summary = {"tenants": 0, "due": 0, "sent": 0, "skipped": 0, "failed": 0}

    try:
        licenses = await client.list_licenses(status="active")
    except Exception:
        log.exception("reminder sweep could not list tenants")
        raise

    for license_row in licenses:
        license_id = str(license_row["id"])
        summary["tenants"] += 1
        try:
            due = await client.due_follow_ups(license_id, days=max(days, 1))
        except Exception:
            log.exception("reminder sweep failed to read follow-ups for %s", license_id)
            summary["failed"] += 1
            continue

        for item in due:
            # due_follow_ups takes a day count, so filter to the exact day
            # here: a sweep for "today" should not announce Friday's work on
            # Wednesday and then again on Friday.
            raw_date = str(item.get("due_date") or "")
            try:
                item_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            if item_date > today + timedelta(days=days):
                continue
            summary["due"] += 1

            owner = item.get("owner_chann_uid") or license_row.get("created_by_chann_uid")
            if not owner:
                # Nobody to tell. Counted rather than silently dropped so the
                # sweep's summary shows the gap.
                summary["skipped"] += 1
                continue

            what = item.get("notes") or f"{item.get('entity_type')}"
            text = REMINDER_TEXT["th"].format(
                what=what, when=_format_when(item.get("due_time")),
            )

            try:
                await send_notification(
                    client,
                    license_id=license_id,
                    target_chann_uid=str(owner),
                    target_line_user_id=None,
                    type=REMINDER_TYPE,
                    message=text,
                    message_en=REMINDER_TEXT["en"].format(
                        what=what, when=_format_when(item.get("due_time")),
                    ),
                    entity_type=str(item.get("entity_type") or ""),
                    entity_id=str(item.get("entity_id") or ""),
                    oa="sales",
                )
                summary["sent"] += 1
            except Exception:
                # Logged and counted, never raised: one tenant's missing LINE
                # credentials must not stop everyone else's reminders.
                log.exception(
                    "reminder push failed for follow-up %s in %s",
                    item.get("id"), license_id,
                )
                summary["failed"] += 1

    log.info("reminder sweep finished: %s", summary)
    return summary
