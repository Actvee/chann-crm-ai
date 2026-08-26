"""Registration through chat — Phase 6.5 (Master Spec 6.5.6).

The entry point for someone the system has never seen. Deliberately does NOT
go through the AI intent parser: registration is a short, closed set of
choices, and burning a model call — plus risking a hallucinated action — to
recognise "1" or an invite code would be worse on latency, cost, and
reliability all at once. The AI takes over once the user is a member.

State lives in the message text rather than a session table. A registration
conversation is two or three turns and LINE gives no reliable session anyway;
inventing server-side state here would mean expiry rules, cleanup, and a new
failure mode for a flow this short.
"""
from __future__ import annotations

import logging
import re

from ..data_client import DataClient
from .identity import ResolvedContext, TenantResolution

log = logging.getLogger(__name__)

# An invite code is 10 chars from a confusable-free alphabet; a company code is
# 8. Matching on shape lets someone paste a code with no command word, which
# is what people actually do.
INVITE_CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{10}$")
COMPANY_CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$")

WELCOME = {
    "th": (
        "ยินดีต้อนรับสู่ Chann CRM\n"
        "คุณยังไม่ได้ผูกกับบริษัทใด เลือกได้ 2 ทาง:\n\n"
        "1) พิมพ์ \"เปิดบริษัทใหม่ <ชื่อบริษัท>\" เพื่อสร้างบริษัทของคุณเอง\n"
        "2) ถ้ามีรหัสเชิญจากเพื่อนร่วมงาน พิมพ์รหัสนั้นได้เลย"
    ),
    "en": (
        "Welcome to Chann CRM\n"
        "This account is not linked to a company yet. Two options:\n\n"
        '1) Type "create company <name>" to start your own\n'
        "2) If a colleague gave you an invite code, just type the code"
    ),
}

ASK_COMPANY_NAME = {
    "th": 'กรุณาระบุชื่อบริษัท เช่น "เปิดบริษัทใหม่ ร้านสมชายการช่าง"',
    "en": 'Please include the company name, e.g. "create company Somchai Repairs"',
}

CREATED = {
    "th": (
        "สร้างบริษัท \"{name}\" เรียบร้อย\n"
        "คุณเป็นเจ้าของบริษัทนี้แล้ว\n\n"
        "รหัสร้านของคุณคือ {code}\n"
        "ให้ลูกค้าพิมพ์รหัสนี้เพื่อผูกกับร้านคุณ\n\n"
        "ทดลองใช้ฟรี 30 วัน"
    ),
    "en": (
        'Company "{name}" created — you are its owner.\n\n'
        "Your shop code is {code}\n"
        "Customers type this code to link to your shop.\n\n"
        "30-day free trial."
    ),
}

JOINED = {
    "th": "เข้าร่วม \"{name}\" เรียบร้อย สิทธิ์ของคุณคือ {role}",
    "en": 'Joined "{name}" — your role is {role}',
}

LINKED = {
    "th": "ผูกกับร้าน \"{name}\" เรียบร้อย ครั้งต่อไปไม่ต้องพิมพ์รหัสอีก",
    "en": 'Linked to "{name}". You will not need the code again.',
}

BAD_CODE = {
    "th": "ไม่พบรหัสนี้ กรุณาตรวจสอบอีกครั้ง",
    "en": "That code was not found — please check it",
}

ALREADY_HAVE_COMPANY = {
    "th": "บัญชีนี้สร้างบริษัทไปแล้ว หนึ่งบัญชี LINE สร้างได้บริษัทเดียว",
    "en": "This account already created a company — one per LINE account",
}


def _t(table: dict[str, str], language: str) -> str:
    return table.get(language) or table["th"]


