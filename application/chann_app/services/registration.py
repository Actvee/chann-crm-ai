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
CUSTOMER_PENDING_TTL_S = 3600

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

# Technician OA has no "create a company" option at all — a technician does
# not start a company through this channel, they join one that already
# exists. Holding a Sales-side membership at Company X does not count either
# (see identity.resolve_context / MemberRepository.memberships_of): only a
# license_members row with role="technician" does, and the only way to get
# one is this invite code, requested by someone on the Sales OA who holds
# member.manage (see TECHNICIAN_INVITE_TRIGGERS in chat.py).
WELCOME_TECHNICIAN = {
    "th": (
        "ยินดีต้อนรับ\n"
        "บัญชีนี้ยังไม่ได้ผูกกับบริษัทไหนในฐานะช่าง\n"
        "ขอรหัสเชิญจากบริษัทที่คุณจะไปทำงานด้วยก่อน แล้วพิมพ์รหัสนั้นที่นี่"
    ),
    "en": (
        "Welcome\n"
        "This account is not yet linked to any company as a technician.\n"
        "Ask the company you'll be working with for an invite code, then "
        "type that code here."
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

    # Technician OA: same reasoning as Customer OA above — the only thing to
    # do is redeem an invite code obtained from the company beforehand.
    # "Create a company" is a Sales-OA-only concept and never offered here.
    if audience == "technician":
        if INVITE_CODE_RE.match(text.upper()):
            return await _redeem_invite_reply(client, text, ctx, language)
        return _t(WELCOME_TECHNICIAN, language)

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
        return await _redeem_invite_reply(client, text, ctx, language)

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
        linked = _t(LINKED, language).format(name=link.get("company_name", ""))

        # Deliver whatever they said before they were linked. Making
        # someone repeat themselves after they have already explained a
        # problem is how a chat product loses people.
        try:
            pending = await client.get_pending_intent(ctx.chann_uid, "customer")
        except Exception:
            pending = None
        held = ((pending or {}).get("fields") or {}).get("message")
        if held and (pending or {}).get("entity") == "pending_customer_message":
            try:
                await client.clear_pending_intent(ctx.chann_uid, "customer")
            except Exception:
                log.exception("could not clear a held customer message")
            follow_up = (
                f'\n\nส่งเรื่อง "{held[:80]}" ให้ทางร้านแล้วครับ'
                if language != "en"
                else f'\n\nI have passed on: "{held[:80]}"'
            )
            return linked + follow_up
        return linked

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

    # Nothing matched. What the person typed is almost certainly not a
    # shop name — it is what they actually wanted to say, usually a fault.
    #
    # The old reply here was "พิมพ์รหัสร้าน หรือชื่อร้านเพื่อค้นหา", which
    # asks for a code they have never seen, does not explain why, and
    # throws away what they just wrote. Someone who scanned a QR code at a
    # shop and typed "แอร์เสีย" got a demand for paperwork.
    #
    # Their message is kept and repeated back, so they can see it was not
    # lost, and the question is asked in terms they can answer: the shop's
    # NAME, which they know, rather than its code, which they do not.
    if text and not COMPANY_CODE_RE.match(text.upper()):
        # Best-effort: holding the message is a courtesy, and a cache
        # failure must not stop a person who is trying to report a fault
        # from getting an answer at all.
        try:
            await client.set_pending_intent(
                ctx.chann_uid, "customer",
                action="report", entity="pending_customer_message",
                fields={"message": text[:500]}, missing=["company"],
                ttl_seconds=CUSTOMER_PENDING_TTL_S,
            )
        except Exception:
            log.exception("could not hold a customer's message while unlinked")
        if language == "en":
            return (
                f'Got it: "{text[:80]}"\n\n'
                "This account serves several shops, so I do not yet know which "
                "one you are contacting.\n"
                "Type the shop's name and I will pass your message straight on."
            )
        return (
            f"รับเรื่องแล้วครับ: \"{text[:80]}\"\n\n"
            "บัญชีนี้ดูแลหลายร้าน ผมยังไม่ทราบว่าคุณติดต่อร้านไหน\n"
            "พิมพ์ชื่อร้านมาได้เลย แล้วผมจะส่งเรื่องให้ทางร้านทันที"
        )

    if language == "en":
        return (
            "Welcome.\n"
            "This account serves several shops. Type the name of the shop you "
            "are contacting, or its code if you have one."
        )
    return (
        "ยินดีต้อนรับครับ\n"
        "บัญชีนี้ดูแลหลายร้าน พิมพ์ชื่อร้านที่คุณติดต่อมาได้เลย "
        "หรือถ้ามีรหัสร้านก็พิมพ์รหัสได้"
    )


async def _redeem_invite_reply(
    client: DataClient, text: str, ctx: ResolvedContext, language: str
) -> str:
    """Shared by the Sales-OA and Technician-OA invite-code paths — the code
    itself carries the role being granted, so redemption does not need to
    know or care which OA it arrived on."""
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


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 409 or "409" in str(exc)


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 or "404" in str(exc)


def is_unregistered(ctx: ResolvedContext) -> bool:
    return ctx.resolution is TenantResolution.NONE
