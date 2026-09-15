"""A new customer goes to a salesperson the way a job goes to a technician
(round 18, 14 Sep 2026).

Phase 11's engine could execute a "sales" rule since the day it was
written, and the policy translator produced them — but nothing ever
called the engine with one. A shop that typed "ตั้งกฎมอบหมาย ลูกค้าใหม่
ให้กลุ่มขาย A สลับกัน" was told the rule was saved, and every new
customer still belonged to whoever typed them in.

This is the one place that call is made. Every path that creates a
customer asks here afterwards:

  * a customer who linked the shop on LINE (onboarding, source "line");
  * a staff member typing "เพิ่มลูกค้า …" on the Sales OA (source "staff");
  * the dashboard form (source "dashboard");
  * a CSV import (source "csv").

The rule decides only when there is one. A salesperson who adds a
customer themselves keeps that customer: the rule is for work that
ARRIVES, not for taking a lead off the person who found it. Anyone
else — the owner, an admin, CS, the system — is handing the customer to
the sales team, and the rule says to whom.
"""
from __future__ import annotations

import logging

from ..data_client import DataClient
from .notify import send_notification

log = logging.getLogger(__name__)

NOTIFY_TYPE = "customer_assigned"
ASSIGNED_TEXT = {
    "th": "🧑‍💼 ลูกค้าใหม่ {name} ({code}) ถูกมอบหมายให้คุณตามกฎมอบหมาย{phone}",
    "en": "🧑‍💼 New customer {name} ({code}) was assigned to you by rule{phone}",
}
# What the person who created the customer is told, appended to their
# own confirmation.
ROUTED_LINE = {
    "th": "มอบหมายให้ {name} ดูแล (ตามกฎมอบหมาย)",
    "en": "Assigned to {name} by the assignment rule",
}


async def has_sales_rule(client: DataClient, license_id: str) -> bool:
    try:
        rules = await client.get_assignment_rules(str(license_id))
    except Exception:  # noqa: BLE001 — a shop without the table or a Data Tier hiccup: no rule
        log.exception("could not read assignment rules for %s", license_id)
        return False
    return any(
        r.get("is_active") and str(r.get("scope") or "") == "sales" for r in rules or []
    )


async def _actor_is_sales(client: DataClient, license_id: str, actor_chann_uid: str | None) -> bool:
    """A salesperson keeps what they add themselves. "Sales" is the role
    word the templates use; a custom role that grants the sales
    permissions but is called something else is treated as management
    and the rule applies — the shop can name the role "sales" if that
    is wrong for them."""
    if not actor_chann_uid:
        return False
    try:
        member = await client.get_member(str(license_id), actor_chann_uid, channel="sales")
    except Exception:  # noqa: BLE001
        return False
    return str((member or {}).get("role") or "").lower() == "sales"


async def route_new_customer(
    client: DataClient, license_id: str, customer: dict, *, source: str,
    actor_chann_uid: str | None = None, language: str = "th",
) -> dict | None:
    """Hand a just-created customer to a salesperson by the active sales
    rule. Returns {"member_id", "name", "reason"} when someone was
    picked, None when nothing changed (no rule, the creator is sales,
    the engine found nobody, or any failure — creating the customer
    already succeeded and must not be undone by this)."""
    license_id = str(license_id)
    customer_uuid = str(customer.get("id") or "")
    if not customer_uuid:
        return None
    if not await has_sales_rule(client, license_id):
        return None
    if await _actor_is_sales(client, license_id, actor_chann_uid):
        return None
    try:
        outcome = await client.execute_assignment(
            license_id, scope="sales", entity_type="customer", entity_id=customer_uuid,
            context={
                "customer": {
                    "stage": str(customer.get("stage") or "lead"),
                    "source": source,
                },
            },
            actor_id=actor_chann_uid,
        )
    except Exception:  # noqa: BLE001
        log.exception("sales assignment failed for customer %s", customer_uuid)
        return None
    member_id = str((outcome or {}).get("member_id") or "")
    if not member_id:
        log.info("sales rule picked nobody for %s: %s", customer_uuid, (outcome or {}).get("reason"))
        return None

    name, uid = await _member_label(client, license_id, member_id)
    customer["owner_member_id"] = member_id
    if uid and uid != actor_chann_uid:
        shown = " ".join(
            p for p in (customer.get("first_name"), customer.get("last_name")) if p
        ) or str(customer.get("customer_id") or "")
        phone = str(customer.get("phone") or "")
        try:
            await send_notification(
                client, license_id=license_id, target_chann_uid=uid,
                target_line_user_id=await client.line_target_of(uid),
                type=NOTIFY_TYPE,
                message=ASSIGNED_TEXT["th"].format(
                    name=shown, code=customer.get("customer_id") or "", phone=f" · {phone}" if phone else "",
                ),
                message_en=ASSIGNED_TEXT["en"].format(
                    name=shown, code=customer.get("customer_id") or "", phone=f" · {phone}" if phone else "",
                ),
                entity_type="customer", entity_id=customer_uuid, oa="sales",
            )
        except Exception:  # noqa: BLE001
            log.exception("could not tell %s about customer %s", uid, customer_uuid)
    return {"member_id": member_id, "name": name, "reason": str((outcome or {}).get("reason") or "")}


async def _member_label(client: DataClient, license_id: str, member_id: str) -> tuple[str, str | None]:
    """(display name, chann_uid) of a member id — the engine speaks in ids,
    people do not."""
    try:
        for m in await client.list_members(license_id):
            if str(m.get("id") or "") == member_id:
                uid = str(m.get("chann_uid") or "") or None
                name = str(m.get("display_name") or "").strip()
                if not name and uid:
                    try:
                        profile = await client.get_profile(uid) or {}
                    except Exception:  # noqa: BLE001
                        profile = {}
                    name = " ".join(
                        p for p in (profile.get("first_name"), profile.get("last_name")) if p
                    ) or str(profile.get("display_name") or "")
                return (name or member_id[:8]), uid
    except Exception:  # noqa: BLE001
        log.exception("could not list members of %s", license_id)
    return member_id[:8], None


def routed_line(picked: dict | None, language: str) -> str:
    """The sentence appended to the creator's confirmation, or ""."""
    if not picked:
        return ""
    return ROUTED_LINE["en" if language == "en" else "th"].format(name=picked.get("name") or "")
