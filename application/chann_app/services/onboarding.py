"""Phase 16.4 — what happens in the shop when a new customer links.

`auto_accept_new_customers` (a license setting, off by default): on, and
the person's profile is complete (name + phone) → they become a customer
record in the shop's CRM at once; off, or incomplete → the shop's
owner/admin/CS are told and asked to add them. Either way the link
itself already stands — the person can report a fault; this is about
the shop's own customer list.

Review E8 (6 Sep 2026): a person whose phone the shop already keyed in
by hand IS that customer. Whatever the setting, the existing row gets
their identity attached — nothing new is written, the shop is told
which record it was — instead of the old path, which took the Data
Tier's duplicate refusal as "already on the list" and left the row
without a chann_uid (so PDPA export could not see the person and the
ticket's contact stayed NULL).
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


async def _attach_to_existing(
    client: DataClient, *, license_id: str, chann_uid: str, phone: str,
) -> dict | None:
    """The staff-created row with this phone, now the person's own — or
    None when there is no such row (or the Data Tier cannot say)."""
    if not phone:
        return None
    try:
        return await client.link_customer_identity(
            license_id, phone=phone, customer_chann_uid=chann_uid, actor_id=chann_uid,
        )
    except DataTierError as exc:
        if exc.status_code == 409:
            log.warning("phone of %s belongs to another identity's record: %s", chann_uid, exc.detail)
        else:
            log.warning("could not attach %s to an existing customer: %s", chann_uid, exc.detail)
    except Exception:  # noqa: BLE001 — an older fake/Data Tier without the route
        log.exception("identity attach failed for %s", chann_uid)
    return None


async def after_customer_linked(
    client: DataClient, *, license_id: str, chann_uid: str, display_name: str | None,
    language: str = "th",
) -> dict:
    """Returns {"created": bool, "linked": bool, "customer_id": str | None,
    "notified": int} — for tests and logs. `created` is true when the
    person now has a customer row of their own here (new or attached)."""
    license_id = str(license_id)
    try:
        profile = await client.get_profile(chann_uid) or {}
    except Exception:
        profile = {}
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    phone = str(profile.get("phone") or "").strip()
    shown = " ".join(p for p in (first, last) if p) or (display_name or "").strip() or chann_uid

    customer: dict | None = None
    linked = False
    existing = await _attach_to_existing(client, license_id=license_id, chann_uid=chann_uid, phone=phone)
    if existing is not None:
        customer, linked = existing, True

    auto = await auto_accept_enabled(client, license_id)
    complete = bool(first and phone)
    if customer is None and auto and complete:
        try:
            customer = await client.create_customer(
                license_id,
                {
                    "first_name": first, "last_name": last or None, "phone": phone,
                    "customer_chann_uid": chann_uid,
                    "notes": "เพิ่มอัตโนมัติเมื่อลูกค้าผูกร้านผ่าน LINE",
                },
                actor_id=chann_uid,
            )
        except DataTierError as exc:
            # A 409 is no longer "already on the list": the attach step
            # above already claimed any row with this phone, so what is
            # left is a genuine refusal (another identity's record, or a
            # rule the shop set) and the shop is asked to sort it out.
            log.warning("auto-create of a linked customer refused: %s", exc.detail)
        except Exception:
            log.exception("auto-create of a linked customer failed")
    created = customer is not None
    customer_code = str((customer or {}).get("customer_id") or "")

    head = f"ลูกค้าใหม่ผูกร้านผ่าน LINE: {shown}" + (f" · {phone}" if phone else "")
    head_en = f"New customer linked via LINE: {shown}" + (f" · {phone}" if phone else "")
    if linked:
        text = head + f"\nผูกกับรายชื่อลูกค้าเดิม {customer_code} ให้แล้ว (เบอร์ตรงกัน)"
        text_en = head_en + f"\nMatched to existing customer {customer_code} by phone"
    elif created:
        text = head + "\nเพิ่มเข้ารายชื่อลูกค้าให้แล้ว (ตั้งค่ารับลูกค้าใหม่อัตโนมัติ: เปิด)"
        text_en = head_en + "\nAdded to your customer list (auto-accept new customers: on)"
    elif auto:
        text = (
            f"ลูกค้าใหม่ผูกร้านผ่าน LINE: {shown}\n"
            "ยังเพิ่มเข้ารายชื่อให้ไม่ได้เพราะไม่มีชื่อ/เบอร์ในโปรไฟล์ — "
            f"เพิ่มเองด้วย \"สร้างลูกค้า {shown} <เบอร์>\""
        )
        text_en = (
            f"New customer linked via LINE: {shown}\n"
            "Not added yet — no name/phone in their profile. "
            f"Add them with \"create customer {shown} <phone>\""
        )
    else:
        text = (
            head + "\n"
            f"เพิ่มเข้ารายชื่อลูกค้าด้วย \"สร้างลูกค้า {shown}{' ' + phone if phone else ''}\" "
            "(หรือเปิดรับอัตโนมัติ: \"ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด\")"
        )
        text_en = (
            head_en + "\n"
            f"Add them with \"create customer {shown}{' ' + phone if phone else ''}\" "
            "(or switch auto-accept on: \"auto accept new customers on\")"
        )
    # The notification points at the customer row (a UUID) when there is
    # one, and at nothing otherwise. It used to carry the CHN- uid, which
    # the Data Tier refused (entity_id is a UUID column) — so the shop was
    # never told at all (review E1).
    customer_uuid = str((customer or {}).get("id") or "") or None
    notified = await _tell_the_shop(
        client, license_id, text, language, text_en=text_en, customer_uuid=customer_uuid,
    )
    return {"created": created, "linked": linked, "customer_id": customer_uuid, "notified": notified}


async def _tell_the_shop(
    client: DataClient, license_id: str, text: str, language: str,
    text_en: str | None = None, customer_uuid: str | None = None,
) -> int:
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
                type="chat_session_new", message=text, message_en=text_en,
                entity_type="customer" if customer_uuid else None,
                entity_id=customer_uuid, language=language, oa="sales",
            )
            sent += 1
        except Exception:
            log.exception("could not tell %s about a new customer", uid)
    return sent