# Ordered longest-first so "เปิดบริษัทใหม่ ร้าน ก" strips the full trigger and
# not just "เปิดบริษัท", which would leave "ใหม่" as part of the company name.
#
# "เปิดบริษัท" and "สมัคร" were added after a live test: the menu says
# "เปิดบริษัทใหม่", and the user naturally typed the shorter "เปิดบริษัท",
# which matched nothing and fell through to the menu again. Triggers should
# cover what people actually type, not only the exact wording we printed.
CREATE_TRIGGERS = (
    "เปิดบริษัทใหม่",
    "สร้างบริษัทใหม่",
    "ลงทะเบียนบริษัท",
    "เปิดบริษัท",
    "สร้างบริษัท",
    "สมัครบริษัท",
    "create new company",
    "create company",
    "new company",
    "register company",
)


def parse_create_company(message: str) -> str | None:
    """Return the company name if this is a create request, else None.

    Returns "" (falsy but not None) when the trigger is present without a
    name, so the caller can tell "not a create request" from "a create
    request missing its name" and ask for the name rather than showing the
    whole menu again.
    """
    text = (message or "").strip()
    lowered = text.lower()
    # Longest match wins, so a shorter trigger that is a prefix of a longer one
    # cannot swallow the difference into the company name.
    for trigger in sorted(CREATE_TRIGGERS, key=len, reverse=True):
        if lowered.startswith(trigger.lower()):
            return text[len(trigger):].strip(" \t:：-—")
    return None


async def handle_registration(
    client: DataClient,
    *,
    message: str,
    ctx: ResolvedContext,
    audience: str = "sales",
    language: str = "th",
) -> str:
    """Handle a message from someone with no tenant. Returns reply text."""
    text = (message or "").strip()

    # Customer OA: the only thing to do is bind to a shop. Offering "create a
    # company" to an end customer would be nonsense.
    if audience == "customer":
        return await _handle_customer(client, text, ctx, language)

    if not text:
        return _t(WELCOME, language)

    name = parse_create_company(text)
    if name is not None:
        if not name:
            return _t(ASK_COMPANY_NAME, language)
        try:
            created = await client.create_license(
                company_name=name,
                created_by_chann_uid=ctx.chann_uid,
                display_name=ctx.display_name,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return _t(ALREADY_HAVE_COMPANY, language)
            log.error("create_license failed: %s", exc)
            raise
        return _t(CREATED, language).format(
            name=created.get("company_name", name),
            code=created.get("company_code", "?"),
        )

    if INVITE_CODE_RE.match(text.upper()):
        try:
            member = await client.redeem_invite(
                invite_code=text.upper(),
                chann_uid=ctx.chann_uid,
                display_name=ctx.display_name,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc) or _is_conflict(exc):
                return _t(BAD_CODE, language)
            raise
        return _t(JOINED, language).format(
            name=member.get("company_name", ""), role=member.get("role", "")
        )

    return _t(WELCOME, language)


async def _handle_customer(
    client: DataClient, text: str, ctx: ResolvedContext, language: str
) -> str:
    if COMPANY_CODE_RE.match(text.upper()):
        try:
            link = await client.link_customer(
                chann_uid=ctx.chann_uid, company_code=text.upper()
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc) or _is_conflict(exc):
                return _t(BAD_CODE, language)
            raise
        return _t(LINKED, language).format(name=link.get("company_name", ""))

    if len(text) >= 2:
        shops = await client.search_shops(text)
        if shops:
            lines = "\n".join(
                f"• {s['company_name']} — {s['company_code']}" for s in shops[:5]
            )
            header = (
                "พบร้านเหล่านี้ พิมพ์รหัสร้านเพื่อผูก:"
                if language != "en"
                else "Found these shops — type the code to link:"
            )
            return f"{header}\n{lines}"

    return (
        "พิมพ์รหัสร้าน หรือชื่อร้านเพื่อค้นหา"
        if language != "en"
        else "Type a shop code, or a shop name to search"
    )


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 409 or "409" in str(exc)


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 or "404" in str(exc)


def is_unregistered(ctx: ResolvedContext) -> bool:
    return ctx.resolution is TenantResolution.NONE
