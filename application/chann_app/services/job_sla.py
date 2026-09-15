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
ESCALATE_MINUTES = 60
SLA_MARK = "🔔 SLA"

# The shop's own numbers (owner, 15 ก.ย. 2569: "ตั้ง SLA ได้แล้วใช่ไหม") —
# license_settings rows, set from chat ("ตั้ง SLA งานไม่มีคนรับ 1 ชม. …") or
# the company page. Missing rows fall back to the constants above.
SLA_SETTING_KEYS = {
    "unassigned": "job_sla_unassigned_minutes",
    "unaccepted": "job_sla_unaccepted_minutes",
    "no_checkin": "job_sla_late_checkin_minutes",
    "escalate": "sla_escalate_minutes",
    "approval": "approval_sla_hours",
}
SLA_DEFAULTS = {
    "unassigned": UNASSIGNED_HOURS * 60,
    "unaccepted": UNACCEPTED_HOURS * 60,
    "no_checkin": LATE_CHECKIN_MINUTES,
    "escalate": ESCALATE_MINUTES,
    "approval": 24,
}


def _as_int(value, default: int, *, lo: int = 0, hi: int = 100000) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if lo <= number <= hi else default


def sla_from_rows(rows: list[dict]) -> dict:
    """The SLA numbers a shop set, defaults for the rest. Minutes except
    "approval" (hours); 0 switches a rule off."""
    by_key = {str(r.get("setting_key") or ""): r.get("setting_value") for r in rows or []}
    return {
        name: _as_int(by_key.get(key), SLA_DEFAULTS[name]) for name, key in SLA_SETTING_KEYS.items()
    }


async def sla_settings(client: DataClient, license_id: str) -> dict:
    try:
        rows = await client.list_license_settings(str(license_id))
    except Exception:  # noqa: BLE001
        log.exception("could not read SLA settings for %s", license_id)
        rows = []
    return sla_from_rows(rows)

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
# Told once, to the owner and admins, when a rule stayed tripped for the
# escalation window after the first nudge — the shop's own number.
_ESCALATED_TEXT = {
    "unassigned": (
        "⚠️ เกิน SLA: งาน {code} ยังไม่มีคนรับผิดชอบมา {hours} ชม. หลังเตือนแล้วก็ยังไม่ขยับ ({who}) — ช่วยดูด้วยครับ",
        "⚠️ SLA breached: job {code} still has nobody assigned after {hours} h and a reminder ({who}).",
    ),
    "unaccepted": (
        "⚠️ เกิน SLA: งาน {code} ช่างยังไม่ตอบรับมา {hours} ชม. หลังเตือนแล้ว ({who}) — มอบหมายใหม่หรือโทรตามด้วยครับ",
        "⚠️ SLA breached: job {code} is still unaccepted after {hours} h and a reminder ({who}).",
    ),
    "no_checkin": (
        "⚠️ เกิน SLA: งาน {code} เลยเวลานัด {when} มา {minutes} นาที ยังไม่เช็คอินหลังเตือนแล้ว ({who})",
        "⚠️ SLA breached: job {code} is {minutes} min past {when} with no check-in after a reminder ({who}).",
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


def rules_tripped(ticket: dict, now: datetime, sla: dict | None = None) -> list[tuple[str, dict]]:
    """Which rules this job trips right now, with the words for the message.

    `sla` is the shop's numbers (sla_settings); a rule set to 0 is off. Past
    the escalation window an "escalated_<rule>" entry follows the rule."""
    sla = sla or dict(SLA_DEFAULTS)
    status = str(ticket.get("status") or "")
    if status in ("completed", "cancelled", "in_progress"):
        return []
    out: list[tuple[str, dict]] = []
    who = str(ticket.get("customer_name") or "").strip() or "-"
    escalate = timedelta(minutes=int(sla.get("escalate") or 0))

    def _both(rule: str, words: dict, waited: timedelta, limit: timedelta) -> None:
        out.append((rule, words))
        if escalate > timedelta(0) and waited >= limit + escalate:
            out.append((f"escalated_{rule}", words))

    if status == "open" and not ticket.get("assigned_to_ref") and int(sla.get("unassigned") or 0) > 0:
        since = _parse_dt(ticket.get("created_at"))
        limit = timedelta(minutes=int(sla["unassigned"]))
        if since is not None and now - since >= limit:
            _both("unassigned", {"hours": int((now - since).total_seconds() // 3600), "who": who}, now - since, limit)
    if status == "assigned" and ticket.get("assigned_to_ref") and str(ticket.get("accept_status") or "") == "pending" \
            and int(sla.get("unaccepted") or 0) > 0:
        since = _parse_dt(ticket.get("updated_at")) or _parse_dt(ticket.get("created_at"))
        limit = timedelta(minutes=int(sla["unaccepted"]))
        if since is not None and now - since >= limit:
            _both("unaccepted", {"hours": int((now - since).total_seconds() // 3600), "who": who}, now - since, limit)
    if status == "assigned" and int(sla.get("no_checkin") or 0) > 0:
        when = _appointment(ticket)
        limit = timedelta(minutes=int(sla["no_checkin"]))
        if when is not None and now - when >= limit:
            _both("no_checkin", {
                "minutes": int((now - when).total_seconds() // 60), "who": who,
                "when": when.strftime("%H:%M"),
            }, now - when, limit)
    return out


async def notify_owners(client: DataClient, license_id: str, text: str, text_en: str, *, entity_id: str | None = None,
                        kind: str = "sla_escalated") -> int:
    """The owner and the admins, in LINE — where an SLA breach escalates."""
    from .notify import send_notification

    told = 0
    try:
        members = await client.list_members(license_id)
    except Exception:  # noqa: BLE001
        return 0
    for m in members or []:
        if str(m.get("role") or "").lower() not in ("owner", "admin") or str(m.get("status") or "active") != "active":
            continue
        uid = str(m.get("chann_uid") or "")
        if not uid:
            continue
        try:
            line_uid = await client.line_target_of(uid)
            await send_notification(
                client, license_id=license_id, target_chann_uid=uid, target_line_user_id=line_uid,
                type=kind, message=text, message_en=text_en, entity_type="service_ticket" if entity_id else None,
                entity_id=entity_id, language="th", oa="sales",
            )
            told += 1
        except Exception:  # noqa: BLE001
            log.exception("SLA escalation to %s failed", uid)
    return told


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
        sla = await sla_settings(client, license_id)
        for ticket in tickets or []:
            checked += 1
            tripped = rules_tripped(ticket, now, sla)
            if not tripped:
                continue
            ticket_id = str(ticket.get("id") or "")
            code = str(ticket.get("ticket_number") or ticket_id)
            done = await _already_told(client, license_id, ticket_id)
            for rule, words in tripped:
                if rule in done:
                    continue
                if rule.startswith("escalated_"):
                    th, en = _ESCALATED_TEXT[rule[len("escalated_"):]]
                else:
                    th, en = _TEXT[rule]
                th, en = th.format(code=code, **words), en.format(code=code, **words)
                try:
                    if rule.startswith("escalated_"):
                        await notify_owners(client, license_id, th, en, entity_id=ticket_id)
                    elif rule == "unassigned":
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
