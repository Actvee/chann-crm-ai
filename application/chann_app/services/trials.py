"""Master Spec 17.5.4 — the trial's clock.

A new tenant gets 30 days (phase65.TRIAL_DAYS). The Data Tier could
suspend an overdue trial since Phase 6.5, but nothing ever called it and
nobody was warned first (review E3, 6 Sep 2026). This sweep, run daily
by Cloud Scheduler (scheduler.tf, Asia/Bangkok):

  * tells the owner 3 days and 1 day before the trial ends
    (`trial_expiring`, dual delivery through the usual notify path);
  * suspends what is past its date and tells the owner that too
    (`trial_expired`) — suspended means read-only, never deleted.

Both notices point at the license row (a UUID) so the day's duplicate
guard (announced_today) can recognise a Scheduler retry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..data_client import DataClient
from .notify import send_notification

log = logging.getLogger(__name__)

BANGKOK_TZ = timezone(timedelta(hours=7))
WARN_DAYS_BEFORE = (3, 1)
TYPE_EXPIRING = "trial_expiring"
TYPE_EXPIRED = "trial_expired"

EXPIRING = {
    "th": "ทดลองใช้ของ {company} จะหมดอายุใน {days} วัน ({date}) — ติดต่อทีมงานเพื่อเปิดใช้งานต่อ ไม่งั้นร้านจะถูกระงับ (อ่านได้อย่างเดียว)",
    "en": "The trial for {company} ends in {days} day(s) ({date}). Contact us to keep the shop active; otherwise it becomes read-only.",
}
EXPIRED = {
    "th": "ทดลองใช้ของ {company} หมดอายุแล้ว — ร้านอยู่ในโหมดอ่านอย่างเดียว ติดต่อทีมงานเพื่อเปิดใช้งานต่อ",
    "en": "The trial for {company} has ended — the shop is now read-only. Contact us to reactivate it.",
}


def _fmt_date(value) -> str:
    text = str(value or "")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(BANGKOK_TZ).strftime("%d/%m/%Y")
    except ValueError:
        return text[:10]


async def _owner_of(client: DataClient, license_row: dict) -> str | None:
    """The owner's chann_uid: from the row when the Data Tier supplied it,
    else the first active owner member, else whoever created the tenant."""
    uid = str(license_row.get("owner_chann_uid") or "")
    if uid:
        return uid
    try:
        for member in await client.list_members(str(license_row.get("id"))):
            if str(member.get("role") or "") == "owner" and str(member.get("status") or "active") == "active":
                return str(member.get("chann_uid") or "") or None
    except Exception:  # noqa: BLE001
        log.exception("could not list members of %s", license_row.get("id"))
    return str(license_row.get("created_by_chann_uid") or "") or None


async def _tell_owner(client: DataClient, license_row: dict, *, type: str, text: dict, summary: dict, **fmt) -> bool:
    license_id = str(license_row.get("id") or "")
    owner = await _owner_of(client, license_row)
    if not license_id or not owner:
        summary["skipped"] += 1
        return False
    try:
        if license_id in await client.announced_today(license_id, type):
            summary["skipped"] += 1
            return False
    except Exception:  # noqa: BLE001
        log.exception("could not read today's %s notices for %s; duplicate guard is off", type, license_id)
    company = str(license_row.get("company_name") or license_row.get("license_code") or "")
    try:
        line_uid = await client.line_target_of(owner)
        await send_notification(
            client, license_id=license_id, target_chann_uid=owner, target_line_user_id=line_uid,
            type=type, message=text["th"].format(company=company, **fmt),
            message_en=text["en"].format(company=company, **fmt),
            entity_type="license", entity_id=license_id, oa="sales",
        )
        return True
    except Exception:  # noqa: BLE001
        log.exception("could not send %s for %s", type, license_id)
        summary["failed"] += 1
        return False


async def sweep_trials(client: DataClient, *, today=None) -> dict:
    """Warn, then suspend. One tenant's failure never stops the rest."""
    today = today or datetime.now(BANGKOK_TZ).date()
    summary = {"warned": {str(d): 0 for d in WARN_DAYS_BEFORE}, "expired": 0, "expired_ids": [], "skipped": 0, "failed": 0}

    for days in WARN_DAYS_BEFORE:
        day = today + timedelta(days=days)
        try:
            rows = await client.trials_expiring(day)
        except Exception:  # noqa: BLE001
            log.exception("could not list trials ending on %s", day)
            summary["failed"] += 1
            continue
        for row in rows:
            if await _tell_owner(
                client, row, type=TYPE_EXPIRING, text=EXPIRING, summary=summary,
                days=days, date=_fmt_date(row.get("trial_expires_at")),
            ):
                summary["warned"][str(days)] += 1

    try:
        expired = await client.expire_due_trials()
    except Exception:  # noqa: BLE001
        log.exception("trial expiry sweep could not suspend overdue trials")
        summary["failed"] += 1
        expired = []
    for row in expired:
        summary["expired"] += 1
        summary["expired_ids"].append(str(row.get("id") or ""))
        await _tell_owner(client, row, type=TYPE_EXPIRED, text=EXPIRED, summary=summary)
    return summary
