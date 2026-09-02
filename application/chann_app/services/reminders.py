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
from .thai_datetime import format_thai_date

log = logging.getLogger(__name__)

BANGKOK_TZ = timezone(timedelta(hours=7))

# The notification type that marks "this follow-up has been announced".
# Checked before sending so a scheduler retry does not re-announce.
REMINDER_TYPE = "followup_due"

DIGEST_HEADING = {
    "th": "งานวันนี้ {count} รายการ",
    "en": "{count} due today",
}

# Owner decision (2 Sep 2026): the morning digest announces only work that
# has not passed — today and, when the sweep is called with a lookahead,
# ahead. Overdue rows are NOT announced. First design put them under their
# own "ค้างเกินกำหนด" heading; the owner reviewed the rendering and chose
# silence instead: the digest is "what is coming", and slipped work is
# looked up on demand with รายการเตือน (which names each row's real date)
# and cleared with ยกเลิกเตือน. due_within() still returns overdue rows on
# purpose — the filter lives in the sweep, not the query, so the list
# command keeps seeing them.

# Whole-day items line up under a dash so the timed ones read as a column.
NO_TIME_MARK = "—"

ENTITY_LABEL = {
    "customer": {"th": "ลูกค้า", "en": "Customer"},
    "deal": {"th": "ดีล", "en": "Deal"},
    "quote": {"th": "ใบเสนอราคา", "en": "Quote"},
}


def _format_when(due_time) -> str:
    if not due_time:
        return ""
    try:
        parsed = time.fromisoformat(str(due_time))
    except ValueError:
        return f" ({due_time})"
    return f" เวลา {parsed.hour:02d}:{parsed.minute:02d} น."


