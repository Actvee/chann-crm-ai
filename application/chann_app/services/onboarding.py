"""Phase 16.4 — what happens in the shop when a new customer links.

`auto_accept_new_customers` (a license setting, off by default): on, and
the person's profile is complete (name + phone) → they become a customer
record in the shop's CRM at once; off, or incomplete → the shop's
owner/admin/CS are told and asked to add them. Either way the link
itself already stands — the person can report a fault; this is about
the shop's own customer list.
"""
from __future__ import annotations

import logging

from ..data_client import DataClient, DataTierError
from .notify import send_notification

log = logging.getLogger(__name__)

SETTING_KEY = "auto_accept_new_customers"
_RECIPIENT_ROLES = ("owner", "admin", "cs")


async def auto_accept_enabled(client: DataClient, license_id: str) -> bool:
    try:
        rows = await client.list_license_settings(license_id)
    except Exception:
        log.exception("could not read license settings for %s", license_id)
        return False
    for row in rows:
        if str(row.get("setting_key")) == SETTING_KEY:
            value = row.get("setting_value")
            return value is True or str(value).lower() in ("true", "1", "on", "yes", "เปิด")
    return False


async def after_customer_linked(
    client: DataClient, *, license_id: str, chann_uid: str, display_name: str | None,
    language: str = "th",
) -> dict:
    """Returns {"created": bool, "notified": int} — for tests and logs."""
    license_id = str(license_id)
    try:
        profile = await client.get_profile(chann_uid) or {}
    except Exception:
        profile = {}
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    phone = str(profile.get("phone") or "").strip()
    shown = " ".join(p for p in (first, last) if p) or (display_name or "").strip() or chann_uid

    created = False
    auto = await auto_accept_enabled(client, license_id)
    complete = bool(first and phone)
    if auto and complete:
        try:
            await client.create_customer(
                license_id,
                {
                    "first_name": first, "last_name": last or None, "phone": phone,
                    "customer_chann_uid": chann_uid,
                    "notes": "เพิ่มอัตโนมัติเมื่อลูกค้าผูกร้านผ่าน LINE",
                },
                actor_id=chann_uid,
            )
            created = True
        except DataTierError as exc:
            if exc.status_code != 409:
                log.warning("auto-create of a linked customer refused: %s", exc.detail)
            else:
                created = True  # already on the list — the goal is met
        except Exception:
            log.exception("auto-create of a linked customer failed")

    if created:
        text = f"ลูกค้าใหม่ผูกร้านผ่าน LINE: {shown}" + (f" · {phone}" if phone else "") + "\nเพิ่มเข้ารายชื่อลูกค้าให้แล้ว (ตั้งค่ารับลูกค้าใหม่อัตโนมัติ: เปิด)"
    elif auto:
        text = (
            f"ลูกค้าใหม่ผูกร้านผ่าน LINE: {shown}\n"
            "ยังเพิ่มเข้ารายชื่อให้ไม่ได้เพราะไม่มีชื่อ/เบอร์ในโปรไฟล์ — "
            f"เพิ่มเองด้วย \"สร้างลูกค้า {shown} <เบอร์>\""
        )
    else:
        text = (
            f"ลูกค้าใหม่ผูกร้านผ่าน LINE: {shown}" + (f" · {phone}" if phone else "") + "\n"
            f"เพิ่มเข้ารายชื่อลูกค้าด้วย \"สร้างลูกค้า {shown}{' ' + phone if phone else ''}\" "
            "(หรือเปิดรับอัตโนมัติ: \"ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด\")"
        )
    notified = await _tell_the_shop(client, license_id, text, chann_uid, language)
    return {"created": created, "notified": notified}


async def _tell_the_shop(client: DataClient, license_id: str, text: str, about_uid: str, language: str) -> int:
    try:
        members = await client.list_members(license_id)
    except Exception:
        log.exception("could not list members to announce a new customer")
        return 0
    sent = 0
    for member in members:
        role = str(member.get("role") or "").lower()
        if role not in _RECIPIENT_ROLES or str(member.get("status") or "active") != "active":
            continue
        uid = str(member.get("chann_uid") or "")
        if not uid:
            continue
        try:
            line_uid = await client.line_target_of(uid)
            await send_notification(
                client, license_id=license_id, target_chann_uid=uid, target_line_user_id=line_uid,
                type="chat_session_new", message=text, entity_type="customer_link",
                entity_id=about_uid, language=language, oa="sales",
            )
            sent += 1
        except Exception:
            log.exception("could not tell %s about a new customer", uid)
    return sent
