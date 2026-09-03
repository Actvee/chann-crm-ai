"""Phase 9.4 on the customer's side (PLAN_3OA B5) — the storefront, the
"interested" tap, and what this customer has bought from this shop.

Shared by the chat engine and the home screen so both say the same thing
(parity rule): a search is the same cross-tenant query, "สนใจ" creates
the same lead in the one shop the customer picked and tells the same
people, and the purchase history is the same list of deals.
"""
from __future__ import annotations

import logging

from ..data_client import DataClient
from .notify import send_notification

log = logging.getLogger(__name__)

_INTEREST_RECIPIENT_ROLES = ("owner", "admin", "sales")


async def search(client: DataClient, *, q: str, limit: int = 20) -> list[dict]:
    """Product info across every active shop; no term → the whole storefront."""
    limit = max(1, min(int(limit or 20), 50))
    term = (q or "").strip()
    if term:
        return await client.storefront_search(term, limit=limit)
    return await client.storefront_browse(limit=limit)


async def record_interest(
    client: DataClient, *, chann_uid: str, license_id: str, product_name: str,
    company_name: str | None = None, display_name: str | None = None, language: str = "th",
) -> dict:
    """The "กดสนใจ" step: a lead in the chosen shop, and the shop's
    owner/admin/sales hear about it — a lead nobody sees is not a lead."""
    row = await client.storefront_record_interest(
        chann_uid=chann_uid, license_id=str(license_id), product_name=product_name,
    )
    shown = (display_name or "").strip() or chann_uid
    text = (
        f"ลูกค้าสนใจสินค้า: {product_name}\n"
        f"จาก: {shown}\n"
        "สร้างเป็น lead ให้แล้ว — ดูที่ \"รายชื่อลูกค้า\" หรือหน้าจอ > ลูกค้า แล้วติดต่อกลับ"
    )
    text_en = (
        f"A customer is interested in: {product_name}\n"
        f"From: {shown}\n"
        "Saved as a lead — see \"customers\" or home > Customers, then get in touch"
    )
    try:
        members = await client.list_members(str(license_id))
    except Exception:
        log.exception("could not list members to announce a storefront lead")
        return row
    for member in members:
        role = str(member.get("role") or "").lower()
        if role not in _INTEREST_RECIPIENT_ROLES or str(member.get("status") or "active") != "active":
            continue
        uid = str(member.get("chann_uid") or "")
        if not uid:
            continue
        try:
            line_uid = await client.line_target_of(uid)
            await send_notification(
                client, license_id=str(license_id), target_chann_uid=uid,
                target_line_user_id=line_uid, type="chat_session_new", message=text,
                message_en=text_en,
                entity_type="customer", entity_id=str(row.get("id") or ""),
                language=language, oa="sales",
            )
        except Exception:
            log.exception("could not tell %s about a storefront lead", uid)
    return row


async def my_orders(client: DataClient, *, license_id: str, chann_uid: str) -> list[dict]:
    """Deals of this customer's record in this shop — what they bought or
    were quoted. A person the shop never made a customer record for has
    no history here, which is the honest answer, not an error."""
    rows = await client.list_customers(str(license_id), customer_chann_uid=chann_uid)
    if not rows:
        return []
    contact_id = str(rows[0].get("id") or "")
    if not contact_id:
        return []
    deals = await client.list_deals(str(license_id), contact_id=contact_id)
    return [d for d in deals if not d.get("archived_at")]
