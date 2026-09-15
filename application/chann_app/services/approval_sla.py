"""Service reports nobody is approving — the approval SLA.

A report a technician submitted waits on its current step. Past the shop's
`approval_sla_hours` the approvers of that step are reminded once; past the
escalation window on top of it the owner and admins are told once (owner,
15 ก.ย. 2569). Runs inside the five-minute chat sweep, next to the job SLA.
The "told" marks live as notes on the report's TICKET, which every shop's
data tier already has.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..data_client import DataClient
from .job_sla import _parse_dt, notify_owners, sla_settings
from .thai_datetime import local_tz

log = logging.getLogger(__name__)

MARK = "🔔 SLA-approval"
_TEXT = {
    "late": (
        "รายงาน {report} ของงาน {ticket}{customer} รอคุณตรวจมา {hours} ชม. แล้ว — พิมพ์ \"อนุมัติ {report}\" หรือ \"ตีกลับ {report} เหตุผล\"",
        "Report {report} for job {ticket}{customer} has waited {hours} h for your review — \"approve {report}\" or \"reject {report} reason\".",
    ),
    "escalated": (
        "⚠️ เกิน SLA อนุมัติ: รายงาน {report} ของงาน {ticket}{customer} ค้างมา {hours} ชม. ผู้อนุมัติยังไม่ตรวจ — เจ้าของอนุมัติแทนได้ (\"อนุมัติ {report}\")",
        "⚠️ Approval SLA breached: report {report} for job {ticket}{customer} has waited {hours} h — the owner may approve in their place (\"approve {report}\").",
    ),
}


async def _told(client: DataClient, license_id: str, ticket_id: str, report_id: str) -> set[str]:
    try:
        notes = await client.list_notes(license_id, "service_ticket", ticket_id)
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    prefix = f"{MARK}:{report_id}:"
    for note in notes or []:
        body = str((note or {}).get("body") or "")
        if body.startswith(prefix):
            out.add(body[len(prefix):].split(" ", 1)[0].strip())
    return out


async def sweep_reports(client: DataClient, *, now: datetime | None = None, license_ids: list[str] | None = None) -> dict:
    """One pass over every shop's submitted reports. Returns counts, never raises."""
    from . import approval
    from .notify import send_notification

    now = now or datetime.now(local_tz())
    if license_ids is None:
        try:
            tenants = await client.platform_tenants()
        except Exception:  # noqa: BLE001
            log.exception("approval sweep: could not list tenants")
            return {"error": "tenants"}
        license_ids = [str(t.get("id") or "") for t in tenants if str(t.get("status") or "") != "suspended"]
    told = 0
    checked = 0
    for license_id in license_ids:
        if not license_id:
            continue
        sla = await sla_settings(client, license_id)
        hours = int(sla.get("approval") or 0)
        if hours <= 0:
            continue
        try:
            reports = [r for r in await client.list_service_reports(license_id, status="submitted") or []
                       if str(r.get("status") or "submitted") == "submitted"]
        except Exception:  # noqa: BLE001
            log.exception("approval sweep: could not list reports for %s", license_id)
            continue
        members = None
        for report in reports:
            checked += 1
            report_id = str(report.get("id") or "")
            try:
                steps = await client.approval_steps_for_entity(license_id, approval.ENTITY_TYPE, report_id)
            except Exception:  # noqa: BLE001
                continue
            pending = [s for s in steps or [] if str(s.get("status") or "") == "pending"]
            if not pending:
                continue
            current = min(pending, key=lambda s: int(s.get("step_order") or 0))
            since = _parse_dt(current.get("created_at")) or _parse_dt(report.get("updated_at")) or _parse_dt(report.get("created_at"))
            if since is None:
                continue
            waited = now - since
            if waited < timedelta(hours=hours):
                continue
            ticket_id = str(report.get("ticket_id") or "")
            done = await _told(client, license_id, ticket_id, report_id)
            escalate = timedelta(minutes=int(sla.get("escalate") or 0))
            due = [("late", waited)]
            if escalate > timedelta(0) and waited >= timedelta(hours=hours) + escalate:
                due.append(("escalated", waited))
            if all(rule in done for rule, _w in due):
                continue
            if members is None:
                try:
                    members = await client.list_members(license_id)
                except Exception:  # noqa: BLE001
                    members = []
            try:
                ticket = await client.get_ticket(license_id, ticket_id) or {}
            except Exception:  # noqa: BLE001
                ticket = {}
            fields = dict(
                report=str(report.get("report_id") or report_id), ticket=str(ticket.get("ticket_number") or ""),
                customer=f" · {ticket['customer_name']}" if ticket.get("customer_name") else "",
                hours=int(waited.total_seconds() // 3600),
            )
            for rule, _w in due:
                if rule in done:
                    continue
                th, en = _TEXT[rule]
                th, en = th.format(**fields), en.format(**fields)
                try:
                    if rule == "escalated":
                        await notify_owners(client, license_id, th, en, entity_id=None, kind="approval_sla_escalated")
                    else:
                        for member in approval.approvers_for(current, members or []):
                            uid = str(member.get("chann_uid") or "")
                            if not uid:
                                continue
                            line_uid = await client.line_target_of(uid)
                            await send_notification(
                                client, license_id=license_id, target_chann_uid=uid, target_line_user_id=line_uid,
                                type="approval_pending", message=th, message_en=en,
                                entity_type=approval.ENTITY_TYPE, entity_id=report_id, language="th", oa="sales",
                            )
                    await client.create_note(
                        license_id, {"entity_type": "service_ticket", "entity_id": ticket_id, "body": f"{MARK}:{report_id}:{rule} {th}"},
                        actor_id=None,
                    )
                    told += 1
                except Exception:  # noqa: BLE001
                    log.exception("approval sweep: could not tell about %s on %s", rule, report_id)
    return {"checked": checked, "told": told}
