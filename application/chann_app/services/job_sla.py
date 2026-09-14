"""Jobs that nobody is moving — the nudge the shop never had.

Every other clock in the product has a sweep: overdue chats (every five
minutes), follow-ups (08:00), trials, quotes and warranties (nightly). A
repair job had none. A customer's fault could sit unassigned all day, a
technician could leave an assignment unanswered, or the appointment hour
could pass with no check-in, and nobody was told unless they went looking
(found 14 ก.ย. 2569 while auditing the business flow).

Three rules, each told ONCE per job (a marker note on the job remembers):

  unassigned   open with nobody assigned for longer than UNASSIGNED_HOURS
  unaccepted   assigned but not accepted for longer than UNACCEPTED_HOURS
  no_checkin   the appointment time passed LATE_CHECKIN_MINUTES ago and the
               job is still "assigned" (nobody checked in)

Dispatchers hear all three; the technician hears the two that are theirs.
Runs inside the five-minute chat sweep, so no new Scheduler job.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from ..data_client import DataClient
from .thai_datetime import local_tz

log = logging.getLogger(__name__)

UNASSIGNED_HOURS = 2
UNACCEPTED_HOURS = 1
LATE_CHECKIN_MINUTES = 30
SLA_MARK = "🔔 SLA"

_TEXT = {
    "unassigned": (
        "งาน {code} ยังไม่มีคนรับผิดชอบมา {hours} ชม. แล้ว ({who}) — มอบหมายช่างด้วยครับ",
        "Job {code} has had nobody assigned for {hours} h ({who}) — please dispatch it.",
    ),
    "unaccepted": (
        "งาน {code} มอบหมายแล้วแต่ช่างยังไม่ตอบรับมา {hours} ชม. ({who})",
        "Job {code} was assigned {hours} h ago and the technician has not accepted it ({who}).",
    ),
    "no_checkin": (
        "งาน {code} เลยเวลานัด {when} มาแล้ว {minutes} นาที ยังไม่มีการเช็คอิน ({who})",
        "Job {code} is {minutes} min past its {when} appointment with no check-in ({who}).",
    ),
}


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=local_tz())
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=local_tz())


def _appointment(ticket: dict) -> datetime | None:
    raw_date, raw_time = ticket.get("scheduled_date"), ticket.get("scheduled_time")
    if not raw_date:
        return None
    try:
        day = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)[:10])
        clock = raw_time if isinstance(raw_time, time) else (time.fromisoformat(str(raw_time)[:8]) if raw_time else None)
    except ValueError:
        return None
    if clock is None:
        return None
    return datetime.combine(day, clock, tzinfo=local_tz())


def rules_tripped(ticket: dict, now: datetime) -> list[tuple[str, dict]]:
    """Which rules this job trips right now, with the words for the message."""
    status = str(ticket.get("status") or "")
    if status in ("completed", "cancelled", "in_progress"):
        return []
    out: list[tuple[str, dict]] = []
    who = str(ticket.get("customer_name") or "").strip() or "-"
    if status == "open" and not ticket.get("assigned_to_ref"):
        since = _parse_dt(ticket.get("created_at"))
        if since is not None and now - since >= timedelta(hours=UNASSIGNED_HOURS):
            out.append(("unassigned", {"hours": int((now - since).total_seconds() // 3600), "who": who}))
    if status == "assigned" and ticket.get("assigned_to_ref") and str(ticket.get("accept_status") or "") == "pending":
        since = _parse_dt(ticket.get("updated_at")) or _parse_dt(ticket.get("created_at"))
        if since is not None and now - since >= timedelta(hours=UNACCEPTED_HOURS):
            out.append(("unaccepted", {"hours": int((now - since).total_seconds() // 3600), "who": who}))
    if status == "assigned":
        when = _appointment(ticket)
        if when is not None and now - when >= timedelta(minutes=LATE_CHECKIN_MINUTES):
            out.append(("no_checkin", {
                "minutes": int((now - when).total_seconds() // 60), "who": who,
                "when": when.strftime("%H:%M"),
            }))
    return out


async def _already_told(client: DataClient, license_id: str, ticket_id: str) -> set[str]:
    try:
        notes = await client.list_notes(license_id, "service_ticket", ticket_id)
    except Exception:  # noqa: BLE001
        return set()
    told = set()
    for note in notes or []:
        body = str((note or {}).get("body") or "")
        if body.startswith(SLA_MARK + ":"):
            told.add(body[len(SLA_MARK) + 1:].split(" ", 1)[0].strip())
    return told


async def sweep_jobs(client: DataClient, *, now: datetime | None = None, license_ids: list[str] | None = None) -> dict:
    """One pass over every shop's jobs. Returns counts, never raises."""
    from . import chat  # the notify helpers live with the handlers

    now = now or datetime.now(local_tz())
    if license_ids is None:
        try:
            tenants = await client.platform_tenants()
        except Exception:  # noqa: BLE001
            log.exception("job sweep: could not list tenants")
            return {"error": "tenants"}
        license_ids = [str(t.get("id") or "") for t in tenants if str(t.get("status") or "") != "suspended"]
    told = 0
    checked = 0
    for license_id in license_ids:
        if not license_id:
            continue
        try:
            tickets = await client.list_tickets(license_id)
        except Exception:  # noqa: BLE001
            log.exception("job sweep: could not list tickets for %s", license_id)
            continue
        for ticket in tickets or []:
            checked += 1
            tripped = rules_tripped(ticket, now)
            if not tripped:
                continue
            ticket_id = str(ticket.get("id") or "")
            code = str(ticket.get("ticket_number") or ticket_id)
            done = await _already_told(client, license_id, ticket_id)
            for rule, words in tripped:
                if rule in done:
                    continue
                th, en = _TEXT[rule]
                th, en = th.format(code=code, **words), en.format(code=code, **words)
                try:
                    if rule == "unassigned":
                        await chat._notify_dispatchers(client, license_id, th, en)
                    else:
                        await chat._notify_ticket_change(client, license_id, ticket_id, th, "th", text_en=en)
                    await client.create_note(
                        license_id,
                        {"entity_type": "service_ticket", "entity_id": ticket_id, "body": f"{SLA_MARK}:{rule} {th}"},
                        actor_id=None,
                    )
                    told += 1
                except Exception:  # noqa: BLE001
                    log.exception("job sweep: could not tell about %s on %s", rule, code)
    return {"checked": checked, "told": told}