def _coerce_date(value) -> date | None:
    """A `date` from whatever the wire actually carried, or None.

    Returns None rather than raising so one malformed row cannot stop every
    other tenant's reminders — the caller logs the offending value and its
    type, which is what would have identified a real production failure
    here immediately instead of after several wrong guesses.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        # Handles "2026-09-01" and "2026-09-01T00:00:00[+07:00]" alike.
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _render_line(item: dict, language: str, today: date) -> str:
    """One row of the digest: when, who, and what to do.

    A row due on a day other than today shows that date, so the reader
    never has to guess which entries the heading's "วันนี้" actually
    covers. Today's rows keep the bare clock column they always had.
    """
    raw_time = item.get("due_time")
    clock = NO_TIME_MARK
    if raw_time:
        try:
            parsed = time.fromisoformat(str(raw_time))
            clock = f"{parsed.hour:02d}:{parsed.minute:02d}"
        except ValueError:
            clock = str(raw_time)

    due_on = item.get("due_on")
    if isinstance(due_on, date) and due_on != today:
        when = format_thai_date(due_on) if language == "th" else due_on.isoformat()
        head = when if clock == NO_TIME_MARK else f"{when} {clock}"
    else:
        head = clock

    line = f"{head} · {item['who']}"
    if item.get("subject"):
        # Indented under its own row rather than appended: a subject is a
        # sentence, and running it onto the end makes both harder to scan.
        line += f"\n        {item['subject']}"
    return line


def _render_digest(items: list[dict], language: str, today: date) -> str:
    """The whole message one person receives.

    One message instead of one per follow-up: five due items used to arrive
    as five separate LINE pushes, which is harder to read than a list and
    spends five of the 500 free pushes a LINE account gets each month.

    Overdue rows never reach here — the sweep drops them (owner decision,
    see the note by DIGEST_HEADING). A row dated later than today (a
    lookahead sweep) shows its date via _render_line.
    """
    heading = DIGEST_HEADING[language].format(count=len(items))
    return heading + "\n\n" + "\n".join(_render_line(i, language, today) for i in items)


async def _describe_entity(
    client: DataClient, license_id: str, entity_type: str, entity_id: str,
) -> str:
    """A person or record a reminder is about, named the way its owner would
    name it.

    Mirrors chat.py's _describe_entity_by_id, which fixed the identical
    "every row just says customer" problem in the work list. Kept as its own
    copy rather than imported across the seam: reminders.py is a background
    service and must not depend on the chat module's import graph.

    Falls back to the type label rather than raising — a reminder that says
    less is still worth sending.
    """
    label = ENTITY_LABEL.get(entity_type, {}).get("th", entity_type)
    try:
        if entity_type == "customer":
            rows = await client.list_customers(license_id)
            row = next((r for r in rows if str(r.get("id")) == entity_id), None)
            if row:
                name = " ".join(
                    p for p in (row.get("first_name"), row.get("last_name")) if p
                ).strip()
                code = row.get("customer_id") or ""
                return f"{label} {name} ({code})" if name else f"{label} {code}"
        elif entity_type == "deal":
            rows = await client.list_deals(license_id)
            row = next((r for r in rows if str(r.get("id")) == entity_id), None)
            if row:
                return f"{label} {row.get('deal_id') or ''}".strip()
        elif entity_type == "quote":
            rows = await client.list_quotes(license_id)
            row = next((r for r in rows if str(r.get("id")) == entity_id), None)
            if row:
                return f"{label} {row.get('quote_id') or ''}".strip()
    except Exception:
        log.exception(
            "could not describe %s/%s for a reminder", entity_type, entity_id
        )
    return label


async def sweep_due_follow_ups(client: DataClient, *, days: int = 0) -> dict:
    """Send each person ONE message listing everything they owe today.

    Collects across every tenant first, then sends per person. Two reasons
    it works this way rather than pushing as it goes:

    * Five due follow-ups used to mean five separate LINE messages arriving
      together, which is harder to read than one list and burns five of the
      500 free pushes a LINE account gets per month.
    * A person who belongs to more than one tenant does not think in
      tenants. Their work is their work; the fact that the platform stores
      it under two licenses is not their problem.

    Returns a summary rather than raising on individual failures: one
    tenant's missing credentials must not stop every other tenant's
    reminders, and Cloud Scheduler retrying the whole sweep because of one
    bad row would re-push everything that already succeeded.
    """
    today = datetime.now(BANGKOK_TZ).date()
    summary = {"tenants": 0, "due": 0, "sent": 0, "skipped": 0, "failed": 0, "overdue_dropped": 0}

    try:
        # NOT status="active": a new tenant's license defaults to "trial"
        # (see License.status), so filtering on "active" silently swept
        # zero tenants — the sweep would have reported a clean
        # {"tenants": 0} and looked like it worked. Excluding "suspended"
        # matches how phase65.py already selects usable tenants elsewhere,
        # and treats any status added later as active by default, which is
        # the safer direction for a reminder nobody would otherwise send.
        licenses = await client.list_licenses(exclude_status="suspended")
    except Exception:
        log.exception("reminder sweep could not list tenants")
        raise

    # chann_uid -> the lines that person needs to see today.
    per_person: dict[str, list[dict]] = {}

    for license_row in licenses:
        license_id = str(license_row["id"])
        summary["tenants"] += 1
        try:
            due = await client.due_follow_ups(license_id, days=max(days, 1))
        except Exception:
            log.exception("reminder sweep failed to read follow-ups for %s", license_id)
            summary["failed"] += 1
            continue

        # What this tenant already announced today. Cloud Scheduler retries a
        # failed run, and a run can fail after sending some of its messages;
        # without this every retry re-sends what already went out, and a
        # person who gets the same reminder twice stops trusting reminders.
        try:
            already = await client.announced_today(license_id, REMINDER_TYPE)
        except Exception:
            # A failure here must not stop the sweep, but it does mean the
            # duplicate guard is off for this tenant — say so rather than
            # silently risking a repeat.
            log.exception(
                "could not read today's notifications for %s; duplicate guard is off",
                license_id,
            )
            already = set()

        for item in due:
            # due_follow_ups takes a day count, so filter to the exact day
            # here: a sweep for "today" should not announce Friday's work on
            # Wednesday and then again on Friday.
            #
            # Parsed defensively rather than assuming one wire format. A
            # production TypeError comparing str to date happened here with
            # code that looked correct in isolation and could not be
            # reproduced locally from any obvious input.
            item_date = _coerce_date(item.get("due_date"))
            if item_date is None:
                log.warning(
                    "skipping follow-up %s in %s: unreadable due_date %r (%s)",
                    item.get("id"), license_id,
                    item.get("due_date"), type(item.get("due_date")).__name__,
                )
                summary["skipped"] += 1
                continue
            if item_date > today + timedelta(days=days):
                continue
            if item_date < today:
                # Owner decision: the digest announces what is coming, not
                # what slipped — overdue rows are dropped here, visible
                # only through รายการเตือน. Counted under their own key so
                # the sweep response still shows how much sits past due
                # (the rows stay pending; nothing here mutates them).
                summary["overdue_dropped"] += 1
                continue
            summary["due"] += 1

            entity_id = str(item.get("entity_id") or "")
            if entity_id and entity_id in already:
                # Announced earlier today — this is a retry, not new work.
                summary["skipped"] += 1
                continue

            owner = item.get("owner_chann_uid") or license_row.get("created_by_chann_uid")
            if not owner:
                # Nobody to tell. Counted rather than silently dropped so the
                # sweep's summary shows the gap.
                summary["skipped"] += 1
                continue

            entity_type = str(item.get("entity_type") or "")
            per_person.setdefault(str(owner), []).append({
                "license_id": license_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "due_time": item.get("due_time"),
                # Already parsed above; rendering needs it to tell today's
                # work from work that slipped, and re-parsing the wire value
                # at render time is how the str-vs-date comparison bug got
                # in the first time.
                "due_on": item_date,
                # Name the record, not its type. "customer" on its own is
                # true and useless — it was what every reminder said before
                # this, because notes were never stored and entity_type was
                # the fallback.
                "who": await _describe_entity(client, license_id, entity_type, entity_id),
                "subject": (item.get("notes") or "").strip(),
            })

    for owner, items in per_person.items():
        # Timed work first and in clock order, then whole-day items: someone
        # reading this at 08:00 wants to know what is coming and when.
        items.sort(key=lambda i: (i["due_time"] is None, str(i["due_time"] or "")))

        try:
            line_target = await client.line_target_of(owner)
        except Exception:
            log.exception("could not resolve a LINE target for %s", owner)
            line_target = None

        # Recorded against the tenant the first item belongs to. A single
        # message can span tenants for a person who belongs to several; the
        # notification row has to name one, and the alternative — splitting
        # the message back apart per tenant — is the thing this batching
        # exists to avoid.
        license_id = items[0]["license_id"]

        try:
            await send_notification(
                client,
                license_id=license_id,
                target_chann_uid=owner,
                target_line_user_id=line_target,
                type=REMINDER_TYPE,
                message=_render_digest(items, "th", today),
                message_en=_render_digest(items, "en", today),
                entity_type=items[0]["entity_type"],
                # The first item's id, so the duplicate guard has something
                # to match on. Every other item in the digest is recorded by
                # its own marker row below.
                entity_id=items[0]["entity_id"],
                oa="sales",
            )
            summary["sent"] += 1
        except Exception:
            # Logged and counted, never raised: one person's delivery
            # failure must not stop everyone else's.
            log.exception("reminder digest failed for %s", owner)
            summary["failed"] += 1
            continue

        # Mark the remaining items as announced, so a retry does not rebuild
        # a digest containing work that already went out. Dashboard-only, so
        # these produce no second LINE message.
        for extra in items[1:]:
            try:
                await client.create_notification(
                    extra["license_id"],
                    target_chann_uid=owner,
                    type=REMINDER_TYPE,
                    message=_render_line(extra, "th", today),
                    message_en=_render_line(extra, "en", today),
                    entity_type=extra["entity_type"],
                    entity_id=extra["entity_id"],
                    delivery_line=False,
                    delivery_dashboard=True,
                )
            except Exception:
                log.exception(
                    "could not record digest item %s for %s", extra["entity_id"], owner
                )

    log.info("reminder sweep finished: %s", summary)
    return summary
