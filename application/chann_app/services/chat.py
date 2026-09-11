"""Chat foundation — Master Spec 6.4-6.7.

This is the first place anything actually calls OpenRouter for real. Phase 4
built and unit-tested the client entirely against a mock transport, so if
something is wrong with the API key, the model slug, or Qwen's willingness to
return parseable JSON for Thai input, it surfaces here.

Structure follows spec 6.4's pattern in the order it states, because the order
is load-bearing: missing fields are asked about BEFORE permission is
considered, so a user is never told "you can't do that" about a request we
never actually understood.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal, InvalidOperation

from ..data_client import DataClient, DataTierError
from .notify import send_notification
from .ai.client import AIUnavailable, AINotConfigured
# Reused rather than reimplemented on purpose: what a salesperson reads
# in chat must never disagree with what the customer receives on the PDF.
from .documents.snapshot import build_line_items
from .guides import (
    guide_images, help_menu_quick_replies, help_step_by_text, help_step_count,
    render_help_menu, render_help_step, render_help_text,
)
from . import notify as _notify_mod
from .thai_datetime import DATE_FORMATS, local_today, thai_number_words
from .photos import PhotoRefused, store_ticket_photo
from ..line.client import get_message_content
from .ai.intent import parse_intent, unavailable_reply
from .identity import ResolvedContext, TenantResolution, member_channel
# One decision, made before anything writes: does this sentence ask for the
# action, or does it only mention it? Every mutating path in this module
# goes through it (review v3, 9-10 Sep 2026 — B01-B04, B07).
from .capabilities import CUSTOMER_CREATE, capability
from .intent_guard import ASK, intent_to_act
from .registration import COMPANY_CODE_RE
from . import storefront as storefront_service
from . import live_chat
from . import pdpa as pdpa_service
from . import ticket_machine

# Phase 19/E10: the per-person rich menu follows the language. The module
# lands from another branch; until it merges, switching the language just
# does not re-link a menu.
try:
    from .richmenu import sync_rich_menu
except ImportError:  # pragma: no cover - depends on the other branch
    sync_rich_menu = None

# Which permission key an (action, entity) pair requires. This is the real
# gate — the prompt tells the model what the user holds, but a model that
# ignores that, or names an entity the system has never heard of, must still
# be stopped here. Prompt text is guidance; this table is the boundary.
#
# Actions are normalised: the model emits read/view/list interchangeably.
ACTION_ALIASES = {
    "view": "read",
    "list": "read",
    "get": "read",
    "show": "read",
    # The model reaches for these whenever the sentence is a lookup rather
    # than a list — "หาลูกค้าชื่อสมหมาย" comes back as action="search",
    # "ขอดูข้อมูลคุณสมหมาย" as action="find". Neither was an alias, so
    # required_permission answered None and a plain read was reported as
    # something the system cannot do at all (owner, 8 Sep 2026).
    "search": "read",
    "find": "read",
    "add": "create",
    "new": "create",
    "edit": "update",
    "modify": "update",
    "remove": "delete",
}

# Every verb that means "show me". Derived from the aliases above rather
# than written out a second time: the per-entity handlers below see the
# model's RAW verb (the dispatcher normalises only for the permission
# gate), so a list kept by hand would drift from what the gate accepts and
# a read would pass the gate and then fall off the end of its handler —
# which is exactly the bug this set exists to stop.
READ_ACTIONS = frozenset({"read"} | {a for a, canon in ACTION_ALIASES.items() if canon == "read"})

# (action, entity) -> permission key. An entity that is not in this table is
# not something the system can do at all, which is a different answer from
# "you lack permission" but produces the same reply: here is what you CAN do.
ACTION_PERMISSIONS: dict[tuple[str, str], str] = {
    ("read", "customer"): "customer.read",
    ("create", "customer"): "customer.create",
    ("update", "customer"): "customer.update",
    ("archive", "customer"): "customer.archive",
    # 9.5 — confirming a Lead as a Contact is a customer.update-level
    # action; the spec does not define a separate permission for it.
    ("promote", "customer"): "customer.update",
    ("read", "deal"): "deal.read",
    ("create", "deal"): "deal.create",
    ("update", "deal"): "deal.update",
    ("archive", "deal"): "deal.archive",
    # A line ON a deal or quote, not the catalogue product. Editing one
    # changes a single record, so it needs the permission for that record
    # rather than product.manage — and without these entries the request
    # was reported as "not a feature yet" when it plainly is one.
    # The verbs the AI uses for field work. Without these the gate above
    # reads "no permission covers that" and answers "not a feature" — for
    # things the technician OA does all day.
    ("claim", "ticket"): "ticket.update",
    ("reject", "ticket"): "ticket.update",
    ("check_in", "service_report"): "service_report.create",
    ("check_out", "service_report"): "service_report.create",
    ("update", "line_item"): "deal.update",
    ("delete", "line_item"): "deal.update",
    ("create", "line_item"): "deal.update",
    ("read", "note"): "note.read",
    ("create", "note"): "note.create",
    ("update", "note"): "note.update",
    # Deleting is an edit down to nothing, so it takes note.update rather
    # than a note.delete the catalogue has never had. Registered so the
    # AI can route "ลบบันทึกอันล่าสุดออกให้หน่อย" to the same handler the
    # deterministic trigger reaches.
    ("delete", "note"): "note.update",
    ("read", "followup"): "followup.read",
    ("create", "followup"): "followup.create",
    ("update", "followup"): "followup.update",
    # Cancelling is an update to the row's status, not its own permission —
    # same reasoning as ("promote", "customer") above.
    ("cancel", "followup"): "followup.update",
    # Removing the row outright. followup.update, not a followup.delete the
    # catalogue has never had — the same call note.delete already makes,
    # and deleting is an edit down to nothing. Registered so "ลบนัดนี้ทิ้ง"
    # reaches the real DELETE instead of the capability list; the typed
    # ยกเลิกนัด/ลบนัด triggers still cancel, which keeps the record.
    ("delete", "followup"): "followup.update",
    ("read", "ticket"): "ticket.read",
    ("create", "ticket"): "ticket.create",
    ("update", "ticket"): "ticket.update",
    ("assign", "ticket"): "ticket.assign",
    ("close", "ticket"): "ticket.close",
    ("read", "quote"): "quote.read",
    ("create", "quote"): "quote.create",
    ("update", "quote"): "quote.update",
    ("read", "service_report"): "service_report.read",
    ("create", "service_report"): "service_report.create",
    ("update", "service_report"): "service_report.update",
    ("issue", "service_report"): "service_report.read",
    # Phase 14-B — approvals. Registered here first so check-parity holds
    # 14-C (the queue and config pages) to the same set.
    ("read", "approval"): "approval.view",
    ("approve", "approval"): "approval.approve",
    ("reject", "approval"): "approval.reject",
    ("update", "approval"): "approval.manage",
    ("read", "warranty"): "warranty.read",
    ("create", "warranty"): "warranty.create",
    ("update", "warranty"): "warranty.update",
    # Phase 7 master data
    ("read", "product"): "product.manage",
    ("create", "product"): "product.manage",
    ("update", "product"): "product.manage",
    ("delete", "product"): "product.manage",
    ("read", "team"): "team.manage",
    ("create", "team"): "team.manage",
    ("update", "team"): "team.manage",
    ("delete", "team"): "team.manage",
    ("read", "sales_group"): "team.manage",
    ("create", "sales_group"): "team.manage",
    ("update", "sales_group"): "team.manage",
    ("read", "report"): "view_reports",
    ("read", "audit_log"): "audit_log.view",
    ("read", "role"): "role.manage",
    ("update", "role"): "role.manage",
    ("create", "role"): "role.manage",
    ("read", "member"): "member.manage",
    ("update", "member"): "member.manage",
    ("read", "setting"): "setting.manage",
    ("update", "setting"): "setting.manage",
}


# Which permission keys are even IN SCOPE for a given LINE channel, per
# Master Spec §6's OA activity tables. This is a SECOND, separate boundary
# from the tenant permission gate above: an Owner holds every permission key
# there is, but "อนุมัติ", "จัดการการเรียกเก็บเงิน" and "จัดการบทบาทและสิทธิ์"
# have no business ever surfacing in a Technician or Customer OA
# conversation — those channels are scoped to a small, specific set of
# activities (claim ticket / check-in-out / service report / own profile for
# Technician; storefront, repair ticket, warranty and own profile for
# Customer), and the rest of the tenant's permission surface (deals, quotes,
# billing, roles, approvals...) belongs to Sales OA regardless of who
# happens to be texting from where.
#
# A value of None means "no additional restriction beyond the tenant
# permission gate" — Sales OA's own table in the spec covers nearly
# everything a tenant does, so there is nothing meaningful left to narrow.
OA_ALLOWED_PERMISSION_KEYS: dict[str, frozenset[str] | None] = {
    "customer": frozenset({
        "customer.read", "customer.update",
        "ticket.create", "ticket.read",
        "warranty.read", "warranty.create",
    }),
    "technician": frozenset({
        "ticket.read", "ticket.update", "ticket.assign", "ticket.close",
        "service_report.create", "service_report.read", "service_report.update",
        # Reading a warranty, at the owner's direction. "เครื่องนี้ยังอยู่
        # ในประกันไหม" is the question a customer asks the technician
        # standing in front of them, and sending them away to phone the
        # shop for an answer the system already holds is worse for
        # everyone. Read only — registering one is still the shop's job.
        "warranty.read",
    }),
    "sales": None,
}


def _oa_allows(oa: str, permission_key: str) -> bool:
    """Does this channel even offer this capability, independent of whether
    the caller holds the tenant permission for it?"""
    allowed = OA_ALLOWED_PERMISSION_KEYS.get(oa)
    return allowed is None or permission_key in allowed


def _filter_by_oa(permission_keys, oa: str) -> list[str]:
    """The held permission keys that are also in scope for this channel —
    applied before ranking or suggesting, so the channel's own boundary is
    respected even when the underlying tenant permission is present."""
    return [k for k in permission_keys if _oa_allows(oa, k)]


# Sales OA only: mints the one-time code a technician redeems on the
# Technician OA to actually become one at this company (see
# identity.resolve_context and MemberRepository.memberships_of for why
# holding a Sales-side membership here does not already grant that).
# Trigger-matched like registration.py's create-company/invite-code paths,
# not sent through the AI intent parser — this is a closed, short flow and
# not worth a model call or the risk of a hallucinated action.
TECHNICIAN_INVITE_TRIGGERS = (
    "ขอรหัสเชิญช่าง",
    "สร้างรหัสเชิญช่าง",
    "เชิญช่าง",
    # The short forms people actually type. Reported live: "เพิ่มช่าง" got
    # no match and fell through to the permission list, which reads as
    # "you cannot do that" for something they very much can.
    "เพิ่มช่าง",
    "เพิ่มทีมช่าง",
    "add technician",
    "invite technician",
    "technician invite code",
    "ช่างใหม่", "รับช่างเข้า", "ช่างเข้าร้าน", "ช่างเข้าร่วม", "รหัสให้ช่าง", "รหัสช่าง", "invite code for technician",
    "new technician",
)

TECHNICIAN_INVITE_REPLY = {
    "th": "รหัสเชิญช่าง: {code}\nให้ช่างพิมพ์รหัสนี้ผ่านช่องทาง Technician เพื่อเข้าร่วมบริษัทนี้ (ใช้ได้ครั้งเดียว หมดอายุใน 7 วัน)",
    "en": "Technician invite code: {code}\nHave the technician type this code on the Technician OA to join this company (one-time use, expires in 7 days).",
}
SALES_INVITE_REPLY = {
    "th": "รหัสเชิญทีมขาย/CS: {code}\nให้เขาพิมพ์รหัสนี้ผ่านช่องทาง ฝ่ายขาย/แอดมิน เพื่อเข้าร่วมบริษัทนี้ (ใช้ได้ครั้งเดียว หมดอายุใน 7 วัน)\nเข้าร่วมแล้วเปลี่ยนบทบาทได้ที่ หน้าจอ > สมาชิกในร้าน",
    "en": "Sales/CS invite code: {code}\nHave them type this code on the Sales/Admin OA to join this company (one-time use, expires in 7 days).\nTheir role can be changed afterwards under home > Members.",
}
INVITE_REPLY_BY_ROLE = {
    "technician": TECHNICIAN_INVITE_REPLY,
    "member": SALES_INVITE_REPLY,
    "cs": SALES_INVITE_REPLY,
    "admin": SALES_INVITE_REPLY,
}
INVITE_WHICH_KIND = {
    "th": "ได้ครับ — รหัสเชิญมี 2 แบบ จะเอาแบบไหนครับ\n• ช่าง — ลงทะเบียนใน LINE ช่าง\n• ทีมขาย/CS — ลงทะเบียนใน LINE ฝ่ายขาย/แอดมิน",
    "en": "Sure — there are two kinds of invite code. Which one?\n• Technician — registers on the Technician OA\n• Sales/CS — registers on the Sales/Admin OA",
}

TECHNICIAN_INVITE_DENIED = {
    "th": "การออกรหัสเชิญช่างต้องมีสิทธิ์จัดการสมาชิก",
    "en": "Issuing a technician invite code requires member-management permission",
}


# The other half of the same feature. A shop has two kinds of people to
# invite — a technician, who registers on the Technician OA, and a
# salesperson or CS, who registers on the Sales OA — and the Data tier has
# supported both since the beginning: create_invite takes a role, and
# channel_for_role turns it into the OA the code may be redeemed on
# (data/chann_data/permissions.py:214). Chat only ever offered the
# technician one.
SALES_INVITE_TRIGGERS = (
    "ขอรหัสเชิญเซลส์", "ขอรหัสเชิญพนักงาน", "ขอรหัสเชิญทีมขาย", "ขอรหัสเชิญแอดมิน", "ขอรหัสเชิญ cs",
    "สร้างรหัสเชิญเซลส์", "สร้างรหัสเชิญพนักงาน", "สร้างรหัสเชิญทีมขาย",
    "เชิญเซลส์", "เชิญพนักงาน", "เชิญทีมขาย", "เชิญแอดมิน",
    "เพิ่มเซลส์", "เพิ่มพนักงาน", "เพิ่มทีมขาย", "เพิ่มแอดมิน", "เพิ่ม cs",
    "รหัสเชิญเซลส์", "รหัสเชิญพนักงาน", "รหัสเชิญทีมขาย", "รหัสให้เซลส์", "รหัสให้พนักงาน",
    "พนักงานใหม่", "เซลส์ใหม่",
    "invite sales", "add sales", "sales invite code", "invite code for sales",
    "invite admin", "add admin", "invite cs", "add cs", "new salesperson",
)

# And the bare form, which is what a person actually types. The owner's
# report, 10 ก.ย. 2569: "ขอรหัสเชิญช่าง แบบนี้ได้ แต่พอพิม ขอรหัสเชิญ AI
# กลับไม่เข้าใจ ทั้งๆที่มีอยู่ 2 แบบ … ควรจะเข้าใจและถามว่าจะเอาตัวไหน".
# The guide had been telling people to type it for a year
# (services/guides.py:308 — "ทีมขาย/CS ใช้รหัสเชิญของ LINE ทีมขาย"), so the
# product documented a command the code did not know. Two kinds means ask
# which, not fail: an assistant that knows what you meant and needs one
# more word should say which word.
INVITE_AMBIGUOUS_TRIGGERS = (
    "ขอรหัสเชิญ", "สร้างรหัสเชิญ", "รหัสเชิญ", "ขอโค้ดเชิญ", "โค้ดเชิญ",
    "เชิญคนเข้าร้าน", "เชิญสมาชิก", "เพิ่มสมาชิก", "เพิ่มคนเข้าร้าน", "ชวนคนเข้าร้าน",
    "invite code", "invite someone", "add member", "invite member",
)


def _is_technician_invite_request(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(trigger.lower() in text for trigger in TECHNICIAN_INVITE_TRIGGERS)


def _is_sales_invite_request(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(trigger.lower() in text for trigger in SALES_INVITE_TRIGGERS)


def _is_ambiguous_invite_request(message: str) -> bool:
    """A request for an invite code that does not say for whom. Checked
    AFTER the two specific tables — "ขอรหัสเชิญช่าง" contains "ขอรหัสเชิญ",
    so the order is the whole correctness argument here."""
    text = (message or "").strip().lower()
    if _is_technician_invite_request(text) or _is_sales_invite_request(text):
        return False
    return any(trigger.lower() in text for trigger in INVITE_AMBIGUOUS_TRIGGERS)


async def _handle_technician_invite_request(
    client: DataClient, *, ctx: ResolvedContext, permission_keys: list[str],
    language: str,
) -> ChatReply:
    return await _handle_invite_request(
        client, ctx=ctx, permission_keys=permission_keys, language=language, role="technician",
    )


async def _handle_invite_request(
    client: DataClient, *, ctx: ResolvedContext, permission_keys: list[str],
    language: str, role: str,
) -> ChatReply:
    if "member.manage" not in set(permission_keys):
        return ChatReply(text=_t(TECHNICIAN_INVITE_DENIED, language))
    invite = await client.create_invite(
        str(ctx.license_id),
        {"role": role, "max_uses": 1, "expires_in_days": 7},
        actor_id=ctx.chann_uid,
    )
    # A lookup, not a comparison: the boundary test forbids application
    # policy from branching on a role string, and it is right to — role
    # labels are tenant data. This is only which sentence to print, so the
    # table says so plainly and the invite's own role stays the key.
    reply = INVITE_REPLY_BY_ROLE.get(role, SALES_INVITE_REPLY)
    return ChatReply(text=_t(reply, language).format(code=invite["invite_code"]))


def _ask_which_invite(language: str) -> ChatReply:
    return ChatReply(
        text=_t(INVITE_WHICH_KIND, language),
        quick_replies=[
            ("ช่าง", "ขอรหัสเชิญช่าง"),
            ("ทีมขาย/CS", "ขอรหัสเชิญทีมขาย"),
        ],
    )


# Deal stage transitions (9.6) are matched directly against the message
# rather than sent through the AI parser: a deal_id is a stable,
# machine-parseable token (D-YYYY-NNNN) and the possible stage words are a
# small closed set — free-text understanding buys nothing here and only
# risks a hallucinated stage.
DEAL_ID_RE = re.compile(r"D-\d{4}-\d{4}", re.IGNORECASE)

_DEAL_STAGE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    # lost checked BEFORE won: "ไม่สำเร็จ" contains "สำเร็จ" as a substring,
    # so checking won first would misclassify "ปิดไม่สำเร็จ" as a win.
    (("ปิดไม่สำเร็จ", "ปิดดีลไม่สำเร็จ", "ไม่สำเร็จ", "lost", "lose", "ลูกค้าไม่เอา", "ไม่เอา", "แพ้", "ไปซื้อที่อื่น", "ซื้อที่อื่น",
      "หลุด", "ไม่ซื้อ", "ไม่ตกลง", "ลูกค้าปฏิเสธ", "ลูกค้ายกเลิก", "closed lost", "no deal", "ไม่ปิด"), "lost"),
    (("ปิดสำเร็จ", "ปิดดีลสำเร็จ", "สำเร็จ", "won", "win", "ปิดได้แล้ว", "ปิดได้", "ลูกค้าตกลง", "ตกลงแล้ว", "ตกลงซื้อ", "ซื้อแล้ว",
      "จ่ายแล้ว", "เซ็นแล้ว", "ได้งาน", "ปิดดีล", "ปิดจ๊อบ", "closed won", "deal done", "closed"), "won"),
)
# Checked separately from the table above: "เสนอราคา" (propose a price) is
# also the literal root of "ใบเสนอราคา" (a quote — the document, Phase
# 10's own entity) — "สร้างใบเสนอราคาจากดีล D-2026-0001" contains BOTH a
# valid deal code AND the substring "เสนอราคา", and was being misread as a
# deal-stage command instead of quote creation. If "ใบเสนอราคา" appears
# anywhere in the message, this is about the noun (a quote), never the
# deal-stage verb, regardless of what else the message contains.
_PROPOSED_KEYWORDS = ("เสนอราคาแล้ว", "เสนอราคา", "proposed", "propose")
_QUOTE_NOUN_MARKER = "ใบเสนอราคา"
# Checked separately from the table above: Thai naturally splits this one
# across the deal code ("เปิดดีล D-2026-0001 ใหม่"), so it needs both words
# present rather than one contiguous phrase. Safe as an AND-check because a
# deal code must already be present in the message for this function to be
# called at all — "เปิด"+"ใหม่" alone, without a deal code anywhere, means
# nothing here.
_REOPEN_WORDS = ("เปิด", "ใหม่")
_REOPEN_ENGLISH = ("reopen",)


def _bare_stage_word(message: str) -> str | None:
    """The stage a message names when it names nothing else."""
    lowered = (message or "").strip().lower()
    for keywords, stage in _DEAL_STAGE_KEYWORDS:
        if lowered in {k.lower() for k in keywords}:
            return stage
    return None


def _parse_deal_stage_command(message: str) -> tuple[str, str] | None:
    """Returns (deal_code, target_stage) if the message names a deal AND a
    recognised stage keyword, else None — a bare deal code with no
    recognisable stage word is not a command this function claims.

    The deal code is stripped out before keyword matching so its position
    in the sentence doesn't matter — "เปิดดีล D-2026-0001 ใหม่" and "เปิด
    D-2026-0001 ใหม่อีกครั้ง" both say the same thing with the code in a
    different place.
    """
    match = DEAL_ID_RE.search(message or "")
    if not match:
        return None
    remainder = (message or "").replace(match.group(0), " ").lower()
    if any(w in remainder for w in _REOPEN_ENGLISH) or all(w in remainder for w in _REOPEN_WORDS):
        return match.group(0).upper(), "new"
    for keywords, stage in _DEAL_STAGE_KEYWORDS:
        if any(k.lower() in remainder for k in keywords):
            return match.group(0).upper(), stage
    if _QUOTE_NOUN_MARKER not in remainder and any(
        k.lower() in remainder for k in _PROPOSED_KEYWORDS
    ):
        return match.group(0).upper(), "proposed"
    return None


# ------------------------------------------------- Phase 10 company profile
#
# Deterministic, trigger-matched — never routed through the AI parser, for
# the same reason deal-stage commands aren't: these values end up printed on
# a legal document a customer receives. A hallucinated or "helpfully
# corrected" tax ID is far worse than a command that simply isn't
# recognised, and the field set here is small and closed.

COMPANY_FIELD_TRIGGERS: list[tuple[tuple[str, ...], str]] = [
    # Longest/most specific first: "ตั้งชื่อบริษัท" must not be swallowed by
    # a shorter prefix, and "เลขผู้เสียภาษี" contains no other trigger.
    (("ตั้งเลขผู้เสียภาษี", "เลขผู้เสียภาษี", "เลขภาษี", "tax id", "taxid"), "tax_id"),
    (("ตั้งที่อยู่บริษัท", "ที่อยู่บริษัท", "company address"), "company_address"),
    (("ตั้งชื่อนิติบุคคล", "ชื่อนิติบุคคล", "legal name"), "legal_name"),
    (("ตั้งอีเมลบริษัท", "อีเมลบริษัท", "company email"), "company_email"),
    (("ตั้งเบอร์บริษัท", "เบอร์บริษัท", "โทรบริษัท", "company phone"), "company_phone"),
    (("ตั้งภาษีมูลค่าเพิ่ม", "ภาษีมูลค่าเพิ่ม", "ตั้งแวต", "vat"), "vat_rate"),
]

# "ไม่จด VAT" has to be checked before the plain "vat" trigger above, or the
# negative form gets read as an attempt to set a rate — the same substring
# trap that made every lost deal look won in Phase 9.
COMPANY_NO_VAT_PHRASES = ("ไม่จดvat", "ไม่จดแวต", "ไม่ได้จดvat", "ไม่จดภาษีมูลค่าเพิ่ม", "not vat registered")

COMPANY_VIEW_PHRASES = ("ข้อมูลบริษัท", "ดูข้อมูลบริษัท", "company profile", "company info", "ข้อมูลบริษัทของเรา", "รายละเอียดบริษัท")

COMPANY_PROFILE_LABELS = {
    "legal_name": {"th": "ชื่อนิติบุคคล", "en": "Legal name"},
    "tax_id": {"th": "เลขผู้เสียภาษี", "en": "Tax ID"},
    "company_address": {"th": "ที่อยู่บริษัท", "en": "Company address"},
    "company_phone": {"th": "เบอร์โทรบริษัท", "en": "Company phone"},
    "company_email": {"th": "อีเมลบริษัท", "en": "Company email"},
    "vat_rate": {"th": "ภาษีมูลค่าเพิ่ม", "en": "VAT rate"},
}

COMPANY_UPDATED = {
    "th": "บันทึก{label}เรียบร้อยแล้ว",
    "en": "{label} saved.",
}
COMPANY_NEEDS_VALUE = {
    "th": "กรุณาระบุ{label}ต่อท้ายคำสั่งด้วย เช่น \"ตั้งเลขผู้เสียภาษี 0105558123456\"",
    "en": "Please include the {label} after the command.",
}
COMPANY_BAD_TAX_ID = {
    "th": "เลขผู้เสียภาษีต้องเป็นตัวเลข 13 หลักพอดี",
    "en": "A tax ID must be exactly 13 digits.",
}
COMPANY_BAD_VAT = {
    "th": "อัตราภาษีต้องอยู่ระหว่าง 0 ถึง 100 เช่น \"ตั้งภาษีมูลค่าเพิ่ม 7%\"",
    "en": "The VAT rate must be between 0 and 100, e.g. 7%.",
}
COMPANY_NO_VAT_SAVED = {
    "th": "บันทึกแล้วว่าบริษัทนี้ไม่ได้จดภาษีมูลค่าเพิ่ม เอกสารจะไม่แสดงบรรทัด VAT",
    "en": "Recorded as not VAT-registered. Documents will show no VAT line.",
}
COMPANY_DENIED = {
    "th": "การแก้ไขข้อมูลบริษัทต้องมีสิทธิ์ setting.manage",
    "en": "Editing company details requires the setting.manage permission.",
}
COMPANY_READY = {
    "th": "ข้อมูลครบพร้อมออกเอกสารแล้ว",
    "en": "Ready to issue documents.",
}
COMPANY_MISSING = {
    "th": "ยังขาด: {fields} — ต้องกรอกให้ครบก่อนออกใบเสนอราคา",
    "en": "Still missing: {fields} — required before issuing a quote.",
}
COMPANY_NOT_SET = {"th": "(ยังไม่ได้ตั้ง)", "en": "(not set)"}
COMPANY_SAVE_FAILED = {
    "th": "ขออภัย ไม่สามารถบันทึกข้อมูลบริษัทได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
    "en": "Sorry, the company details could not be saved right now. Please try again.",
}


_LOOKUP_HEAD_RE = re.compile(
    r"^(?:ขอ)?(?:ลูกค้าชื่อ|ลูกค้าที่ชื่อ|ข้อมูลของ|เบอร์โทรลูกค้า|เบอร์ลูกค้า|เบอร์ของ|เบอร์โทรของ|เบอร์|โทรของ|ค้นหา|ค้น|search)\s*(.+)$",
    re.I,
)
_LOOKUP_TAIL_RE = re.compile(r"^(.+?)\s*(?:เบอร์อะไร|เบอร์โทรอะไร|โทรเท่าไหร่|เบอร์เท่าไหร่|เบอร์ไร)$", re.I)


def _customer_lookup_term(message: str) -> str | None:
    """The name (or number) in a lookup phrased without "ค้นหาลูกค้า"."""
    text = (message or "").strip()
    if not text or re.search(r"[CDQT]-\d{4}-\d{4}", text, re.I) or len(text) > 40:
        return None
    for rx in (_LOOKUP_HEAD_RE, _LOOKUP_TAIL_RE):
        m = rx.match(text)
        if m and m.group(1).strip():
            term = re.sub(r"^(?:ลูกค้า|ของ)\s*", "", m.group(1).strip(" :?")).strip()
            if term and not any(w in term.lower() for w in ("ทั้งหมด", "ใครบ้าง", "กี่คน")):
                return term
    return None


def _asks_about_current_job(message: str) -> bool:
    """A technician ASKING about the job in hand without naming it —
    "ลูกค้าเบอร์อะไร", "งานนี้ที่อยู่ไหน". Not "แก้เบอร์เป็น …", which is an
    edit of their own profile."""
    compact = _normalise(TICKET_CODE_RE.sub(" ", message or ""))
    if not compact or len(compact) > 30:
        return False
    if compact.startswith(("แก้", "เปลี่ยน", "อัปเดต", "ตั้ง", "update", "change", "set")) or any(
        w in compact for w in ("เปลี่ยนเป็น", "แก้เป็น", "เป็นเบอร์", "ใหม่คือ", "ใหม่เป็น")
    ) or re.search(r"\d{7,}", compact):
        return False
    words = ("ที่อยู่", "เบอร์", "โทร", "กี่โมง", "นัด", "งานนี้", "ลูกค้าชื่อ", "ไปที่ไหน", "บ้านลูกค้า", "แผนที่", "ที่อยู่ลูกค้า",
             "เบอร์ลูกค้า", "map", "address", "phone", "ชื่อลูกค้า", "รายละเอียด", "อาการ")
    if not any(w in compact for w in words):
        return False
    # A question, or a request ("ขอเบอร์ลูกค้าหน่อย", "ที่อยู่ลูกค้าหน่อย"), or
    # the bare noun ("แผนที่") — review, 6 Sep 2026 (B11).
    return (
        _looks_like_a_question(message) or compact.endswith(("อะไร", "ไหน", "ไหร่", "ไร"))
        or compact != _compact(message) or compact.startswith(("ขอ", "ดู", "เช็ค", "บอก", "ส่ง"))
        or compact in ("ที่อยู่ลูกค้า", "เบอร์ลูกค้า", "บ้านลูกค้า", "แผนที่", "ที่อยู่", "เบอร์", "เบอร์โทรลูกค้า", "map", "address")
    )


def _strip_leading_connector(value: str) -> str:
    for lead in ("เป็น", "คือ", ":", "=", "is"):
        if value.lower().startswith(lead):
            return value[len(lead):].strip()
    return value


def _parse_company_profile_command(message: str) -> tuple[str, str] | None:
    """Single field. Returns (field, raw_value) or None.

    Deliberately returns None rather than guessing when a trigger appears
    with nothing after it — the caller turns that into a "please include
    the value" prompt, which is honest, instead of writing a blank.
    """
    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()
    compact = lowered.replace(" ", "")

    if any(p.replace(" ", "") in compact for p in COMPANY_NO_VAT_PHRASES):
        return "vat_rate", ""

    for triggers, field in COMPANY_FIELD_TRIGGERS:
        for trigger in triggers:
            index = lowered.find(trigger.lower())
            if index == -1:
                continue
            return field, _strip_leading_connector(text[index + len(trigger):].strip())
    return None


def _explicit_trigger_positions(line: str) -> list[tuple[int, int, str]]:
    """Every "ตั้ง…"-prefixed trigger in one line, as (start, end, field).

    Only the explicit `ตั้ง` forms are used as split points, never the bare
    nouns. A bare noun is a legitimate substring of a real value — Bangkok
    has a district literally called เขตภาษีเจริญ, so splitting an address on
    "ภาษี" would silently cut it in half and file the remainder as a VAT
    rate. Nobody writes "ตั้งภาษีมูลค่าเพิ่ม" inside their street address,
    so the explicit form is safe to treat as a boundary.
    """
    lowered = line.lower()
    found: list[tuple[int, int, str]] = []
    for triggers, field in COMPANY_FIELD_TRIGGERS:
        for trigger in triggers:
            if not trigger.startswith("ตั้ง"):
                continue
            start = lowered.find(trigger.lower())
            if start == -1:
                continue
            found.append((start, start + len(trigger), field))
            break
    return sorted(found)


def _parse_company_profile_commands(message: str) -> list[tuple[str, str]]:
    """Zero or more (field, raw_value), so several fields can be set in one
    message — the thing a person actually wants when first filling this in.

    Two shapes are accepted, both deterministic:

      * one field per line (newline-separated), which is unambiguous
        whatever the values contain; and
      * several `ตั้ง…` commands on a single line, split at those explicit
        markers only.

    Later mentions of the same field win, matching how the rest of this
    engine treats a correction typed in the same breath.
    """
    text = (message or "").strip()
    if not text:
        return []

    results: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        positions = _explicit_trigger_positions(line)
        if len(positions) < 2:
            # One command (or none) on this line — the single-field parser
            # already handles the whole-line case, including "ไม่จด VAT".
            parsed = _parse_company_profile_command(line)
            if parsed is not None:
                results[parsed[0]] = parsed[1]
            continue

        # Several explicit commands share this line: each value runs from the
        # end of its own trigger to the start of the next one.
        for index, (_, end, field) in enumerate(positions):
            stop = positions[index + 1][0] if index + 1 < len(positions) else len(line)
            results[field] = _strip_leading_connector(line[end:stop].strip())

    return list(results.items())


def _is_company_profile_view(message: str) -> bool:
    if not (message or "").strip():
        return False
    # Exact-ish match only: a longer sentence that merely contains the words
    # is more likely to be an edit command, which the parser above handles.
    return _matches_phrase(message, COMPANY_VIEW_PHRASES)


# ------------------------------------------------ technician teams (Phase 7)
#
# A shop forms teams so CS can dispatch "ให้ทีม AC" (12.4) and a team
# member can take the job. The Data Tier has had teams since Phase 7;
# nothing above it let anyone create one (owner audit, 3 Sep).

TEAM_LIST_PHRASES = (
    "รายชื่อทีมช่าง", "ทีมช่าง", "ดูทีมช่าง", "technician teams", "teams", "มีทีมอะไรบ้าง", "ทีมมีอะไรบ้าง", "ทีม",
    "ทีมทั้งหมด", "รายชื่อทีม", "ดูทีม", "team list", "ทีมไหนบ้าง", "มีทีมไหนบ้าง", "ทีมช่างทั้งหมด",
)
# Read-level gates for the list/view tiles (review, 6 Sep 2026): the
# shipped role templates — member (salesperson), cs — must be able to use
# every tile on their menu. Seeing who the technicians are, or what the
# catalogue holds, is not the same as managing them.
TEAM_VIEW_KEYS = frozenset({"ticket.read", "customer.read", "deal.read", "team.manage"})
# The catalogue (name, price) is what the public storefront already shows
# every customer, so any member with a read key may list it.
PRODUCT_VIEW_KEYS = frozenset({"product.read", "product.manage", "deal.read", "quote.read", "customer.read", "ticket.read"})
DEAL_VIEW_KEYS = frozenset({"deal.read", "customer.read"})
WORK_VIEW_KEYS = frozenset({"followup.read", "ticket.read"})
TEAM_CREATE_TRIGGERS = ("สร้างทีมช่าง", "ตั้งทีมช่าง", "สร้างทีม ", "create technician team", "create team ")
_TEAM_ADD_RE = re.compile(
    r"^(?:เพิ่ม|ใส่|ให้)\s*(?P<who>.+?)\s*(?:เข้า|ใน|ไป)\s*ทีม\s*(?P<team>.+?)\s*(?P<lead>(?:เป็น)?หัวหน้า(?:ทีม)?)?\s*$"
)
_TEAM_LEAD_RE = re.compile(r"^(?:ตั้ง|ให้|แต่งตั้ง)\s*(?P<who>.+?)\s*เป็นหัวหน้าทีม\s*(?P<team>.+?)\s*$")
_TEAM_REMOVE_RE = re.compile(r"^(?:เอา|ลบ|นำ|ถอด)\s*(?P<who>.+?)\s*ออกจากทีม\s*(?P<team>.+?)\s*$")
_TEAM_DELETE_RE = re.compile(r"^(?:ลบทีมช่าง|ลบทีม|ยุบทีม)\s*(?P<team>.+?)\s*$")
TEAM_TEXT = {
    "list_head": {"th": "ทีมช่าง ({n} ทีม):", "en": "Technician teams ({n}):"},
    "list_empty": {
        "th": "ยังไม่มีทีมช่าง พิมพ์ \"สร้างทีมช่าง แอร์\" แล้ว \"เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า\"",
        "en": "No teams yet — \"create technician team AC\" then \"add Somsak to team AC as lead\"",
    },
    "created": {"th": "สร้างทีม {team} แล้ว เพิ่มช่างด้วย \"เพิ่ม <ชื่อ> เข้าทีม {team}\"", "en": "Team {team} created — add people with \"add <name> to team {team}\""},
    "added": {"th": "เพิ่ม {who} เข้าทีม {team}{lead} แล้ว", "en": "Added {who} to team {team}{lead}"},
    "lead_set": {"th": "ตั้ง {who} เป็นหัวหน้าทีม {team} แล้ว", "en": "{who} is now lead of team {team}"},
    "removed": {"th": "เอา {who} ออกจากทีม {team} แล้ว", "en": "Removed {who} from team {team}"},
    "deleted": {"th": "ลบทีม {team} แล้ว", "en": "Team {team} deleted"},
    "no_team": {"th": "ไม่พบทีม \"{team}\" พิมพ์ \"ทีมช่าง\" เพื่อดูรายชื่อทีม", "en": "No team \"{team}\" — type \"teams\" to list them"},
    "no_tech": {"th": "ไม่พบช่างชื่อ \"{who}\" พิมพ์ \"รายชื่อช่าง\" เพื่อดูรายชื่อ", "en": "No technician named \"{who}\" — type \"technicians\""},
    "many_tech": {"th": "มีช่างชื่อคล้ายกันหลายคน: {names} — พิมพ์ชื่อเต็ม", "en": "Several match: {names} — use the full name"},
    "need_name": {"th": "ตั้งชื่อทีมด้วยครับ เช่น \"สร้างทีมช่าง แอร์\"", "en": "Name the team, e.g. \"create technician team AC\""},
}


async def _team_named(client: DataClient, license_id: str, fragment: str) -> dict | None:
    fragment = (fragment or "").strip().lower()
    if not fragment:
        return None
    teams = await client.list_technician_teams(license_id)
    exact = [t for t in teams if str(t.get("team_name") or "").lower() == fragment]
    if exact:
        return exact[0]
    loose = [t for t in teams if fragment in str(t.get("team_name") or "").lower()]
    return loose[0] if len(loose) == 1 else None


async def _technician_named(
    client: DataClient, license_id: str, fragment: str,
) -> tuple[dict | None, list[str]]:
    """(the one technician whose name contains the fragment, or None, and
    the candidate names when several do)."""
    fragment = (fragment or "").strip().lower()
    if not fragment:
        return None, []
    members = await client.list_members(license_id)
    matches = []
    for m in members:
        if str(m.get("status") or "active") != "active":
            continue
        try:
            profile = await client.get_profile(str(m.get("chann_uid") or "")) or {}
        except Exception:
            profile = {}
        name = " ".join(p for p in (profile.get("first_name"), profile.get("last_name")) if p)
        if name and (fragment in name.lower() or name.lower() in fragment):
            matches.append(({**m, "name": name}, name))
    if len(matches) == 1:
        return matches[0][0], []
    return None, [n for _m, n in matches]


async def _maybe_handle_teams(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    text = (message or "").strip()
    lowered = text.lower()
    license_id = str(license_id)
    held = set(permission_keys)

    if _matches_phrase(text, TEAM_LIST_PHRASES):
        if not held & TEAM_VIEW_KEYS:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        try:
            teams = await client.list_technician_teams(license_id)
        except Exception:
            log.exception("could not list teams")
            return ChatReply(text=unavailable_reply(language))
        if not teams:
            return ChatReply(text=_t(TEAM_TEXT["list_empty"], language))
        lines = []
        for team in teams[:10]:
            try:
                members = await client.list_team_members(license_id, str(team["id"]))
            except Exception:
                members = []
            names = []
            for m in members:
                try:
                    profile = await client.get_profile(str(m.get("chann_uid") or "")) or {}
                except Exception:
                    profile = {}
                name = " ".join(p for p in (profile.get("first_name"), profile.get("last_name")) if p) or str(m.get("chann_uid") or "")
                names.append(name + ((" (lead)" if language == "en" else " (หัวหน้า)") if m.get("is_lead") else ""))
            lines.append(f"· {team.get('team_name')}: " + (", ".join(names) if names else ("no members yet" if language == "en" else "ยังไม่มีสมาชิก")))
        return ChatReply(
            text=_t(TEAM_TEXT["list_head"], language).format(n=len(teams)) + "\n" + "\n".join(lines),
            quick_replies=[("รายชื่อช่าง", "รายชื่อช่าง")],
            quick_reply_url=_dashboard_button("teams", language),
        )

    # The trailing space in "สร้างทีม " is deliberate — it is what stops
    # "สร้างทีมขาย" ("create a sales team", which is not a feature) from
    # being read as "สร้างทีม" + the name "ขาย". `.strip()` threw that
    # space away and the shop got a TECHNICIAN team called ขาย
    # (10 ก.ย. 2569). Matched as written, so a trigger that ends in a space
    # requires the separator and one that does not still matches tightly.
    create = next(
        (t for t in TEAM_CREATE_TRIGGERS
         if lowered.startswith(t.lower()) and len(lowered) > len(t)),
        None,
    )
    if create is not None or lowered in ("สร้างทีมช่าง", "ตั้งทีมช่าง"):
        # "สร้างทีมช่างยังไง" — how do I create a technician team? — took
        # everything after the trigger as the name and created a team
        # called "ยังไง" (10 ก.ย. 2569). A team named out of a question is
        # a row somebody has to find and delete.
        guarded = _intent_guard_reply(message, action="team_manage", language=language)
        if guarded is not None:
            return guarded
        if "team.manage" not in held:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        name = text[len(create):].strip(" :") if create else ""
        if not name:
            return ChatReply(text=_t(TEAM_TEXT["need_name"], language))
        try:
            team = await client.create_technician_team(license_id, name)
        except DataTierError as exc:
            if exc.status_code == 409:
                return ChatReply(text=f"มีทีม {name} อยู่แล้ว" if language != "en" else f"Team {name} already exists")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        except Exception:
            log.exception("could not create a team")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        return ChatReply(
            text=_t(TEAM_TEXT["created"], language).format(team=team.get("team_name") or name),
            quick_replies=[("รายชื่อช่าง", "รายชื่อช่าง"), ("ทีมช่าง", "ทีมช่าง")],
        )

    for kind, regex in (("lead", _TEAM_LEAD_RE), ("remove", _TEAM_REMOVE_RE), ("add", _TEAM_ADD_RE)):
        m = regex.match(text)
        if not m:
            continue
        if "team.manage" not in held:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        who, team_name = m.group("who"), m.group("team")
        if kind == "add" and m.group("lead"):
            team_name = team_name.strip()
        team = await _team_named(client, license_id, team_name)
        if team is None:
            return ChatReply(text=_t(TEAM_TEXT["no_team"], language).format(team=team_name.strip()))
        tech, candidates = await _technician_named(client, license_id, who)
        if tech is None:
            if candidates:
                return ChatReply(text=_t(TEAM_TEXT["many_tech"], language).format(names=", ".join(candidates)))
            return ChatReply(text=_t(TEAM_TEXT["no_tech"], language).format(who=who.strip()))
        try:
            if kind == "remove":
                await client.remove_team_member(license_id, str(team["id"]), str(tech["id"]))
                key, lead = "removed", ""
            else:
                is_lead = kind == "lead" or bool(m.groupdict().get("lead"))
                await client.add_team_member(license_id, str(team["id"]), str(tech["id"]), is_lead=is_lead)
                key = "lead_set" if kind == "lead" else "added"
                lead = (" เป็นหัวหน้า" if language != "en" else " as lead") if (kind == "add" and is_lead) else ""
        except Exception:
            log.exception("team change failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        return ChatReply(
            text=_t(TEAM_TEXT[key], language).format(who=tech["name"], team=team.get("team_name"), lead=lead),
            quick_replies=[("ทีมช่าง", "ทีมช่าง")],
        )

    m = _TEAM_DELETE_RE.match(text)
    if m:
        if "team.manage" not in held:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        team = await _team_named(client, license_id, m.group("team"))
        if team is None:
            return ChatReply(text=_t(TEAM_TEXT["no_team"], language).format(team=m.group("team").strip()))
        try:
            await client.delete_technician_team(license_id, str(team["id"]))
        except Exception:
            log.exception("team delete failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        return ChatReply(text=_t(TEAM_TEXT["deleted"], language).format(team=team.get("team_name")))
    return None


SHOP_INFO_PHRASES = (
    "ข้อมูลร้าน", "ข้อมูลร้านค้า", "ขอข้อมูลร้าน", "ขอข้อมูลร้านค้า", "ดูข้อมูลร้าน", "ร้านของเรา", "ร้านของฉัน",
    "รหัสร้าน", "รหัสร้านค้า", "รหัสบริษัท", "ขอรหัสร้าน", "ขอรหัสร้านค้า", "ขอรหัสบริษัท",
    "รหัสร้านคืออะไร", "shop code", "company code", "shop info", "our shop", "รหัสร้านอะไร", "รหัสร้านคือ", "ร้านรหัสอะไร",
    "รหัสร้านให้ลูกค้า", "รหัสให้ลูกค้า", "รหัสผูกร้าน", "รหัสสำหรับลูกค้า", "ลูกค้าผูกร้านยังไง", "ลูกค้าผูกร้านด้วยรหัสอะไร",
    "code for customers", "shop code for customers", "what is our shop code", "ร้านเรารหัสอะไร", "รหัสร้านเรา",
)
TECHNICIAN_LIST_PHRASES = (
    "รายชื่อช่าง", "ช่างมีใครบ้าง", "ช่างในร้าน", "ช่างทั้งหมด", "ดูช่าง", "รายชื่อทีมช่าง",
    "technicians", "list technicians", "technician list", "ช่างว่างไหม", "ช่างว่าง", "ใครว่างบ้าง", "ใครว่างบ้างวันนี้",
    "ใครว่างวันนี้", "ช่างว่างบ้างไหม", "ช่างคนไหนว่าง", "ช่าง", "technician", "techs", "ทีมช่างมีใครบ้าง", "who is free",
    "who's free", "available technicians", "ช่างมีกี่คน", "มีช่างกี่คน", "ช่างว่างกี่คน",
)
# "ตั้งค่า" on its own: the company profile is where the settings live.
SETTINGS_PHRASES = ("ตั้งค่า", "ตั้งค่าร้าน", "การตั้งค่า", "ตั้งค่าบริษัท", "settings", "setting", "config", "การตั้งค่าร้าน")
SHOP_INFO_TEXT = {
    "th": (
        "ร้าน: {name}\nรหัสร้าน: {code}\n{contact}"
        "\nรหัสนี้ใช้ให้ลูกค้าพิมพ์ใน LINE บริการลูกค้าเพื่อผูกกับร้าน "
        "ส่วนช่างเข้าร่วมด้วยรหัสเชิญ (พิมพ์ \"ขอรหัสเชิญช่าง\")"
    ),
    "en": (
        "Shop: {name}\nShop code: {code}\n{contact}"
        "\nCustomers type this code in the customer LINE to link to the shop; "
        "technicians join with an invite code (\"technician invite code\")"
    ),
}
TECHNICIAN_LIST_HEAD = {"th": "ช่างในร้าน ({n} คน):", "en": "Technicians ({n}):"}
TECHNICIAN_LIST_EMPTY = {
    "th": "ยังไม่มีช่างในร้าน ให้ช่างเพิ่มเพื่อน LINE ช่างแล้วพิมพ์รหัสเชิญ (พิมพ์ \"ขอรหัสเชิญช่าง\" เพื่อออกรหัส)",
    "en": "No technicians yet — a technician adds the technician LINE and types an invite code (\"ขอรหัสเชิญช่าง\" issues one)",
}


async def _handle_shop_info(
    client: DataClient, *, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    """Name, code and how to reach the shop — for any member. The code is
    what a customer types to link; staff kept asking for it (3 Sep) and
    got the permission catalogue back."""
    member = ctx.memberships[0] if ctx.memberships else {}
    name = member.get("company_name") or "—"
    code = member.get("license_code") or "—"
    contact_lines = []
    try:
        profile = await client.get_company_profile(str(license_id)) or {}
        for key, label_th, label_en in (
            ("phone", "โทร", "Phone"), ("email", "อีเมล", "Email"), ("address", "ที่อยู่", "Address"),
        ):
            if profile.get(key):
                contact_lines.append(f"{label_th if language != 'en' else label_en}: {profile[key]}")
    except Exception:
        log.warning("could not read the company profile for shop info")
    contact = ("\n".join(contact_lines) + "\n") if contact_lines else ""
    return ChatReply(
        text=_t(SHOP_INFO_TEXT, language).format(name=name, code=code, contact=contact),
        quick_replies=[("รายชื่อช่าง", "รายชื่อช่าง"), ("ข้อมูลบริษัท", "ข้อมูลบริษัท")],
    )


WARRANTY_BOOK_PHRASES = (
    "รายการประกัน", "ใบรับประกันทั้งหมด", "เครื่องที่ลงทะเบียน", "รายการเครื่องที่ลงทะเบียน",
    "สินค้าที่ลงทะเบียน", "ทะเบียนสินค้า", "warranties", "registered units", "เครื่องที่ลงทะเบียนไว้ทั้งหมด",
    "เครื่องที่ลงทะเบียนไว้", "รายการลงทะเบียน", "ทะเบียนเครื่อง", "เครื่องทั้งหมด", "registered products", "ประกันทั้งหมด",
    "รายการรับประกัน", "เครื่องที่ขายไป",
)
WARRANTY_BOOK_HEAD = {"th": "เครื่องที่ลงทะเบียนไว้ ({n} รายการล่าสุด):", "en": "Registered units (latest {n}):"}
WARRANTY_BOOK_EMPTY = {
    "th": "ยังไม่มีเครื่องที่ลงทะเบียน พิมพ์ \"ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย\" เพื่อบันทึกเครื่องที่ขาย",
    "en": "No registered units yet — type \"register product SN12345678 aircon for Somchai\" to record a sold unit",
}


async def _handle_warranty_book(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    if "warranty.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        rows = await client.list_warranties(str(license_id))
    except Exception:
        log.exception("could not list warranties")
        return ChatReply(text=unavailable_reply(language))
    rows = [r for r in rows if str(r.get("status") or "") != "void"]
    if not rows:
        return ChatReply(text=_t(WARRANTY_BOOK_EMPTY, language))
    shown = rows[:15]
    lines = [
        f"· {r.get('serial_number')} {r.get('product_name') or ''} "
        f"{'✓ ลูกค้าผูกแล้ว' if r.get('customer_chann_uid') else '· ยังไม่มีลูกค้าผูก'}"
        .replace("  ", " ")
        for r in shown
    ]
    return ChatReply(
        text=_t(WARRANTY_BOOK_HEAD, language).format(n=len(shown)) + "\n" + "\n".join(lines),
        quick_replies=[("ลงทะเบียนสินค้า", "ลงทะเบียนสินค้า")],
        quick_reply_url=_dashboard_button("warranties", language),
    )


async def _handle_technician_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    if not set(permission_keys) & TEAM_VIEW_KEYS:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        members = await client.list_members(str(license_id))
    except Exception:
        log.exception("could not list members")
        return ChatReply(text=unavailable_reply(language))
    technicians = [
        m for m in members
        if str(m.get("status") or "active") == "active"
        and ("technician" in str(m.get("role") or "").lower() or "ช่าง" in str(m.get("role") or ""))
    ]
    if not technicians:
        return ChatReply(
            text=_t(TECHNICIAN_LIST_EMPTY, language),
            quick_replies=[("ขอรหัสเชิญช่าง", "ขอรหัสเชิญช่าง")],
        )
    lines = []
    for m in technicians[:20]:
        chann_uid = str(m.get("chann_uid") or "")
        try:
            profile = await client.get_profile(chann_uid) or {}
        except Exception:
            profile = {}
        name = " ".join(
            p for p in (profile.get("first_name"), profile.get("last_name")) if p
        ) or chann_uid
        phone = f" · {profile['phone']}" if profile.get("phone") else ""
        lines.append(f"· {name}{phone}")
    return ChatReply(
        text=_t(TECHNICIAN_LIST_HEAD, language).format(n=len(technicians)) + "\n" + "\n".join(lines),
        quick_replies=[("รายการงาน", "รายการงาน"), ("ข้อมูลร้าน", "ข้อมูลร้าน")],
    )


def _format_company_profile(profile: dict, language: str) -> str:
    lines = []
    for field, labels in COMPANY_PROFILE_LABELS.items():
        raw = profile.get(field)
        if field == "vat_rate":
            shown = (
                _t(COMPANY_NOT_SET, language) if raw in (None, "")
                else f"{Decimal(str(raw)) * 100:g}%"
            )
        else:
            shown = raw if raw else _t(COMPANY_NOT_SET, language)
        lines.append(f"{_t(labels, language)}: {shown}")

    missing = profile.get("missing_for_documents") or []
    if missing:
        names = ", ".join(
            _t(COMPANY_PROFILE_LABELS.get(f, {"th": f, "en": f}), language) for f in missing
        )
        lines.append("")
        lines.append(_t(COMPANY_MISSING, language).format(fields=names))
    else:
        lines.append("")
        lines.append(_t(COMPANY_READY, language))
    return "\n".join(lines)


async def _handle_company_profile_view(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    # Viewing is for every member of the company; editing (below) still
    # needs setting.manage. The view used to demand the edit permission
    # and refuse with a sentence about editing (review, 6 Sep 2026).
    try:
        profile = await client.get_company_profile(str(license_id))
    except Exception:
        log.exception("company profile read failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return ChatReply(text=_format_company_profile(profile, language))


def _company_field_to_payload(field: str, raw_value: str, language: str) -> tuple[dict, str] | str:
    """Validate one field. Returns (payload_fragment, success_line) on
    success, or an error string to show the user.

    Validation lives here, before anything is sent, so a message setting
    three fields where one is malformed writes none of them. A partial
    write would leave the tenant believing the whole message was applied.
    """
    label = _t(COMPANY_PROFILE_LABELS[field], language)

    if field == "vat_rate":
        if not raw_value:
            # The "ไม่จด VAT" path: NULL, meaning no VAT line at all — a
            # different thing from a 0% rate.
            return {"vat_rate": None}, _t(COMPANY_NO_VAT_SAVED, language)
        digits = raw_value.replace("%", "").replace("เปอร์เซ็นต์", "").strip()
        try:
            percent = Decimal(digits)
        except (InvalidOperation, ValueError):
            return _t(COMPANY_BAD_VAT, language)
        if not (0 <= percent <= 100):
            return _t(COMPANY_BAD_VAT, language)
        # Typed as a percent, stored as a fraction.
        return (
            {"vat_rate": str(percent / Decimal(100))},
            _t(COMPANY_UPDATED, language).format(label=label),
        )

    if not raw_value:
        return _t(COMPANY_NEEDS_VALUE, language).format(label=label)

    if field == "tax_id":
        digits = "".join(ch for ch in raw_value if ch.isdigit())
        if len(digits) != 13:
            return _t(COMPANY_BAD_TAX_ID, language)
        raw_value = digits

    return {field: raw_value}, _t(COMPANY_UPDATED, language).format(label=label)


async def _handle_company_profile_command(
    client: DataClient, *, license_id, updates: list[tuple[str, str]],
    permission_keys: list[str], language: str, actor_id: str,
) -> ChatReply:
    """Applies one or several fields in a single write.

    All-or-nothing on purpose: every field is validated first, and one bad
    value refuses the whole message. Writing the two good fields out of
    three and reporting an error for the third reads as "it failed" while
    having silently changed the company's details.
    """
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(COMPANY_DENIED, language))

    payload: dict = {}
    success_lines: list[str] = []
    for field, raw_value in updates:
        outcome = _company_field_to_payload(field, raw_value, language)
        if isinstance(outcome, str):
            return ChatReply(text=outcome)
        fragment, line = outcome
        payload.update(fragment)
        success_lines.append(line)

    if not payload:
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    try:
        profile = await client.update_company_profile(
            str(license_id), payload, actor_id=actor_id
        )
    except Exception as exc:  # noqa: BLE001
        # The Data tier validates these too (tax_id length, vat_rate range)
        # and answers 422. Surfacing that as the same specific message the
        # local check would have given keeps one rule with one wording,
        # rather than a vague failure for the same mistake caught one layer
        # further in.
        if getattr(exc, "status_code", None) == 422 or "422" in str(exc):
            fields = {f for f, _ in updates}
            return ChatReply(
                text=_t(COMPANY_BAD_TAX_ID if "tax_id" in fields else COMPANY_BAD_VAT, language)
            )
        log.exception("company profile update failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    text = "\n".join(success_lines)
    missing = profile.get("missing_for_documents") or []
    if missing:
        names = ", ".join(
            _t(COMPANY_PROFILE_LABELS.get(f, {"th": f, "en": f}), language) for f in missing
        )
        text += "\n" + _t(COMPANY_MISSING, language).format(fields=names)
    else:
        text += "\n" + _t(COMPANY_READY, language)
    return ChatReply(text=text)


# ------------------------------------------- Notes, reminders, appointments
#
# Master Spec 6.3/6.7. The follow_ups table and its endpoints have existed
# since Phase 6, and ACTION_PERMISSIONS has promised note.* and followup.*
# just as long — but no chat handler ever implemented either, so both passed
# the permission gate and fell through to nothing. Notes had no table at all
# until migration 0013.
#
# Matched deterministically. A reminder that lands on the wrong day is worse
# than one the system says it did not understand: the person believes they
# are covered, and finds out when the customer has already gone quiet.

NOTE_TRIGGERS = (
    # "note" alone matched inside "notes C-2026-0001" and wrote a note whose
    # body was the leftover "s" (10 ก.ย. 2569). The separator is what makes
    # it a command with an argument — "note ลูกค้าจะโทรกลับ" still works,
    # "notes …" and "notebook" no longer do.
    "บันทึกว่า", "จดว่า", "โน้ตว่า", "note ", "note:", "note that", "จดไว้ว่า", "จดไว้หน่อยว่า", "จดไว้ด้วยว่า", "บันทึกไว้ว่า", "บันทึกไว้หน่อยว่า",
    "โน้ตไว้ว่า", "โน้ต:", "โน้ต :", "จด:", "บันทึก:", "memo:", "จดหน่อยว่า", "ช่วยจดว่า", "ช่วยบันทึกว่า",
)
NOTE_LIST_TRIGGERS = ("ดูบันทึก", "บันทึกของ", "ประวัติ")
# Correcting and removing the last note on a record. Dispatched before
# both the list and the create triggers, which they all contain.
NOTE_EDIT_TRIGGERS = ("แก้บันทึก", "แก้ไขบันทึก", "เปลี่ยนบันทึก", "edit note")
NOTE_DELETE_TRIGGERS = ("ลบบันทึก", "เอาบันทึกออก", "delete note")
# "ตั้งนัด"/"ตั้งเตือน" added from live use (2 Sep): "ตั้งนัดวันที่ 6
# ที่จะถึง" starts with neither "นัด" nor "เตือน", missed every matcher,
# and the person got a capability list for a perfectly ordinary sentence.
REMINDER_TRIGGERS = ("เตือน", "นัด", "ตั้งนัด", "ตั้งเตือน", "เพิ่มนัด", "remind", "อย่าลืม", "ช่วยเตือน", "don't forget", "dont forget", "remind me")

# Moving an existing appointment. Dispatched BEFORE creating, like
# cancelling: every one of these contains a create trigger inside it.
REMINDER_MOVE_TRIGGERS = (
    "เลื่อนนัด", "เลื่อนเตือน", "เปลี่ยนเวลา", "เปลี่ยนวันนัด", "เปลี่ยนวัน",
    "แก้เวลา", "แก้วันนัด", "reschedule",
)
# Dispatched BEFORE reminder creation: every one of these contains "เตือน",
# so the create matcher would otherwise claim "ยกเลิกเตือน C-2026-0011" and
# answer "ไม่เข้าใจวันที่" — the same longer-first rule every Thai trigger
# collision in this project has ended up needing.
REMINDER_CANCEL_TRIGGERS = (
    "ยกเลิกเตือน", "ยกเลิกการเตือน", "ลบเตือน", "cancel reminder", "ยกเลิกนัด", "ลบนัด", "เอานัดออก",
    "ยกเลิกอันเมื่อกี้", "ยกเลิกเมื่อกี้", "ยกเลิกที่ตั้งไว้", "ยกเลิกการนัด", "cancel appointment", "cancel the reminder",
    "cancel that", "delete reminder",
)
TODAY_WORK_PHRASES = (
    "งานวันนี้", "ที่ต้องทำวันนี้", "today",
    # "วันนี้มีอะไรบ้าง" opened the help menu (review, 6 Sep 2026).
    "วันนี้มีอะไรบ้าง", "วันนี้มีอะไร", "วันนี้ต้องทำอะไร", "วันนี้ต้องทำอะไรบ้าง", "วันนี้มีนัดอะไร",
    "วันนี้มีนัดอะไรบ้าง", "มีนัดอะไรวันนี้", "มีนัดอะไรวันนี้บ้าง", "วันนี้มีงานอะไร", "วันนี้มีงานอะไรบ้าง",
    "มีงานอะไรวันนี้", "มีงานอะไรวันนี้บ้าง", "มีอะไรต้องทำวันนี้", "วันนี้ทำอะไร", "what's on today", "what is on today",
    "วันนี้มีนัดใครบ้าง", "วันนี้มีนัดใคร", "วันนี้นัดใคร", "วันนี้มีนัดไหม", "วันนี้มีนัด", "วันนี้มีงานไหม", "วันนี้มีงาน",
    "วันนี้ต้องไปไหน", "วันนี้ต้องไปไหนบ้าง", "ต้องไปไหนบ้าง", "ต้องไปไหนวันนี้", "งานฉันวันนี้", "งานของฉันวันนี้",
    "today's work", "todays work", "today's jobs", "my day",
)
_DAY_SCOPES = {
    "วันนี้": 1, "พรุ่งนี้": 2, "มะรืน": 3, "มะรืนนี้": 3, "สัปดาห์นี้": 7, "อาทิตย์นี้": 7, "7วัน": 7,
    "today": 1, "tomorrow": 2, "thisweek": 7,
}


def _reminder_list_day(message: str) -> int | None:
    """"นัดหมายวันนี้", "นัดพรุ่งนี้", "นัดหมายสัปดาห์นี้": a LIST for that
    day, which the datetime guard used to read as creating one (review,
    6 Sep 2026). Returns the day span, or None."""
    compact = _normalise(message)
    day_first = re.match(
        r"^(วันนี้|พรุ่งนี้|มะรืนนี้|มะรืน|สัปดาห์นี้|อาทิตย์นี้|today|tomorrow|thisweek)(?:มี|ต้อง)?(?:นัดหมาย|นัด|เตือน|appointments?|reminders?)"
        r"(?:ใครบ้าง|อะไรบ้าง|ไหม|มั้ย|บ้าง|กี่นัด|กี่ราย|อะไร|ใคร|หรือเปล่า)?$", compact,
    )
    if day_first:
        return _DAY_SCOPES.get(day_first.group(1))
    heads = sorted(
        {t.replace(" ", "").lower() for t in REMINDER_LIST_TRIGGERS} | {"นัด", "นัดหมาย", "ดูนัด", "มีนัด", "appointments", "reminders"},
        key=len, reverse=True,
    )
    for head in heads:
        if compact.startswith(head):
            rest = re.sub(r"^(?:ของฉัน|ของผม|ทั้งหมด|มีอะไรบ้าง|มีอะไร|อะไรบ้าง|มี)", "", compact[len(head):]).strip()
            rest = re.sub(r"(?:มีอะไรบ้าง|มีอะไร|อะไรบ้าง|มีไหม|บ้าง)$", "", rest).strip()
            if rest in _DAY_SCOPES:
                return _DAY_SCOPES[rest]
    return None
UPCOMING_WORK_PHRASES = (
    "งานสัปดาห์นี้", "งานที่ค้าง", "ที่ต้องติดตาม", "upcoming", "มีอะไรต้องตาม", "มีอะไรต้องตามบ้าง", "ต้องตามอะไรบ้าง",
    "ต้องตามใครบ้าง", "สัปดาห์นี้มีอะไร", "สัปดาห์นี้มีอะไรบ้าง", "อาทิตย์นี้มีอะไร", "อาทิตย์นี้มีอะไรบ้าง", "อาทิตย์นี้ต้องทำอะไร",
    "อาทิตย์นี้ต้องทำอะไรบ้าง", "สัปดาห์นี้ต้องทำอะไร", "สัปดาห์นี้ต้องทำอะไรบ้าง", "งานอาทิตย์นี้", "this week", "what's on this week",
    "งานที่ต้องติดตาม", "ที่ต้องตาม", "follow ups", "follow-ups",
)

NOTE_SAVED = {
    "th": "บันทึกไว้กับ {code} แล้ว",
    "en": "Noted against {code}.",
}
NOTE_NEEDS_TARGET = {
    "th": "ระบุรหัสด้วยว่าบันทึกกับใคร เช่น \"บันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด\" หรือเปิดดูข้อมูลลูกค้า/ดีลนั้นก่อนแล้วค่อยพิมพ์บันทึกตาม",
    "en": "Say which record the note is about, e.g. \"note C-2026-0001 asked for a discount\", or open that record first.",
}
NOTE_EMPTY = {
    "th": "ยังไม่มีบันทึกของ {code}",
    "en": "No notes for {code} yet.",
}
REMINDER_SAVED = {
    "th": "ตั้งเตือน {code} วันที่ {date}{time} แล้ว",
    "en": "Reminder set for {code} on {date}{time}.",
}
REMINDER_NEEDS_DATE = {
    "th": "ไม่เข้าใจวันที่ ลองพิมพ์แบบนี้ดู: \"เตือน D-2026-0001 พรุ่งนี้\" · \"เตือน D-2026-0001 วันศุกร์ บ่าย 2\" · \"เตือน D-2026-0001 15 มี.ค.\"",
    "en": "Could not read the date. Try: \"remind D-2026-0001 tomorrow\" or \"remind D-2026-0001 15 มี.ค. 14:00\".",
}
REMINDER_NEEDS_TARGET = {
    "th": "ระบุรหัสด้วยว่าเตือนเรื่องอะไร เช่น \"เตือน D-2026-0001 พรุ่งนี้\" หรือเปิดดูข้อมูลลูกค้า/ดีลนั้นก่อนแล้วค่อยพิมพ์เตือนตาม",
    "en": "Say what the reminder is about, e.g. \"remind D-2026-0001 tomorrow\", or open that record first.",
}
REMINDER_DATE_PAST = {
    "th": "วันที่ {date} ผ่านมาแล้ว ตั้งเตือนได้ตั้งแต่วันนี้เป็นต้นไปครับ ลองพิมพ์ใหม่ เช่น \"เตือน {code} พรุ่งนี้\"",
    "en": "{date} has already passed — a reminder can only be set for today onward. Try \"remind {code} tomorrow\".",
}
REMINDER_CANCELLED = {
    "th": "ยกเลิกการเตือนของ {code} แล้ว {count} รายการ",
    "en": "Cancelled {count} reminder(s) for {code}.",
}
REMINDER_CANCEL_NONE = {
    "th": "ไม่มีการเตือนที่ค้างอยู่ของ {code}",
    "en": "No pending reminders for {code}.",
}
# Removing the row outright, not cancelling it. Reached only when the
# person explicitly asks for the appointment to be *gone* — a cancelled
# one still shows in the history, which is what most people mean and what
# every deterministic ยกเลิก/ลบนัด trigger still does.
REMINDER_DELETED = {
    "th": "ลบนัดของ {code} ออกแล้ว {count} รายการ",
    "en": "Removed {count} appointment(s) for {code}.",
}
REMINDER_DELETE_NONE = {
    "th": "ไม่มีนัดที่ค้างอยู่ของ {code} ให้ลบ",
    "en": "No pending appointment for {code} to remove.",
}
NOTE_UPDATED = {
    "th": "แก้บันทึกล่าสุดของ {code} แล้ว",
    "en": "Updated the latest note on {code}.",
}
NOTE_DELETED = {
    "th": "ลบบันทึกล่าสุดของ {code} แล้ว",
    "en": "Deleted the latest note on {code}.",
}
NOTE_EDIT_NEEDS_BODY = {
    "th": "จะแก้เป็นข้อความว่าอะไร เช่น \"แก้บันทึก {code} เป็น ลูกค้าขอส่วนลด 10%\"",
    "en": "Change it to what? e.g. \"edit note {code} to customer asked for 10% off\".",
}
REMINDER_MOVED = {
    "th": "เลื่อนนัดของ {code} เป็นวันที่ {date} {time} แล้ว",
    "en": "Moved the appointment for {code} to {date} {time}.",
}
REMINDER_MOVE_NONE = {
    "th": "ไม่มีนัดที่ค้างอยู่ของ {code} ให้เลื่อน ถ้าต้องการตั้งใหม่พิมพ์ได้เลย เช่น \"เตือน {code} พรุ่งนี้ 13.00\"",
    "en": "No pending appointment for {code} to move. To make one: \"remind {code} tomorrow 13:00\".",
}
REMINDER_MOVE_NEEDS_WHEN = {
    "th": "จะเลื่อนเป็นวันไหนเวลาใด ลองพิมพ์ เช่น \"เลื่อนนัด {code} เป็นพรุ่งนี้ 13.00\"",
    "en": "Move it to when? e.g. \"reschedule {code} to tomorrow 13:00\".",
}
REMINDER_CANCEL_NEEDS_TARGET = {
    "th": "ระบุรหัสด้วยว่ายกเลิกการเตือนของอะไร เช่น \"ยกเลิกเตือน C-2026-0001\" หรือเปิดดูข้อมูลนั้นก่อนแล้วค่อยพิมพ์ยกเลิก",
    "en": "Say which record to cancel reminders for, e.g. \"cancel reminder C-2026-0001\", or open that record first.",
}
WORK_EMPTY = {
    "th": "ไม่มีงานที่ต้องติดตามในช่วงนี้",
    "en": "Nothing to follow up on right now.",
}
WORK_HEADING = {
    "th": "งานที่ต้องติดตาม:",
    "en": "Follow-ups due:",
}

# Codes are how a person names a record in chat. The prefix tells us which
# kind it is, so one regex covers all three without the caller having to say.
ENTITY_CODE_RE = re.compile(r"\b([CDQ]-\d{4}-\d{4})\b", re.IGNORECASE)
# Service reports (SR-2026-0001). Lookarounds, not \b: Thai letters are
# word characters, so \b never fires between "ของ" and "SR-…" and the code
# in "อนุมัติรายงานของSR-2026-0001" would be invisible.
SERVICE_REPORT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(SR-\d{4}-\d{4})(?![0-9])", re.IGNORECASE)

CODE_PREFIX_TO_ENTITY = {"C": "customer", "D": "deal", "Q": "quote"}


def _find_entity_code(message: str) -> tuple[str, str] | None:
    """(entity_type, code) for the first record code in the message."""
    match = ENTITY_CODE_RE.search(message or "")
    if not match:
        return None
    code = match.group(1).upper()
    return CODE_PREFIX_TO_ENTITY[code[0]], code


# Codes the model may name, and what each prefix actually is. T- and SR-
# are not in CODE_PREFIX_TO_ENTITY because that map serves the sales
# lookup; here we need every prefix a person can type.
_ALL_CODE_PREFIXES = {
    "C": "customer", "D": "deal", "Q": "quote", "T": "ticket", "SR": "service_report",
}
_ANY_CODE_RE = re.compile(r"(?<![A-Za-z0-9])((?:SR|[CDQT])-\d{4}-\d{4})(?![0-9])", re.IGNORECASE)
# Field values the system defines. A model may only choose from these; a
# word it made up is dropped rather than acted on.
_CLOSED_VALUES: dict[str, frozenset[str]] = {
    "status": frozenset({
        "open", "assigned", "in_progress", "completed", "cancelled",
        "draft", "submitted", "approved", "rejected",
        "new", "contacted", "proposed", "won", "lost",
        "sent", "accepted", "expired",
    }),
    "stage": frozenset({"lead", "new", "contacted", "proposed", "won", "lost"}),
}
CODE_IS_ANOTHER_ENTITY = {
    "th": "{code} เป็นรหัส{kind} ไม่ใช่{claimed}ครับ ถ้าต้องการทำกับ{kind} พิมพ์คำสั่งของ{kind}ได้เลย",
    "en": "{code} is a {kind} code, not a {claimed}. Use the {kind} command for it.",
}
_CODE_KIND_LABEL = {
    "customer": {"th": "ลูกค้า", "en": "customer"},
    "deal": {"th": "ดีล", "en": "deal"},
    "quote": {"th": "ใบเสนอราคา", "en": "quotation"},
    "ticket": {"th": "งานซ่อม", "en": "job"},
    "service_report": {"th": "รายงานการซ่อม", "en": "service report"},
}


def _entity_code_mismatch(intent: dict) -> ChatReply | None:
    """The model named a record code that belongs to a different entity.

    Not a guess: the prefix says what the code IS. Refusing here is the
    difference between "that is a deal code" and quietly writing a note on
    a job the person never mentioned.
    """
    entity = str(intent.get("entity") or "")
    if entity not in _CODE_KIND_LABEL:
        return None
    fields = intent.get("fields") or {}
    for key in ("code", "entity_code", "target_code", "ticket_code", "deal_code", "quote_code"):
        raw = str(fields.get(key) or "").strip()
        if not raw:
            continue
        found = _ANY_CODE_RE.search(raw)
        if not found:
            continue
        prefix = found.group(1).upper().split("-")[0]
        kind = _ALL_CODE_PREFIXES.get(prefix)
        if kind and kind != entity:
            language = "th"
            return ChatReply(text=_t(CODE_IS_ANOTHER_ENTITY, language).format(
                code=found.group(1).upper(),
                kind=_t(_CODE_KIND_LABEL[kind], language),
                claimed=_t(_CODE_KIND_LABEL[entity], language),
            ), intent=intent)
    return None


#: A Thai mobile or landline as a person types it, with or without the
#: country code and with the separators people actually use.
_LOOKS_LIKE_A_PHONE_RE = re.compile(r"^(?:\+?66|0)\d{8,9}$")
#: Fields that carry money. A value here reaches a total, a quote and a
#: customer's screen, so it is checked before it is believed.
_MONEY_FIELDS = ("amount", "quoted_unit_price", "unit_price", "price", "discount", "total")
#: Fields that carry a count of things.
_COUNT_FIELDS = ("qty", "quantity")


def _reads_as_a_phone(raw: str) -> bool:
    return bool(_LOOKS_LIKE_A_PHONE_RE.match(re.sub(r"[ \-().]", "", raw)))


def _drop_invented_values(intent: dict) -> None:
    """Remove field values that are not values this system defines.

    A closed field has a fixed set of values. The model answering
    status="เลื่อนนัด" has not chosen one of them; it has written a phrase.
    Dropping it lets the handler ask, which is what should have happened.

    Money and counts are checked the same way, for the same reason. Asked
    for a price and given a phone number, the model returned
    quoted_unit_price="0812345678" — and the line was rewritten to
    812,345,678.00 baht, taking the deal total with it. A phone is never a
    price, and neither is a word; dropping the value makes the handler ask
    which it should have done.
    """
    fields = intent.get("fields")
    if not isinstance(fields, dict):
        return
    for key, allowed in _CLOSED_VALUES.items():
        value = fields.get(key)
        if value is None:
            continue
        if str(value).strip().lower() not in allowed:
            fields.pop(key, None)
    for key in _MONEY_FIELDS:
        raw = fields.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        text = str(raw).strip()
        if _reads_as_a_phone(text):
            fields.pop(key, None)
            continue
        try:
            if Decimal(text.replace(",", "")) <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError, ArithmeticError):
            fields.pop(key, None)
    for key in _COUNT_FIELDS:
        raw = fields.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            if int(str(raw).strip().replace(",", "")) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            fields.pop(key, None)


async def _resolve_entity(client: DataClient, license_id: str, entity_type: str, code: str):
    """The row a code refers to, or None. Tenant-scoped by every underlying
    list call, so a code from another tenant simply does not resolve."""
    if entity_type == "customer":
        rows = await client.list_customers(license_id)
        return next((r for r in rows if str(r.get("customer_id", "")).upper() == code), None)
    if entity_type == "deal":
        rows = await client.list_deals(license_id)
        return next((r for r in rows if str(r.get("deal_id", "")).upper() == code), None)
    if entity_type == "service_report":
        rows = await client.list_service_reports(license_id)
        return next((r for r in rows if str(r.get("report_id", "")).upper() == code), None)
    rows = await client.list_quotes(license_id)
    return next((r for r in rows if str(r.get("quote_id", "")).upper() == code), None)


class _TargetNotFound(Exception):
    """An explicit code was given but does not resolve to a real record —
    distinct from no code being given at all, so the reply can say "not
    found" rather than the more general "please specify"."""

    def __init__(self, entity_type: str, code: str):
        self.entity_type = entity_type
        self.code = code


async def _code_for_entity(
    client: DataClient, license_id: str, entity_type: str, entity_id: str,
) -> str | None:
    """The human-facing code for a record, given its type and id.

    The inverse of _resolve_entity, which looks a record up BY code. Needed
    by the reply path: a line_message_entity_map row carries a UUID, while
    everything downstream addresses records by C-/D-/Q- codes.
    """
    try:
        if entity_type == "customer":
            rows = await client.list_customers(license_id)
            key = "customer_id"
        elif entity_type == "deal":
            rows = await client.list_deals(license_id)
            key = "deal_id"
        elif entity_type == "quote":
            rows = await client.list_quotes(license_id)
            key = "quote_id"
        elif entity_type == "service_report":
            # So that replying to the approval notification and typing
            # "อนุมัติ" resolves the report the notification was about.
            rows = await client.list_service_reports(license_id)
            key = "report_id"
        elif entity_type in ("service_ticket", "ticket"):
            # Owner's transcript, 10 ก.ย. 2569: replying to "แจ้งซ่อมใหม่
            # T-2026-0004" with "มอบหมายให้ช่าง" was refused for naming no
            # job — because this returned None for the one entity type the
            # ticket notifications actually use, so nothing was remembered.
            rows = await client.list_tickets(license_id)
            key = "ticket_number"
        else:
            return None
    except Exception:
        log.exception("could not look up a code for %s/%s", entity_type, entity_id)
        return None

    row = next((r for r in rows if str(r.get("id")) == str(entity_id)), None)
    return str(row.get(key)) if row and row.get(key) else None


async def _resolve_target_or_context(
    client: DataClient, ctx: ResolvedContext, license_id: str, message: str,
) -> tuple[str, str, str] | None:
    """(entity_type, entity_id, code) from an explicit code in the message,
    or from "the record we were just looking at" when there is none.

    Reported live: "ข้อมูลลูกค้า C-2026-0001" followed immediately by
    "นัดประชุมพรุ่งนี้ตอน 9 โมงเช้า" with no code at all — refusing that
    reads as the system not noticing the record it had just shown.

    Returns None only when NO code was given and there is nothing recent to
    fall back on. A code that IS given but does not resolve raises
    _TargetNotFound, so the caller can tell the two situations apart in its
    reply. The fallback only fires when a real, recent reference exists
    (see cache.k_last_entity_ref), and the caller always names the record
    it used, so a stale guess is caught immediately rather than discovered
    later.
    """
    found = _find_entity_code(message)
    if found is not None:
        entity_type, code = found
        row = await _resolve_entity(client, license_id, entity_type, code)
        if row is None:
            raise _TargetNotFound(entity_type, code)
        return entity_type, str(row["id"]), code

    last_ref = await _last_entity_ref(client, ctx)
    if last_ref is None:
        return None
    return last_ref["entity_type"], last_ref["entity_id"], last_ref["code"]


async def _handle_note_create(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, trigger: str,
    permission_keys: list[str], language: str, actor_id: str,
) -> ChatReply:
    if "note.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )
    if target is None:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

    # The note body is everything after the trigger, minus the code itself
    # (there may be none at all, when the target came from context).
    lowered = message.lower()
    index = lowered.find(trigger.lower())
    body = message[index + len(trigger):] if index >= 0 else message
    body = ENTITY_CODE_RE.sub("", body).strip(" :·-").strip()
    if not body:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))

    try:
        await client.create_note(
            license_id,
            {"entity_type": entity_type, "entity_id": entity_id, "body": body},
            actor_id=actor_id,
        )
    except Exception:
        log.exception("note create failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(NOTE_SAVED, language).format(code=code),
        entity_type=entity_type, entity_id=entity_id,
        quick_replies=[
            ("ดูบันทึกทั้งหมด", f"ดูบันทึก {code}"),
            ("ตั้งเตือน", f"เตือน {code} พรุ่งนี้"),
        ],
    )


async def _handle_note_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    """The AI-routed path into the same note.create the deterministic
    triggers use, for free-text remarks with no trigger word at all —
    "ลูกค้าสนใจเรื่องการซื้อบ้าน" names no field to change and matches none
    of NOTE_TRIGGERS, so without this it fell through to the generic "not
    available yet" stub despite note.create having existed since Phase 6.

    Reuses _resolve_target_or_context rather than a separate lookup, so a
    note routed here resolves against an explicit code or the record just
    viewed exactly like one routed by a trigger word does.
    """
    fields = intent.get("fields") or {}
    body = (fields.get("body") or "").strip()
    if not body:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))

    license_id = str(license_id)
    # entity_code is optional in the prompt on purpose: the model is told to
    # omit it rather than invent one, so a message that names no code always
    # falls through to context here — never to a guess.
    lookup_message = fields.get("entity_code") or ""
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, lookup_message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )
    if target is None:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

    try:
        await client.create_note(
            license_id,
            {"entity_type": entity_type, "entity_id": entity_id, "body": body},
            actor_id=ctx.chann_uid,
        )
    except Exception:
        log.exception("note create failed (AI-routed)")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(NOTE_SAVED, language).format(code=code),
        entity_type=entity_type, entity_id=entity_id,
        quick_replies=[
            ("ดูบันทึกทั้งหมด", f"ดูบันทึก {code}"),
            ("ตั้งเตือน", f"เตือน {code} พรุ่งนี้"),
        ],
    )


async def _handle_note_edit(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, actor_id: str, delete: bool = False,
) -> ChatReply:
    """Correct or remove the most recent note on a record.

    Parity with the dashboard, which gains edit and delete on every note
    in this same patch: whatever one surface can do to a record, the other
    must be able to do too — the owner's rule, and the reason this exists
    rather than only the button.

    The LATEST note, not a chosen one: chat has no note ids and inventing
    a numbering scheme to type back would be worse than the mistake it
    fixes. Someone correcting a note is almost always correcting the one
    they just wrote; anything older is a job for the dashboard, where the
    notes are on screen with their own buttons.
    """
    needed = "note.update"
    if needed not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )
    if target is None:
        try:
            target = await _customer_named_in(client, license_id, message, permission_keys)
        except _AmbiguousName as exc:
            return _name_choice(message, exc, language)
    if target is None:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

    try:
        notes = await client.list_notes(license_id, entity_type, str(entity_id))
    except Exception:
        log.exception("note edit could not list notes")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    if not notes:
        return ChatReply(text=_t(NOTE_EMPTY, language).format(code=code))
    latest = sorted(notes, key=lambda n: str(n.get("created_at") or ""))[-1]

    if delete:
        try:
            await client.delete_note(license_id, str(latest.get("id")), actor_id=actor_id)
        except Exception:
            log.exception("note delete failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        return ChatReply(
            text=_t(NOTE_DELETED, language).format(code=code),
            entity_type=entity_type, entity_id=entity_id,
        )

    body = _note_body_after_trigger(message)
    if not body:
        return ChatReply(text=_t(NOTE_EDIT_NEEDS_BODY, language).format(code=code))
    try:
        await client.update_note(license_id, str(latest.get("id")), body, actor_id=actor_id)
    except Exception:
        log.exception("note update failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return ChatReply(
        text=_t(NOTE_UPDATED, language).format(code=code) + f"\n· {body}",
        entity_type=entity_type, entity_id=entity_id,
    )


def _note_body_after_trigger(message: str) -> str:
    """The new text, after the verb, the code, and any "เป็น"/"to".

    "แก้บันทึก C-2026-0001 เป็น ลูกค้าขอส่วนลด" — everything before the
    new text is scaffolding the person had to type to be understood, and
    none of it belongs in the note.
    """
    text = (message or "").strip()
    for trigger in (*NOTE_EDIT_TRIGGERS, *NOTE_DELETE_TRIGGERS):
        idx = text.lower().find(trigger)
        if idx >= 0:
            text = text[idx + len(trigger):]
            break
    text = re.sub(r"(?<![A-Za-z0-9])[CDQT]-\d{4}-\d{4}(?![0-9])", "", text, flags=re.I).strip()
    for lead in ("เป็น", "ว่า", "to", ":"):
        if text.startswith(lead):
            text = text[len(lead):].strip()
            break
    return text.strip(" -–—")


async def _record_scope(
    client: DataClient, ctx: ResolvedContext, license_id: str, message: str,
    permission_keys: list[str],
) -> tuple[str, str, str] | None:
    """Which single record a list request is about, if any.

    The 21:48 screenshots: replying to สมบัติ's card with "ดูนัดหมายของ
    ลูกค้า" answered with the whole shop's diary — nine records, one of
    them from 1963 — because every list handler read only its trigger and
    threw the rest of the sentence, and the context, away.

    Order matters and is not the create-handler's order: an explicit code
    wins, then a NAME IN THE SENTENCE, then the record in context — name
    before context because "ดูนัดหมายของสมบัติ" typed while จิตวิทยา's
    card is open is about สมบัติ, and context would answer about the wrong
    person while looking perfectly confident.

    "ทั้งหมด" anywhere opts out: the whole point of scoping by default is
    that the person can always widen with one word, but can never tell a
    wrongly-widened list apart from a complete one.
    """
    if "ทั้งหมด" in (message or ""):
        return None
    if _find_entity_code(message) is not None:
        try:
            return await _resolve_target_or_context(client, ctx, license_id, message)
        except _TargetNotFound:
            raise
    named = await _customer_named_in(client, license_id, message, permission_keys)
    if named is not None:
        return named
    try:
        return await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound:
        return None


async def _handle_note_list(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """A record's notes — resolved the same three ways every list now is.

    This handler demanded a typed code and nothing else: "ดูบันทึก" with
    the customer's card open, or "ดูบันทึกของสมบัติ", both answered
    "ระบุรหัสด้วย" about a record already on screen — the exact
    form-shaped failure the 21:48 screenshots showed for appointments.
    """
    if "note.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        scope = await _record_scope(client, ctx, license_id, message, permission_keys)
    except _AmbiguousName as exc:
        return _name_choice(message, exc, language)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )
    if scope is None:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))
    entity_type, entity_id, code = scope

    try:
        notes = await client.list_notes(license_id, entity_type, str(entity_id))
    except Exception:
        log.exception("note list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _remember_entity(
        client, ctx, entity_type=entity_type, entity_id=str(entity_id), code=code,
    )
    if not notes:
        return ChatReply(
            text=_t(NOTE_EMPTY, language).format(code=code),
            entity_type=entity_type, entity_id=entity_id,
        )

    lines = [f"บันทึกของ {code}:"]
    for note in notes[:LIST_LIMIT]:
        stamp = _iso_to_thai_date(note.get("created_at"))
        lines.append(f"· {stamp} {note.get('body') or ''}")
    return ChatReply(
        text="\n".join(lines),
        entity_type=entity_type, entity_id=entity_id,
    )


# Words that only tell us WHEN, not WHAT. Stripped so a reminder's subject
# reads as the thing to do rather than repeating the date the reminder
# already carries in its own field.
_WHEN_ONLY_WORDS = (
    "วันนี้", "พรุ่งนี้", "มะรืนนี้", "มะรืน", "สัปดาห์หน้า", "อาทิตย์หน้า",
    "เดือนหน้า", "ตอน", "เวลา", "วันที่", "หน้า", "น.",
    # "วัน" last, after the weekday names have been removed: stripping it
    # first would turn "วันศุกร์" into "ศุกร์" and leave the leftover "วัน"
    # behind as a subject, which is what happened before this line existed.
    "วัน",
)


def _reminder_subject(message: str, code: str) -> str:
    """What the reminder is ABOUT, from the message that set it.

    Best-effort and deliberately conservative: strips the trigger word, the
    record code, and the date/time expression, and returns whatever is left
    only if something meaningful remains. An empty result is fine — the
    caller falls back to naming the record — but a reminder that can quote
    the person's own words is far more use than one that cannot.
    """
    from .thai_datetime import _THAI_MONTHS, _THAI_WEEKDAYS

    text = ENTITY_CODE_RE.sub("", message or "")
    for trigger in REMINDER_TRIGGERS + QUOTE_REISSUE_PHRASES:
        text = text.replace(trigger, " ")
    # Digits and separators belong to the date/time, which is stored
    # structurally; keeping them here would duplicate it in the text.
    text = re.sub(r"\d{1,4}[:./-]?\d{0,2}", " ", text)
    for word in (
        # Weekdays and months FIRST: "วัน" is in _WHEN_ONLY_WORDS and would
        # otherwise split "วันศุกร์" before "ศุกร์" itself is removed.
        tuple(_THAI_WEEKDAYS)
        + tuple(_THAI_MONTHS)
        + ("โมงเช้า", "โมง", "ทุ่ม", "เช้า", "สาย", "เที่ยง", "บ่าย", "เย็น", "ค่ำ")
        + _WHEN_ONLY_WORDS
    ):
        text = text.replace(word, " ")
    subject = " ".join(text.split()).strip(" ·-:")
    # One stray character is noise, not a subject.
    return subject if len(subject) >= 3 else ""


# Seeing what is coming up. Reminders could be created and never read —
# a reminder nobody can look at is a reminder that only exists when it
# fires, which is not what "ดูนัดหมาย" means.
#
# Listed BEFORE the create triggers, because "ดูนัดหมาย" contains "นัด"
# and the shorter phrase was swallowing it: asking to see the diary
# opened a form for adding to it.
REMINDER_LIST_TRIGGERS = (
    "ดูนัดหมาย", "นัดหมาย", "รายการนัด", "ดูการเตือน", "รายการเตือน",
    "นัดวันนี้", "งานที่ต้องทำ", "my reminders",
)

REMINDER_LIST_EMPTY = {
    "th": "ยังไม่มีนัดหมายที่จะถึง",
    "en": "Nothing scheduled.",
}
REMINDER_LIST_HEAD_FOR = {
    "th": "นัดหมายของ {code} — {count} รายการ",
    "en": "{count} appointment(s) for {code}",
}
REMINDER_LIST_EMPTY_FOR = {
    "th": "ยังไม่มีนัดหมายของ {code}",
    "en": "No appointments for {code} yet.",
}
REMINDER_LIST_HEAD = {
    "th": "นัดหมายที่จะถึง {count} รายการ",
    "en": "{count} upcoming",
}


async def _handle_reminder_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str, message: str = "",
) -> ChatReply:
    """What is coming up, soonest first — for one record when one is meant."""
    if "followup.read" not in set(permission_keys):
        if "ticket.read" in set(permission_keys):
            # CS: their appointments are the scheduled visits.
            return await _handle_work_list(
                client, license_id=license_id, permission_keys=permission_keys, language=language, days=7,
            )
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    try:
        scope = await _record_scope(
            client, ctx, str(license_id), message, permission_keys,
        )
    except _AmbiguousName as exc:
        return _name_choice(message, exc, language)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )

    try:
        rows = await client.list_follow_ups(str(license_id), status="pending")
    except Exception:
        log.exception("could not list follow-ups")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    # An appointment has no status (owner, 4 Sep): the diary answers "what
    # is still ahead"; anything already past stays on record and is not
    # listed here.
    rows = [r for r in rows if _is_ahead(r)]

    if scope is not None:
        entity_type, entity_id, code = scope
        shown = sorted(
            (r for r in rows if str(r.get("entity_id")) == str(entity_id)),
            key=lambda r: str(r.get("due_date") or "9999-12-31"),
        )
        await _remember_entity(
            client, ctx, entity_type=entity_type, entity_id=str(entity_id), code=code,
        )
        if not shown:
            return ChatReply(
                text=_t(REMINDER_LIST_EMPTY_FOR, language).format(code=code),
                entity_type=entity_type, entity_id=entity_id,
                quick_replies=[
                    ("เพิ่มนัด", f"เตือน {code} พรุ่งนี้"),
                    ("ดูทั้งหมด", "นัดหมายทั้งหมด"),
                ],
            )
        lines = []
        for row in shown[:LIST_LIMIT]:
            lines.append(await _reminder_list_line(client, str(license_id), row, language))
        return ChatReply(
            text=_t(REMINDER_LIST_HEAD_FOR, language).format(code=code, count=len(shown))
            + "\n" + "\n".join(lines),
            entity_type=entity_type, entity_id=entity_id,
            quick_replies=[
                ("เพิ่มนัด", f"เตือน {code} พรุ่งนี้"),
                ("ดูทั้งหมด", "นัดหมายทั้งหมด"),
            ],
        )

    # Mine first, then everyone's — a salesperson opening this wants their
    # own day, not the shop's whole diary.
    mine = [r for r in rows if str(r.get("owner_chann_uid") or "") == ctx.chann_uid]
    shown = (mine or rows)
    shown = sorted(shown, key=lambda r: str(r.get("due_date") or "9999-12-31"))

    if not shown:
        return ChatReply(text=_t(REMINDER_LIST_EMPTY, language))

    lines = []
    for row in shown[:LIST_LIMIT]:
        lines.append(await _reminder_list_line(client, str(license_id), row, language))

    return ChatReply(
        text=_t(REMINDER_LIST_HEAD, language).format(count=len(shown))
        + "\n" + "\n".join(lines)
    )


def _is_ahead(row: dict) -> bool:
    raw = str(row.get("due_date") or "")
    try:
        return date.fromisoformat(raw) >= local_today()
    except ValueError:
        return True


async def _reminder_list_line(
    client: DataClient, license_id: str, row: dict, language: str,
) -> str:
    """One diary row: real date (BE), who, and what — no status.

    With the digest silent about overdue work (owner decision — see
    reminders.py), this list is the ONE place a slipped or misfiled row
    can be seen, and ยกเลิกเตือน needs the code printed here to act on.
    Shared by the whole-diary view and the one-record view so the two can
    never drift apart in format.
    """
    from .thai_datetime import format_thai_date as _fmt_date

    today = local_today()
    raw = str(row.get("due_date") or "")
    try:
        due_on = date.fromisoformat(raw)
        when = _fmt_date(due_on) if language == "th" else raw
    except ValueError:
        when = raw
    if row.get("due_time"):
        when = f"{when} {str(row['due_time'])[:5]}"
    who = await _describe_entity_by_id(
        client, license_id, str(row.get("entity_type") or ""),
        str(row.get("entity_id") or ""),
    )
    what = str(row.get("notes") or "").strip()
    return f"· {when} · {who}{f' — {what}' if what else ''}"


def _mentions_a_datetime(message: str) -> bool:
    """Whether the sentence carries a readable date or time at all.

    Used to tell "นัดหมาย" (show me the diary) from "นัดหมายพรุ่งนี้"
    (make one), and as the entry condition for the appointment net: a
    sentence with a concrete day in it is somebody arranging something.
    """
    from .thai_datetime import parse_thai_date, parse_thai_time

    today = local_today()
    return parse_thai_date(message, today) is not None or parse_thai_time(message) is not None


def _reads_as_a_date(message: str) -> bool:
    """A DAY, not merely a day or an hour.

    _mentions_a_datetime is true for a bare "บ่าย 2", which dropped the
    "which day?" question from the model's report and then met a handler
    that cannot read a date out of it — the ask disappeared and the flow
    stalled. Answering the day is what closes that question.
    """
    from .thai_datetime import parse_thai_date

    return parse_thai_date(message or "", local_today()) is not None


def _is_reminder_command(message: str) -> bool:
    """Is this message a reminder instruction, rather than a sentence
    that happens to contain the word?"""
    lowered = (message or "").strip().lower()
    if not lowered:
        return False
    # Starts with the verb: "เตือน ...", "นัด ...", "remind ...".
    if any(lowered.startswith(t) for t in REMINDER_TRIGGERS):
        return True
    # Or names a record and mentions the verb anywhere: "D-2026-0001 เตือนพรุ่งนี้".
    has_code = re.search(r"(?<![A-Za-z0-9])[CDQT]-\d{4}-\d{4}(?![0-9])", message or "", re.I) is not None
    return has_code and any(t in lowered for t in REMINDER_TRIGGERS)


def _is_typed_reminder_command(message: str) -> bool:
    """The reminder shapes that answer themselves.

    Two, and only two: a record code with the verb — which is what this
    system's own buttons carry, and the guard's own example "ตั้งนัด
    C-2026-0001 พรุ่งนี้ 14:00" — or a sentence carrying a day the parser
    can read, which is the one-shot command _handle_reminder_create can
    actually satisfy.

    Everything else this matcher used to claim dead-ended: "ตั้งนัดสมชาย"
    resolved the person, found no date, answered REMINDER_NEEDS_DATE and
    set NO pending intent, so the next turn had nothing to continue. That
    is a sentence to be read, so it goes to the model, which asks for the
    day and merges the answer.
    """
    from .thai_datetime import parse_thai_date

    if not _is_reminder_command(message):
        return False
    if re.search(r"(?<![A-Za-z0-9])[CDQT]-\d{4}-\d{4}(?![0-9])", message or "", re.I):
        return True
    return parse_thai_date(message or "", local_today()) is not None


def _is_reminder_cancel_command(message: str) -> bool:
    """A cancel instruction, told apart from setting a reminder.

    Checked before _is_reminder_command on purpose: "ยกเลิกเตือน
    C-2026-0011" starts with a cancel verb but also contains "เตือน" and a
    record code, which is exactly what the create matcher claims.
    """
    lowered = _canonical(message)
    if not lowered:
        return False
    if any(lowered.startswith(t) for t in REMINDER_CANCEL_TRIGGERS + _REMINDER_CANCEL_HEADS):
        return True
    has_code = re.search(r"(?<![A-Za-z0-9])[CDQT]-\d{4}-\d{4}(?![0-9])", message or "", re.I) is not None
    return has_code and any(t in lowered for t in REMINDER_CANCEL_TRIGGERS + _REMINDER_CANCEL_HEADS)


# "ไม่ต้องเตือนเรื่องสมชายแล้ว": a cancel said as a negation.
_REMINDER_CANCEL_HEADS = ("ไม่ต้องเตือน", "ไม่ต้องนัด", "ไม่เตือนแล้ว", "เลิกเตือน", "stop reminding", "no reminder")


def _is_reminder_move_command(message: str) -> bool:
    """Moving an existing appointment, told apart from making a new one.

    Checked before both cancel and create: "เปลี่ยนเวลาเป็น 13.00" carries
    no code, no verb they match, and a bare time — and on 2 Sep it fell all
    the way through to "คุณยังไม่มีสิทธิ์ทำสิ่งนี้" for a person who had
    every permission involved.
    """
    lowered = (message or "").strip().lower()
    return bool(lowered) and any(t in lowered for t in REMINDER_MOVE_TRIGGERS)


async def _handle_reminder_move(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, actor_id: str,
) -> ChatReply:
    """Move the pending appointment on a record to a new day and/or time.

    Reported live (12:08, 2 Sep): the owner replied to a reminder message
    with "เปลี่ยนเวลาเป็น 13.00" and got a capability list. Nothing in the
    system could change an appointment's time at all — the chat had no
    handler, the dashboard had no control, and the AI's ("update",
    "followup") intent had a permission key registered but nowhere to go.

    First shipped as cancel-then-create, because the Data Tier could only
    set a follow-up's status or create a new one. That gave the right
    answer and the wrong record: every postponement minted a fresh id, so
    the reminder already pushed to LINE pointed at a cancelled row and the
    appointment's audit trail restarted from empty. Since the owner's
    9 Sep report ("นัดหมายเหมือนจะแก้ไข หรือลบไม่ได้") there is a real
    PATCH, and this edits in place: same id, same history, one audit entry
    that names the old day and the new one.

    The reply is unchanged — only what was given changes, and a bare time
    keeps the original date.
    """
    keys = set(permission_keys)
    if "followup.update" not in keys:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )
    if target is None:
        try:
            target = await _customer_named_in(client, license_id, message, permission_keys)
        except _AmbiguousName as exc:
            return _name_choice(message, exc, language)
    if target is None:
        return ChatReply(text=_t(REMINDER_CANCEL_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

    try:
        rows = await client.list_follow_ups(license_id, status="pending")
    except Exception:
        log.exception("reminder move could not list follow-ups")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    mine = [r for r in rows if str(r.get("entity_id")) == str(entity_id)]
    if not mine:
        return ChatReply(text=_t(REMINDER_MOVE_NONE, language).format(code=code))
    # The soonest one: "เลื่อนนัด" with two pending means the next one.
    mine.sort(key=lambda r: (str(r.get("due_date") or ""), str(r.get("due_time") or "")))
    row = mine[0]

    from .thai_datetime import (
        format_thai_date, format_thai_time, looks_like_a_time_attempt,
        parse_thai_date, parse_thai_time,
    )

    today = local_today()
    new_date = parse_thai_date(message, today)
    new_time = parse_thai_time(message)
    if new_date is None and new_time is None:
        return ChatReply(text=_t(REMINDER_MOVE_NEEDS_WHEN, language).format(code=code))
    if new_date is None:
        # Only a time was given — keep the day it was already on.
        try:
            new_date = date.fromisoformat(str(row.get("due_date") or ""))
        except ValueError:
            new_date = today
    if new_date < today:
        return ChatReply(
            text=_t(REMINDER_DATE_PAST, language).format(
                date=format_thai_date(new_date), code=code,
            ),
        )
    if new_time is None:
        if looks_like_a_time_attempt(message):
            # A time was stated and could not be read. Keeping the old one
            # is as silent as inventing 09:00 would be.
            return ChatReply(text=_t(TIME_NOT_UNDERSTOOD, language))
        try:
            new_time = time.fromisoformat(str(row.get("due_time") or "09:00:00"))
        except ValueError:
            new_time = time(9, 0)

    # Only the day and the time — the note, the owner and the record it
    # hangs off all stay exactly as they were. Sending the note back would
    # be harmless today and wrong the moment someone edits it elsewhere.
    try:
        await client.update_follow_up(
            license_id,
            str(row.get("id")),
            {
                "due_date": new_date.isoformat(),
                "due_time": new_time.isoformat(),
            },
            actor_id=actor_id,
        )
    except Exception:
        log.exception("reminder move failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _remember_entity(
        client, ctx, entity_type=entity_type, entity_id=str(entity_id), code=code,
    )
    return ChatReply(
        text=_t(REMINDER_MOVED, language).format(
            code=code, date=format_thai_date(new_date), time=format_thai_time(new_time),
        ),
        entity_type=entity_type, entity_id=entity_id,
        quick_replies=[("รายการเตือน", "รายการเตือน")],
    )


async def _handle_reminder_cancel(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, actor_id: str,
) -> ChatReply:
    """Cancel every pending follow-up on one record.

    This is also the only self-service way to clear a reminder that was
    stored on the wrong day: the row the ISO-parse bug filed under
    26 ก.ย. 2506 would otherwise sit in every morning digest forever,
    because nothing in chat or the dashboard could touch a follow-up's
    status even though the data tier has carried the endpoint since
    Phase 6.

    All pending rows for the record, not "the nearest one": which of two
    reminders a person means is a guess, and the reply states the count so
    an over-broad cancel is visible immediately.
    """
    if "followup.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
        )
    if target is None:
        return ChatReply(
            text=_t(REMINDER_CANCEL_NEEDS_TARGET, language),
            quick_replies=[("รายการเตือน", "รายการเตือน"), ("นัดหมายวันนี้", "นัดหมายวันนี้")],
        )
    entity_type, entity_id, code = target

    # Remembered the moment the target resolves, not only on success: the
    # live 2 Sep flow was "ยกเลิกเตือน C-2026-0011" then "ตั้งนัดวันที่ 6
    # ที่จะถึง", and the second sentence only means anything if the first
    # left the conversation on that record — which is just as true when
    # the cancel finds nothing to cancel.
    await _remember_entity(
        client, ctx, entity_type=entity_type, entity_id=str(entity_id), code=code,
    )

    try:
        rows = await client.list_follow_ups(license_id, status="pending")
    except Exception:
        log.exception("reminder cancel could not list follow-ups")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    mine = [r for r in rows if str(r.get("entity_id")) == str(entity_id)]
    if not mine:
        return ChatReply(text=_t(REMINDER_CANCEL_NONE, language).format(code=code))

    cancelled = 0
    for row in mine:
        try:
            await client.set_follow_up_status(
                license_id, str(row.get("id")), "cancelled", actor_id=actor_id,
            )
            cancelled += 1
        except Exception:
            log.exception("could not cancel follow-up %s", row.get("id"))
    if cancelled == 0:
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(REMINDER_CANCELLED, language).format(code=code, count=cancelled),
        entity_type=entity_type, entity_id=entity_id,
        quick_replies=[
            ("ตั้งเตือนใหม่", f"เตือน {code} พรุ่งนี้"),
            ("รายการเตือน", "รายการเตือน"),
        ],
    )


async def _handle_reminder_delete(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, actor_id: str,
) -> ChatReply:
    """Remove every pending appointment on one record outright.

    Deliberately NOT what "ยกเลิกนัด" does. Cancelling leaves the row, so
    the history still shows that something was planned and called off, and
    that is what almost everyone means. This is the other case — an
    appointment booked against the wrong record, or by mistake, that the
    person wants gone. Only the model's explicit delete intent reaches
    here; every deterministic trigger still cancels.

    Same target resolution and same all-pending-rows rule as the cancel
    handler, and the reply states the count for the same reason: an
    over-broad delete has to be visible immediately, and here it cannot be
    undone.
    """
    if "followup.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(
                what=_entity_noun(exc.entity_type, language), code=exc.code
            )
        )
    if target is None:
        return ChatReply(
            text=_t(REMINDER_CANCEL_NEEDS_TARGET, language),
            quick_replies=[("รายการเตือน", "รายการเตือน"), ("นัดหมายวันนี้", "นัดหมายวันนี้")],
        )
    entity_type, entity_id, code = target

    # Remembered as soon as the target resolves, same as cancelling: the
    # next sentence is usually about the same record whether or not there
    # was anything here to remove.
    await _remember_entity(
        client, ctx, entity_type=entity_type, entity_id=str(entity_id), code=code,
    )

    try:
        rows = await client.list_follow_ups(license_id, status="pending")
    except Exception:
        log.exception("reminder delete could not list follow-ups")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    mine = [r for r in rows if str(r.get("entity_id")) == str(entity_id)]
    if not mine:
        return ChatReply(text=_t(REMINDER_DELETE_NONE, language).format(code=code))

    removed = 0
    for row in mine:
        try:
            await client.delete_follow_up(
                license_id, str(row.get("id")), actor_id=actor_id,
            )
            removed += 1
        except Exception:
            log.exception("could not delete follow-up %s", row.get("id"))
    if removed == 0:
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(REMINDER_DELETED, language).format(code=code, count=removed),
        entity_type=entity_type, entity_id=entity_id,
        quick_replies=[
            ("ตั้งเตือนใหม่", f"เตือน {code} พรุ่งนี้"),
            ("รายการเตือน", "รายการเตือน"),
        ],
    )


async def _appointment_net(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
):
    """Last understanding before giving up: a dated sentence about a known
    record is somebody arranging something.

    Reported live (2 Sep): "ตั้งนัดวันที่ 6 ที่จะถึง", typed right after
    working on a customer, got the capability list — the assistant had the
    date, the context, and the permission, and answered with a menu. This
    net runs only where the reply would otherwise BE that menu, so a wrong
    guess here replaces "I did not understand" with a dated, echoed,
    one-message-to-cancel reminder — strictly more useful, never louder.

    Returns None (fall through to the suggestions) unless the message
    carries a date/time, the OA is a staff one, the person may create
    follow-ups, and a target record resolves from the text or context.
    """
    # Both halves of the boundary, not one. Holding the key is not the same
    # as being allowed to use it HERE: a technician holding followup.create
    # booked a real reminder from this net on the technician OA, where
    # _oa_allows says the action does not exist (10 ก.ย. 2569). Every other
    # write in this file asks _oa_allows; this one asked only the key.
    if not _oa_allows(ctx.oa, "followup.create"):
        return None
    if "followup.create" not in set(permission_keys):
        return None
    if not _mentions_a_datetime(message):
        return None
    try:
        target = await _resolve_target_or_context(client, ctx, str(license_id), message)
    except _TargetNotFound:
        return None
    if target is None:
        return None
    # The net catches sentences the model could not read at all, so it is
    # the one place an appointment can still be created without any
    # trigger matching: "ยังไม่ต้องนัดวันศุกร์" carries a date and a
    # record and must not become one.
    if _intent_guard_reply(message, action="appointment_create", language=language) is not None:
        return None
    return await _handle_reminder_create(
        client, ctx=ctx, license_id=license_id, message=message,
        permission_keys=permission_keys, language=language,
        actor_id=ctx.chann_uid,
    )


class _AmbiguousName(Exception):
    """Two people fit the name the sentence used.

    Raised instead of returning None because silence here was a live bug:
    with two สมชาย, "ดูนัดหมายของสมชาย" fell through to the record in
    context — the wrong person, answered confidently — or to "ระบุรหัส".
    The owner's requirement (2 Sep) is explicit: a duplicate name must
    become a CHOICE.
    """

    def __init__(self, matches: list[dict], fragments: dict[str, str]):
        self.matches = matches
        # customer id -> the exact substring of the sentence that matched
        # them, so the picker can rewrite the sentence per candidate.
        self.fragments = fragments


def _name_choice(message: str, exc: _AmbiguousName, language: str) -> ChatReply:
    """The picker: same sentence, each button swaps the name for a code.

    Tapping re-sends the person's own command with the ambiguity removed
    — no pending state, no special reply parser, and it works identically
    for every command shape present and future, because the command is
    simply run again the way the person would have typed it had they
    known the code.
    """
    lead = _t(CUSTOMER_AMBIGUOUS_LEAD, language).format(
        name=next(iter(exc.fragments.values()), ""),
    )
    lines = [
        f"· {c.get('customer_id')} {_display_name(c)}"
        for c in exc.matches[:LIST_LIMIT]
    ]
    buttons = []
    for c in exc.matches[:4]:
        code = str(c.get("customer_id") or "")
        frag = exc.fragments.get(str(c.get("id")), "")
        # Padded with spaces: Thai letters count as word characters, so a
        # code glued to "ของ" is invisible to ENTITY_CODE_RE's \b — the
        # simulator caught the button "ดูนัดหมายของC-2026-0002" resolving
        # to nothing at all.
        if frag and frag in message:
            rewritten = " ".join(message.replace(frag, f" {code} ", 1).split())
        else:
            rewritten = f"{message} {code}"
        buttons.append((_display_name(c)[:20], rewritten))
    return ChatReply(text=lead + "\n" + "\n".join(lines), quick_replies=buttons)


async def _customer_named_in(
    client: DataClient, license_id: str, message: str, permission_keys: list[str],
) -> tuple[str, str, str] | None:
    """The customer whose name appears in what the person wrote.

    _find_one_customer_by_name asks "is this name inside that record"; this
    asks the reverse — "is one of my records named inside this sentence" —
    which is the shape a real message has: "นัดคุณสมบัติดูสินค้าวันที่ 7"
    names a customer without a code, without a field, and without the word
    order any lookup helper expects.

    One match resolves; several raise _AmbiguousName so the caller can
    offer the choice; none returns None.
    """
    if "customer.read" not in set(permission_keys):
        return None
    text = (message or "").lower()
    try:
        rows = await client.list_customers(license_id)
    except Exception:
        log.exception("could not list customers to resolve a target")
        return None
    matches: list[dict] = []
    fragments: dict[str, str] = {}
    for row in rows:
        first = str(row.get("first_name") or "").strip()
        last = str(row.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
        # Full name first, then the given name alone — "คุณสมบัติ" is how
        # people write it, with the honorific glued on and no surname.
        if full and full.lower() in text:
            matches.append(row)
            fragments[str(row["id"])] = full
        elif len(first) >= 2 and first.lower() in text:
            matches.append(row)
            fragments[str(row["id"])] = first
    if not matches:
        return None
    if len(matches) > 1:
        raise _AmbiguousName(matches, fragments)
    row = matches[0]
    return "customer", str(row["id"]), str(row.get("customer_id") or "")


async def _handle_reminder_create(  # noqa: PLR0913
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, actor_id: str,
    target: tuple[str, str, str] | None = None,
) -> ChatReply:
    from .thai_datetime import (
        format_thai_date, format_thai_time, looks_like_a_time_attempt,
        parse_thai_date, parse_thai_time,
    )

    if "followup.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    if target is None:
        # No caller-resolved target: fall back to a code in the text, then
        # to the record we were just looking at.
        try:
            target = await _resolve_target_or_context(client, ctx, license_id, message)
        except _TargetNotFound as exc:
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun(exc.entity_type, language), code=exc.code)
            )
    if target is None:
        # Before giving up: a name in the sentence is a target too.
        try:
            target = await _customer_named_in(client, license_id, message, permission_keys)
        except _AmbiguousName as exc:
            # Two people fit — booking against either would be a guess
            # written into the diary. The choice keeps the whole command.
            return _name_choice(message, exc, language)
    if target is None:
        return ChatReply(text=_t(REMINDER_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

    # Parse against the tenant's own day, not UTC: at 23:00 in Bangkok, UTC
    # is still yesterday, and "พรุ่งนี้" would land on today.
    today = local_today()
    due_date = parse_thai_date(message, today)
    if due_date is None:
        return ChatReply(text=_t(REMINDER_NEEDS_DATE, language))
    if due_date < today:
        # A reminder about the past cannot ring. Storing it anyway is how a
        # misread date ended up in every morning digest with nothing able to
        # remove it — refuse, echo what was read (the echo is what catches a
        # misparse), and offer a way forward.
        return ChatReply(
            text=_t(REMINDER_DATE_PAST, language).format(
                date=format_thai_date(due_date), code=code,
            ),
            quick_replies=[("พรุ่งนี้", f"เตือน {code} พรุ่งนี้")],
        )
    due_time = parse_thai_time(message)
    if due_time is None and looks_like_a_time_attempt(message):
        # A time WAS given and could not be read — "บ่าย 9" is not an hour.
        # Falling through to the default below would put the reminder at
        # 09:00 without a word about it (review v3, B06).
        return ChatReply(text=_t(TIME_NOT_UNDERSTOOD, language))
    if due_time is None:
        # Owner request (2 Sep): an appointment nobody gave a time for
        # still gets one — 09:00, start of the working day — instead of a
        # bare "–" in the digest. The confirmation echoes it, so a wrong
        # guess is visible and one message away from being corrected.
        due_time = time(9, 0)

    try:
        payload = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "due_date": due_date.isoformat(),
        }
        if due_time is not None:
            payload["due_time"] = due_time.isoformat()
        # Keep what the person actually asked to be reminded about. Without
        # this, "นัดดูสินค้าวันนี้ตอน 3 โมง" was reduced to a date and a
        # time, and the reminder that arrived days later could only say
        # "customer" — true, and useless.
        subject = _reminder_subject(message, code)
        if subject:
            payload["notes"] = subject
        await client.create_follow_up(license_id, payload, actor_id=actor_id)
    except Exception:
        log.exception("reminder create failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    # The resolved date and time are echoed back deliberately: the parse is
    # a best reading of free text, and showing what it decided is how the
    # person catches a misread before it matters. The code is echoed too,
    # for exactly the same reason when the target came from context rather
    # than being typed.
    await _remember_entity(
        client, ctx, entity_type=entity_type, entity_id=str(entity_id), code=code,
    )
    time_text = f" {format_thai_time(due_time)}" if due_time else ""
    return ChatReply(
        text=_t(REMINDER_SAVED, language).format(
            code=code, date=format_thai_date(due_date), time=time_text,
        ),
        entity_type=entity_type, entity_id=entity_id,
        quick_replies=[("งานวันนี้", "งานวันนี้")],
    )


async def _describe_entity_by_id(
    client: DataClient, license_id: str, entity_type: str, entity_id: str,
) -> str:
    """A human-readable name for a record, given its type and id.

    _resolve_entity looks records up BY CODE because that is what a typed
    command gives it; a follow-up row only carries entity_id (a UUID), so
    this is the id-keyed counterpart. Falls back to the bare code (or the
    type, as a last resort) rather than raising: a work list is a summary
    view, and one unresolvable row should not blank out the whole list.
    """
    try:
        if entity_type == "customer":
            rows = await client.list_customers(license_id)
            row = next((r for r in rows if str(r.get("id")) == str(entity_id)), None)
            if row:
                return f"{_customer_name(row)} ({row.get('customer_id')})"
        elif entity_type == "deal":
            rows = await client.list_deals(license_id)
            row = next((r for r in rows if str(r.get("id")) == str(entity_id)), None)
            if row:
                return str(row.get("deal_id") or entity_type)
        elif entity_type == "quote":
            rows = await client.list_quotes(license_id)
            row = next((r for r in rows if str(r.get("id")) == str(entity_id)), None)
            if row:
                return str(row.get("quote_id") or entity_type)
    except Exception:
        log.exception("could not describe entity %s/%s for a work list", entity_type, entity_id)
    return entity_type


async def _handle_work_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
    days: int,
) -> ChatReply:
    from .thai_datetime import format_thai_date, format_thai_time

    held = set(permission_keys)
    if not held & WORK_VIEW_KEYS:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    # Scheduled visits are part of the day too — and for CS (ticket.read,
    # no followup.read) they are the whole of it (review, 6 Sep 2026: the
    # highlighted "งานวันนี้" tile refused CS).
    visit_lines = await _ticket_schedule_lines(client, str(license_id), days, language) if "ticket.read" in held else []
    due = []
    if "followup.read" in held:
        try:
            due = await client.due_follow_ups(str(license_id), days=days)
        except Exception:
            log.exception("due follow-ups failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not due and not visit_lines:
        return ChatReply(
            text=_t(WORK_EMPTY, language),
            quick_replies=[("รายการดีล", "รายการดีล")] if "deal.read" in held else [("รายการงาน", "รายการงาน")],
        )
    if not due:
        return ChatReply(text="\n".join([_t(WORK_HEADING, language)] + visit_lines))

    # Same owner policy as the morning digest (see reminders.py): "งานวันนี้"
    # names only work that has not passed. due_follow_ups includes overdue
    # rows on purpose so they are never lost — they stay visible in
    # รายการเตือน, flagged เลยกำหนด, which is where cancelling lives too.
    today_local = local_today()

    def _not_past(item) -> bool:
        try:
            return date.fromisoformat(str(item.get("due_date") or "")) >= today_local
        except ValueError:
            # An unreadable date is a data problem, not a reason to hide
            # the row from the one list that could surface it.
            return True

    due = [item for item in due if _not_past(item)]
    if not due:
        return ChatReply(text=_t(WORK_EMPTY, language), quick_replies=[("รายการดีล", "รายการดีล")])

    lines = [_t(WORK_HEADING, language)]
    for item in due[:LIST_LIMIT]:
        raw_date = str(item.get("due_date") or "")
        try:
            shown = format_thai_date(date.fromisoformat(raw_date))
        except ValueError:
            shown = raw_date
        raw_time = item.get("due_time")
        clock = ""
        if raw_time:
            try:
                clock = " " + format_thai_time(time.fromisoformat(str(raw_time)))
            except ValueError:
                clock = f" {raw_time}"
        # A name, not the bare word "customer"/"deal" repeated on every row —
        # reported live as unreadable, since every row looked identical.
        who = await _describe_entity_by_id(
            client, str(license_id), str(item.get("entity_type") or ""),
            str(item.get("entity_id") or ""),
        )
        note = item.get("notes")
        lines.append(f"· {shown}{clock} · {who}" + (f" · {note}" if note else ""))
    return ChatReply(text="\n".join(lines + visit_lines))


async def _ticket_schedule_lines(client: DataClient, license_id: str, days: int, language: str) -> list[str]:
    """The visits scheduled from today for `days` days, one line each."""
    from .thai_datetime import format_thai_date, format_thai_time

    try:
        tickets = await client.list_tickets(license_id)
    except Exception:
        log.exception("could not list tickets for the day's work")
        return []
    today = local_today()
    end = today + timedelta(days=max(days, 1) - 1)
    rows = []
    for t in tickets:
        if str(t.get("status") or "") in ("completed", "cancelled") or not t.get("scheduled_date"):
            continue
        try:
            when = date.fromisoformat(str(t["scheduled_date"])[:10])
        except ValueError:
            continue
        if today <= when <= end:
            rows.append((when, str(t.get("scheduled_time") or ""), t))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines = []
    for when, clock, t in rows[:LIST_LIMIT]:
        shown_clock = ""
        if clock:
            try:
                shown_clock = " " + format_thai_time(time.fromisoformat(clock))
            except ValueError:
                shown_clock = f" {clock}"
        who = str(t.get("customer_name") or "").strip()
        issue = str(t.get("issue_description") or "").strip()[:40]
        lines.append(
            f"· {format_thai_date(when)}{shown_clock} · {t.get('ticket_number') or ''}"
            + (f" {who}" if who else "") + (f" — {issue}" if issue else "")
        )
    return lines


# ------------------------------- Phase 7.5 / 16 serial-first customer flow
#
# What a customer actually has when something breaks is the sticker on the
# machine. They do not know a product code, they may not remember which
# branch they bought from, and asking them to look either up before they
# can report a fault is the friction this phase removes.
#
# A serial identifies the product, the shop AND the entitlement at once.

SERIAL_REGISTER_TRIGGERS = ("ลงทะเบียนสินค้า", "ลงทะเบียนรับประกัน", "register product")
SERIAL_LOOKUP_TRIGGERS = ("ค้นหาซีเรียล", "เช็คประกัน", "ตรวจสอบประกัน", "check warranty")
# "ลงทะเบียน SN12345678", "register SN…", "S/N 12345678", "ซีเรียล SN…":
# registering, not a street (review, 6 Sep 2026, B3).
SERIAL_REGISTER_HEAD_TRIGGERS = (
    "ลงทะเบียน", "ลงทะเบียนเครื่อง", "ขอลงทะเบียน", "register", "ซีเรียล", "serial", "s/n", "sn ", "s/n:",
    "หมายเลขเครื่อง", "เลขเครื่อง", "รหัสเครื่อง", "บันทึกเครื่อง", "บันทึกเครื่องที่ขาย", "บันทึกซีเรียล", "เครื่องที่ขาย sn",
    "ขายเครื่อง", "บันทึกการขายเครื่อง",
)
# A serial number in the message: "SN12345678", "ABC-12345".
_SERIAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(?:SN|S/N)?\s?[A-Z]{0,4}\d{5,}[A-Z0-9-]*(?![A-Za-z0-9])", re.IGNORECASE)


def _names_a_serial(message: str) -> bool:
    text = message or ""
    if TICKET_CODE_RE.search(text) or re.search(r"(?<![A-Za-z0-9])(?:SR|[CDQ])-\d{4}-\d{4}", text, re.I) or _looks_like_phone(text):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])[A-Z]{1,4}\d{5,}(?![A-Za-z0-9])", text)) or bool(re.search(r"\bS/?N\s*[A-Z0-9-]{5,}", text, re.I))


def _is_register_request(message: str) -> bool:
    canon = _canonical(message)
    if not canon:
        return False
    if _normalise(message) in {t.replace(" ", "") for t in SERIAL_REGISTER_HEAD_TRIGGERS}:
        return True
    return any(canon.startswith(h) for h in SERIAL_REGISTER_HEAD_TRIGGERS) or "ลงทะเบียน" in canon

# Serial numbers vary wildly by manufacturer, so this is loose on purpose:
# anything alphanumeric of a plausible length. Being strict here would
# reject real serials and there is nothing to gain — an unknown serial
# simply finds nothing.
SERIAL_RE = re.compile(r"\b([A-Z0-9][A-Z0-9\-]{4,31})\b", re.IGNORECASE)

WARRANTY_REGISTERED = {
    "th": "ลงทะเบียนรับประกันแล้วครับ\n{number} · {product}\nคุ้มครองถึง {end}",
    "en": "Registered.\n{number} · {product}\nCovered until {end}",
}
WARRANTY_NEEDS_SERIAL = {
    "th": "ขอหมายเลขเครื่อง (serial) ที่อยู่บนตัวสินค้าด้วยครับ",
    "en": "What is the serial number on the unit?",
}
WARRANTY_FOUND = {
    "th": "{number} · {product}\nสถานะ: {status}\nคุ้มครองถึง {end}",
    "en": "{number} · {product}\nStatus: {status}\nCovered until {end}",
}
WARRANTY_ALREADY_REGISTERED = {
    "th": "หมายเลข {serial} ลงทะเบียนไว้แล้วครับ พิมพ์ \"เช็คประกัน {serial}\" เพื่อดูรายละเอียด",
    "en": "Serial {serial} is already registered. Type \"check warranty {serial}\" for details.",
}
# The DB enum is for the machine; a customer reads whether they are covered.
WARRANTY_STATUS_LABELS = {
    "active": {"th": "ยังอยู่ในประกัน", "en": "in warranty"},
    "expired": {"th": "หมดประกันแล้ว", "en": "expired"},
    "void": {"th": "ยกเลิกแล้ว", "en": "void"},
}
WARRANTY_MINE_NONE = {
    "th": "ยังไม่มีสินค้าที่ลงทะเบียนรับประกันไว้ครับ พิมพ์ \"ลงทะเบียนรับประกัน <หมายเลขเครื่อง>\" ได้เลย",
    "en": "Nothing registered yet. Type \"register product <serial>\" to add one.",
}
WARRANTY_MINE_HEAD = {"th": "สินค้าที่ลงทะเบียนไว้:", "en": "Your registered products:"}
WARRANTY_MINE_LINE = {
    "th": "· {number} {product} — {status}{end}",
    "en": "· {number} {product} — {status}{end}",
}
WARRANTY_NOT_FOUND_HERE = {
    "th": "ไม่พบการลงทะเบียนของหมายเลข {serial} ที่ร้านนี้",
    "en": "No registration for {serial} at this shop.",
}
SERIAL_SHOPS_FOUND = {
    "th": "หมายเลข {serial} ลงทะเบียนไว้ที่:\n{shops}\n\nพิมพ์รหัสร้านเพื่อติดต่อร้านนั้น",
    "en": "Serial {serial} is registered at:\n{shops}\n\nType a shop code to reach them.",
}
SERIAL_NO_SHOP = {
    "th": "ไม่พบหมายเลข {serial} ในระบบครับ ลองตรวจสอบตัวเลขอีกครั้ง หรือติดต่อร้านที่ซื้อโดยตรง",
    "en": "Serial {serial} is not registered anywhere. Check the number, or contact the shop you bought from.",
}


async def _handle_warranty_register(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    language: str, permission_keys: list[str] | None = None,
) -> ChatReply:
    """Registering a unit.

    Two different acts behind one phrase (owner rule, 3 Sep): on the
    Customer OA the person is CLAIMING a serial the shop recorded — they
    cannot invent one; on the Sales OA staff are RECORDING a sold unit,
    optionally against a customer record, for the customer to claim.
    """
    match = SERIAL_RE.search(message or "")
    if not match:
        return ChatReply(text=_t(WARRANTY_NEEDS_SERIAL, language))
    serial = match.group(1).upper()

    # Guarded here rather than at the call sites, because both OAs arrive
    # through this one function and the customer half was missed:
    # "ไม่ต้องลงทะเบียน ONLY00001" — a customer waving off the prompt the
    # welcome message pushes at them — claimed the unit, and so did
    # "ลงทะเบียนยังไง ONLY00001" and "ลงทะเบียน ONLY00001 ไปหรือยัง"
    # (adversarial sweep, 10 ก.ย. 2569). Registration is a one-shot act;
    # unlike a reminder there is no standing state that "ไม่ต้อง" could be
    # ordering the cancellation of.
    held_warranty = _intent_guard_reply(
        message, action="warranty_register", language=language,
        triggers=SERIAL_REGISTER_TRIGGERS + SERIAL_REGISTER_HEAD_TRIGGERS,
    )
    if held_warranty is not None:
        return held_warranty

    license_id = str(license_id)
    if ctx.oa == "customer":
        return await _claim_for_customer(
            client, ctx=ctx, license_id=license_id, serial=serial, language=language,
        )
    if "warranty.create" not in set(permission_keys or []):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        # The product, if the serial or the message names one we know.
        # Optional by design: the customer has the sticker, not the
        # catalogue, and refusing over that loses the registration.
        products = await client.list_products(license_id)
    except Exception:
        products = []
    lowered = (message or "").lower()
    product = next(
        (p for p in products
         if str(p.get("product_name") or "").lower() in lowered
         or str(p.get("product_id") or "").lower() in lowered),
        None,
    )

    # "ให้ลูกค้า สมชาย" / "ของ สมชาย": the customer record the unit was sold
    # to. Optional — a walk-in sale has none yet. Ambiguous → buttons.
    contact_id = None
    contact_name = ""
    try:
        named = await _customer_named_in(client, license_id, message, permission_keys or [])
    except _AmbiguousName as exc:
        return _name_choice(message, exc, language)
    except Exception:
        named = None
    if named:
        # ("customer", id, code) — the name comes from the record itself.
        contact_id = str(named[1])
        try:
            row_named = await client.get_customer(license_id, contact_id)
            contact_name = _display_name(row_named) if row_named else str(named[2])
        except Exception:
            contact_name = str(named[2])

    try:
        row = await client.register_warranty(
            license_id,
            {
                "serial_number": serial,
                "customer_chann_uid": None,
                "contact_id": contact_id,
                "product_id": str(product["id"]) if product else None,
                "product_name": (product or {}).get("product_name"),
            },
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        # Already registered is a normal thing to hit — someone checking
        # rather than registering — so it reads as information, not error.
        # In Thai: the Data Tier's sentence is English and names internal
        # ids, which is not what a customer holding a sticker should read.
        if exc.status_code == 409:
            return ChatReply(
                text=_t(WARRANTY_ALREADY_REGISTERED, language).format(serial=serial),
                quick_replies=[("เช็คประกัน", f"เช็คประกัน {serial}")],
            )
        log.warning("warranty registration refused: %s", exc.detail)
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("warranty registration failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    text = _t(WARRANTY_REGISTERED, language).format(
        number=(row.get("warranty_number") or "-"),
        product=row.get("product_name") or serial,
        end=_iso_to_thai_date(row.get("warranty_end")),
    )
    if contact_name:
        text += f"\n{'สำหรับลูกค้า' if language != 'en' else 'For'} {contact_name}"
    text += "\n" + _t(WARRANTY_STAFF_NEXT, language).format(serial=serial)
    return ChatReply(
        text=text,
        entity_type="warranty", entity_id=str(row.get("id") or ""),
        quick_replies=[("รายการประกัน", "รายการประกัน")],
    )


WARRANTY_STAFF_NEXT = {
    "th": "ลูกค้าพิมพ์ {serial} ใน LINE บริการลูกค้าเพื่อผูกเครื่องนี้กับตัวเอง",
    "en": "The customer types {serial} in the customer LINE to attach this unit to themselves",
}
WARRANTY_CLAIMED = {
    "th": "ลงทะเบียน {product} (S/N {serial}) เป็นของคุณแล้ว ใบรับประกัน {number} ถึง {end}",
    "en": "{product} (S/N {serial}) is registered to you — warranty {number} until {end}",
}
SERIAL_NOT_AT_SHOP = {
    "th": (
        "ร้านยังไม่มีเครื่องหมายเลข {serial} ในระบบครับ ลองเช็คตัวเลขบนสติกเกอร์อีกครั้ง "
        "ถ้าถูกต้องแล้ว ติดต่อร้านให้ลงทะเบียนเครื่องให้ก่อน (พิมพ์ \"ติดต่อร้าน\")"
    ),
    "en": (
        "The shop has no unit {serial} on record. Check the sticker; if it is right, "
        "ask the shop to register the unit first (type \"contact the shop\")."
    ),
}
SERIAL_CLAIMED_BY_OTHER = {
    "th": "หมายเลข {serial} ถูกลงทะเบียนโดยลูกค้าท่านอื่นแล้ว ถ้าเป็นเครื่องของคุณ ติดต่อร้านครับ",
    "en": "Serial {serial} is registered to another customer. If it is yours, contact the shop.",
}


async def _claim_for_customer(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, serial: str,
    language: str,
) -> ChatReply:
    outcome, row = await _claim_serial(client, ctx, license_id, serial)
    if outcome == "ok":
        return ChatReply(
            text=_t(WARRANTY_CLAIMED, language).format(
                product=row.get("product_name") or ("เครื่อง" if language != "en" else "Unit"),
                serial=serial, number=(row.get("warranty_number") or "-"),
                end=_iso_to_thai_date(row.get("warranty_end")),
            ),
            entity_type="warranty", entity_id=str(row.get("id") or ""),
            quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("ประกันของฉัน", "ประกันของฉัน")],
        )
    if outcome == "not_found":
        return ChatReply(
            text=_t(SERIAL_NOT_AT_SHOP, language).format(serial=serial),
            quick_replies=[("ติดต่อร้าน", "ติดต่อร้าน")],
        )
    if outcome == "taken":
        return ChatReply(
            text=_t(SERIAL_CLAIMED_BY_OTHER, language).format(serial=serial),
            quick_replies=[("ติดต่อร้าน", "ติดต่อร้าน")],
        )
    return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))


def _by_serial(warranty: dict | None) -> dict[str, dict]:
    """One warranty as the index `ticket_machine.attach` expects."""
    serial = str((warranty or {}).get("serial_number") or "").strip().upper()
    return {serial: warranty} if serial and warranty else {}


async def _contact_id_of(client: DataClient, license_id: str, chann_uid: str) -> str:
    """The shop's contact row for this LINE identity, when it has one.

    A customer who has bought before is already a `customers` row here, and
    a ticket that points at it shows up in their history instead of
    starting a second, nameless record of the same person. A customer the
    shop has never recorded links nothing — the report is still taken
    (owner rule: never block a fault on paperwork).
    """
    if not chann_uid:
        return ""
    try:
        rows = await client.list_customers(str(license_id), customer_chann_uid=chann_uid)
    except Exception:
        log.exception("could not look up the contact behind %s", chann_uid)
        return ""
    for row in rows or []:
        if str(row.get("customer_chann_uid") or "") == chann_uid and row.get("id"):
            return str(row["id"])
    return ""


async def _claim_serial(
    client: DataClient, ctx: ResolvedContext, license_id: str, serial: str,
) -> tuple[str, dict]:
    """("ok" | "not_found" | "taken" | "error", row)."""
    try:
        row = await client.claim_warranty(
            license_id,
            {"serial_number": serial, "customer_chann_uid": ctx.chann_uid},
            actor_id=ctx.chann_uid,
        )
        return "ok", row
    except DataTierError as exc:
        if exc.status_code == 404:
            return "not_found", {}
        if exc.status_code == 409:
            return "taken", {}
        log.warning("claim of %s refused: %s", serial, exc.detail)
        return "error", {}
    except Exception:
        log.exception("claim of %s failed", serial)
        return "error", {}


async def _handle_serial_enquiry(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    language: str,
) -> ChatReply:
    """A serial the customer typed: check cover here, or find the shop.

    Looks inside this tenant first. Only when the serial is unknown HERE
    does it cross the boundary — the cross-tenant query is a fallback for
    "I don't know who I bought this from", not the normal path.
    """
    match = SERIAL_RE.search(message or "")
    if not match:
        return ChatReply(text=_t(WARRANTY_NEEDS_SERIAL, language))
    serial = match.group(1).upper()

    if license_id:
        try:
            here = await client.list_warranties(str(license_id), serial_number=serial)
        except Exception:
            log.exception("warranty lookup failed")
            here = []
        if here:
            row = here[0]
            return ChatReply(
                text=_t(WARRANTY_FOUND, language).format(
                    number=(row.get("warranty_number") or "-"),
                    product=row.get("product_name") or serial,
                    status=_label(WARRANTY_STATUS_LABELS, row.get("status"), language),
                    end=_iso_to_thai_date(row.get("warranty_end")),
                ),
                entity_type="warranty", entity_id=str(row.get("id") or ""),
                quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม")],
            )

    # 16.4: not here, so ask the platform which shop has it.
    try:
        result = await client.lookup_serial(serial, actor_chann_uid=ctx.chann_uid)
    except Exception:
        log.exception("cross-tenant serial lookup failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    matches = result.get("matches") or []
    if not matches:
        return ChatReply(text=_t(SERIAL_NO_SHOP, language).format(serial=serial))

    if len(matches) == 1 and license_id:
        # Registered, but at a different shop from the one they are
        # talking to. Saying so plainly is better than a bare "not found"
        # that leaves them thinking the record is gone.
        shop = matches[0]
        return ChatReply(
            text=_t(SERIAL_SHOPS_FOUND, language).format(
                serial=serial,
                shops=f"· {shop['company_name']} — {shop['company_code']}",
            )
        )

    shops = "\n".join(
        f"· {m['company_name']} — {m['company_code']}" for m in matches[:5]
    )
    return ChatReply(text=_t(SERIAL_SHOPS_FOUND, language).format(serial=serial, shops=shops))


def _chat_start_text(message: str) -> str | None:
    """The first line typed after "คุยกับร้าน", "" for the bare tile, None
    when this is not a request to talk to the shop."""
    text = (message or "").strip()
    lowered = text.lower()
    for phrase in CUSTOMER_CHAT_PHRASES:
        if lowered == phrase or lowered.startswith(phrase + " "):
            return text[len(phrase):].strip(" \t:：-—")
    return None


async def _handle_customer_chat_start(
    client: DataClient, *, ctx: ResolvedContext, license_id, first_message: str, language: str,
) -> ChatReply:
    """Phase 15.4: open (or rejoin) the conversation; every agent hears."""
    try:
        session, created, unseen = await live_chat.start_session(
            client, license_id=str(license_id), chann_uid=ctx.chann_uid,
            display_name=ctx.display_name, first_message=first_message or None, language=language,
        )
        shop = await live_chat.company_name(client, str(license_id))
        sla, _ = await live_chat.chat_settings(client, str(license_id))
    except Exception:
        log.exception("could not open a chat session")
        return ChatReply(text=_t(CHAT_OPEN_FAILED, language))
    opened = _t(CHAT_STARTED if created else CHAT_RESUMED, language).format(shop=shop, sla=sla)
    if unseen:
        opened = _t(CHAT_CATCH_UP, language).format(shop=shop) + "\n" + unseen + "\n\n" + opened
    return ChatReply(
        text=opened,
        entity_type="chat_session", entity_id=str(session.get("id") or ""),
        quick_replies=[("จบการสนทนา", "จบการสนทนา"), ("สินค้าทั้งหมด", "สินค้าทั้งหมด")],
    )


async def _handle_customer_chat_end(
    client: DataClient, *, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    try:
        session = await live_chat.live_session(client, license_id=str(license_id), chann_uid=ctx.chann_uid)
        if session is None:
            return ChatReply(text=_t(CHAT_NONE_TO_END, language), quick_replies=[("คุยกับร้าน", "คุยกับร้าน")])
        await live_chat.close_session(
            client, license_id=str(license_id), session=session, by="customer",
            actor_chann_uid=ctx.chann_uid, language=language,
        )
    except Exception:
        log.exception("could not close a chat session")
        return ChatReply(text=_t(CHAT_OPEN_FAILED, language))
    return ChatReply(text=_t(CHAT_ENDED, language), quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน")])


async def _handle_customer_chat_line(
    client: DataClient, *, ctx: ResolvedContext, license_id, session: dict, message: str, language: str,
) -> ChatReply:
    """A line in the running conversation — stored and routed to the
    agent, never turned into a repair job (15.4 "AI ไม่ auto-create")."""
    try:
        await live_chat.customer_message(
            client, license_id=str(license_id), session=session, chann_uid=ctx.chann_uid,
            text=message, language=language,
        )
    except Exception:
        log.exception("could not store a chat line")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    # No echo: the customer is talking to a person, and "sent" after every
    # line reads like a robot in the middle of the conversation (owner,
    # 4 Sep). The webhook sends nothing for an empty reply; a failure
    # above still answers in words.
    return ChatReply(text="", entity_type="chat_session", entity_id=str(session.get("id") or ""))


async def _handle_orders_mine(
    client: DataClient, *, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    """Spec page 2 "คำสั่งซื้อ/ประวัติ" — the chat side of the home's
    purchase-history list (parity rule). Same service call as the page."""
    try:
        deals = await storefront_service.my_orders(
            client, license_id=str(license_id), chann_uid=ctx.chann_uid,
        )
    except Exception:
        log.exception("order history failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    if not deals:
        return ChatReply(
            text=_t(ORDERS_NONE, language),
            quick_replies=[("สินค้าทั้งหมด", "สินค้าทั้งหมด"), ("ประกันของฉัน", "ประกันของฉัน")],
        )
    lines = [_t(ORDERS_HEAD, language)]
    for d in deals[:LIST_LIMIT]:
        stage = str(d.get("stage") or "")
        stage_text = ORDER_STAGE_LABELS.get(stage, {}).get(language) or stage
        products = ", ".join(
            f"{p.get('product_name')}" + (f" ×{p.get('qty')}" if (p.get("qty") or 1) > 1 else "")
            for p in (d.get("products") or [])
        )
        when = _iso_to_thai_date(str(d.get("created_at") or "")[:10]) if d.get("created_at") else ""
        lines.append(
            f"{d.get('deal_id') or ''} · {stage_text}"
            + (f" · {products}" if products else "")
            + (f" · {when}" if when else "")
        )
    return ChatReply(text="\n".join(lines))


async def _handle_warranty_mine(
    client: DataClient, *, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    """Everything this customer has registered — the chat side of the LIFF
    home's warranty list (parity rule). Before this, "ประกันของฉัน" fell
    through the fault-report catch-all and opened a repair ticket."""
    try:
        rows = await client.list_warranties(
            str(license_id), customer_chann_uid=ctx.chann_uid,
        )
    except Exception:
        log.exception("warranty list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    rows = [r for r in rows if str(r.get("status") or "") != "void"]
    if not rows:
        return ChatReply(
            text=_t(WARRANTY_MINE_NONE, language),
            quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน")],
        )
    lines = [_t(WARRANTY_MINE_HEAD, language)] + [
        _t(WARRANTY_MINE_LINE, language).format(
            number=r.get("warranty_number") or "-",
            product=r.get("product_name") or r.get("serial_number") or "",
            status=_label(WARRANTY_STATUS_LABELS, r.get("status"), language),
            end=(
                f" · {'ถึง' if language == 'th' else 'until'} "
                f"{_iso_to_thai_date(r.get('warranty_end'))}"
                if r.get("warranty_end") else ""
            ),
        )
        for r in rows[:LIST_LIMIT]
    ]
    return ChatReply(
        text="\n".join(lines),
        quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน")],
    )


CUSTOMER_CONTACT_INFO = {
    "th": "ติดต่อ {company} ได้ที่\n{lines}\n\nหรือพิมพ์เรื่องที่ต้องการติดต่อมาได้เลย ทางร้านจะเห็นและติดต่อกลับครับ",
    "en": "Reach {company} at\n{lines}\n\nOr just type your message — the shop will see it and get back to you.",
}
CUSTOMER_CONTACT_FORWARDED = {
    "th": "พิมพ์เรื่องที่ต้องการติดต่อมาได้เลยครับ ทางร้านจะเห็นข้อความนี้และติดต่อกลับ",
    "en": "Type what you need — the shop will see it and get back to you.",
}


async def _handle_customer_contact(
    client: DataClient, *, license_id, language: str, ctx: ResolvedContext | None = None,
) -> ChatReply:
    """The "ติดต่อร้าน" rich-menu tile. It used to fall into the fault-report
    catch-all and file a repair job whose fault was literally "ติดต่อร้าน".

    The profile's keys are company_phone / company_email (CompanyProfile
    schema) — the tile never showed either (review, 6 Sep 2026). And the
    promise "ทางร้านจะเห็นข้อความนี้" is kept: the next line is forwarded
    (see _maybe_forward_to_shop), not filed as a repair.
    """
    try:
        profile = await client.get_company_profile(str(license_id))
    except Exception:
        profile = None
    profile = profile or {}
    company = str(profile.get("company_name") or "").strip()
    lines = [
        f"· {label} {value}"
        for label, value in (
            ("โทร" if language == "th" else "Tel", profile.get("company_phone") or profile.get("phone")),
            ("อีเมล" if language == "th" else "Email", profile.get("company_email") or profile.get("email")),
            ("ที่อยู่" if language == "th" else "Address", profile.get("company_address")),
        )
        if value
    ]
    if ctx is not None:
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="contact", entity="customer_contact", fields={}, missing=["message"],
                ttl_seconds=CUSTOMER_CONTACT_TTL_S,
            )
        except Exception:
            log.exception("could not remember the contact prompt")
    quick = [("คุยกับร้าน", "คุยกับร้าน"), ("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน")]
    if not company or not lines:
        return ChatReply(text=_t(CUSTOMER_CONTACT_FORWARDED, language), quick_replies=quick)
    return ChatReply(
        text=_t(CUSTOMER_CONTACT_INFO, language).format(company=company, lines="\n".join(lines)),
        quick_replies=quick,
    )


CUSTOMER_CONTACT_TTL_S = 600
CUSTOMER_CONTACT_SENT = {
    "th": "\nทางร้านจะตอบกลับในแชทนี้ (ปกติภายใน {sla} นาที) พิมพ์ต่อได้เลย · \"จบการสนทนา\" เมื่อเสร็จ",
    "en": "\nThe shop answers here (usually within {sla} minutes). Keep typing; \"end chat\" when done.",
}


async def _maybe_forward_to_shop(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, language: str,
) -> ChatReply | None:
    """The line typed right after "ติดต่อร้าน": it goes to the shop as the
    first line of a live conversation — the same path "คุยกับร้าน" uses,
    so the shop is pushed and the reply lands back here. Before this the
    tile promised the shop would see the message and the message then
    opened a repair job (review, 6 Sep 2026)."""
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        return None
    if not pending or pending.get("entity") != "customer_contact":
        return None
    await _drop_pending_quietly(client, ctx)
    text = (message or "").strip()
    if not text or _is_customer_command(text) or _is_bare_serial(text) or _is_small_talk(text) or len(text) < 2:
        return None
    reply = await _handle_customer_chat_start(
        client, ctx=ctx, license_id=license_id, first_message=text, language=language,
    )
    if reply.entity_type == "chat_session":
        try:
            sla, _ = await live_chat.chat_settings(client, str(license_id))
        except Exception:  # noqa: BLE001
            sla = live_chat.DEFAULT_SLA_MINUTES
        reply.text = _t(CUSTOMER_QUESTION_FORWARDED, language).split("\n")[0] + _t(CUSTOMER_CONTACT_SENT, language).format(sla=sla)
        return reply
    # No live chat available (data tier refused / not deployed): the
    # dispatchers get the text as a notification instead, so the promise
    # "ทางร้านจะเห็นข้อความนี้" still holds.
    try:
        members = await client.list_members(str(license_id))
        told = 0
        for m in await _dispatchers(client, str(license_id), members):
            uid = str(m.get("chann_uid") or "")
            await send_notification(
                client, license_id=str(license_id), target_chann_uid=uid,
                target_line_user_id=await client.line_target_of(uid), type="customer_message",
                message=f"💬 ลูกค้า {ctx.display_name or ctx.chann_uid} ฝากข้อความ: {text[:300]}",
                message_en=f"💬 Customer {ctx.display_name or ctx.chann_uid} left a message: {text[:300]}",
                oa="sales",
            )
            told += 1
    except Exception:  # noqa: BLE001
        log.exception("could not forward a customer's message to the shop")
        told = 0
    if not told:
        return reply
    return ChatReply(
        text=_t(CUSTOMER_QUESTION_FORWARDED, language),
        quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน")],
    )


# ------------------------------------------- Phase 12 customer fault report
#
# The half of 12.4 that was missing: "ลูกค้าแจ้งซ่อม (แชทหรือ LIFF) → สร้าง
# ticket". Everything else in Phase 12 and 13 — dispatch, claim, check-in,
# the report — operates on tickets that nothing could create.
#
# A fault report is accepted on the FIRST message, with only a description.
# Address, phone and appointment are chased afterwards, by CS or by the
# follow-up questions below, because the dispatch gate is where
# completeness is enforced and demanding it up front turns a person
# reporting a broken appliance into a form to fill in.

STAFF_GREETING = {
    "th": "สวัสดีครับ พิมพ์คุยได้เลย หรือพิมพ์ \"วิธีใช้\" เพื่อดูตัวอย่างคำสั่ง",
    "en": 'Hello. Just type what you need, or "help" for examples.',
}

GREETING_PHRASES = (
    "สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "ดีคับ", "ดีค้าบ", "ดีจ้า", "ดีจ้ะ", "ดีจ๊ะ", "ดีงับ", "hello", "hi", "hey",
    "good morning", "good afternoon", "good evening", "morning", "อรุณสวัสดิ์", "สวัสดีตอนเช้า", "สวัสดีตอนบ่าย",
    "สวัสดีตอนเย็น", "สอบถาม", "ขอสอบถาม", "รบกวนสอบถาม", "ขอถาม", "รบกวนถาม",
)

CUSTOMER_JOB_STATUS = {
    "th": "งาน {code} ของคุณ: {status}\nนัด: {when}\nช่าง: {tech}",
    "en": "Your job {code}: {status}. Scheduled: {when}. Technician: {tech}.",
}
# The rich-menu tiles and the phrases people type for the same things.
# Each of these used to reach the fault-report catch-all and open a job.
CUSTOMER_STATUS_PHRASES = (
    "สถานะการซ่อม", "สถานะงาน", "ดูสถานะงาน", "ดูสถานะ", "สถานะ", "เช็คสถานะ",
    "repair status", "status", "งานที่แจ้งไว้", "ดูงานที่แจ้งไว้", "งานที่แจ้งไป", "ที่แจ้งไว้", "งานที่แจ้ง", "เลขงาน",
    "เลขงานของฉัน", "ติดตามงาน", "ตามงาน", "เช็คงาน", "ดูงาน", "งานล่าสุด", "check status", "track", "track my job",
    "สถานะการแจ้งซ่อม", "สถานะล่าสุด", "งานซ่อมของฉัน", "การซ่อมของฉัน",
    # A customer's "appointments" and "today" are their own jobs, not a
    # repair called "นัดหมาย" (review, 6 Sep 2026).
    "นัดหมาย", "นัดหมายทั้งหมด", "นัดของฉัน", "นัดหมายของฉัน", "ดูนัด", "งานวันนี้", "my appointments",
)
# Staff tiles typed on the customer OA: said to be the shop team's menu,
# never turned into a repair job named after the tile.
CUSTOMER_STAFF_TILE = {
    "th": "\"{text}\" เป็นเมนูของทีมร้านครับ ในไลน์นี้แจ้งซ่อมได้เลย (พิมพ์อาการ เช่น \"แอร์ไม่เย็น\") หรือดู \"งานของฉัน\"",
    "en": "\"{text}\" is the shop team's menu. Here you can report a fault (describe it) or check \"my jobs\".",
}
CUSTOMER_CONTACT_PHRASES = ("ติดต่อร้าน", "ติดต่อ", "เบอร์ร้าน", "contact shop", "contact", "ที่อยู่ร้าน", "เวลาทำการ", "แผนที่ร้าน", "ไลน์ร้าน")
# Where the shop is, when it opens, how to reach it — asked in a sentence
# rather than by the tile (review, 6 Sep 2026, B6: 11 of 14 phrasings fell
# through; "ร้านเปิดกี่โมง" was answered with the job's status).
_CONTACT_CONTAINS = (
    "เบอร์ร้าน", "เบอร์โทรร้าน", "เบอร์ของร้าน", "โทรหาร้าน", "โทรร้าน", "โทรไปร้าน", "ติดต่อร้าน", "ติดต่อทางร้าน", "ร้านเปิด",
    "ร้านปิด", "เปิดกี่โมง", "ปิดกี่โมง", "เวลาเปิด", "เวลาทำการ", "เวลาปิด", "เปิดวันไหน", "เปิดวันอาทิตย์", "เปิดเสาร์",
    "ร้านอยู่", "ที่อยู่ร้าน", "ที่ตั้งร้าน", "ไปที่ร้าน", "ไปร้าน", "ไปหน้าร้าน", "แผนที่ร้าน", "ไลน์ร้าน", "line ร้าน",
    "อีเมลร้าน", "เมลร้าน", "เว็บร้าน", "หน้าร้าน", "สาขา", "ร้านอยู่ไหน", "ร้านอยู่แถวไหน", "shop phone", "phone number",
    "opening hours", "open hours", "what time do you open", "what time do you close", "where is the shop", "shop address",
    "shop location", "how to contact", "contact number", "your address", "your phone", "your line",
)


def _asks_shop_contact(text: str) -> bool:
    canon = _canonical(text).replace(" ", "")
    return any(w.replace(" ", "") in canon for w in _CONTACT_CONTAINS)


# "ขอคุยกับคนจริงๆ", "แอดมินอยู่ไหม", "ไม่อยากคุยกับบอท" — a person, please
# (review, 6 Sep 2026, B7: 8 of 9 phrasings fell through).
_HUMAN_WORDS = (
    "คุยกับคน", "คนจริง", "พนักงาน", "เจ้าหน้าที่", "แอดมิน", "admin", "บอท", "bot", "มีคนตอบ", "มีคนอยู่", "คนตอบ",
    "มีใครอยู่", "มีใครตอบ", "ขอสาย", "โอนสาย", "คุยกับร้าน", "คุยกับทางร้าน", "คุยกับเจ้าของ", "คุยกับช่างโดยตรง", "โทรกลับ",
    "ติดต่อกลับ", "human", "real person", "staff", "agent", "operator", "someone", "a person", "talk to", "speak to",
    "call me", "call back",
)
_COMPLAINT_WORDS = (
    "แย่", "ห่วย", "ร้องเรียน", "ไม่พอใจ", "ผิดหวัง", "ไม่โอเค", "บริการไม่ดี", "รอนาน", "ไม่มีใครติดต่อ", "ไม่มีใครตอบ",
    "ไม่ประทับใจ", "โกง", "หลอก", "ไม่รับผิดชอบ", "เสียเวลา", "แย่มาก", "ช้ามาก", "ไม่ได้เรื่อง", "complain", "complaint",
    "terrible", "awful", "unacceptable", "disappointed", "bad service", "poor service", "ripped off", "scam",
    # Said about a person rather than the service: "ช่างพูดจาไม่ดีเลย" was
    # answered by opening a repair job, because the only thing that read it
    # was a matcher looking for the word "ช่าง" (10 ก.ย. 2569). This list
    # can only ever route someone to a human — it never writes — so it is
    # allowed to be generous.
    "พูดจาไม่ดี", "พูดไม่ดี", "ไม่สุภาพ", "หยาบคาย", "มารยาทไม่ดี", "rude", "impolite",
)


def _wants_a_human(text: str) -> bool:
    canon = _canonical(text).replace(" ", "")
    if not canon or _looks_like_fault(text) or TICKET_CODE_RE.search(text or ""):
        return False
    return any(w.replace(" ", "") in canon for w in _HUMAN_WORDS)


def _is_complaint(text: str) -> bool:
    canon = _canonical(text).replace(" ", "")
    return bool(canon) and any(w.replace(" ", "") in canon for w in _COMPLAINT_WORDS)

CUSTOMER_WARRANTY_MINE_PHRASES = (
    "ประกันของฉัน", "ใบรับประกันของฉัน", "รายการประกัน", "สินค้าของฉัน",
    "my warranties", "my products",
)
# The same request in other words — a frozenset, since these are exact
# matches and the trigger checker only reads tuples for containment order.
CUSTOMER_WARRANTY_MINE_WORDS = frozenset({
    "ประกัน", "warranty", "warranties", "ใบรับประกัน", "ดูประกัน", "เช็คประกันของฉัน", "ประกันเครื่อง", "ประกันเหลือ",
    "ประกันเหลือเท่าไหร่", "ประกันหมดเมื่อไหร่", "ประกันหมดยัง", "เครื่องของฉัน", "สินค้าที่ลงทะเบียน", "เครื่องที่ลงทะเบียน",
    "การรับประกัน", "ประกันของเครื่อง",
})
# Spec page 2: "คำสั่งซื้อ/ประวัติ" — this customer's deals in this shop.
CUSTOMER_ORDERS_PHRASES = (
    "ประวัติการซื้อ", "ประวัติการสั่งซื้อ", "คำสั่งซื้อของฉัน", "ประวัติของฉัน",
    "my orders", "order history", "purchase history", "ประวัติ", "ดูประวัติ", "เคยซื้ออะไรไปบ้าง", "เคยซื้ออะไร",
    "ซื้ออะไรไปบ้าง", "เคยซื้อ", "ที่เคยซื้อ", "ประวัติการซื้อของฉัน", "orders", "my purchases", "history", "คำสั่งซื้อ",
    "รายการที่ซื้อ", "ของที่ซื้อ",
)
ORDERS_NONE = {
    "th": "ยังไม่มีประวัติการซื้อกับร้านนี้ครับ ดูสินค้าได้ด้วย \"สินค้าทั้งหมด\" หรือ \"ค้นหา\" ตามด้วยชื่อสินค้า",
    "en": "No purchase history with this shop yet. \"all products\" or \"search …\" to browse.",
}
ORDERS_HEAD = {"th": "ประวัติการซื้อกับร้านนี้:", "en": "Your history with this shop:"}
ORDER_STAGE_LABELS = {
    "new": {"th": "รอเสนอราคา", "en": "new"},
    "proposed": {"th": "เสนอราคาแล้ว", "en": "quoted"},
    "won": {"th": "ซื้อแล้ว", "en": "purchased"},
    "lost": {"th": "ไม่ได้ซื้อ", "en": "not purchased"},
}
# Phase 15 — "คุยกับร้าน": a live conversation with a person at the shop.
# A prefix, so "คุยกับร้าน ราคาแอร์เท่าไหร่" opens it with that first line.
CUSTOMER_CHAT_PHRASES = (
    "คุยกับร้าน", "แชทกับร้าน", "คุยกับเจ้าหน้าที่", "คุยกับพนักงาน",
    "ขอคุยกับร้าน", "ขอคุยกับพนักงาน", "ขอคุยกับเจ้าหน้าที่", "ขอคุยกับทางร้าน", "คุยกับทางร้าน",
    "คุยกับคน", "คุยกับแอดมิน", "ขอคุยกับแอดมิน", "ติดต่อพนักงาน", "ติดต่อเจ้าหน้าที่",
    "talk to the shop", "chat with the shop", "live chat", "talk to a person", "talk to staff",
)
CUSTOMER_CHAT_END_PHRASES = (
    "จบการสนทนา", "จบแชท", "ปิดแชท", "ปิดการสนทนา", "end chat", "close chat",
)
CHAT_OPEN_FAILED = {
    "th": "ขออภัย เปิดการสนทนากับร้านไม่ได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
    "en": "Sorry — the conversation with the shop could not be opened right now. Please try again.",
}
CHAT_STARTED = {
    "th": "เปิดการสนทนากับ {shop} แล้ว พิมพ์ข้อความได้เลย ทางร้านจะตอบกลับในแชทนี้ (ปกติภายใน {sla} นาที)\nพิมพ์ \"จบการสนทนา\" เมื่อเสร็จ",
    "en": "You are now talking to {shop}. Just type — they answer here (usually within {sla} minutes).\nType \"end chat\" when done.",
}
CHAT_RESUMED = {
    "th": "คุณกำลังคุยกับ {shop} อยู่แล้ว พิมพ์ข้อความต่อได้เลย",
    "en": "You are already talking to {shop} — just keep typing.",
}
CHAT_LINE_SENT = {
    "th": "ส่งถึงร้านแล้ว รอคำตอบในแชทนี้ครับ",
    "en": "Sent to the shop — their answer will appear here.",
}
CHAT_ENDED = {
    "th": "จบการสนทนาแล้ว ขอบคุณครับ พิมพ์ \"คุยกับร้าน\" ได้อีกเมื่อต้องการ",
    "en": "Conversation ended, thank you. Type \"talk to the shop\" any time.",
}
CHAT_CATCH_UP = {
    "th": "ล่าสุดที่คุยกับ {shop} ไว้:",
    "en": "Where you left off with {shop}:",
}
# Owner, 4 Sep: the timers are the company's to set — here and on the
# dashboard (Company profile > chat policy). setting.manage.
CHAT_POLICY_SLA_PHRASES = ("ตั้งค่าเวลาตอบแชท", "ตั้งเวลาตอบแชท", "เวลาตอบแชท", "chat answer time", "chat sla")
CHAT_POLICY_TIMEOUT_PHRASES = ("ตั้งค่าปิดแชทเมื่อเงียบ", "ตั้งปิดแชทเมื่อเงียบ", "ปิดแชทเมื่อเงียบ", "chat quiet close", "chat timeout")
CHAT_POLICY_VIEW = ("ตั้งค่าแชท", "นโยบายแชท", "การตั้งค่าแชท", "chat policy", "chat settings")
CHAT_POLICY_STATE = {
    "th": (
        "นโยบายแชทของร้าน:\n"
        "· ร้านต้องตอบภายใน {sla} นาที — เกินแล้วระบบแจ้งลูกค้าว่าจะติดต่อกลับและพักการสนทนา\n"
        "· ปิดเองเมื่อลูกค้าเงียบ {timeout} นาที\n"
        "เปลี่ยน: \"ตั้งค่าเวลาตอบแชท 15\" · \"ตั้งค่าปิดแชทเมื่อเงียบ 60\" (นาที)"
    ),
    "en": (
        "Chat policy:\n"
        "· the shop answers within {sla} min — past that the customer is told and the chat is paused\n"
        "· closes after {timeout} quiet minutes\n"
        "Change: \"chat answer time 15\" · \"chat quiet close 60\" (minutes)"
    ),
}
CHAT_POLICY_BAD_NUMBER = {
    "th": "ระบุเป็นจำนวนนาที 1–1440 เช่น \"ตั้งค่าเวลาตอบแชท 15\"",
    "en": "Give minutes 1–1440, e.g. \"chat answer time 15\"",
}

CHAT_NONE_TO_END = {
    "th": "ตอนนี้ไม่มีการสนทนาที่เปิดอยู่ พิมพ์ \"คุยกับร้าน\" เพื่อเริ่ม",
    "en": "No conversation is open. Type \"talk to the shop\" to start one.",
}
# A bare "แจ้งซ่อม" is the tile, not a fault: ask what is wrong instead of
# opening a job whose fault reads "แจ้งซ่อม".
CUSTOMER_REPORT_BARE = ("แจ้งซ่อม", "แจ้งเสีย", "แจ้งปัญหา", "report a fault", "report")
REPORT_ASK_ISSUE = {
    "th": "อาการเสียเป็นอย่างไรครับ พิมพ์มาได้เลย เช่น \"แอร์ไม่เย็น มีน้ำหยด\"",
    "en": "What is wrong? Just describe it, e.g. \"air con not cooling, dripping\".",
}
REPORT_ASK_ISSUE_FOR = {
    "th": "รับทราบเรื่อง \"{thing}\" ครับ",
    "en": "Noted: \"{thing}\".",
}
# "ได้ทุกวันเลยค่ะ", "เมื่อไหร่ก็ได้": no date to book — the shop picks one
# and the flexibility is written on the job (review, 6 Sep 2026).
_ANY_DAY_PHRASES = (
    "ได้ทุกวัน", "ทุกวัน", "เมื่อไหร่ก็ได้", "เมื่อไรก็ได้", "วันไหนก็ได้", "แล้วแต่ร้าน", "แล้วแต่ช่าง", "สะดวกทุกวัน",
    "ว่างทุกวัน", "ได้หมด", "ได้เลย", "ตามสะดวก", "anytime", "any day", "any time", "whenever", "up to you", "flexible",
)
REPORT_ANY_DAY = {
    "th": "รับทราบครับ บันทึกไว้ว่าสะดวกทุกวัน ทางร้านจะเลือกวันที่เร็วที่สุดและติดต่อกลับเพื่อยืนยันนัดครับ",
    "en": "Noted — any day suits you. The shop will pick the earliest slot and confirm the appointment with you.",
}
CUSTOMER_PICK_JOB = {
    "th": "คุณมีงานเปิดอยู่ {n} งาน เลือกงานที่ต้องการดูครับ",
    "en": "You have {n} open jobs — pick one.",
}


_NEW_REPORT_PREFIXES = (
    "แจ้งซ่อม", "อยากแจ้งซ่อม", "ขอแจ้งซ่อม", "แจ้งเสีย", "อยากแจ้งเสีย", "แจ้งปัญหา", "report a fault",
)
_REPORT_PLACEHOLDERS = (
    "อันใหม่", "อีกอัน", "ใหม่", "อีกงาน", "อีกเครื่อง", "อีกตัว", "เพิ่ม", "อีก", "งานใหม่", "เครื่องใหม่",
    "another", "new", "one more",
)
# The rich-menu tiles, per OA — the message each tile sends (scripts/
# richmenu/generate.py TILES; a unit test keeps the two in step) plus the
# text a uri tile falls back to when no LIFF id is configured. A tile is
# ALWAYS answered as itself, on every OA and in every pending state (review,
# 6 Sep 2026: tapping วิธีใช้/เช็คอิน/สิทธิ์ของฉัน mid-report filed a service
# report reading found_issue="วิธีใช้").
RICH_MENU_TILE_TEXTS: dict[str, tuple[str, ...]] = {
    "sales": (
        "งานวันนี้", "รายชื่อลูกค้า", "รายการรออนุมัติ", "นัดหมายทั้งหมด", "วิธีใช้",
        "รายการดีล", "รายการสินค้า", "ทีมช่าง", "ข้อมูลบริษัท", "สลับภาษา",
        "เปิดแดชบอร์ด", "แชทลูกค้า",
    ),
    "technician": (
        "งานของฉัน", "งานที่เปิดรับ", "เช็คอิน", "ปิดงาน", "วิธีใช้",
        "งานของทีม", "ปฏิเสธงาน", "ข้อมูลของฉัน", "สิทธิ์ของฉัน", "สลับภาษา",
        "เปิดหน้าจอช่าง", "รายงานของฉัน",
    ),
    "customer": (
        "แจ้งซ่อม", "สถานะการซ่อม", "คุยกับร้าน", "ลงทะเบียนสินค้า", "วิธีใช้",
        "สินค้าทั้งหมด", "ประวัติการซื้อ", "ประกันของฉัน", "ติดต่อร้าน", "ข้อมูลของฉัน", "สลับภาษา",
        "เปิดหน้าจอลูกค้า",
    ),
}
# Commands the bot itself puts on quick-reply buttons and in the help
# menu; they round-trip like a tile does.
_MENU_COMMAND_TEXTS = (
    "งานของฉัน", "งานวันนี้", "รับงาน", "ถึงแล้ว", "วิธีใช้ทั้งหมด", "หัวข้อทั้งหมด",
    "รายการรออนุมัติ", "รายชื่อช่าง", "ข้อมูลร้าน", "รายการงาน", "ดูสถานะงาน", "จบการสนทนา",
    "สร้างลูกค้า", "สร้างดีล", "สร้างสินค้า", "ดูทุกงาน", "งานที่เปิดรับ", "แก้ที่อยู่ของฉัน",
)
_ALL_TILE_TEXTS = frozenset(
    t.replace(" ", "").lower()
    for texts in RICH_MENU_TILE_TEXTS.values() for t in texts
) | frozenset(t.replace(" ", "").lower() for t in _MENU_COMMAND_TEXTS)


_ADDRESS_MARKERS = (
    "ถ.", "ถนน", "ซ.", "ซอย", "หมู่", "ม.", "ต.", "ตำบล", "อ.", "อำเภอ", "จ.", "จังหวัด", "แขวง", "เขต",
    "/", "บ้านเลขที่", "คอนโด", "หมู่บ้าน", "อาคาร", "ชั้น", "ตึก", "road", "soi", "district",
)
_FAULT_MARKERS = (
    "ไม่เย็น", "ไม่ติด", "ไม่แรง", "ไม่ทำงาน", "ไม่หมุน", "ไม่ปั่น", "ปั่นไม่ไป", "เสีย", "พัง", "รั่ว", "หยด", "เสียงดัง",
    "ซ่อม", "ร้อน", "ดับ", "ช็อต", "มีกลิ่น", "not cooling", "broken", "leak",
    # 6 Sep 2026: "ประตูเลื่อนไม่ได้", "น้ำไม่ไหล", "เครื่องค้าง" are faults too.
    "ไม่ได้", "ไม่ออก", "เข้าไม่ได้", "ค้าง", "แตก", "หลุด", "ชำรุด", "ผิดปกติ", "มีปัญหา", "กระตุก", "ไม่ไหล",
    "อุดตัน", "ตัน", "ไม่ดูด", "ไม่ร้อน", "ไม่อุ่น", "ไม่เปิด", "เปิดไม่ติด", "ไม่ดัง", "ไม่มีภาพ", "ไม่มีเสียง",
    "not working", "doesn't work", "does not work", "stuck", "noise",
    # Review, 6 Sep 2026 (B4): symptoms people report that were not listed.
    "กระพริบ", "กะพริบ", "กลิ่นไหม้", "ไหม้", "น้ำแข็งเกาะ", "น้ำแข็ง", "รีโมทหาย", "รีโมทเสีย", "ดังมาก", "ดังผิดปกติ",
    "เสียงแปลก", "มีเสียง", "สั่น", "เย็นน้อย", "ไม่ค่อยเย็น", "น้ำรั่ว", "น้ำหยด", "น้ำไหล", "ควัน", "ไฟดูด", "ไฟรั่ว",
    "ไม่ปล่อยน้ำ", "ไม่ปั่นหมาด", "หมุนไม่ได้", "เปิดไม่ได้", "ปิดไม่ได้", "ล็อค", "ไม่แข็ง", "ไม่ละลาย", "ละลาย", "ขึ้น error",
    "error", "ไฟไม่เข้า", "ไฟไม่ติด", "ไม่มีไฟ", "ใช้ไม่ได้", "ใช้งานไม่ได้", "แจ้งซ่อม", "ต้องซ่อม", "มาซ่อม", "อาการ",
    "not cold", "no power", "won't turn on", "wont turn on", "not turning on", "dripping", "leaking", "smell",
    "burning", "noisy", "loud", "frozen", "ice", "flickering", "blinking", "remote lost", "lost the remote", "no picture",
)
# The thing itself, named and nothing else: "แอร์", "ตู้เย็น Samsung". The
# symptom is asked for rather than a job called "แอร์" opened or "not
# sure" answered (review, 6 Sep 2026, B4).
_APPLIANCE_WORDS = (
    "แอร์", "เครื่องปรับอากาศ", "ตู้เย็น", "ตู้แช่", "เครื่องซักผ้า", "เครื่องอบผ้า", "ทีวี", "โทรทัศน์", "พัดลม",
    "เครื่องทำน้ำอุ่น", "ไมโครเวฟ", "ปั๊มน้ำ", "เครื่องกรองน้ำ", "เครื่องดูดฝุ่น", "หม้อหุงข้าว", "เตาอบ", "เครื่องล้างจาน",
    "คอมเพรสเซอร์", "คอม", "รีโมท", "เครื่องฟอกอากาศ", "ตู้น้ำ", "เครื่องทำน้ำแข็ง", "แอร์บ้าน", "แอร์รถ",
    "fridge", "refrigerator", "freezer", "washing machine", "washer", "dryer", "tv", "television", "fan", "microwave",
    "water heater", "heater", "compressor", "remote", "air purifier", "pump", "oven",
)
_URGENT_WORDS = frozenset({"ด่วน", "ด่วนมาก", "ด่วนๆ", "ด่วนที่สุด", "รีบ", "รีบมาก", "เร่งด่วน", "urgent", "asap", "emergency", "ฉุกเฉิน", "help"})


def _names_appliance_only(text: str) -> str | None:
    """"แอร์", "ตู้เย็น", "เครื่องซักผ้า Samsung", "แอร์ Daikin 12000": the
    machine, no symptom. Returns the appliance word, else None."""
    canon = _normalise(text)
    if not canon or len(canon) > 40 or _looks_like_fault(text) or _asks_price(text) or _looks_like_a_question(text):
        return None
    if any(w in canon for w in ("ราคา", "ซื้อ", "ขาย", "รุ่นไหน", "มีไหม", "แนะนำ", "โปร", "buy", "price")):
        return None
    for word in sorted(_APPLIANCE_WORDS, key=len, reverse=True):
        w = word.replace(" ", "")
        if canon == w or (canon.startswith(w) and not re.match(r"[ก-๙]", canon[len(w):len(w) + 1] or "")):
            return word
    return None

# A request for a visit that names no fault: "ล้างแอร์", "ขอนัดช่าง",
# "อยากให้ช่างมาดู". A job all the same.
_SERVICE_MARKERS = (
    "ช่าง", "ซ่อม", "ล้าง", "ติดตั้ง", "เช็ค", "ตรวจ", "บำรุง", "นัด", "มาดู", "ย้าย", "ถอด", "เปลี่ยน",
    "service", "install", "clean", "repair", "fix", "maintenance", "technician",
)


def _looks_like_service_request(text: str) -> bool:
    lowered = _canonical(text)
    return any(m in lowered for m in _SERVICE_MARKERS)


# "รบกวนช่างมาดูแอร์ให้หน่อยได้ไหมคะ", "ขอนัดช่างมาล้างแอร์ได้มั้ยคะ": a
# polite REQUEST for a visit, phrased as a question — a job, not a question
# about the job already open (review, 6 Sep 2026, B5).
_REQUEST_HEADS = ("รบกวน", "ขอ", "อยาก", "ช่วย", "อยากให้", "ขอให้", "ให้ช่าง", "นัดช่าง", "ขอนัด", "จอง", "อยากนัด", "ส่งช่าง", "เรียกช่าง", "can you send", "could you send", "please send", "i want", "i need", "i'd like")
_REQUEST_MARKERS = ("มาดู", "มาซ่อม", "มาล้าง", "มาเช็ค", "มาติดตั้ง", "มาตรวจ", "มาเปลี่ยน", "มาแก้", "ให้หน่อย", "หน่อยได้ไหม", "ได้ไหม", "ได้มั้ย", "come and", "come to", "send a technician", "send someone")
_STATUS_QUESTION_WORDS = ("กี่โมง", "เมื่อไหร่", "เมื่อไร", "ถึงไหน", "หรือยัง", "รึยัง", "สถานะ", "มาแล้ว", "ยังไม่มา", "ได้เรื่อง", "เป็นไง", "คืบหน้า", "ใคร", "when", "status", "yet")


def _is_service_request_sentence(text: str) -> bool:
    canon = _canonical(text)
    if not canon or not _looks_like_service_request(text) or _asks_price(text):
        return False
    if any(w in canon for w in _STATUS_QUESTION_WORDS):
        return False
    compact = canon.replace(" ", "")
    return any(compact.startswith(h.replace(" ", "")) for h in _REQUEST_HEADS) or any(m.replace(" ", "") in compact for m in _REQUEST_MARKERS)


# A question about the job, as opposed to a question about anything else.
_JOB_QUESTION_WORDS = (
    "ช่าง", "งาน", "สถานะ", "ซ่อม", "เสร็จ", "นัด", "เมื่อไหร่", "เมื่อไร", "กี่โมง", "ถึงไหน", "มาหรือยัง",
    "technician", "job", "status", "when", "แจ้งไป", "แจ้งไว้", "ได้เรื่อง", "คืบหน้า", "เลขงาน", "ที่แจ้ง",
)
_WARRANTY_WORDS = ("ประกัน", "warranty")


def _asks_about_job(text: str) -> bool:
    lowered = _canonical(text)
    return any(w in lowered for w in _JOB_QUESTION_WORDS) and not _is_service_request_sentence(text)


def _asks_about_warranty(text: str) -> bool:
    lowered = _canonical(text)
    return any(w in lowered for w in _WARRANTY_WORDS)


def _is_reschedule_request(text: str) -> bool:
    """"เลื่อนนัด…", "ขอเลื่อน…", "เปลี่ยนวัน…" — a request that STARTS with
    moving, or names the appointment. "ประตูเลื่อนไม่ได้" is a fault."""
    compact = _normalise(text)
    heads = ("เลื่อน", "ขอเลื่อน", "เปลี่ยนวัน", "เปลี่ยนเวลา", "reschedule", "move", "ย้ายนัด", "ขอย้ายนัด", "เลื่อนเวลา")
    return any(f.startswith(heads) for f in _bare_forms(text)) or any(
        w in compact for w in ("เลื่อนนัด", "ขอเลื่อน", "เปลี่ยนวันนัด", "เปลี่ยนเวลานัด", "ขอเปลี่ยนเวลา", "ขอเปลี่ยนวัน")
    )


# "พรุ่งนี้ไม่สะดวกค่ะ ขอเป็นวันเสาร์": the part after the request word is
# the date that DOES suit.
_NEW_DATE_LEADS = ("ขอเป็น", "เอาเป็น", "เปลี่ยนเป็น", "เลื่อนเป็น", "เลื่อนไป", "ขอเลื่อนไป", "ย้ายไป", "ขอเป็นวัน", "เป็นวัน", "ขอวัน", "ขอเวลา", "ขอบ่าย", "ขอเช้า", "ไปเป็น", "instead")


def _new_date_part(text: str) -> str:
    lowered = _canonical(text)
    best = ""
    for lead in sorted(_NEW_DATE_LEADS, key=len, reverse=True):
        idx = lowered.find(lead)
        if idx != -1:
            best = lowered[idx + len(lead):].strip() if lead not in ("ขอเป็นวัน", "เป็นวัน", "ขอวัน", "ขอบ่าย", "ขอเช้า") else lowered[idx + len(lead) - (3 if lead.endswith("วัน") else 4):].strip()
            break
    return best



CUSTOMER_NOT_SURE = {
    "th": (
        "ยังไม่แน่ใจว่าต้องการอะไรครับ\n"
        "• แจ้งซ่อม: พิมพ์อาการ เช่น \"แอร์ไม่เย็น\"\n"
        "• ถามร้าน: แตะ \"คุยกับร้าน\" ด้านล่าง ข้อความนี้จะส่งถึงร้าน\n"
        "• ดูงานที่แจ้งไว้: \"งานของฉัน\""
    ),
    "en": (
        "Not sure what you need.\n"
        "• Report a fault: describe it, e.g. \"air con not cooling\"\n"
        "• Ask the shop: tap \"talk to the shop\" below and this message goes to them\n"
        "• Your jobs: \"my jobs\""
    ),
}


_PRODUCT_WORDS = ("ราคา", "รุ่น", "ซื้อ", "สินค้า", "โปรโมชั่น", "โปร", "price", "model", "buy", "product", "btu", "ผ่อน", "เท่าไหร่", "กี่บาท", "how much", "cost")
CUSTOMER_PRODUCT_HINT = {
    "th": "สินค้าและราคาดูได้เลยครับ พิมพ์ \"ค้นหา\" ตามด้วยชื่อสินค้า เช่น \"ค้นหา แอร์\" หรือแตะ \"สินค้าทั้งหมด\" · ถามร้านตรงๆ ก็ได้ที่ \"คุยกับร้าน\"",
    "en": "Products and prices: type \"search\" and a product, e.g. \"search air con\", or tap \"all products\" — or ask the shop directly.",
}
CUSTOMER_ADDRESS_NO_JOB = {
    "th": "ตอนนี้ไม่มีงานที่รอที่อยู่ครับ ถ้าต้องการแก้ที่อยู่ของคุณ แตะปุ่มด้านล่างได้เลย",
    "en": "No job is waiting for an address right now. To change your own address, tap below.",
}
CUSTOMER_ISSUE_TTL_S = 900
REPORT_PHONE_SAVED = {
    "th": "บันทึกเบอร์ {phone} ไว้กับงานแล้วครับ ขอที่อยู่ที่จะให้ช่างไปด้วยครับ",
    "en": "Phone {phone} saved on the job. What address should the technician go to?",
}
REPORT_ADDRESS_REUSED = {
    "th": "ใช้ที่อยู่เดิมครับ: {address}\nสะดวกให้ช่างไปวันไหน เวลาไหนครับ",
    "en": "Using your usual address: {address}\nWhen would suit you for the visit?",
}
REPORT_NO_PREVIOUS_ADDRESS = {
    "th": "ยังไม่มีที่อยู่เดิมในระบบครับ พิมพ์ที่อยู่มาได้เลย",
    "en": "I have no previous address on file — please type it.",
}
NO_SERIAL_NOTHING_HELD = {
    "th": "ตอนนี้ไม่มีเรื่องที่รอหมายเลขเครื่องครับ แจ้งซ่อมได้เลย พิมพ์อาการมา เช่น \"แอร์ไม่เย็น\" แล้วผมจะถามหมายเลขเครื่องทีหลัง",
    "en": "Nothing is waiting for a serial right now. Describe the fault, e.g. \"air con not cooling\", and I will ask for the serial after.",
}
# The customer's own date examples — not the sales OA's "เตือน D-2026-0001"
# (review, 6 Sep 2026).
CUSTOMER_NEEDS_DATE = {
    "th": "ไม่เข้าใจวันที่ครับ ลองพิมพ์แบบนี้ดู: \"พรุ่งนี้ 10 โมง\" · \"วันศุกร์ บ่าย 2\" · \"15 ก.ย. 14:00\"",
    "en": "Could not read the date. Try: \"tomorrow 10am\" · \"Friday 2pm\" · \"15 Sep 14:00\"",
}
# A time the person DID state and this could not read. Silently writing
# 09:00 over it books an hour they never chose and tells them nothing
# (review v3, B06); the 09:00 default stays for a message with no time in
# it at all.
TIME_NOT_UNDERSTOOD = {
    "th": "ไม่เข้าใจเวลาที่ระบุครับ ขอเวลาอีกครั้งได้ไหมครับ เช่น \"10 โมง\" · \"บ่าย 2\" · \"ทุ่มครึ่ง\" · \"14:00\"",
    "en": "I could not read the time you gave. Could you say it again? e.g. \"10am\" · \"2pm\" · \"14:00\"",
}
AMEND_ASK_NEW_DATE = {
    "th": "รับทราบครับ สะดวกให้ช่างไปวันไหน เวลาไหนแทนครับ เช่น \"พรุ่งนี้ 10 โมง\" · \"วันศุกร์ บ่าย 2\"",
    "en": "Understood — which day and time would suit instead? e.g. \"tomorrow 10am\" · \"Friday 2pm\"",
}
_STAFF_TILE_TEXTS_ON_CUSTOMER_OA = tuple(
    t for oa in ("sales", "technician") for t in RICH_MENU_TILE_TEXTS[oa]
    if t not in RICH_MENU_TILE_TEXTS["customer"] and t not in ("งานของฉัน", "งานวันนี้", "นัดหมายทั้งหมด", "ข้อมูลบริษัท", "รายการสินค้า")
) + ("รับงาน", "ถึงแล้ว", "รายชื่อช่าง", "ข้อมูลร้าน")


def _staff_tile_text(text: str) -> str | None:
    forms = _polite_forms(text)
    for tile in _STAFF_TILE_TEXTS_ON_CUSTOMER_OA:
        if tile.replace(" ", "").lower() in forms:
            return tile
    return None


async def _ask_for_issue(client: DataClient, ctx: ResolvedContext, language: str, prefix: str = "") -> ChatReply:
    """"แจ้งซ่อม" with nothing after it: ask, and remember that the next
    line is the fault (review, 6 Sep 2026 — with a conversation open it
    was stored as a chat line). `prefix` is the machine already named
    ("แอร์ Daikin"), kept so the fault is filed as "แอร์ Daikin ไม่เย็น"."""
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="report", entity="customer_ticket",
            fields={"prefix": prefix[:80]} if prefix else {}, missing=["issue"],
            ttl_seconds=CUSTOMER_ISSUE_TTL_S,
        )
    except Exception:
        log.exception("could not remember the fault prompt")
    if prefix:
        return ChatReply(text=_t(REPORT_ASK_ISSUE_FOR, language).format(thing=prefix[:40]) + " " + _t(REPORT_ASK_ISSUE, language))
    return ChatReply(text=_t(REPORT_ASK_ISSUE, language))


async def _previous_customer_address(
    client: DataClient, ctx: ResolvedContext, license_id: str, *, exclude_ticket_id: str = "",
) -> str:
    """The address on the customer's profile, else the one on their most
    recent job."""
    try:
        profile = await client.get_profile(ctx.chann_uid) or {}
    except Exception:
        profile = {}
    if str(profile.get("address") or "").strip():
        return str(profile["address"]).strip()
    try:
        tickets = await client.list_tickets(license_id)
    except Exception:
        return ""
    mine = [
        t for t in tickets
        if t.get("customer_chann_uid") == ctx.chann_uid and str(t.get("id")) != exclude_ticket_id
        and str(t.get("service_address") or "").strip()
    ]
    return str(mine[-1]["service_address"]).strip() if mine else ""


async def _ask_new_schedule(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, language: str,
) -> ChatReply:
    try:
        tickets = await client.list_tickets(license_id)
    except Exception:
        log.exception("could not read a customer's tickets")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    mine = [
        t for t in tickets
        if t.get("customer_chann_uid") == ctx.chann_uid
        and str(t.get("status")) not in ("completed", "cancelled")
    ]
    if not mine:
        return ChatReply(text=_t(AMEND_NO_OPEN_JOB, language))
    if len(mine) > 1:
        return ChatReply(
            text=_t(AMEND_PICK_ONE, language),
            quick_replies=[
                (str(t.get("ticket_number") or ""), f"เลื่อนนัด {t.get('ticket_number')}") for t in mine[:4]
            ],
        )
    ticket = mine[0]
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="report", entity="customer_ticket",
            fields={"ticket_id": str(ticket["id"]), "code": str(ticket.get("ticket_number") or ""), "reschedule": 1},
            missing=["schedule"], ttl_seconds=CUSTOMER_TICKET_TTL_S,
        )
    except Exception:
        log.exception("could not hold a reschedule prompt")
    return ChatReply(text=_t(AMEND_ASK_NEW_DATE, language))


def _customer_fallback(text: str, language: str) -> ChatReply:
    """Not a fault, not a command, not a job question: say so and offer
    the three things it could have been — never open a repair job for
    "ราคาแอร์เท่าไหร่" (which is what happened before 6 Sep 2026)."""
    clean = (text or "").strip()
    carried = f"คุยกับร้าน {clean}"[:300]
    lowered = _canonical(clean)
    if any(w in lowered for w in _PRODUCT_WORDS if w not in _SPEC_WORDS) or _asks_price(clean):
        return ChatReply(
            text=_t(CUSTOMER_PRODUCT_HINT, language),
            quick_replies=[("สินค้าทั้งหมด", "สินค้าทั้งหมด"), ("คุยกับร้าน", carried)],
        )
    # A digit is not an address: "ลงทะเบียน SN12345678", "งาน T-2026-0001"
    # and "เบอร์ใหม่ 089-…" were all told "no job is waiting for an
    # address" (review, 6 Sep 2026, B3). A place has a place word in it.
    if _strongly_address(clean) and len(clean) >= 8 and not _looks_like_a_question(clean) and not re.search(
        r"(?<![A-Za-z0-9])(?:SR|[CDQT])-\d{4}-\d{4}|[A-Za-z]{1,3}\d{5,}|\d{9,}", clean, re.I,
    ):
        return ChatReply(
            text=_t(CUSTOMER_ADDRESS_NO_JOB, language),
            quick_replies=[("แก้ที่อยู่ของฉัน", f"แก้ที่อยู่เป็น {clean}"[:300]), ("แจ้งซ่อม", "แจ้งซ่อม")],
        )
    return ChatReply(
        text=_t(CUSTOMER_NOT_SURE, language),
        quick_replies=[
            ("คุยกับร้าน", carried), ("แจ้งซ่อม", "แจ้งซ่อม"),
            ("งานของฉัน", "งานของฉัน"), ("วิธีใช้", "วิธีใช้"),
        ],
    )


def _starts_new_report(text: str) -> bool:
    lowered = _canonical(text)
    return any(lowered.startswith(p) for p in _NEW_REPORT_PREFIXES)


def _strip_report_trigger(text: str) -> str:
    """The sentence minus its "แจ้งซ่อม" head — read from the canonical
    spelling ("แจ้งซอม", "อยากแจ้งซ้อม") but returned as typed."""
    raw = (text or "").strip()
    canon = _canonical(raw)
    for prefix in sorted(_NEW_REPORT_PREFIXES, key=len, reverse=True):
        if canon.startswith(prefix):
            rest = canon[len(prefix):].strip(" :,-—")
            if not rest:
                return ""
            # Keep the person's own spelling of the remainder when it is
            # still there; otherwise the canonical remainder is fine.
            idx = raw.lower().find(rest[:4]) if len(rest) >= 4 else -1
            return raw[idx:].strip(" :,-—") if idx > 0 else rest
    return raw


def _is_report_placeholder(text: str) -> bool:
    compact = (text or "").strip().lower()
    return compact in _REPORT_PLACEHOLDERS or all(
        w in _REPORT_PLACEHOLDERS for w in compact.split()
    )


def _looks_like_address(text: str) -> bool:
    lowered = (text or "").lower()
    return any(ch.isdigit() for ch in lowered) or any(m in lowered for m in _ADDRESS_MARKERS)


# Markers that name a place, as opposed to a bare digit that could be a
# quantity ("2 เครื่อง") — the words that make a line an address.
_STRONG_ADDRESS_MARKERS = (
    "ถ.", "ถนน", "ซ.", "ซอย", "แขวง", "เขต", "ต.", "ตำบล", "อ.", "อำเภอ", "จ.", "จังหวัด", "หมู่บ้าน", "หมู่ ",
    "คอนโด", "อาคาร", "บ้านเลขที่", "ตึก", "road", "soi", "district", "กรุงเทพ", "กทม",
)
# Short fault words are place-name material too: "แขวงคลองตัน" is not a
# blocked drain and "ซอยแตกต่าง" is not a breakage (review, 6 Sep 2026).
_SHORT_FAULT_MARKERS = frozenset(m for m in _FAULT_MARKERS if re.search(r"[ก-๙]", m) and len(m) <= 4)


def _strongly_address(text: str) -> bool:
    lowered = (text or "").lower()
    return any(m in lowered for m in _STRONG_ADDRESS_MARKERS) or bool(re.search(r"\d+/\d+", lowered))


def _looks_like_fault(text: str) -> bool:
    lowered = _canonical(text)
    if any(m in lowered for m in _FAULT_MARKERS if m not in _SHORT_FAULT_MARKERS):
        return True
    if _strongly_address(text):
        return False
    for token in re.split(r"\s+", lowered):
        if not token or any(ch.isdigit() for ch in token):
            continue
        if any(token.startswith(m.strip()) for m in _STRONG_ADDRESS_MARKERS):
            continue
        if any(m in token for m in _SHORT_FAULT_MARKERS):
            return True
    return False


_PRICE_WORDS = (
    "เท่าไหร่", "เท่าไร", "กี่บาท", "จักบาท", "ราคา", "ค่าบริการ", "ค่าซ่อม", "ค่าล้าง", "ค่าใช้จ่าย", "ค่าแรง", "ค่าอะไหล่",
    "ค่าเดินทาง", "ค่าตรวจ", "คิดเงิน", "คิดราคา", "โปรโมชั่น", "how much", "price", "cost", "quote me",
    "ผ่อน", "ผ่อนได้", "installment", "credit card",
    "btu", "มีขาย", "ขายไหม", "มีรุ่น", "รุ่นไหนดี", "แนะนำรุ่น", "cheaper", "discount", "ลดราคา", "ส่วนลด",
)
_CANCEL_HINTS = (
    "ไม่เอาแล้ว", "ไม่ต้องมาแล้ว", "ไม่ต้องแล้ว", "ไม่ซ่อมแล้ว", "ซ่อมเองได้แล้ว", "ซ่อมเองแล้ว", "หายแล้ว",
    "ใช้ได้แล้ว", "ไม่ต้องส่งช่าง", "ไม่ต้องมา", "ไม่ทำแล้ว", "ไม่ต้องซ่อมแล้ว", "ยกเลิก", "ไม่เอา", "cancel", "never mind", "nevermind",
)


# A request for a visit, as long as the asking itself is not negated:
# "ไม่อยากให้มาซ่อม" is still a refusal, "อยากให้ช่างมาดู" never is.
_REPAIR_ASK_RE = re.compile(
    r"(?<!ไม่)(?:อยากให้|ช่วยดู|ช่วยส่ง|ช่วยซ่อม|ขอช่าง|ขอให้ช่าง|ส่งช่างมา|ให้ช่างมา|มาดูให้|เข้ามาดู|นัดช่าง)"
)


def _denies_repair_request(text: str) -> bool:
    """A negated fault or a hypothetical report is not consent to open a job.

    Do not reject every sentence containing ไม่: ไม่เย็น/ไม่ทำงาน are
    affirmative symptoms. Check the object of the negation instead.

    Two things this must not swallow (owner review, 9 Sep 2026):
    "แอร์ไม่ได้ซ่อมมานาน อยากให้ช่างมาดู" is a request, not a refusal —
    the negation is about the past, and the sentence asks for a visit.
    """
    compact = _compact(text)
    if _REPAIR_ASK_RE.search(compact):
        return False
    return bool(re.search(
        # "ไม่ได้ซ่อมมานาน" / "ไม่ได้ล้างมาหลายปี" describe neglect, not refusal.
        r"(?:ยังไม่|ไม่ได้|ไม่)(?:ได้)?(?:ต้อง)?(?:แจ้งซ่อม|ส่งช่าง|ซ่อม)"
        r"(?!มานาน|นาน|มาหลาย|หลาย|มาตั้งแต่|มาเป็น)|"
        r"(?:ยังไม่|ไม่ได้)(?:เสีย|พัง)|ไม่ใช่.{0,16}(?:เสีย|พัง)|"
        r"(?:ถ้า|หาก).{0,60}(?:ค่อยแจ้ง|ค่อยซ่อม)", compact,
    ))


REPAIR_NOT_REQUESTED = {
    "th": 'รับทราบครับ ยังไม่เปิดงานซ่อมใหม่ หากต้องการให้ช่างเข้าดู พิมพ์ "แจ้งซ่อม" ได้ครับ',
    "en": 'Understood. No new repair has been opened. Type "report a fault" when you want a visit.',
}
REPORT_ADDRESS_REQUIRED = {
    "th": "ยังรอที่อยู่ที่จะให้ช่างไปครับ พิมพ์บ้านเลขที่และถนน/ซอยได้เลย",
    "en": "I still need the service address. Please send the house number and street.",
}
_UNAVAILABLE_HINTS = ("ไม่ได้", "ไม่สะดวก", "ไม่ว่าง", "ไม่อยู่", "ติดธุระ", "can't make", "cannot make", "not free")
_SAME_ADDRESS_PHRASES = (
    "ที่อยู่เดิม", "ที่เดิม", "เหมือนเดิม", "ที่อยู่เดียวกัน", "ที่อยู่เดียวกับครั้งก่อน", "ตามที่อยู่เดิม",
    "same address", "same as before", "same place",
)


# A specification is not a price. "BTU" earns its place in the price
# vocabulary because "แอร์ 12000 BTU ราคาเท่าไหร่" is shopping — but on its
# own it only says WHICH machine, and "แอร์ 12000 BTU ไม่เย็น" was answered
# with the product catalogue in all four of its polite forms (review v3,
# B07). A fault with a spec in it is still a fault.
#
# Only words ALREADY in the price/product vocabulary belong here: this list
# subtracts from those, it must never add to them. "ตัน" was in the first
# draft and turned "แขวงคลองตัน" into a price question.
_SPEC_WORDS = ("btu",)


def _asks_price(text: str) -> bool:
    """"ราคาล้างแอร์", "สอบถามค่าบริการล้างแอร์": a question about money,
    which opened a repair job named after it (review, 6 Sep 2026)."""
    lowered = _canonical(text)
    if any(w in lowered for w in _PRICE_WORDS if w not in _SPEC_WORDS):
        return True
    return any(w in lowered for w in _SPEC_WORDS) and not _looks_like_fault(text)


def _is_cancel_hint(text: str) -> bool:
    lowered = _canonical(text)
    return any(w in lowered for w in _CANCEL_HINTS)


def _says_unavailable(text: str) -> bool:
    """"ช่างมาพรุ่งนี้ไม่ได้นะ", "วันศุกร์ไม่สะดวก": the appointment does not
    suit — a reschedule, not a fault called "ไม่ได้"."""
    lowered = _canonical(text)
    if not _looks_like_a_date_attempt(text):
        return False
    if _looks_like_a_question(text) and not _new_date_part(text):
        return False
    return any(w in lowered for w in _UNAVAILABLE_HINTS) and any(w in lowered for w in ("ช่าง", "มา", "นัด", "วัน", "technician", "visit"))


def _looks_like_phone(text: str) -> bool:
    """A phone number and nothing else: 9–11 digits, dashes and spaces
    allowed, a leading 0 or +66. Typed while the bot waits for an address
    it was read as a serial (review, 6 Sep 2026)."""
    token = (text or "").strip()
    if not re.fullmatch(r"(?:\+66|0)[\d\-\s]{7,13}", token):
        return False
    digits = re.sub(r"\D", "", token)
    return 9 <= len(digits) <= 11


def _is_customer_command(text: str) -> bool:
    """A rich-menu tile or a known command, as opposed to free text."""
    lowered = (text or "").lower()
    return _matches_phrase(
        text,
        TICKET_MINE_PHRASES + TICKET_LIST_PHRASES + CUSTOMER_STATUS_PHRASES
        + CUSTOMER_CONTACT_PHRASES + CUSTOMER_WARRANTY_MINE_PHRASES + CUSTOMER_ORDERS_PHRASES
        + CUSTOMER_CHAT_PHRASES + CUSTOMER_CHAT_END_PHRASES + PRODUCT_LIST_PHRASES + STOREFRONT_BROWSE_EXTRA
        + CUSTOMER_REPORT_BARE + HELP_TRIGGERS + CUSTOMER_CANCEL_PHRASES + CUSTOMER_PROFILE_PHRASES
        + NO_SERIAL_PHRASES + LANGUAGE_TOGGLE_PHRASES,
    ) or _is_reschedule_request(text) or _is_help_request(text, "customer") or _is_small_talk(text) or (
        # Every OA's tiles: "เช็คอิน" typed here is not a street either.
        _is_menu_tile(text)
        # The bot's own cancel button and the cancel verbs, wherever they sit
        # in the sentence: "ยืนยันยกเลิกงาน T-2026-0001" was saved as the
        # address (review, 6 Sep 2026).
        or any(w in lowered for w in CUSTOMER_CANCEL_TRIGGERS) or "ยืนยันยกเลิก" in lowered
        or any(t in lowered for t in SERIAL_REGISTER_TRIGGERS + SERIAL_LOOKUP_TRIGGERS)
    )


def _not_an_address(text: str) -> bool:
    """What must never be saved as the street while the address is being
    asked: a command, a price question, a phone number, a cancellation,
    "the technician can't come tomorrow"."""
    return (
        _is_customer_command(text) or _asks_price(text) or _looks_like_phone(text)
        or _is_cancel_hint(text) or _says_unavailable(text)
    )
CUSTOMER_QUESTION_FORWARDED = {
    "th": "ผมตอบคำถามนี้เองไม่ได้ครับ แต่ทางร้านจะเห็นข้อความนี้และติดต่อกลับ\nถ้าต้องการแจ้งซ่อม พิมพ์อาการมาได้เลย",
    "en": "I can't answer that myself, but the shop will see this and get back to you.",
}

CUSTOMER_GREETING = {
    "th": (
        "สวัสดีครับ\n\n"
        "แจ้งซ่อมได้เลย พิมพ์อาการที่เสียมา เช่น \"แอร์ไม่เย็น\"\n"
        "หรือพิมพ์ \"งานของฉัน\" เพื่อดูสถานะงานที่แจ้งไว้"
    ),
    "en": (
        "Hello.\n\n"
        "To report a fault, just describe it — e.g. \"air con not cooling\".\n"
        'Type "my jobs" to check something you already reported.'
    ),
}

_QUESTION_MARKERS = (
    "ไหม", "มั้ย", "กี่โมง", "เมื่อไหร่", "เมื่อไร", "เท่าไหร่", "เท่าไร", "ยังไง", "ถึงไหน",
    "อย่างไร", "ทำไม", "ที่ไหน", "ใคร", "หรือเปล่า", "หรือยัง", "รึเปล่า", "รึยัง", "?",
    "เป็นไง", "ไงบ้าง", "อยู่ไหน", "แถวไหน", "กี่บาท", "กี่วัน", "วันไหน", "อันไหน", "ตอนไหน", "ได้ป่าว", "ได้เปล่า",
    "what", "when", "where", "how", "who", "which", "can i", "can you", "is it", "do you",
)


_DATE_WORDS = (
    "วัน", "พรุ่ง", "มะรืน", "เช้า", "บ่าย", "เย็น", "โมง", "ทุ่ม", "น.", "/", "ม.ค", "ก.พ",
    "มี.ค", "เม.ย", "พ.ค", "มิ.ย", "ก.ค", "ส.ค", "ก.ย", "ต.ค", "พ.ย", "ธ.ค",
)


def _looks_like_a_date_attempt(text: str) -> bool:
    """Was the person TRYING to give a date, even if it did not parse?

    "วันจันทร์หน้าตอนสายๆ" is a date attempt the parser may miss and
    should be asked to rephrase. "ABC123456" is not, and should not be
    met with a date-format lecture.
    """
    lowered = (text or "").lower()
    return any(w in lowered for w in _DATE_WORDS) or bool(re.search(r"\d{1,2}[:.]\d{2}", lowered))


def _is_bare_serial(text: str) -> bool:
    """One token that is a serial number and nothing else.

    "ABC123456" typed while the bot was waiting for an address is a
    serial the customer is offering, not where they live. An address
    has spaces, Thai letters, or a slash; a serial has none of those.
    """
    token = (text or "").strip()
    return (
        " " not in token
        and "/" not in token
        and not re.search(r"[\u0e00-\u0e7f]", token)
        and any(ch.isdigit() for ch in token)  # "hello" is a greeting, not S/N HELLO
        and not _looks_like_phone(token)  # "0812345678" is a phone, not S/N (review, 6 Sep 2026)
        and SERIAL_RE.fullmatch(token) is not None
    )


NO_SERIAL_PHRASES = (
    "ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลข", "ไม่มีสติกเกอร์", "ไม่ทราบหมายเลข", "หาไม่เจอ",
    "ไม่มี s/n", "ไม่มี sn", "no serial",
)
REPORT_REGISTER_FIRST = {
    "th": (
        "รับเรื่อง \"{issue}\" ไว้แล้วครับ\n\n"
        "ก่อนแจ้งซ่อม ขอลงทะเบียนสินค้าก่อน 1 ครั้ง เพื่อให้ร้านรู้ว่าเครื่องไหน: "
        "พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) มาได้เลย ผมจะแจ้งซ่อมเรื่องนี้ให้ต่อทันที\n"
        "ถ้าหาหมายเลขไม่เจอ พิมพ์ \"ไม่มีหมายเลขเครื่อง\""
    ),
    "en": (
        "Noted: \"{issue}\"\n\n"
        "Before filing it, register the product once so the shop knows which "
        "machine: type the serial number (S/N on the sticker) and I will file this "
        "right after. If you cannot find it, type \"no serial\"."
    ),
}
REPORT_WHICH_PRODUCT = {
    "th": "เครื่องไหนครับ เลือกได้เลย",
    "en": "Which machine? Pick one.",
}


async def _customer_report_waiting(client: DataClient, ctx: ResolvedContext, message: str) -> bool:
    """Is the report flow waiting for THIS line (the fault after "แจ้งซ่อม",
    the address, the serial, a date)? Then it is not a chat line even
    while a conversation with the shop is open (review, 6 Sep 2026)."""
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        return False
    if not pending:
        return False
    entity = pending.get("entity")
    missing = pending.get("missing") or []
    if entity == "pending_customer_message":
        return True
    if entity == "customer_ticket":
        if "schedule" in missing:
            return _looks_like_a_date_attempt(message)
        return True
    return False


async def _hold_customer_message(client: DataClient, ctx: ResolvedContext, text: str) -> None:
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa,
            action="report", entity="pending_customer_message",
            fields={"message": text[:500]}, missing=["serial"],
            ttl_seconds=CUSTOMER_TICKET_TTL_S,
        )
    except Exception:
        log.exception("could not hold a customer's fault while asking for the product")


async def _clear_customer_hold(client: DataClient, ctx: ResolvedContext) -> None:
    try:
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        log.exception("could not clear a held customer message")


# Phase 16.3 — the person's language, kept against the identity so it
# follows them across every shop and every OA.
LANGUAGE_TO_EN = (
    "เปลี่ยนภาษาเป็นอังกฤษ", "เปลี่ยนเป็นภาษาอังกฤษ", "ใช้ภาษาอังกฤษ", "ภาษาอังกฤษ", "english please",
    "switch to english", "use english", "english", "ขอเป็นภาษาอังกฤษ", "ขอเป็นอังกฤษ", "เป็นอังกฤษ", "อังกฤษ", "ขออังกฤษ",
    "เอาภาษาอังกฤษ", "พูดอังกฤษได้ไหม", "พูดอังกฤษ", "speak english", "in english", "en", "eng",
)
LANGUAGE_TO_TH = (
    "เปลี่ยนภาษาเป็นไทย", "เปลี่ยนเป็นภาษาไทย", "ใช้ภาษาไทย", "ภาษาไทย", "switch to thai", "use thai", "thai",
    "ขอไทย", "ขอเป็นไทย", "ขอเป็นภาษาไทย", "เป็นไทย", "ไทย", "พูดไทยได้ไหม", "พูดไทย", "ภาษาไทยได้ไหม", "เอาภาษาไทย",
    "speak thai", "in thai", "th",
)
# One rich-menu tile for both languages (Phase 19 page 2): flips whichever
# the person is reading now.
LANGUAGE_TOGGLE_PHRASES = ("สลับภาษา", "เปลี่ยนภาษา", "switch language", "toggle language")
LANGUAGE_SWITCHED = {
    "th": "เปลี่ยนเป็นภาษาไทยแล้วครับ ข้อความจากระบบทุกช่องทางจะเป็นภาษาไทย",
    "en": "Switched to English. Every message from the system, on every channel, will be in English.",
}


# 16.3: the other two preferences, by prefix — "รูปแบบวันที่ yyyy-mm-dd",
# "เขตเวลา Asia/Tokyo". Validated here so a typo never becomes a stored
# preference that breaks every date afterwards.
DATE_FORMAT_PHRASES = ("รูปแบบวันที่", "ตั้งรูปแบบวันที่", "date format")
TIMEZONE_PHRASES = ("เขตเวลา", "ตั้งเขตเวลา", "timezone", "time zone")
DATE_FORMAT_SET = {
    "th": "ตั้งรูปแบบวันที่เป็น {fmt} แล้ว ตัวอย่าง: {sample}",
    "en": "Date format set to {fmt}, e.g. {sample}",
}
DATE_FORMAT_CHOICES = {
    "th": "เลือกรูปแบบวันที่ได้: dd/mm/yyyy · mm/dd/yyyy · yyyy-mm-dd  เช่น \"รูปแบบวันที่ dd/mm/yyyy\"",
    "en": "Choose one of: dd/mm/yyyy · mm/dd/yyyy · yyyy-mm-dd, e.g. \"date format dd/mm/yyyy\"",
}
TIMEZONE_SET = {"th": "ตั้งเขตเวลาเป็น {tz} แล้ว", "en": "Time zone set to {tz}"}
TIMEZONE_CHOICES = {
    "th": "ระบุเขตเวลาเป็นชื่อมาตรฐาน เช่น \"เขตเวลา Asia/Bangkok\" (Asia/Tokyo, Asia/Singapore, UTC)",
    "en": "Give a standard zone name, e.g. \"timezone Asia/Bangkok\" (Asia/Tokyo, Asia/Singapore, UTC)",
}


async def _maybe_set_display_pref(
    client: DataClient, *, ctx: ResolvedContext, message: str, language: str,
) -> ChatReply | None:
    text = (message or "").strip()
    lowered = text.lower()
    for prefix in DATE_FORMAT_PHRASES:
        if lowered.startswith(prefix):
            wanted = text[len(prefix):].strip().lower()
            if wanted not in DATE_FORMATS:
                return ChatReply(text=_t(DATE_FORMAT_CHOICES, language))
            try:
                await client.set_display_preferences(ctx.chann_uid, {"date_format": wanted})
            except Exception:
                log.exception("could not store the date format")
                return ChatReply(text=unavailable_reply(language))
            from .thai_datetime import format_thai_date, set_display_prefs, display_prefs

            set_display_prefs({**display_prefs(), "date_format": wanted, "language": language})
            return ChatReply(text=_t(DATE_FORMAT_SET, language).format(fmt=wanted, sample=format_thai_date(local_today())))
    for prefix in TIMEZONE_PHRASES:
        if lowered.startswith(prefix):
            wanted = text[len(prefix):].strip()
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(wanted)
            except Exception:
                return ChatReply(text=_t(TIMEZONE_CHOICES, language))
            try:
                await client.set_display_preferences(ctx.chann_uid, {"timezone": wanted})
            except Exception:
                log.exception("could not store the timezone")
                return ChatReply(text=unavailable_reply(language))
            return ChatReply(text=_t(TIMEZONE_SET, language).format(tz=wanted))
    return None


# 16.4: whether a customer who links becomes a CRM record at once.
AUTO_ACCEPT_PHRASES = ("ตั้งค่ารับลูกค้าใหม่อัตโนมัติ", "รับลูกค้าใหม่อัตโนมัติ", "auto accept customers", "auto-accept customers")
AUTO_ACCEPT_VIEW = ("การตั้งค่ารับลูกค้าใหม่", "ดูการตั้งค่ารับลูกค้าใหม่", "auto accept setting")
AUTO_ACCEPT_STATE = {
    "th": "รับลูกค้าใหม่อัตโนมัติ: {state}\n{meaning}\nเปลี่ยน: \"ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด\" หรือ \"… ปิด\"",
    "en": "Auto-accept new customers: {state}\n{meaning}\nChange: \"auto accept customers on\" / \"… off\"",
}
AUTO_ACCEPT_MEANING = {
    ("th", True): "ลูกค้าที่ผูกร้านผ่าน LINE และมีชื่อ+เบอร์ จะเข้ารายชื่อลูกค้าทันที",
    ("th", False): "ลูกค้าที่ผูกร้านผ่าน LINE จะแจ้งให้ CS เพิ่มเข้ารายชื่อเอง",
    ("en", True): "A customer who links in LINE with a name and phone joins the customer list at once",
    ("en", False): "A customer who links in LINE is announced to CS to add by hand",
}


async def _maybe_auto_accept_setting(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    from .onboarding import SETTING_KEY, auto_accept_enabled

    text = (message or "").strip()
    lowered = text.lower()
    matched = next((p for p in AUTO_ACCEPT_PHRASES if lowered.startswith(p)), None)
    if matched is None and not _matches_phrase(text, AUTO_ACCEPT_VIEW):
        return None
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    rest = text[len(matched):].strip().lower() if matched else ""
    if rest in ("เปิด", "on", "true", "yes"):
        await client.put_license_setting(str(license_id), SETTING_KEY, True, actor_id=ctx.chann_uid)
        state = True
    elif rest in ("ปิด", "off", "false", "no"):
        await client.put_license_setting(str(license_id), SETTING_KEY, False, actor_id=ctx.chann_uid)
        state = False
    else:
        state = await auto_accept_enabled(client, str(license_id))
    lang = "en" if language == "en" else "th"
    return ChatReply(
        text=_t(AUTO_ACCEPT_STATE, language).format(
            state=("เปิด" if state else "ปิด") if lang == "th" else ("on" if state else "off"),
            meaning=AUTO_ACCEPT_MEANING[(lang, state)],
        ),
    )


async def _maybe_chat_policy_setting(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    text = (message or "").strip()
    lowered = text.lower()
    key = None
    matched = None
    for phrases, setting in ((CHAT_POLICY_SLA_PHRASES, "chat_sla_minutes"),
                             (CHAT_POLICY_TIMEOUT_PHRASES, "chat_timeout_minutes")):
        matched = next((p for p in phrases if lowered.startswith(p)), None)
        if matched:
            key = setting
            break
    if key is None and not _matches_phrase(text, CHAT_POLICY_VIEW):
        return None
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if key is not None:
        rest = text[len(matched):].strip().rstrip("นาที").strip() if matched else ""
        try:
            minutes = int(rest)
            if not 1 <= minutes <= 1440:
                raise ValueError
        except ValueError:
            if rest:
                return ChatReply(text=_t(CHAT_POLICY_BAD_NUMBER, language))
        else:
            await client.put_license_setting(str(license_id), key, minutes, actor_id=ctx.chann_uid)
    sla, timeout = await live_chat.chat_settings(client, str(license_id))
    return ChatReply(text=_t(CHAT_POLICY_STATE, language).format(sla=sla, timeout=timeout))


def _language_switch_requested(message: str) -> str | None:
    if _matches_phrase(message, LANGUAGE_TO_EN):
        return "en"
    if _matches_phrase(message, LANGUAGE_TO_TH):
        return "th"
    return None


async def _switch_language(client: DataClient, *, ctx: ResolvedContext, language: str) -> ChatReply:
    try:
        await client.set_display_preferences(ctx.chann_uid, {"language": language})
    except Exception:
        log.exception("could not store the language preference")
        return ChatReply(text=unavailable_reply(language))
    if sync_rich_menu:
        try:
            await sync_rich_menu(client, oa=ctx.oa, chann_uid=ctx.chann_uid, language=language)
        except Exception:  # noqa: BLE001
            log.exception("could not re-link the rich menu after a language switch")
    return ChatReply(text=_t(LANGUAGE_SWITCHED, language))


# ------------------------------------------------- a picture, not words (13.1)

PHOTO_ATTACHED = {
    "th": "แนบรูปกับงาน {code} แล้วครับ ({n} รูป) ส่งเพิ่มได้เรื่อยๆ",
    "en": "Photo attached to {code} ({n} so far). Send more any time.",
}
PHOTO_NO_JOB = {
    "th": "ยังไม่มีงานที่กำลังทำให้แนบรูปครับ รับงานและเช็คอินก่อน แล้วค่อยส่งรูป",
    "en": "No job to attach this to yet — take a job and check in, then send the picture.",
}
PHOTO_NO_JOB_CUSTOMER = {
    "th": "ยังไม่มีงานซ่อมที่เปิดอยู่ให้แนบรูปครับ พิมพ์อาการที่เสียก่อน แล้วค่อยส่งรูป",
    "en": "No open repair to attach this to — describe the fault first, then send the picture.",
}
PHOTO_FAILED = {
    "th": "รับรูปไม่สำเร็จ ลองส่งใหม่อีกครั้งครับ",
    "en": "Could not take the picture — please send it again.",
}


LOCATION_NO_JOB = {
    "th": "ได้รับตำแหน่งแล้ว แต่ยังไม่มีงานที่รับไว้ให้เช็คอินครับ พิมพ์ \"งานที่เปิดรับ\" เพื่อรับงานก่อน",
    "en": "Location received, but there is no job of yours to check in to. \"open jobs\" to take one first.",
}
LOCATION_PICK_ONE = {
    "th": "มีงานที่รับไว้หลายงาน เลือกงานที่จะเช็คอินครับ (เช็คอินจากปุ่มจะไม่บันทึกพิกัด)",
    "en": "You hold several jobs — pick the one to check in to (a button check-in records no coordinates).",
}
LOCATION_NOTED = {
    "th": "ได้รับตำแหน่งแล้วครับ ตอนนี้ระบบใช้ตำแหน่งสำหรับการเช็คอินของช่างเท่านั้น",
    "en": "Location received. Right now locations are used for technician check-ins only.",
}


async def handle_incoming_location(
    client: DataClient, *, ctx: ResolvedContext, oa: str, latitude: float, longitude: float,
    language: str = "th",
) -> ChatReply:
    """A LINE location message. On the technician OA it IS the check-in,
    with the coordinates the text command cannot carry (13.3: GPS on the
    visit). Anywhere else it is acknowledged and not stored."""
    if oa != "technician" or not ctx.license_id:
        return ChatReply(text=_t(LOCATION_NOTED, language))
    license_id = str(ctx.license_id)
    try:
        member, ticket, _inferred = await _ticket_for_action(
            client, license_id, ctx, "", prefer_status=("assigned",),
        )
        if member is not None and ticket is None:
            assigned = [
                t for t in await client.list_tickets(license_id, visible_to=str(member["id"]))
                if str(t.get("assigned_to_ref") or "") == str(member["id"])
                and str(t.get("status") or "") == "assigned"
            ]
            if len(assigned) > 1:
                return ChatReply(
                    text=_t(LOCATION_PICK_ONE, language),
                    quick_replies=[
                        (f"เช็คอิน {t.get('ticket_number')}"[:20], f"เช็คอิน {t.get('ticket_number')}")
                        for t in assigned[:4]
                    ],
                )
        if member is None or ticket is None:
            return ChatReply(text=_t(LOCATION_NO_JOB, language), quick_replies=[("งานที่เปิดรับ", "งานที่เปิดรับ")])
        code = str(ticket.get("ticket_number") or "")
        result = await client.check_in_ticket(
            license_id, str(ticket["id"]), member_id=str(member["id"]),
            gps_lat=float(latitude), gps_lng=float(longitude), actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        return _field_service_failure(
            exc, code=locals().get("code", ""), language=language, template=CHECKIN_FAILED,
        )
    except Exception:
        log.exception("location check-in failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    done = _t(CHECKIN_DONE, language).format(
        code=code,
        customer=" ".join(p for p in (result.get("customer_name"), result.get("customer_phone")) if p) or "—",
        address=result.get("service_address") or "—",
    )
    done += "\n" + ("(บันทึกตำแหน่งที่เช็คอินไว้แล้ว)" if language != "en" else "(check-in location recorded)")
    return ChatReply(
        text=done, entity_type="service_ticket", entity_id=str(result.get("id") or ""),
        quick_replies=[("ปิดงาน", f"ปิดงาน {code}")],
    )


async def handle_incoming_image(
    client: DataClient, *, ctx: ResolvedContext, oa: str, message_id: str, language: str = "th",
) -> ChatReply:
    """An image message on any OA. A technician's picture is evidence on
    the job they are on (13.1: check-in / evidence / check-out — the
    job's status says which); a customer's picture goes on their open
    repair for CS and the technician to see. Nowhere to put it → say so,
    and do not store it."""
    if ctx.resolution is not TenantResolution.SINGLE:
        return ChatReply(text=greet(ctx, language))
    license_id = str(ctx.license_id)
    ticket = None
    member_id = None
    photo_type = "evidence"
    try:
        if oa == "customer":
            rows = await client.list_tickets(license_id)
            mine = [
                t for t in rows
                if str(t.get("customer_chann_uid") or "") == ctx.chann_uid
                and str(t.get("status") or "") not in ("completed", "cancelled")
            ]
            ticket = mine[0] if mine else None
        else:
            member, ticket, _inferred = await _ticket_for_action(
                client, license_id, ctx, "", prefer_status=("in_progress", "assigned"),
            )
            member_id = str((member or {}).get("id") or "") or None
            if ticket and str(ticket.get("status") or "") == "in_progress":
                photo_type = "evidence"
            elif ticket:
                photo_type = "checkin"
    except Exception:
        log.exception("could not find a ticket for a picture")
        ticket = None
    if ticket is None:
        return ChatReply(text=_t(PHOTO_NO_JOB_CUSTOMER if oa == "customer" else PHOTO_NO_JOB, language))
    try:
        content, content_type = await get_message_content(oa, message_id)
        await store_ticket_photo(
            client, license_id=license_id, ticket_id=str(ticket["id"]), content=content,
            content_type=content_type, photo_type=photo_type, uploaded_by_member_id=member_id,
        )
        try:
            count = len([p for p in await client.list_ticket_photos(license_id, str(ticket["id"])) if p.get("photo_url")])
        except Exception:
            count = 1
    except PhotoRefused as exc:
        log.warning("picture refused: %s", exc)
        return ChatReply(text=_t(PHOTO_FAILED, language))
    except Exception:
        log.exception("picture could not be stored")
        return ChatReply(text=_t(PHOTO_FAILED, language))
    return ChatReply(
        text=_t(PHOTO_ATTACHED, language).format(code=ticket.get("ticket_number") or "", n=count),
        entity_type="service_ticket", entity_id=str(ticket.get("id") or ""),
    )


def _looks_like_a_question(text: str) -> bool:
    """Is this asking something rather than reporting something?

    Thai questions end in a particle or contain a question word; a fault
    report is a statement. Not perfect — "แอร์เสียไหม" is a question that
    is also about a fault — but a question about a fault is still not a
    request to open a second job.
    """
    lowered = re.sub(r"(?:ครับ|ค่ะ|คะ|คับ|จ้า|นะ|[?!. ])+$", "", _canonical(text)).replace("ไหม้", "")
    return any(marker in lowered for marker in _QUESTION_MARKERS) or lowered.endswith(
        ("ยัง", "ไหม", "มั้ย", "ป่าว", "เปล่า", "หรือไม่", "อะไรบ้าง", "ใครบ้าง", "ไหนบ้าง", "หรอ", "เหรอ", "รึ", "มะ", "ป่ะ")
    )


def _is_only_a_greeting(text: str) -> bool:
    """True when the message is a greeting and nothing else.

    Length matters as much as the words. "สวัสดีครับ" is someone saying
    hello; "สวัสดีครับ แอร์เสียครับ" is someone being polite before
    reporting a fault, and treating the second as a greeting would discard
    the reason they wrote.
    """
    if not (text or "").strip():
        return True
    stripped = _canonical(text).rstrip("!?. ")
    if not stripped:
        return False
    greetings = sorted({g.replace(" ", "") for g in GREETING_PHRASES}, key=len, reverse=True)
    for form in _bare_forms(text) | {_compact(text)}:
        for greeting in greetings:
            if not form.startswith(greeting):
                continue
            # Whatever follows the greeting, minus the usual polite
            # particles and the person addressed, is the real message —
            # if anything is left, it is not just a greeting.
            rest = form[len(greeting):]
            for particle in ("ครับผม", "ครับ", "ค่ะ", "คะ", "คับ", "จ้า", "จ้ะ", "there", "พี่", "ผม", "ค้าบ", "ทุกคน"):
                rest = rest.replace(particle, "")
            if len(rest.strip()) < 3:
                return True
            # "สวัสดีครับ ขอสอบถามหน่อย": the second half is itself a greeting.
            if _normalise(rest) in _GREETING_TAILS or rest in _GREETING_TAILS:
                return True
    return False


_GREETING_TAILS = frozenset({"ขอสอบถาม", "สอบถาม", "ขอถาม", "ถาม", "รบกวน", "รบกวนสอบถาม", "มีเรื่องสอบถาม"})


CUSTOMER_REPORT_HINTS = (
    "เสีย", "ไม่ทำงาน", "พัง", "ซ่อม", "แจ้งซ่อม", "มีปัญหา", "ใช้ไม่ได้",
    "ไม่เย็น", "น้ำรั่ว", "รั่ว", "เสียงดัง", "broken", "not working", "repair",
)

REPORT_TAKEN = {
    # {machine} is the unit the fault is about, or a plain statement that
    # none is attached — a customer who was just asked which machine is
    # owed the answer either way (owner, 10 ก.ย. 2569).
    "th": "รับแจ้งแล้วครับ เลขงาน {code}\n\"{issue}\"{machine}\n\nขอที่อยู่ที่จะให้ช่างไปด้วยครับ",
    "en": "Logged as {code}.\n\"{issue}\"{machine}\n\nWhat address should the technician go to?",
}
REPORT_ADDRESS_SAVED = {
    "th": "บันทึกที่อยู่แล้วครับ\nสะดวกให้ช่างไปวันไหน เวลาไหนครับ",
    "en": "Address saved. When would suit you for the visit?",
}
REPORT_SCHEDULED = {
    "th": "นัดวันที่ {date}{time} แล้วครับ\nทางร้านจะจัดช่างและติดต่อกลับ",
    "en": "Booked for {date}{time}. The shop will assign a technician and get back to you.",
}
REPORT_STATUS_LINE = {
    "th": "{code} · {status}{when}",
    "en": "{code} · {status}{when}",
}
REPORT_NONE = {
    "th": "ยังไม่มีงานแจ้งซ่อมครับ พิมพ์อาการที่เสียมาได้เลย",
    "en": "No open jobs. Just describe the problem and I will log it.",
}

CUSTOMER_TICKET_TTL_S = 3600
CHECKOUT_DRAFT_TTL_S = 3600
REPORT_DRAFT_CANCEL_WORDS = frozenset({"ยกเลิก", "ยกเลิกรายงาน", "ยกเลิกปิดงาน", "ไม่ปิดงาน", "เลิก", "cancel", "ยกเลิกก่อน"})
REPORT_DRAFT_CANCELLED = {
    "th": "ยกเลิกการปิดงาน {code} แล้ว งานยังเปิดอยู่ พิมพ์ \"ปิดงาน\" เมื่อพร้อม",
    "en": "Closing {code} was cancelled; the job stays open. Type \"check out\" when ready.",
}


async def _drop_pending_quietly(client: DataClient, ctx: ResolvedContext) -> None:
    try:
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        log.exception("could not drop a pending intent")

# Asked in this order, one at a time. Same two fields the Data tier's gate
# requires — kept in step by test, not by hope.
REPORT_REQUIRED_FIELDS = (
    ("found_issue", "ปัญหาที่พบ"),
    ("work_done", "สิ่งที่แก้ไข"),
)

# Asked after the required ones, and skippable with "ไม่มี". Parts are
# what the shop bills for; a report that never asked leaves the office
# to phone the technician later, which is the friction check-out exists
# to remove. Kept separate from REQUIRED so the boundary test that pins
# the required list to the Data Tier's gate keeps meaning what it says.
REPORT_OPTIONAL_FIELDS = (
    ("parts_changed", "อะไหล่ที่เปลี่ยน"),
)

REPORT_QUESTIONS = {
    "found_issue": {
        "th": "พบปัญหาอะไรครับ",
        "en": "What did you find?",
    },
    "work_done": {
        "th": "แก้ไขอะไรไปบ้างครับ",
        "en": "What did you do about it?",
    },
    "parts_changed": {
        "th": "เปลี่ยนอะไหล่อะไรบ้างครับ (พิมพ์ \"ไม่มี\" ถ้าไม่ได้เปลี่ยน)",
        "en": 'Any parts replaced? ("none" if not)',
    },
}

# Answers that mean "nothing" for an optional question.
_NONE_ANSWERS = frozenset({"ไม่มี", "ไม่", "ไม่ได้เปลี่ยน", "none", "no", "-", "ไม่มีครับ", "ไม่มีค่ะ"})

CHECKOUT_STARTED = {
    "th": "ปิดงาน {code}",
    "en": "Closing {code}",
}
REPORT_DRAFT_RESUME = {
    "th": "กำลังปิดงาน {code} อยู่ครับ ตอบคำถามต่อได้เลย",
    "en": "Still closing {code} — carry on with the question.",
}
REPORT_DRAFT_SWITCHED = {
    "th": "เปลี่ยนเป็นปิดงาน {code} แล้วครับ คำตอบที่ให้ไว้ยังอยู่",
    "en": "Switched to closing {code}; the answers so far are kept.",
}
REPORT_DRAFT_HINT = {
    "th": "(พิมพ์ \"ยกเลิก\" ถ้ายังไม่ปิดงานตอนนี้)",
    "en": '(type "cancel" to stop closing the job for now)',
}
REPORT_ANSWER_IN_WORDS = {
    "th": "พิมพ์เป็นข้อความสั้น ๆ ครับ เช่น \"คอมเพรสเซอร์รั่ว\"",
    "en": 'Answer in words, e.g. "compressor leak".',
}
REPORT_ANSWER_REQUIRED = {
    "th": "ข้อนี้ต้องมีครับ รายงานที่ไม่มีคำตอบนี้ส่งไม่ได้",
    "en": "This one is required — the report cannot be filed without it.",
}
_SKIP_ANSWERS = frozenset({"ข้าม", "skip", "ไม่ระบุ", "-", "—", "ไม่มี", "ว่าง"})


# Phrases that mean "change my own details" rather than "something is
# broken". Kept narrow: anything not clearly a profile edit is treated as
# a fault report, because that is what a customer messaging a repair shop
# is overwhelmingly doing.
_PROFILE_EDIT_HINTS = (
    "แก้เบอร์", "เปลี่ยนเบอร์", "แก้ชื่อ", "เปลี่ยนชื่อ", "แก้อีเมล",
    "เปลี่ยนอีเมล", "แก้ที่อยู่ของฉัน", "ข้อมูลส่วนตัว", "โปรไฟล์",
    "my profile", "change my",
    # Review, 6 Sep 2026 (B3): "เบอร์ใหม่ 089-…", "แก้ที่อยู่เป็น 12/3 …" were
    # read as an address for a job that was not waiting for one.
    "เบอร์ใหม่", "เบอร์ฉันเปลี่ยน", "เปลี่ยนเบอร์เป็น", "แก้ที่อยู่เป็น", "แก้ที่อยู่", "เปลี่ยนที่อยู่", "ที่อยู่ใหม่",
    "อัปเดตเบอร์", "อัปเดตที่อยู่", "update my", "new phone", "new address", "new number",
)


def _looks_like_profile_edit(message: str) -> bool:
    lowered = _canonical(message)
    for form in _bare_forms(message):
        lowered += " " + form
    return any(hint in lowered for hint in _PROFILE_EDIT_HINTS)


async def _customer_line_is_a_job(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, ai_client=None,
) -> bool | None:
    """Does this line describe something the shop has to come and fix?

    True when the model reads it as a job, False when it reads it as
    anything else, None when the model could not be asked — and None means
    the old behaviour, because an outage must never stop a customer
    reporting a fault.
    """
    try:
        intent = await parse_intent(
            message=message, chann_uid=ctx.chann_uid, role=ctx.primary_role,
            license_id=str(license_id), permission_keys=list(permission_keys or []),
            language=language, client=ai_client, oa=ctx.oa,
        )
    except Exception:  # noqa: BLE001 — AINotConfigured, AIUnavailable, anything
        log.info("could not read a customer line; treating it as a report")
        return None
    entity = str(intent.get("entity") or "")
    action = str(intent.get("action") or "")
    if entity in ("ticket", "service_report") and action in ("create", "update"):
        return True
    if action != "suggest":
        # The model read it as something else — a status question
        # ("เช็คสถานะงานหน่อยค่ะ" -> read/ticket), who the technician is
        # ("ช่างชื่ออะไร" -> read/member), a reschedule. Those have their
        # own paths further down this handler; a veto here would answer a
        # perfectly clear question with a shrug. Only "the model has no
        # reading at all" is a reason to stop.
        return None
    return False


async def _handle_customer_report(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    language: str, serial_hint: str | None = None, skip_serial: bool = False,
    permission_keys: list[str] | None = None, ai_client=None,
) -> ChatReply:
    """A customer reporting a fault, or answering the follow-up questions.

    No permission check: a customer is not a tenant member and holds no
    permission keys at all. The tenant boundary is the shop they are linked
    to, which is what license_id already is here.

    serial_hint: the machine the fault is about, when the caller already
    knows it. skip_serial: the person said they have no serial — file the
    job without one rather than asking again.
    """
    license_id = str(license_id)
    text = (message or "").strip()

    # An in-flight report waiting for its address or its appointment. Held
    # in the same Redis slot as every other multi-turn exchange.
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        pending = None

    # Run before the pending-address/issue handlers: those slots must not
    # turn a refusal or a price question into an address or a new fault.
    if _asks_price(text) and not _strongly_address(text):
        return _customer_fallback(text, language)
    if _denies_repair_request(text) and not _is_cancel_hint(text):
        return ChatReply(text=_t(REPAIR_NOT_REQUESTED, language))
    if pending and pending.get("entity") == "customer_ticket" and _is_cancel_hint(text):
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=True,
        )

    # "แจ้งซ่อม" tapped: the next line is the fault, whatever it looks like
    # — and even while a live conversation is open (review, 6 Sep 2026:
    # the answer to "อาการเสียเป็นอย่างไรครับ" was stored as a chat line).
    forced_fault = False
    if pending and pending.get("entity") == "customer_ticket" and "issue" in (pending.get("missing") or []):
        prefix = str((pending.get("fields") or {}).get("prefix") or "").strip()
        await _drop_pending_quietly(client, ctx)
        pending = None
        if _is_customer_command(text) or _looks_like_a_question(text) or _is_only_a_greeting(text) or len(text) < 3:
            pass
        else:
            forced_fault = True
            if prefix and prefix.lower() not in text.lower():
                # "แอร์ Daikin" then "ไม่เย็น": the machine named first is
                # part of the fault.
                text = f"{prefix} {text}"

    # A fault held while we waited for the product (owner rule: register
    # first). What arrives now is the serial, "no serial", or a new
    # sentence — each continues the same report.
    if pending and pending.get("entity") == "pending_customer_message":
        held = str((pending.get("fields") or {}).get("message") or "")
        linked_serial = str((pending.get("fields") or {}).get("serial") or "")
        if held and text == held.strip():
            # Re-entered by registration right after the shop was linked:
            # the held sentence is being replayed, with the serial that
            # found the shop (if one did). Not a restatement.
            await _clear_customer_hold(client, ctx)
            return await _handle_customer_report(
                client, ctx=ctx, license_id=license_id, message=held,
                language=language, serial_hint=linked_serial or None,
            )
        if held and _matches_phrase(text, NO_SERIAL_PHRASES):
            await _clear_customer_hold(client, ctx)
            return await _handle_customer_report(
                client, ctx=ctx, license_id=license_id, message=held,
                language=language, skip_serial=True,
            )
        if held and _is_bare_serial(text):
            serial = text.upper()
            outcome, _row = await _claim_serial(client, ctx, license_id, serial)
            if outcome == "not_found":
                # The fault stays held; the serial was not a unit this
                # shop recorded, and inventing one is what the owner ruled
                # out (3 Sep). "ไม่มีหมายเลขเครื่อง" still files without.
                return ChatReply(
                    text=_t(SERIAL_NOT_AT_SHOP, language).format(serial=serial),
                    quick_replies=[
                        ("ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลขเครื่อง"), ("ติดต่อร้าน", "ติดต่อร้าน"),
                    ],
                )
            if outcome == "taken":
                return ChatReply(
                    text=_t(SERIAL_CLAIMED_BY_OTHER, language).format(serial=serial),
                    quick_replies=[("ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลขเครื่อง")],
                )
            await _clear_customer_hold(client, ctx)
            return await _handle_customer_report(
                client, ctx=ctx, license_id=license_id, message=held,
                language=language, serial_hint=serial,
            )
        if held and not _is_customer_command(text) and not _matches_phrase(text, HELP_TRIGGERS):
            # Not a serial, not a menu tap: most often the address or a
            # restatement typed before reading the question. The fault
            # is kept and the question asked once more — replacing the
            # held sentence with "99/1 ถ.สุขุมวิท" would file a repair
            # called "99/1 ถ.สุขุมวิท".
            return ChatReply(
                text=_t(REPORT_REGISTER_FIRST, language).format(issue=held[:60]),
                quick_replies=[("ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลขเครื่อง")],
            )
        # A menu tap while a fault is held: the person moved on. Drop the
        # hold so it cannot swallow the next thing they type.
        await _clear_customer_hold(client, ctx)
        pending = None
    elif _is_bare_serial(text) and not serial_hint:
        # A bare serial with nothing held is the product being registered
        # (the rich-menu tile, or the welcome's instruction) — not a fault
        # called "ABC123456".
        return await _handle_warranty_register(
            client, ctx=ctx, license_id=license_id,
            message=f"ลงทะเบียนสินค้า {text}", language=language,
        )

    # "แจ้งซ่อม อันใหม่" / "อยากแจ้งซ่อมพัดลม อีกอัน": a second report, not
    # an answer to the open question (owner transcript, 3 Sep 18:34 — the
    # sentence was saved as the address). The trigger word is stripped;
    # a placeholder like "อันใหม่" leaves nothing, so the symptoms are
    # asked for.
    if _starts_new_report(text):
        rest = _strip_report_trigger(text)
        if not rest or _is_report_placeholder(rest):
            return await _ask_for_issue(client, ctx, language)
        pending = None
        text = rest

    if pending and pending.get("entity") == "customer_ticket":
        ticket_id = (pending.get("fields") or {}).get("ticket_id")
        awaiting = pending.get("missing") or []

        if ticket_id and "address" in awaiting and _looks_like_phone(text):
            # A phone number is welcome — on the ticket's phone field, not
            # as the street (review, 6 Sep 2026: it was "not a serial we
            # know"). The address is still asked.
            try:
                await client.update_ticket(
                    license_id, str(ticket_id), {"customer_phone": re.sub(r"[\s-]", "", text)},
                    actor_id=ctx.chann_uid,
                )
            except Exception:
                log.exception("could not save a customer's phone")
            return ChatReply(text=_t(REPORT_PHONE_SAVED, language).format(phone=text))

        if ticket_id and "address" in awaiting and _matches_phrase(text, _SAME_ADDRESS_PHRASES):
            # "ที่อยู่เดิมครับ": the address on file, not those words
            # (review, 6 Sep 2026).
            previous = await _previous_customer_address(client, ctx, license_id, exclude_ticket_id=str(ticket_id))
            if not previous:
                return ChatReply(text=_t(REPORT_NO_PREVIOUS_ADDRESS, language))
            try:
                await client.update_ticket(
                    license_id, str(ticket_id), {"service_address": previous}, actor_id=ctx.chann_uid,
                )
                await client.set_pending_intent(
                    ctx.chann_uid, ctx.oa,
                    action="report", entity="customer_ticket",
                    fields={"ticket_id": ticket_id}, missing=["schedule"],
                    ttl_seconds=CUSTOMER_TICKET_TTL_S,
                )
            except Exception:
                log.exception("could not save a customer's address")
                return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
            return ChatReply(text=_t(REPORT_ADDRESS_REUSED, language).format(address=previous[:80]))

        if ticket_id and "address" in awaiting and not _strongly_address(text) and (
            _is_only_a_greeting(text)
            or (_looks_like_a_date_attempt(text) and not _looks_like_a_question(text))
            or any(w in _canonical(text) for w in ("ไม่สะดวก", "ยังไม่บอก", "ไม่อยากบอก", "not ready"))
        ):
            return ChatReply(text=_t(REPORT_ADDRESS_REQUIRED, language))

        if (
            ticket_id and "address" in awaiting
            and not _is_bare_serial(text)
            # A question is not an address. "ช่างจะมากี่โมง" was saved as
            # where the customer lives, and the address was never asked
            # for again.
            and not _looks_like_a_question(text)
            # Neither is a menu tile or a command: with a report waiting
            # for its address, tapping "สถานะการซ่อม" saved that as the
            # street (3 Sep sim). The prompt stays open; the tile is
            # answered as itself.
            and not _not_an_address(text)
            # A sentence about a fault with nothing address-like in it
            # is a new fault, not where they live.
            and not (_looks_like_fault(text) and not _looks_like_address(text))
            # "ใช้งานยังไง" and "ขอบคุณครับ" are not streets either (6 Sep sim:
            # both were saved as the address of an open job).
            and not _is_help_request(text, "customer")
            and not _is_small_talk(text)
            and len(text) >= 5
        ):
            try:
                await client.update_ticket(
                    license_id, str(ticket_id), {"service_address": text},
                    actor_id=ctx.chann_uid,
                )
                await client.set_pending_intent(
                    ctx.chann_uid, ctx.oa,
                    action="report", entity="customer_ticket",
                    fields={"ticket_id": ticket_id}, missing=["schedule"],
                    ttl_seconds=CUSTOMER_TICKET_TTL_S,
                )
            except Exception:
                log.exception("could not save a customer's address")
                return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
            return ChatReply(text=_t(REPORT_ADDRESS_SAVED, language))

        if ticket_id and "schedule" in awaiting:
            from .thai_datetime import (
                format_thai_date, format_thai_time, looks_like_a_time_attempt,
                parse_thai_date, parse_thai_time,
            )

            today = local_today()
            if _matches_phrase(text, _ANY_DAY_PHRASES) or any(
                p.replace(" ", "") in _normalise(text) for p in _ANY_DAY_PHRASES if len(p) > 5
            ):
                try:
                    await client.update_ticket(
                        license_id, str(ticket_id), {"customer_notes": "ลูกค้าสะดวกทุกวัน — ร้านเลือกวันได้"},
                        actor_id=ctx.chann_uid,
                    )
                except Exception:
                    log.exception("could not note the customer's flexibility")
                await _drop_pending_quietly(client, ctx)
                await _notify_ticket_change(
                    client, license_id, str(ticket_id),
                    "ลูกค้าสะดวกทุกวัน — เลือกวันนัดและยืนยันกับลูกค้าได้เลย", language,
                    text_en="The customer is free any day — pick a slot and confirm with them",
                )
                return ChatReply(text=_t(REPORT_ANY_DAY, language), quick_replies=[("ดูสถานะงาน", "งานของฉัน")])
            due_date = parse_thai_date(text, today)
            if due_date is None:
                # Not a date. If it does not even look like an attempt at
                # one, the person has moved on — a serial number, a
                # question — and holding the schedule prompt open swallowed
                # every message after it with "ไม่เข้าใจวันที่". Drop the
                # prompt and let the message be what it is; the schedule
                # can still be given later with "เลื่อนนัด".
                if not _looks_like_a_date_attempt(text):
                    try:
                        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
                    except Exception:
                        log.exception("could not drop a stale schedule prompt")
                    return await _handle_customer_report(  # re-enter, prompt gone
                        client, ctx=ctx, license_id=license_id, message=text,
                        language=language,
                    )
                return ChatReply(text=_t(CUSTOMER_NEEDS_DATE, language))
            if due_date < today:
                return ChatReply(
                    text=_t(AMEND_PAST_DATE, language).format(date=format_thai_date(due_date)),
                )
            # Owner rule 1: no time given means 09:00, and the reply says
            # so — the shop then sees a real appointment, not a date with
            # a blank next to it. A time that WAS given and could not be
            # read is a different thing: ask, rather than book an hour the
            # customer did not choose (review v3, B06). The schedule prompt
            # stays open, so saying it again is all they have to do.
            due_time = parse_thai_time(text)
            if due_time is None:
                if looks_like_a_time_attempt(text):
                    return ChatReply(text=_t(TIME_NOT_UNDERSTOOD, language))
                due_time = time(9, 0)
            fields: dict = {
                "scheduled_date": due_date.isoformat(),
                "scheduled_time": due_time.isoformat(),
            }
            try:
                await client.update_ticket(
                    license_id, str(ticket_id), fields, actor_id=ctx.chann_uid,
                )
                await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
            except Exception:
                log.exception("could not save a customer's appointment")
                return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

            if (pending.get("fields") or {}).get("reschedule"):
                # The new date answers "ช่างมาพรุ่งนี้ไม่ได้": a move the
                # shop must hear about, like any other reschedule.
                code = str((pending.get("fields") or {}).get("code") or "")
                when = f"{format_thai_date(due_date)} {format_thai_time(due_time)}"
                await _notify_ticket_change(
                    client, license_id, str(ticket_id), f"ลูกค้าเลื่อนนัด {code} เป็น {when}", language,
                    text_en=f"The customer moved job {code} to {when}",
                )
                return ChatReply(text=_t(AMEND_RESCHEDULED, language).format(
                    code=code, date=format_thai_date(due_date), time=f" {format_thai_time(due_time)}",
                ))
            return ChatReply(
                text=_t(REPORT_SCHEDULED, language).format(
                    date=format_thai_date(due_date),
                    time=f" {format_thai_time(due_time)}",
                )
            )

    # Changing or cancelling an appointment. A customer whose plans change
    # and who cannot say so will simply not be there when the technician
    # arrives — which costs the shop a visit and the customer their day.
    # Negation first: "ไม่เอาแล้วค่ะ ซ่อมเองได้แล้ว" is a cancel, not a
    # repair called that (review, 6 Sep 2026).
    explicit_cancel = _matches_phrase(message, CUSTOMER_CANCEL_PHRASES) or any(
        w in text.lower() for w in CUSTOMER_CANCEL_TRIGGERS
    )
    if explicit_cancel:
        # Only the explicit-trigger half. The _is_cancel_hint clause below
        # exists BECAUSE a customer often cancels by negating something
        # ("ไม่เอาแล้วค่ะ ซ่อมเองได้แล้ว"), and reading that as a refusal
        # would refuse the cancellation itself — so it stays outside the
        # guard, exactly as the note above says.
        held_cancel = _intent_guard_reply(
            message, action="ticket_cancel", language=language,
            triggers=CUSTOMER_CANCEL_TRIGGERS + CUSTOMER_CANCEL_PHRASES,
        )
        if held_cancel is not None:
            # A hold means "this is not the cancel branch" — it does not
            # mean the message has been answered. "ไม่ได้จะยกเลิกงาน ขอแค่
            # เลื่อนเป็นวันอาทิตย์" says both halves out loud: not a
            # cancellation, and a reschedule. Returning the refusal here
            # threw the second half away. Fall through and let the branches
            # below have it; the refusal is only the answer when nothing
            # else claims the sentence.
            if not (_is_reschedule_request(text) or _says_unavailable(text)):
                return held_cancel
            explicit_cancel = False
    if explicit_cancel or (
        _is_cancel_hint(text) and not _looks_like_a_question(text) and not forced_fault
    ):
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=True,
        )
    if _is_reschedule_request(text) and not forced_fault:
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=False,
        )
    if _says_unavailable(text) and not forced_fault:
        # "ช่างมาพรุ่งนี้ไม่ได้นะ": the date named is the one that does NOT
        # suit — ask for one that does; "พรุ่งนี้ไม่สะดวก ขอเป็นวันเสาร์"
        # names it, so that one is booked (review, 6 Sep 2026).
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=False, date_text=_new_date_part(text),
        )
    correction = _issue_correction(text)
    if correction and not forced_fault:
        # "ไม่ใช่ๆ ผมหมายถึงแอร์ห้องนอน": the fault on the open job, restated.
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=False, new_issue=correction,
        )
    if _matches_phrase(text, NO_SERIAL_PHRASES):
        # Nothing held (the phrase is the bot's own escape hatch): say so
        # instead of "not sure what you need" (review, 6 Sep 2026).
        return ChatReply(text=_t(NO_SERIAL_NOTHING_HELD, language), quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม")])
    if _staff_tile_text(text) is not None and not forced_fault:
        return ChatReply(
            text=_t(CUSTOMER_STAFF_TILE, language).format(text=_staff_tile_text(text)),
            quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน"), ("วิธีใช้", "วิธีใช้")],
        )

    # "งานของฉัน T-2026-0002": one job by code — what the choose-a-job
    # buttons send when a customer has several open. "งาน T-2026-0001" and
    # the bare code ask the same (review, 6 Sep 2026, B3).
    status_code = TICKET_CODE_RE.search(text)
    if status_code and (
        any(text.lower().startswith(p.lower()) for p in TICKET_MINE_PHRASES + CUSTOMER_STATUS_PHRASES)
        or not _looks_like_fault(TICKET_CODE_RE.sub("", text))
    ):
        try:
            tickets = await client.list_tickets(license_id)
        except Exception:
            log.exception("customer ticket lookup failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        wanted = status_code.group(1).upper()
        t = next(
            (x for x in tickets
             if x.get("customer_chann_uid") == ctx.chann_uid
             and str(x.get("ticket_number") or "").upper() == wanted),
            None,
        )
        if t is None:
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=wanted)
            )
        return ChatReply(
            text=_t(CUSTOMER_JOB_STATUS, language).format(
                code=t.get("ticket_number"),
                status=_label(TICKET_STATUS_LABELS, t.get("status"), language),
                when=_ticket_when(t) or "ยังไม่ได้นัด",
                tech=t.get("assigned_to_name") or "ยังไม่ได้มอบหมาย",
            ),
            quick_replies=[("ดูทุกงาน", "งานของฉัน")],
        )

    # "งานของฉัน" from a customer means their own reports, not a
    # technician's queue. "สถานะการซ่อม" is the rich-menu tile for the
    # same thing.
    if _matches_phrase(
        message, TICKET_MINE_PHRASES + TICKET_LIST_PHRASES + CUSTOMER_STATUS_PHRASES,
    ):
        try:
            tickets = await client.list_tickets(license_id)
        except Exception:
            log.exception("customer ticket list failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        mine = [t for t in tickets if t.get("customer_chann_uid") == ctx.chann_uid]
        if not mine:
            return ChatReply(
                text=_t(REPORT_NONE, language),
                quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม")],
            )
        lines = [
            _t(REPORT_STATUS_LINE, language).format(
                code=t.get("ticket_number"),
                status=_label(TICKET_STATUS_LABELS, t.get("status"), language),
                when=f" · {_ticket_when(t)}" if t.get("scheduled_date") else "",
            )
            for t in mine[:LIST_LIMIT]
        ]
        return ChatReply(text="\n".join(lines))

    # The bare tile / word: ask what is wrong rather than log "แจ้งซ่อม"
    # as the fault — and remember that the next line IS the fault. "ด่วน"
    # and "แอร์" on their own are the same request minus the symptom.
    if _matches_phrase(message, CUSTOMER_REPORT_BARE) or _normalise(text) in _URGENT_WORDS:
        return await _ask_for_issue(client, ctx, language)
    appliance = None if forced_fault else _names_appliance_only(text)
    if appliance:
        return await _ask_for_issue(client, ctx, language, prefix=text)

    # A greeting is not a fault report. "สวัสดี" came back as
    # 'รับเรื่องแล้วครับ: "สวัสดี"' — a repair job opened because someone
    # said hello, which is both wrong and slightly insulting.
    #
    # Answered with what this account can do, which is also the moment
    # someone is most likely to read it.
    if _is_only_a_greeting(text) or len(text) < 3:
        return ChatReply(
            text=_t(CUSTOMER_GREETING, language),
            quick_replies=[("ดูสถานะงาน", "งานของฉัน"), ("วิธีใช้", "วิธีใช้")],
        )

    # A question is not a fault report. "ช่างจะมากี่โมง" opened a second
    # repair job while the first was still waiting, because every message
    # that was not a command became a ticket. Someone asking about their
    # job gets their job; someone asking something the bot cannot answer
    # gets told the shop will, rather than a new ticket they never wanted.
    if not forced_fault and (_asks_price(text) or (_strongly_address(text) and not _looks_like_fault(text))):
        # A price question, or a bare address with no job waiting for one:
        # neither is a fault (review, 6 Sep 2026 — "ราคาล้างแอร์" and
        # "99/1 ถ.สุขุมวิท แขวงคลองตัน" both opened repair jobs).
        return _customer_fallback(text, language)
    if _looks_like_a_question(text) and _asks_about_warranty(text):
        # "เครื่องผมยังมีประกันไหม", "หมดประกันเมื่อไหร่" → their registered products.
        return await _handle_warranty_mine(
            client, ctx=ctx, license_id=license_id, language=language,
        )
    # "รบกวนช่างมาดูแอร์ให้หน่อยได้ไหมคะ" is a request for a visit, not a
    # question about the job already open (review, 6 Sep 2026, B5).
    service_request = _is_service_request_sentence(text)
    if _looks_like_a_question(text) and not service_request and not _asks_about_job(text) and not _looks_like_fault(text):
        # Anything else the bot cannot answer is offered to the shop, honestly.
        return _customer_fallback(text, language)
    if _looks_like_a_question(text) and not service_request:
        try:
            tickets = await client.list_tickets(license_id)
        except Exception:
            tickets = []
        mine = [
            t for t in tickets
            if t.get("customer_chann_uid") == ctx.chann_uid
            and str(t.get("status") or "") not in ("completed", "cancelled")
        ]
        if len(mine) > 1:
            # Two open jobs: answering about the first one silently would
            # be a guess. Buttons, one per job (rule 3).
            return ChatReply(
                text=_t(CUSTOMER_PICK_JOB, language).format(n=len(mine)),
                quick_replies=[
                    (str(t.get("ticket_number") or ""), f"งานของฉัน {t.get('ticket_number')}")
                    for t in mine[:4]
                ],
            )
        if mine:
            t = mine[0]
            when = _ticket_when(t)
            return ChatReply(
                text=_t(CUSTOMER_JOB_STATUS, language).format(
                    code=t.get("ticket_number"),
                    status=_label(TICKET_STATUS_LABELS, t.get("status"), language),
                    when=when or "ยังไม่ได้นัด",
                    tech=t.get("assigned_to_name") or "ยังไม่ได้มอบหมาย",
                ),
                quick_replies=[("ดูสถานะงาน", "งานของฉัน")],
            )
        # Nothing open, and the question was about a repair: answer about
        # repairs — "ยังไม่แน่ใจว่าต้องการอะไร" is a shrug at somebody who
        # asked something perfectly clear ("ซ่อมแอร์ใช้เวลากี่วัน",
        # "ยังไม่แน่ใจว่าแอร์เสียไหม"; review v3, no-ticket-008/009).
        guarded = _intent_guard_reply(text, action="ticket_open", language=language)
        if guarded is not None:
            return ChatReply(
                text=guarded.text,
                quick_replies=[
                    ("แจ้งซ่อม", "แจ้งซ่อม"),
                    ("คุยกับร้าน", f"คุยกับร้าน {text.strip()}"[:300]),
                    ("งานของฉัน", "งานของฉัน"),
                ],
            )
        return _customer_fallback(text, language)

    # Only a sentence that ASKS for a repair opens one. "อย่าเพิ่งเปิดงานซ่อม",
    # "ถ้าแอร์เสียจะมาแจ้งอีกที", "แอร์ซ่อมแล้ว ใช้ได้ปกติ" and "ทดสอบระบบ
    # คำว่า แอร์เสีย" all contain a fault word and none of them is a
    # request; all eight opened a real ticket (review v3, B04). Placed
    # here, at the one point a customer ticket is created, so the
    # address/appointment questions above still read their own answers —
    # and before the fallback below, so "ซ่อมแอร์ใช้เวลากี่วัน" is answered
    # about repairs rather than met with "not sure what you want".
    #
    # Not over a sentence _is_service_request_sentence already reads as a
    # booking: "ช่างว่างมาดูวันเสาร์ไหมครับ" is shaped like a question and
    # is how people ask for a visit (B5, 6 Sep 2026). That rule is the
    # narrower one — it wants a request head or "มาดู"/"มาซ่อม" — so where
    # the two disagree it wins.
    guarded = None if service_request else _intent_guard_reply(
        text, action="ticket_open", language=language,
    )
    if guarded is not None:
        return guarded
    # Only a fault or a request for a visit opens a job. Everything else
    # typed here used to become a ticket called, say, "ใช้งานยังไง".
    if not (forced_fault or serial_hint or skip_serial or _looks_like_fault(text) or _looks_like_service_request(text)):
        return _customer_fallback(text, language)
    # docs/MODEL_FIRST.md — where the words alone said "job", the sentence
    # is READ before one is opened.
    #
    # _looks_like_service_request matches "ช่าง" anywhere, so "ขอบคุณมาก
    # ครับ ช่างทำงานดีมาก" and "ช่างมาตรงเวลาดีค่ะ" — thanking the
    # technician — asked the customer to register a machine and file a
    # repair (10 ก.ย. 2569). Asked directly, the model answers "suggest"
    # for all four praise forms and "create ticket" for all three real
    # requests, including "อยากล้างแอร์".
    #
    # Only where the service-request words are the ONLY signal: a fault
    # marker, a serial or an in-flight report is never second-guessed, and
    # the model being unavailable means the old behaviour, because an
    # outage must never stop somebody reporting a fault.
    # An appliance named in the sentence is a positive signal, like a fault
    # marker: "มีช่างมาติดตั้งแอร์ให้ไหม" is somebody asking the shop to
    # come, and the model reads it as a plain question. The markers stay
    # first; the model only decides where they say nothing.
    names_a_thing = any(w.replace(" ", "") in _normalise(text) for w in _APPLIANCE_WORDS)
    if _looks_like_service_request(text) and not (
        forced_fault or serial_hint or skip_serial or _looks_like_fault(text) or names_a_thing
    ):
        read = await _customer_line_is_a_job(
            client, ctx=ctx, license_id=license_id, message=text,
            permission_keys=list(permission_keys or []), language=language,
            ai_client=ai_client,
        )
        if read is False:
            return _customer_fallback(text, language)
    if _is_only_a_greeting(text) or _is_small_talk(text):
        return _customer_fallback(text, language)

    # Owner rule (3 Sep): a fault is reported against a registered
    # product, so the shop knows which machine. The serial comes from the
    # message that linked the shop (held in the pending slot), from the one
    # product this customer has on file, from a choice when they have
    # several, or is asked for — with "ไม่มีหมายเลขเครื่อง" as the way out.
    held_serial = ""
    if pending and pending.get("entity") == "pending_customer_message":
        held_serial = str((pending.get("fields") or {}).get("serial") or "")
    serial = held_serial or (serial_hint or "")
    if not serial and not skip_serial:
        try:
            registered = [
                w for w in await client.list_warranties(
                    license_id, customer_chann_uid=ctx.chann_uid,
                )
                if str(w.get("status") or "") != "void" and w.get("serial_number")
            ]
        except Exception:
            log.exception("could not read a customer's registered products")
            registered = []
        if len(registered) == 1:
            serial = str(registered[0]["serial_number"])
        elif len(registered) > 1:
            await _hold_customer_message(client, ctx, text)
            return ChatReply(
                text=_t(REPORT_WHICH_PRODUCT, language),
                quick_replies=[
                    (
                        f"{w.get('product_name') or ''} {w['serial_number']}".strip()[:20],
                        str(w["serial_number"]),
                    )
                    for w in registered[:4]
                ] + [("ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลขเครื่อง")],
            )
        else:
            await _hold_customer_message(client, ctx, text)
            return ChatReply(
                text=_t(REPORT_REGISTER_FIRST, language).format(issue=text[:60]),
                quick_replies=[("ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลขเครื่อง")],
            )

    try:
        profile = await client.get_profile(ctx.chann_uid)
    except Exception:
        profile = None

    # Owner, 10 ก.ย. 2569: the fault is tied to the unit the customer
    # registered. The serial was already established above; the warranty
    # behind it carries the catalogue row and the real cover dates, so the
    # ticket links to the product and the technician is told whether the
    # visit is warranty work before setting off.
    warranty = await ticket_machine.warranty_for_serial(client, license_id, serial) if serial else None
    contact_id = await _contact_id_of(client, license_id, ctx.chann_uid)

    try:
        ticket = await client.create_ticket(
            license_id,
            {
                "issue_description": text,
                "customer_chann_uid": ctx.chann_uid,
                # Prefilled from the profile where it exists — a customer
                # who registered once should not retype their phone number
                # every time something breaks.
                "customer_name": " ".join(
                    p for p in (
                        (profile or {}).get("first_name"), (profile or {}).get("last_name"),
                    ) if p
                ) or ctx.display_name,
                "customer_phone": (profile or {}).get("phone"),
                **({"contact_id": contact_id} if contact_id else {}),
                **ticket_machine.link_fields(warranty, serial),
            },
            actor_id=ctx.chann_uid,
        )
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa,
            action="report", entity="customer_ticket",
            fields={"ticket_id": str(ticket["id"])}, missing=["address"],
            ttl_seconds=CUSTOMER_TICKET_TTL_S,
        )
    except Exception:
        log.exception("customer fault report failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    # The shop finds out NOW. It used to hear only once the customer had
    # answered the address and appointment questions — a report abandoned
    # after the first line sat unseen for good (review, 6 Sep 2026).
    await _notify_new_ticket(client, license_id, str(ticket["id"]), language)

    return ChatReply(
        text=_t(REPORT_TAKEN, language).format(
            code=ticket.get("ticket_number"), issue=text[:80],
            machine=ticket_machine.machine_suffix(
                ticket_machine.attach([ticket], _by_serial(warranty))[0], language,
            ),
        ),
        entity_type="service_ticket", entity_id=str(ticket.get("id") or ""),
    )


AMEND_PAST_DATE = {
    "th": "วันที่ {date} ผ่านมาแล้วครับ นัดได้ตั้งแต่วันนี้เป็นต้นไป ลองพิมพ์ใหม่ เช่น \"เลื่อนนัด พรุ่งนี้ 10 โมง\"",
    "en": "{date} is in the past. Pick today or later, e.g. \"reschedule tomorrow 10am\".",
}
CUSTOMER_CANCEL_PHRASES = ("ยกเลิก", "ไม่เอาแล้ว", "ไม่ซ่อมแล้ว", "ยกเลิกการซ่อม", "cancel")
CUSTOMER_CANCEL_TRIGGERS = ("ยกเลิกงาน", "ยกเลิกนัด", "cancel job")
CUSTOMER_RESCHEDULE_TRIGGERS = ("เลื่อนนัด", "ขอเลื่อน", "เปลี่ยนวัน", "เปลี่ยนเวลา", "reschedule")

AMEND_NO_OPEN_JOB = {
    "th": "ไม่มีงานที่นัดไว้อยู่ครับ",
    "en": "You have no scheduled job right now.",
}
AMEND_PICK_ONE = {
    "th": "มีงานอยู่หลายรายการ ระบุเลขงานด้วยครับ เช่น \"เลื่อนนัด T-2026-0001 วันศุกร์\"",
    "en": "You have several jobs — include the number.",
}
AMEND_CANCEL_CONFIRM = {
    "th": "ยกเลิกงาน {code} (นัด {when}) ใช่ไหมครับ กด \"ยืนยันยกเลิก\" เพื่อยืนยัน",
    "en": "Cancel job {code} (scheduled {when})? Tap \"confirm\" to confirm.",
}
AMEND_CANCELLED = {
    "th": "ยกเลิกงาน {code} แล้วครับ ทางร้านจะรับทราบ",
    "en": "Cancelled {code}. The shop has been told.",
}
AMEND_RESCHEDULED = {
    "th": "เลื่อนนัด {code} เป็นวันที่ {date}{time} แล้วครับ",
    "en": "Moved {code} to {date}{time}.",
}
AMEND_ALREADY_DONE = {
    "th": "งาน {code} ปิดไปแล้ว แก้ไขไม่ได้ครับ ถ้ามีปัญหาเพิ่มเติมแจ้งใหม่ได้เลย",
    "en": "{code} is already closed. Report a new fault if something is still wrong.",
}


# "ไม่ใช่ๆ ผมหมายถึงแอร์ห้องนอน", "ผิด ที่จริงเป็นตู้เย็น": the fault on the
# open job, restated (review, 6 Sep 2026, B13/correction).
_CORRECTION_RE = re.compile(
    r"^(?:ไม่ใช่ๆ?|ไม่ๆ|ผิด|ผิดแล้ว|ไม่ใช่นะ|ขอแก้|แก้หน่อย|no,?|wrong,?)\s*(?:ครับ|ค่ะ|คะ)?\s*(?:ฉัน|ผม|ดิฉัน)?\s*"
    r"(?:หมายถึง|ที่จริง|จริงๆ|จริงๆแล้ว|แก้เป็น|เป็น|i mean|actually|it's|its)\s*(.+)$",
    re.IGNORECASE,
)


def _issue_correction(text: str) -> str | None:
    m = _CORRECTION_RE.match((text or "").strip())
    if not m:
        return None
    issue = m.group(1).strip(" .!")
    return issue if len(issue) >= 2 and not TICKET_CODE_RE.search(issue) else None


AMEND_ISSUE_UPDATED = {
    "th": "แก้อาการของงาน {code} เป็น \"{issue}\" แล้วครับ",
    "en": "Updated the fault on {code} to \"{issue}\".",
}


async def _handle_customer_amend(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, message: str,
    language: str, cancel: bool, date_text: str | None = None, new_issue: str | None = None,
) -> ChatReply:
    """A customer moving or cancelling their own appointment — or restating
    the fault. `date_text`, when given, is the only part of the message
    the date is read from ("พรุ่งนี้ไม่สะดวก ขอเป็นวันเสาร์" names two
    days and only the second is wanted)."""
    from .thai_datetime import (
        format_thai_date, format_thai_time, looks_like_a_time_attempt,
        parse_thai_date, parse_thai_time,
    )

    try:
        tickets = await client.list_tickets(license_id)
    except Exception:
        log.exception("could not read a customer's tickets")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    mine = [
        t for t in tickets
        if t.get("customer_chann_uid") == ctx.chann_uid
        and str(t.get("status")) not in ("completed", "cancelled")
    ]

    match = TICKET_CODE_RE.search(message or "")
    if match:
        code = match.group(1).upper()
        ticket = next(
            (t for t in mine if str(t.get("ticket_number", "")).upper() == code), None,
        )
        if ticket is None:
            # Might exist but be finished — worth saying, since "not found"
            # for a job they remember reporting is confusing.
            closed = next(
                (t for t in tickets
                 if str(t.get("ticket_number", "")).upper() == code
                 and t.get("customer_chann_uid") == ctx.chann_uid), None,
            )
            if closed:
                return ChatReply(text=_t(AMEND_ALREADY_DONE, language).format(code=code))
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code)
            )
    elif len(mine) == 1:
        ticket = mine[0]
    elif not mine:
        return ChatReply(text=_t(AMEND_NO_OPEN_JOB, language))
    else:
        # The candidates are in hand: one button per job, re-sending the
        # same sentence with the code added (rule 3 — never make someone
        # type a code they have to go and look up).
        return ChatReply(
            text=_t(AMEND_PICK_ONE, language),
            quick_replies=[
                (str(t.get("ticket_number") or ""), f"{message} {t.get('ticket_number')}")
                for t in mine[:4]
            ],
        )

    code = str(ticket.get("ticket_number") or "")
    ticket_id = str(ticket.get("id") or "")

    if new_issue:
        try:
            await client.update_ticket(license_id, ticket_id, {"issue_description": new_issue[:400]}, actor_id=ctx.chann_uid)
        except Exception:
            log.exception("could not restate a customer's fault")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        await _notify_ticket_change(
            client, license_id, ticket_id, f"ลูกค้าแก้อาการงาน {code} เป็น \"{new_issue[:80]}\"", language,
            text_en=f"The customer restated the fault on {code}: \"{new_issue[:80]}\"",
        )
        return ChatReply(
            text=_t(AMEND_ISSUE_UPDATED, language).format(code=code, issue=new_issue[:80]),
            quick_replies=[("ดูสถานะงาน", "งานของฉัน")],
        )

    if cancel:
        if "ยืนยัน" not in (message or "") and "confirm" not in (message or "").lower():
            # Asked once. "ยกเลิก" typed in passing while a technician is
            # already driving over is too costly to act on unconfirmed.
            return ChatReply(
                text=_t(AMEND_CANCEL_CONFIRM, language).format(code=code, when=_ticket_when(ticket) or "-"),
                quick_replies=[
                    ("ยืนยันยกเลิก", f"ยืนยันยกเลิกงาน {code}"), ("ไม่ยกเลิก", "งานของฉัน"),
                ],
            )
        try:
            await client.set_ticket_status(
                license_id, ticket_id, "cancelled", actor_id=ctx.chann_uid,
            )
        except Exception:
            log.exception("customer cancellation failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        # The address/appointment prompt for the job just cancelled goes
        # with it — otherwise the next line was written onto a cancelled
        # job (review, 6 Sep 2026).
        try:
            pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
        except Exception:
            pending = None
        if pending and pending.get("entity") == "customer_ticket" and str(
            (pending.get("fields") or {}).get("ticket_id") or ""
        ) in ("", ticket_id):
            await _drop_pending_quietly(client, ctx)
        # The shop finds out now. A cancellation nobody is told about is a
        # technician driving to an empty house.
        await _notify_ticket_change(
            client, license_id, ticket_id,
            f"ลูกค้ายกเลิกงาน {code}", language, text_en=f"The customer cancelled job {code}",
        )
        return ChatReply(text=_t(AMEND_CANCELLED, language).format(code=code))

    today = local_today()
    source = message if date_text is None else date_text
    due_date = parse_thai_date(source, today) if source else None
    if due_date is None:
        if source and _looks_like_a_date_attempt(source):
            return ChatReply(text=_t(CUSTOMER_NEEDS_DATE, language))
        # "เลื่อนนัด" / "ช่างมาพรุ่งนี้ไม่ได้" with no new date: ask for
        # one and hold the job, rather than lecture about date formats.
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="report", entity="customer_ticket",
                fields={"ticket_id": ticket_id, "code": code, "reschedule": 1},
                missing=["schedule"], ttl_seconds=CUSTOMER_TICKET_TTL_S,
            )
        except Exception:
            log.exception("could not hold a reschedule prompt")
        return ChatReply(text=_t(AMEND_ASK_NEW_DATE, language))
    # The same guard reminders have had since Phase 6: a two-digit year or
    # a typo must not move a visit into the past without anyone noticing.
    if due_date < today:
        return ChatReply(
            text=_t(AMEND_PAST_DATE, language).format(date=format_thai_date(due_date)),
        )
    # Owner rule 1: unspecified time is 09:00, and the reply echoes it.
    # A time that was given but could not be read is asked again instead:
    # the prompt is held open so the answer lands back on this job.
    due_time = parse_thai_time(source)
    if due_time is None:
        if looks_like_a_time_attempt(source):
            try:
                await client.set_pending_intent(
                    ctx.chann_uid, ctx.oa, action="report", entity="customer_ticket",
                    fields={"ticket_id": ticket_id, "code": code, "reschedule": 1},
                    missing=["schedule"], ttl_seconds=CUSTOMER_TICKET_TTL_S,
                )
            except Exception:
                log.exception("could not hold a reschedule prompt")
            return ChatReply(text=_t(TIME_NOT_UNDERSTOOD, language))
        due_time = time(9, 0)
    fields: dict = {
        "scheduled_date": due_date.isoformat(),
        "scheduled_time": due_time.isoformat(),
    }
    try:
        await client.update_ticket(license_id, ticket_id, fields, actor_id=ctx.chann_uid)
    except Exception:
        log.exception("customer reschedule failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    when = f"{format_thai_date(due_date)} {format_thai_time(due_time)}"
    await _notify_ticket_change(
        client, license_id, ticket_id, f"ลูกค้าเลื่อนนัด {code} เป็น {when}", language,
        text_en=f"The customer moved job {code} to {when}",
    )
    return ChatReply(
        text=_t(AMEND_RESCHEDULED, language).format(
            code=code, date=format_thai_date(due_date),
            time=f" {format_thai_time(due_time)}",
        )
    )


async def _notify_ticket_change(
    client: DataClient, license_id: str, ticket_id: str, text: str, language: str,
    text_en: str | None = None, customer_text: str | None = None, customer_text_en: str | None = None,
) -> None:
    """Tell the shop, and the assigned technician, that a job changed.

    The technician especially: a cancellation they are not told about is a
    drive to an empty house.
    """
    try:
        ticket = await client.get_ticket(license_id, ticket_id)
        members = await client.list_members(license_id)
    except Exception:
        log.exception("could not announce a ticket change")
        return

    # The dispatchers — whoever holds ticket.assign, the same set a new
    # ticket goes to — not just the owner/admin role names: the CS the
    # message says the job "went back to" was never told (review, 6 Sep
    # 2026).
    targets = {str(m.get("chann_uid")) for m in await _dispatchers(client, license_id, members)} | {
        str(m.get("chann_uid"))
        for m in members
        if str(m.get("role") or "").lower() in ("owner", "admin")
        and str(m.get("status") or "active") == "active"
    }
    assignee_ref = str((ticket or {}).get("assigned_to_ref") or "")
    assignee_uids: set[str] = set()
    for member in members:
        if str(member.get("id")) == assignee_ref and member.get("chann_uid"):
            assignee_uids.add(str(member["chann_uid"]))
    targets |= assignee_uids

    for chann_uid in targets:
        if not chann_uid:
            continue
        try:
            line_target = await client.line_target_of(chann_uid)
            await send_notification(
                client,
                license_id=license_id,
                target_chann_uid=chann_uid,
                target_line_user_id=line_target,
                type="ticket_changed",
                message=text,
                message_en=text_en,
                entity_type="service_ticket",
                entity_id=ticket_id,
                # The technician works in the technician OA and may never
                # have added the sales one; a cancellation pushed there
                # failed silently (review, 6 Sep 2026).
                oa="technician" if chann_uid in assignee_uids else "sales",
            )
        except Exception:
            log.exception("could not notify %s about a ticket change", chann_uid)
    if customer_text and ticket:
        await _notify_customer(client, ticket, customer_text, customer_text_en)


async def _notify_customer(client: DataClient, ticket: dict, text: str, text_en: str | None = None) -> None:
    """A line to the customer on the customer OA, in their language.

    Until 6 Sep 2026 a customer heard nothing between filing a fault and
    the satisfaction survey — not the assignment, not the technician's
    acceptance, not a cancellation by the shop. Best-effort, never raises.
    """
    uid = str((ticket or {}).get("customer_chann_uid") or "")
    if not uid:
        return
    try:
        line_uid = await client.line_target_of(uid)
        if not line_uid:
            return
        language = "th"
        try:
            prefs = await client.get_display_preferences(uid) or {}
            language = str(prefs.get("language") or "th")
        except Exception:  # noqa: BLE001
            pass
        await _notify_mod.push_text("customer", line_uid, text_en if (language == "en" and text_en) else text)
    except Exception:  # noqa: BLE001
        log.warning("could not tell the customer about ticket %s", (ticket or {}).get("ticket_number"))


# ------------------------------------------------- the job is finished (owner, 10 Sep 2026)
#
# "พองาน ticket เสร็จแล้วไม่มีแจ้งไปหาลูกค้า" — the customer heard nothing when
# the work on their job was done. The only message that ever said so was the
# satisfaction survey, which goes out when the LAST approval step passes: so a
# customer waited days while a report sat in a queue, heard nothing at all if
# the report was sent back, and heard nothing ever if the shop never approved.
#
# These two moments differ from "the technician is on the way" in a way that
# decides how they are sent: they are things a customer may need to point back
# at later, so the row is written FIRST and the LINE push is attempted second
# (notify.py's rule) — a LINE outage must not erase the fact that the shop
# said the job was finished. The running commentary of a visit keeps using
# `_notify_customer` above, which pushes and keeps no row.

TICKET_COMPLETED_TYPE = "ticket_completed"
TICKET_REOPENED_TYPE = "ticket_reopened"

# No PDF link here on purpose: at this moment the report is `submitted`, not
# approved, and a document the shop has not checked yet is not the customer's
# to hold (13.5 issues it on approval).
TICKET_COMPLETED_CUSTOMER = {
    "th": "งาน {code} ช่างทำเสร็จแล้วครับ{work}\nทางร้านกำลังตรวจรายงาน เสร็จแล้วจะส่งแบบประเมินให้ครับ",
    "en": "Job {code} — the technician has finished the work.{work}\nThe shop is checking the report and will send you a short rating request.",
}
TICKET_COMPLETED_WORK_LINE = {
    "th": "\nสิ่งที่ทำ: {work}",
    "en": "\nWhat was done: {work}",
}
# Told "finished", then not finished after all. Saying nothing would leave the
# customer holding a promise the shop has withdrawn — and they may need to be
# at home again. What the shop rejected is between the shop and its
# technician; what the customer needs is that the visit is not over.
TICKET_REOPENED_CUSTOMER = {
    "th": "งาน {code} ที่แจ้งว่าเสร็จแล้ว ทางร้านตรวจงานแล้วขอให้ช่างกลับไปดูอีกครั้งครับ ทางร้านจะติดต่อนัดหมายกับคุณอีกที ขออภัยในความไม่สะดวก",
    "en": "Job {code}, which we told you was finished, is going back to the technician after the shop's review. The shop will contact you about the next visit. Sorry for the inconvenience.",
}


async def _notify_customer_recorded(
    client: DataClient, license_id, ticket: dict, *, type: str, text: str, text_en: str,
) -> dict | None:
    """Record, then push, on the customer OA — never raises.

    A walk-in ticket has no customer identity at all, and `notifications`
    keys on one: nothing is written and nothing is raised, because a shop
    must still be able to finish a job for someone who never used LINE.
    """
    uid = str((ticket or {}).get("customer_chann_uid") or "")
    if not uid:
        return None
    try:
        line_uid = await client.line_target_of(uid)
        # send_notification reads the RECIPIENT's language preference
        # because message_en is supplied (principle 7), pushes on the
        # customer OA, and swallows a LINE failure with the row kept.
        return await send_notification(
            client,
            license_id=str(license_id),
            target_chann_uid=uid,
            target_line_user_id=line_uid,
            type=type,
            message=text,
            message_en=text_en,
            entity_type="service_ticket",
            entity_id=str((ticket or {}).get("id") or ""),
            oa="customer",
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "could not tell the customer %s about ticket %s",
            type, (ticket or {}).get("ticket_number"),
        )
        return None


async def _ticket_of_report(client: DataClient, license_id, report: dict) -> dict:
    ticket_id = str((report or {}).get("ticket_id") or "")
    if not ticket_id:
        return {}
    try:
        return await client.get_ticket(str(license_id), ticket_id) or {}
    except Exception:  # noqa: BLE001
        log.exception("could not load the ticket behind report %s", (report or {}).get("report_id"))
        return {}


async def announce_job_finished(
    client: DataClient, license_id, report: dict, ticket: dict | None = None,
) -> dict | None:
    """The customer hears that the work on their job is done.

    Sent at the moment the ticket becomes `completed` — the technician's
    check-out — not when an approval step passes later. The summary comes
    from the report's own `work_done` (the "แก้:" answer the technician
    already typed); nothing new is invented and no field was added.

    Sent exactly once per completion because check-out is the one
    transition that produces it: the Data Tier refuses a second check-out
    of a completed ticket (`phase13.check_out`), so there is no second
    event to send a second message for.
    """
    ticket = ticket if ticket is not None else await _ticket_of_report(client, license_id, report)
    code = str((ticket or {}).get("ticket_number") or "")
    if not code:
        return None
    work = str(((report or {}).get("report_data") or {}).get("work_done") or "").strip()
    return await _notify_customer_recorded(
        client, license_id, ticket, type=TICKET_COMPLETED_TYPE,
        text=TICKET_COMPLETED_CUSTOMER["th"].format(
            code=code,
            work=TICKET_COMPLETED_WORK_LINE["th"].format(work=work[:160]) if work else "",
        ),
        text_en=TICKET_COMPLETED_CUSTOMER["en"].format(
            code=code,
            work=TICKET_COMPLETED_WORK_LINE["en"].format(work=work[:160]) if work else "",
        ),
    )


async def announce_job_reopened(
    client: DataClient, license_id, report: dict, ticket: dict | None = None,
) -> dict | None:
    """The "finished" we sent is withdrawn: the report was sent back and
    the Data Tier put the ticket back to `in_progress` (phase14.act)."""
    ticket = ticket if ticket is not None else await _ticket_of_report(client, license_id, report)
    code = str((ticket or {}).get("ticket_number") or "")
    if not code:
        return None
    return await _notify_customer_recorded(
        client, license_id, ticket, type=TICKET_REOPENED_TYPE,
        text=TICKET_REOPENED_CUSTOMER["th"].format(code=code),
        text_en=TICKET_REOPENED_CUSTOMER["en"].format(code=code),
    )


async def after_check_out(client: DataClient, license_id, report: dict, language: str = "th") -> None:
    """Everything that follows a committed check-out, for BOTH surfaces.

    Chat and the technician home screen each closed a visit their own way
    and each remembered their own follow-up work; the customer notice
    would have had to be added twice and could drift. One hook, called
    from both, is what keeps them the same (Master Spec 14.6's rule for
    the approval executor, applied to check-out).

    Each part is guarded separately: the check-out is already committed,
    and a technician standing in a customer's house must not be told their
    work failed because a message could not be composed.
    """
    await _after_report_submitted(client, license_id, report, language)
    try:
        await announce_job_finished(client, license_id, report)
    except Exception:  # noqa: BLE001
        log.exception("could not tell the customer about check-out %s", (report or {}).get("report_id"))


async def _dispatchers(client: DataClient, license_id: str, members: list[dict]) -> list[dict]:
    """The people who dispatch: anyone holding ticket.assign — by
    permission, not by role name (spec §4). Owners and admins remain the
    fallback when the permission lookup is unavailable."""
    out = []
    for m in members:
        if str(m.get("status") or "active") != "active" or not m.get("chann_uid"):
            continue
        try:
            context = await client.authorization_context(license_id, str(m["chann_uid"]))
        except Exception:  # noqa: BLE001
            context = None
        if context is None:
            if str(m.get("role") or "").lower() in ("owner", "admin"):
                out.append(m)
            continue
        if "ticket.assign" in set(context.get("permission_keys") or []):
            out.append(m)
    return out


async def _notify_new_ticket(
    client: DataClient, license_id: str, ticket_id: str, language: str,
) -> None:
    """Tell the shop a job has come in.

    Without this a ticket sits in the database until somebody opens the
    dashboard for unrelated reasons — which for a fault report means the
    customer is waiting and nobody knows.

    Best-effort: a notification failure must not undo a report the
    customer has already been told was accepted.
    """
    try:
        ticket = await client.get_ticket(license_id, ticket_id)
        if ticket is None:
            return
        members = await client.list_members(license_id)
    except Exception:
        log.exception("could not notify anyone about ticket %s", ticket_id)
        return

    # The people whose job it is to dispatch — CS included, which the old
    # owner/admin role test left out (review, 6 Sep 2026). Notifying every
    # member would put a customer's address in front of technicians who
    # have not been given the job.
    targets = await _dispatchers(client, license_id, members)
    when = _ticket_when(ticket)
    # The machine, so whoever dispatches knows what is going out and
    # whether it is warranty work before they price the visit.
    ticket = (await ticket_machine.attach_to(client, license_id, [ticket]))[0]
    machine_th = ticket_machine.machine_line(ticket, "th")
    machine_en = ticket_machine.machine_line(ticket, "en")
    text = (
        f"แจ้งซ่อมใหม่ {ticket.get('ticket_number')}\n"
        f"{ticket.get('customer_name') or '—'}\n"
        f"{ticket.get('issue_description') or ''}\n"
        + (f"{machine_th}\n" if machine_th else "")
        + f"ที่อยู่: {ticket.get('service_address') or 'รอลูกค้าแจ้ง'} · นัด: {when or 'รอลูกค้าแจ้ง'}"
    )
    text_en = (
        f"New repair request {ticket.get('ticket_number')}\n"
        f"{ticket.get('customer_name') or '—'}\n"
        f"{ticket.get('issue_description') or ''}\n"
        + (f"{machine_en}\n" if machine_en else "")
        + f"Address: {ticket.get('service_address') or 'pending'} · Appointment: {when or 'pending'}"
    )
    for member in targets:
        chann_uid = str(member.get("chann_uid") or "")
        if not chann_uid:
            continue
        try:
            line_target = await client.line_target_of(chann_uid)
            await send_notification(
                client,
                license_id=license_id,
                target_chann_uid=chann_uid,
                target_line_user_id=line_target,
                type="ticket_created",
                message=text,
                message_en=text_en,
                entity_type="service_ticket",
                entity_id=ticket_id,
                oa="sales",
            )
        except Exception:
            log.exception("could not notify %s about a new ticket", chann_uid)


# ------------------------------------------------ Phase 13 field service

CHECKIN_TRIGGERS = (
    "เช็คอิน", "เช็กอิน", "ถึงหน้างาน", "ถึงแล้ว", "มาถึงแล้ว", "เริ่มงาน", "ถึงบ้านลูกค้า", "อยู่หน้างาน",
    "เริ่มทำงาน", "ถึงที่หมาย", "check in", "checkin", "arrived",
    # Review, 6 Sep 2026 (B9): the spellings and the sentences.
    "ถึงงาน", "อยู่หน้าบ้าน", "หน้าบ้านลูกค้า", "ถึงบ้าน", "เริ่มแล้ว", "เริ่มซ่อม", "เริ่มทำ", "ถึงที่", "มาถึง", "ถึงร้านลูกค้า",
    "ถึงไซต์", "ถึงหน้าไซต์", "on site", "arrive", "arriving now", "i'm here", "im here", "starting", "ถึงจุดหมาย",
)
CHECKOUT_TRIGGERS = (
    "เช็คเอาท์", "เช็กเอาต์", "ปิดงาน", "ส่งรายงาน", "จบงาน", "check out", "checkout", "เช็คเอาต์", "ปิดจ๊อบ",
)
# "done" / "เสร็จแล้ว" mean a check-out only where a technician says them.
# On the sales OA a salesperson typing "done" after a customer list opened
# a service-report draft that swallowed the next three messages (review,
# 6 Sep 2026); there they count only with a ticket code.
CHECKOUT_LOOSE_TRIGGERS = (
    "เสร็จแล้ว", "งานเสร็จ", "ทำเสร็จ", "ซ่อมเสร็จ", "done", "finished",
    "เรียบร้อย", "งานเรียบร้อย", "finish", "แล้วเสร็จ", "แล้วเจ้า", "จบแล้ว", "เสร็จเรียบร้อย", "complete", "completed",
    "เก็บงานแล้ว", "เสร็จหมดแล้ว", "เสร็จ", "เสร็จสิ้น", "all done", "job done", "work done",
)

CHECKIN_DONE = {
    "th": "เช็คอิน {code} แล้ว\n{customer}\n{address}",
    "en": "Checked in to {code}.\n{customer}\n{address}",
}
CHECKOUT_NEEDS_REPORT = {
    "th": "ปิดงานยังไม่ได้ ต้องบันทึกก่อนว่า: {missing}\n\nพิมพ์แบบนี้:\nปิดงาน {code}\nพบ: <ปัญหาที่พบ>\nแก้: <สิ่งที่แก้ไข>",
    "en": "Cannot close yet — still need: {missing}",
}
CHECKOUT_DONE = {
    "th": "ปิดงาน {code} แล้ว\nใบรายงาน {report}\nรอ CS ตรวจสอบ",
    "en": "Closed {code}. Report {report} is waiting for review.",
}
CHECKIN_FAILED = {
    "th": "เช็คอินไม่สำเร็จ: {detail}",
    "en": "Check-in failed: {detail}",
}

# "พบ:" and "แก้:" as the field markers. Chosen because a technician types
# this one-handed, standing up, often outdoors — anything longer gets
# abbreviated into something the parser will not recognise.
_REPORT_FIELD_PATTERNS = (
    ("found_issue", re.compile(r"(?:พบ|ปัญหา|found)\s*[:：]\s*(.+?)(?=\n\s*(?:แก้|วิธี|work|done)\s*[:：]|$)", re.S | re.I)),
    ("work_done", re.compile(r"(?:แก้|วิธีแก้|work|done)\s*[:：]\s*(.+?)(?=\n\s*(?:พบ|ปัญหา|found)\s*[:：]|$)", re.S | re.I)),
)


def parse_service_report(message: str) -> dict:
    """The report fields out of a typed message.

    Deliberately forgiving about surrounding text and strict about the
    markers: a technician writing "พบ: คอมรั่ว" in the middle of a longer
    message means that, and demanding a rigid format would get the report
    abandoned rather than corrected.
    """
    report: dict[str, str] = {}
    for field, pattern in _REPORT_FIELD_PATTERNS:
        match = pattern.search(message or "")
        if match:
            value = match.group(1).strip()
            if value:
                report[field] = value[:400]
    return report


async def _resolve_ticket_for_member(
    client: DataClient, license_id: str, ctx: ResolvedContext, code: str,
) -> tuple[dict | None, dict | None]:
    """(member, ticket) for a code this person may act on, or (member, None)."""
    member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
    if member is None:
        return None, None
    tickets = await client.list_tickets(license_id, visible_to=str(member["id"]))
    ticket = next(
        (t for t in tickets if str(t.get("ticket_number", "")).upper() == code), None,
    )
    return member, ticket


async def _ticket_for_action(
    client: DataClient, license_id: str, ctx: ResolvedContext, message: str,
    *, prefer_status: tuple[str, ...] = (),
) -> tuple[dict | None, dict | None, bool]:
    """(member, ticket, was_inferred) for a check-in or check-out.

    A technician has one job open at a time in practice, and making them
    read a code off a previous message and retype it — one-handed, in
    someone's hallway — is the kind of friction that gets a step skipped
    and a report never written.

    An explicit code always wins. Inference only happens when exactly ONE
    ticket fits: two candidates means asking is the only honest option,
    since guessing would file a report against the wrong customer.
    """
    member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
    if member is None:
        return None, None, False

    tickets = await client.list_tickets(license_id, visible_to=str(member["id"]))

    match = TICKET_CODE_RE.search(message or "")
    if match:
        code = match.group(1).upper()
        return (
            member,
            next(
                (t for t in tickets if str(t.get("ticket_number", "")).upper() == code),
                None,
            ),
            False,
        )

    # Statuses are tried in the order given: for a check-out, the one job
    # in progress wins even when another is assigned for the afternoon
    # (review, 6 Sep 2026: a technician with two jobs could not close
    # the one they were on, and a photo sent from site was discarded).
    # Two candidates in the same status is genuinely ambiguous — stop.
    mine_all = [t for t in tickets if str(t.get("assigned_to_ref") or "") == str(member["id"])]
    for wanted in (prefer_status or ("assigned", "in_progress")):
        mine = [t for t in mine_all if str(t.get("status") or "") == wanted]
        if len(mine) == 1:
            return member, mine[0], True
        if len(mine) > 1:
            return member, None, False
    return member, None, False


async def _handle_check_in(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    if "ticket.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        member, ticket, inferred = await _ticket_for_action(
            client, license_id, ctx, message, prefer_status=("assigned", "in_progress"),
        )
        if member is None:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        if ticket is None:
            # Nothing, or several: say which (review, 6 Sep 2026 — the old
            # reply gave "ปิดงาน" as the example for a check-in).
            mine = [
                t for t in await client.list_tickets(license_id, visible_to=str(member["id"]))
                if str(t.get("assigned_to_ref") or "") == str(member["id"])
                and str(t.get("status") or "") in ("assigned", "in_progress")
            ]
            if not mine:
                return ChatReply(
                    text=_t(CHECKIN_NOTHING, language),
                    quick_replies=[("งานที่เปิดรับ", "งานที่เปิดรับ"), ("งานของฉัน", "งานของฉัน")],
                )
            return ChatReply(
                text=_t(CHECKIN_PICK_ONE, language),
                quick_replies=[
                    (f"{t.get('ticket_number')}"[:20], f"เช็คอิน {t.get('ticket_number')}") for t in mine[:4]
                ],
            )
        code = str(ticket.get("ticket_number") or "")
        status = str(ticket.get("status") or "")
        if status == "in_progress":
            # Already on site: not "which job?" (review, 6 Sep 2026).
            return ChatReply(
                text=_t(CHECKIN_ALREADY, language).format(code=code),
                quick_replies=[("ปิดงาน", f"ปิดงาน {code}")],
            )
        if status in ("completed", "cancelled"):
            return ChatReply(text=_t(TICKET_ALREADY_CLOSED, language).format(
                code=code, state=_label(TICKET_STATUS_LABELS, status, language),
            ))
        # No GPS from a text message. The LIFF page sends coordinates; chat
        # records the arrival without pretending to a location it does not
        # have, which is better than storing one that is wrong.
        result = await client.check_in_ticket(
            license_id, str(ticket["id"]), member_id=str(member["id"]),
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        return _field_service_failure(
            exc, code=locals().get("code", ""), language=language, template=CHECKIN_FAILED,
        )
    except Exception:
        log.exception("check-in failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _notify_customer(
        client, {**ticket, **result}, f"ช่างถึงหน้างานแล้ว งาน {code}", f"The technician has arrived — job {code}",
    )
    return ChatReply(
        text=_t(CHECKIN_DONE, language).format(
            code=code,
            customer=" ".join(
                p for p in (result.get("customer_name"), result.get("customer_phone")) if p
            ) or "—",
            address=result.get("service_address") or "—",
        ),
        entity_type="service_ticket", entity_id=str(result.get("id") or ""),
        quick_replies=[("ปิดงาน", f"ปิดงาน {code}")],
    )


async def _handle_check_out(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    if "ticket.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)

    # A report being built one answer at a time. Someone typing "ปิดงาน"
    # with nothing else does not know the พบ:/แก้: format, and answering
    # them with a format error teaches nothing — they are standing in a
    # customer's house holding a phone.
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        pending = None

    if pending and pending.get("entity") == "service_report":
        fields = dict((pending.get("fields") or {}))
        awaiting = list(pending.get("missing") or [])
        if _normalise(message) in REPORT_DRAFT_CANCEL_WORDS:
            # "ยกเลิก" mid-report: the job stays open, the draft is dropped.
            # Without this every message for an hour was read as the
            # next answer (review, 6 Sep 2026).
            try:
                await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
            except Exception:
                log.exception("could not drop a report draft")
            return ChatReply(text=_t(REPORT_DRAFT_CANCELLED, language).format(code=str(fields.get("code") or "")))
        if awaiting:
            answer = (message or "").strip()
            switch = TICKET_CODE_RE.search(answer)
            if switch and _normalise(TICKET_CODE_RE.sub(" ", answer)).startswith(
                ("ไม่ใช่", "ผิด", "ผิดงาน", "งานอื่น", "เปลี่ยนเป็น", "เปลี่ยนงาน", "wrong", "notthis", "งานนี้ไม่ใช่")
            ):
                # Mid-report: "ไม่ใช่งานนี้ งาน T-2026-0002" moves the draft to
                # that job instead of becoming its found_issue (review,
                # 6 Sep 2026, B13).
                wanted = switch.group(1).upper()
                try:
                    member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
                    tickets = await client.list_tickets(license_id, visible_to=str((member or {}).get("id") or ""))
                except Exception:
                    tickets = []
                target = next((t for t in tickets if str(t.get("ticket_number") or "").upper() == wanted), None)
                if target is None:
                    return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=wanted))
                if str(target.get("status") or "") != "in_progress":
                    return ChatReply(
                        text=_t(CHECKOUT_NEEDS_CHECKIN, language).format(code=wanted),
                        quick_replies=[(f"เช็คอิน {wanted}"[:20], f"เช็คอิน {wanted}")],
                    )
                fields["ticket_id"] = str(target.get("id") or "")
                fields["code"] = wanted
                try:
                    await client.set_pending_intent(
                        ctx.chann_uid, ctx.oa, action="report", entity="service_report",
                        fields=fields, missing=awaiting, ttl_seconds=CHECKOUT_DRAFT_TTL_S,
                    )
                except Exception:
                    log.exception("could not move a report draft to another job")
                return ChatReply(
                    text=_t(REPORT_DRAFT_SWITCHED, language).format(code=wanted) + "\n" + _t(REPORT_QUESTIONS[awaiting[0]], language),
                    quick_replies=[("ยกเลิกการปิดงาน", "ยกเลิก")],
                )
            resume = (
                _t(REPORT_DRAFT_RESUME, language).format(code=str(fields.get("code") or ""))
                + "\n" + _t(REPORT_QUESTIONS[awaiting[0]], language)
                + "\n" + _t(REPORT_DRAFT_HINT, language)
            )
            if _is_menu_tile(message, ctx.oa) or _command_like(message, CHECKOUT_TRIGGERS + CHECKOUT_LOOSE_TRIGGERS):
                # "ปิดงาน" tapped again mid-report (review, 6 Sep 2026: it
                # was filed as the problem found). Same question, again.
                return ChatReply(text=resume, quick_replies=[("ยกเลิกการปิดงาน", "ยกเลิก")])
            if _menu_digit(answer) is not None or _is_small_talk(answer):
                # A lone digit or an "ok" is not a report line.
                return ChatReply(
                    text=_t(REPORT_ANSWER_IN_WORDS, language) + "\n" + _t(REPORT_QUESTIONS[awaiting[0]], language),
                    quick_replies=[("ยกเลิกการปิดงาน", "ยกเลิก")],
                )
            if answer.lower() in _NONE_ANSWERS or _normalise(answer) in _SKIP_ANSWERS:
                # "-", "ข้าม", "ไม่มี" mean nothing to record — allowed for
                # the optional question, re-asked for a required one, since
                # the Data tier's gate refuses a report without it.
                if awaiting[0] == "parts_changed":
                    answer = ""
                else:
                    return ChatReply(
                        text=_t(REPORT_ANSWER_REQUIRED, language) + "\n" + _t(REPORT_QUESTIONS[awaiting[0]], language),
                        quick_replies=[("ยกเลิกการปิดงาน", "ยกเลิก")],
                    )
            fields[awaiting[0]] = answer[:400]
            awaiting = awaiting[1:]
        ticket_id = fields.pop("ticket_id", None)
        code = str(fields.pop("code", ""))

        if awaiting:
            try:
                await client.set_pending_intent(
                    ctx.chann_uid, ctx.oa,
                    action="report", entity="service_report",
                    fields={**fields, "ticket_id": ticket_id, "code": code},
                    missing=awaiting, ttl_seconds=CHECKOUT_DRAFT_TTL_S,
                )
            except Exception:
                log.exception("could not hold a partial service report")
            return ChatReply(text=_t(REPORT_QUESTIONS[awaiting[0]], language))

        try:
            member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
            result = await client.check_out_ticket(
                license_id, str(ticket_id),
                member_id=str((member or {}).get("id") or ""),
                # Empty answers are dropped: "no parts" is recorded as
                # the field's absence, not as an empty string that a
                # reader has to interpret.
                report_data={k: v for k, v in fields.items() if v},
                actor_id=ctx.chann_uid,
            )
            await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        except DataTierError as exc:
            # The draft is dropped with the failure: a job cancelled under
            # the technician's feet must not turn every later message into
            # the same refusal.
            await _drop_pending_quietly(client, ctx)
            return _field_service_failure(
                exc, code=code, language=language, template=CHECKIN_FAILED,
            )
        except Exception:
            log.exception("check-out failed")
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        await after_check_out(client, license_id, result, language)
        return ChatReply(
            text=_t(CHECKOUT_DONE, language).format(
                code=code, report=result.get("report_id") or "",
            ),
            entity_type="service_report", entity_id=str(result.get("id") or ""),
        )

    report_data = parse_service_report(message)
    try:
        member, ticket, inferred = await _ticket_for_action(
            client, license_id, ctx, message, prefer_status=("in_progress", "assigned"),
        )
        if member is None or ticket is None:
            return ChatReply(text=_t(TICKET_PICK_ONE, language))
        code = str(ticket.get("ticket_number") or "")
        if str(ticket.get("status") or "") in ("completed", "cancelled"):
            # Finished or cancelled: not "check in first" with a button
            # that would 409 (review, 6 Sep 2026).
            return ChatReply(text=_t(TICKET_ALREADY_CLOSED, language).format(
                code=code, state=_label(TICKET_STATUS_LABELS, ticket.get("status"), language),
            ))
        if str(ticket.get("status") or "") != "in_progress":
            # 13.4: check-out ends a visit that check-in began. Asking the
            # three report questions of someone who never arrived, and
            # failing at the end, taught nothing (owner, 3 Sep).
            return ChatReply(
                text=_t(CHECKOUT_NEEDS_CHECKIN, language).format(code=code),
                quick_replies=[(f"เช็คอิน {code}"[:20], f"เช็คอิน {code}")],
            )

        # Nothing written yet: ask, rather than refuse. The gate still
        # holds — the check-out simply does not happen until the answers
        # are in — but the person is walked through it instead of being
        # handed a format.
        missing = [f for f, _ in REPORT_REQUIRED_FIELDS if not report_data.get(f)]
        # The optional question is asked only in the guided flow — when
        # the required answers arrived one at a time. Someone who typed
        # the terse "พบ:/แก้:" form chose it to skip the conversation.
        if missing or pending is not None:
            missing += [f for f, _ in REPORT_OPTIONAL_FIELDS if f not in report_data]
        if missing:
            try:
                await client.set_pending_intent(
                    ctx.chann_uid, ctx.oa,
                    action="report", entity="service_report",
                    fields={**report_data, "ticket_id": str(ticket["id"]), "code": code},
                    missing=missing, ttl_seconds=CHECKOUT_DRAFT_TTL_S,
                )
            except Exception:
                log.exception("could not start a guided service report")
                return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
            return ChatReply(
                text=_t(CHECKOUT_STARTED, language).format(code=code)
                + "\n" + _t(REPORT_QUESTIONS[missing[0]], language)
            )
        result = await client.check_out_ticket(
            license_id, str(ticket["id"]),
            member_id=str(member["id"]), report_data=report_data,
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        # The gate's own list is passed through (the technician is standing
        # in a customer's house reading this); every other refusal is
        # rendered in Thai by the shared helper.
        return _field_service_failure(
            exc, code=locals().get("code", ""), language=language, template=CHECKIN_FAILED,
        )
    except Exception:
        log.exception("check-out failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await after_check_out(client, license_id, result, language)
    return ChatReply(
        text=_t(CHECKOUT_DONE, language).format(
            code=code, report=result.get("report_id") or "",
        ),
        entity_type="service_report", entity_id=str(result.get("id") or ""),
    )


async def _after_report_submitted(client: DataClient, license_id, result: dict, language: str) -> None:
    """Phase 14-B: the check-out committed; open its approval steps and
    tell the first approver now. Best-effort — a notification failure
    must never read as a failed check-out to the technician."""
    from . import approval as approval_service

    try:
        await approval_service.on_report_submitted(
            client, license_id=str(license_id), report=result, language=language,
        )
    except Exception:
        log.exception("approval steps could not be opened for %s", result.get("report_id"))


# ------------------------------------------------------- Phase 12 tickets

TICKET_LIST_PHRASES = (
    "รายการงาน", "รายการงานซ่อม", "งานซ่อม", "รายการซ่อม", "งานทั้งหมด",
    "งานค้าง", "tickets", "ticket", "งานซ่อมค้าง", "งานซ่อมวันนี้", "งานซ่อมทั้งหมด", "งานซ่อมที่ค้าง", "ใบงาน", "ใบงานทั้งหมด",
    "service tickets", "all tickets", "jobs list",
)
TICKET_MINE_PHRASES = (
    "งานของฉัน", "งานที่รับ", "งานของผม", "งานผม", "งานฉัน", "งานผมวันนี้", "งานฉันวันนี้", "ตารางงาน",
    "ตารางงานวันนี้", "ตารางงานของฉัน", "ตารางของฉัน", "งานที่ต้องไป", "งานที่ต้องทำ", "คิวงาน", "คิวของฉัน",
    "วันนี้มีงานไหม", "มีงานอะไรบ้าง", "งานมีอะไรบ้าง", "งานพรุ่งนี้", "my tickets", "my jobs", "my schedule", "schedule",
    "งานฉันมีอะไรบ้าง", "งานมีอะไรบ้างวันนี้", "งานฉันมีอะไรบ้างวันนี้", "งานฉันวันนี้มีอะไรบ้าง", "พรุ่งนี้มีงาน", "พรุ่งนี้มีงานไหม",
    "งานของฉันพรุ่งนี้", "งานฉันพรุ่งนี้", "คิวงานฉัน", "คิวฉัน", "คิว", "งานที่ได้รับ", "งานในมือ", "งานของฉันทั้งหมด",
    "วันนี้ต้องไปที่ไหน", "jobs today", "what jobs do i have",
)
# One word on its own — kept out of the tuple so the trigger checker, which
# cannot see that _matches_phrase is exact, does not flag every longer
# phrase containing it.
BARE_JOB_WORDS = frozenset({"งาน", "jobs", "job"})
BARE_ACCEPT_WORDS = frozenset({
    "รับ", "รับงาน", "ตกลงรับ", "รับครับ", "รับค่ะ", "ok รับ", "accept", "ผมรับ", "ฉันรับ", "รับเอง", "รับได้", "ฉันรับเอง",
    "ฉันเอา", "งานนี้ฉันเอา", "งานนี้ฉันรับ", "เอางาน", "เอา", "ขอรับ", "รับงานนี้", "รับเลย", "โอเครับ", "รับไว้", "ฉันไปเอง",
    "เดี๋ยวฉันไป", "ฉันไป", "ไปได้", "ไปได้เลย", "รับทำ", "ok i'll take it", "i'll take it", "take it", "mine",
})
BARE_DECLINE_WORDS = frozenset({
    "ไม่รับ", "ไม่ว่าง", "ไปไม่ได้", "ไม่สะดวก", "ไม่ไหว", "decline", "ไม่ว่างไปไม่ได้", "ไม่ไป", "ไม่เอา", "ป่วย", "ลาป่วย",
    "ติดงาน", "ติดงานอื่น", "ไม่รับงาน", "ไม่รับงานนี้", "ไม่สะดวกไป", "ขอไม่รับ", "ไม่ทัน", "ไปไม่ทัน", "ไม่พร้อม", "ไม่อยู่",
    "อยู่ต่างจังหวัด", "can't", "cannot", "can't make it", "not free", "busy", "sick", "no", "pass",
})
# A decline or an acceptance said in a sentence: "ไปไม่ได้ครับ ป่วย",
# "งาน T-2026-0001 ผมไปไม่ได้", "รับ T-2026-0002" (review, 6 Sep 2026, B9).
_DECLINE_ONLY_WORDS = frozenset({"ไม่รับ", "decline", "ไม่ว่าง", "ไปไม่ได้", "ไม่สะดวก", "ไม่ไหว", "ไม่ไป", "ไม่เอา", "no", "pass", "cannot", "can't", "ไม่รับงาน", "ขอไม่รับ", "ไม่ว่างไปไม่ได้", "ไม่สะดวกไป", "ไม่พร้อม"})
_DECLINE_HEADS = ("ไปไม่ได้", "ไม่ว่าง", "ไม่สะดวก", "ไม่ไหว", "ไม่รับ", "ไม่ไป", "ไม่เอา", "ป่วย", "ลาป่วย", "ติดงาน", "ไม่ทัน", "ไปไม่ทัน", "can't", "cannot", "not free", "busy", "sick")


def _bare_decision(message: str) -> str | None:
    """"accept" / "decline" when the message is one of those words in any
    polite or dialect dressing, with or without a ticket code and a short
    reason after it — else None."""
    rest = TICKET_CODE_RE.sub(" ", message or "")
    forms = _bare_forms(rest)
    if not forms or _looks_like_a_question(rest):
        return None
    if forms & BARE_DECLINE_WORDS:
        return "decline"
    if forms & BARE_ACCEPT_WORDS:
        return "accept"
    canon = _canonical(rest).replace(" ", "")
    for form in _with_pronoun_forms(canon):
        form = re.sub(r"^(?:งาน|งานนี้|งานนั้น|ฉัน|ตอนนี้|วันนี้)+", "", form)
        if any(form.startswith(h) for h in _DECLINE_HEADS) and len(form) <= 40:
            return "decline"
    return None
# The technician rich-menu tile. Jobs nobody has taken yet, plus the ones
# handed to this person that they have not accepted — the same set the
# LIFF home calls "งานที่เปิดรับ".
TICKET_OPEN_PHRASES = (
    "งานที่เปิดรับ", "งานเปิดรับ", "งานว่าง", "งานที่ว่าง", "งานที่เปิด", "งานเปิด", "งานที่ยังไม่มีคนรับ",
    "งานรอรับ", "มีงานไหม", "มีงานมั้ย", "งานใหม่", "open jobs", "available jobs",
    "งานให้รับ", "งานที่รับได้", "งานรอคนรับ", "งานไหนว่าง", "งานไหนยังว่าง", "งานไหนยังว่างบ้าง", "งานที่ยังไม่ได้มอบหมาย",
    "งานยังไม่มอบหมาย", "งานที่ยังไม่มีช่าง", "งานรอมอบหมาย", "งานใหม่ๆ", "งานว่างๆ", "unassigned", "unassigned jobs",
    "jobs to take", "new jobs",
)
TICKET_DETAIL_TRIGGERS = ("ข้อมูลงาน", "รายละเอียดงาน", "ticket ", "ticket detail", "ticket info", "job detail", "งานเลขที่")
TICKET_ASSIGN_TRIGGERS = ("มอบหมาย", "จ่ายงาน", "assign ticket", "assign", "ส่งงานให้", "โยนงานให้", "จัดช่างให้")
TICKET_CLAIM_TRIGGERS = ("รับงาน", "claim", "ขอรับงาน", "take the job", "i'll take")


def _implicit_assignment(message: str) -> bool:
    """A ticket code, a person, and "ให้"/"ไป" — dispatch without the verb."""
    text = (message or "").strip()
    if not TICKET_CODE_RE.search(text) or _looks_like_a_question(text) or len(text) > 60:
        return False
    rest = TICKET_CODE_RE.sub(" ", text)
    canon = _canonical(rest)
    if any(t in canon for t in CHECKIN_TRIGGERS + CHECKOUT_TRIGGERS + CHECKOUT_LOOSE_TRIGGERS + TICKET_REJECT_TRIGGERS + ("รับงาน", "เตือน", "บันทึก", "นัด")):
        return False
    return bool(re.search(r"ให้|ไปงาน|(?<![ก-๙])ไป(?![ก-๙])|ไป\s*$|\bto\b", canon)) and len(_strip_polite_tail(re.sub(r"ให้|ไปงาน|ไป|\bto\b|งาน|นะ", " ", canon)).strip()) >= 2

TICKET_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(T-\d{4}-\d{4})(?![0-9])", re.IGNORECASE)

TICKET_STATUS_LABELS = {
    "open": {"th": "รอมอบหมาย", "en": "open"},
    "assigned": {"th": "มอบหมายแล้ว", "en": "assigned"},
    "in_progress": {"th": "กำลังทำ", "en": "in progress"},
    "completed": {"th": "เสร็จแล้ว", "en": "completed"},
    "cancelled": {"th": "ยกเลิก", "en": "cancelled"},
}

TICKET_EMPTY = {"th": "ยังไม่มีงานซ่อม", "en": "No service tickets yet."}
TICKET_PICK_ONE = {
    "th": "ไม่แน่ใจว่างานไหนครับ พิมพ์เลขงานด้วย เช่น \"ปิดงาน T-2026-0001\"\n(พิมพ์ \"งานของฉัน\" เพื่อดูเลขงาน)",
    "en": "Not sure which job — include the number, e.g. \"check out T-2026-0001\".",
}

TICKET_CLAIM_PICK = {
    "th": "มีหลายงานให้รับ เลือกงานครับ",
    "en": "Several jobs can be taken — pick one.",
}
TICKET_NOTHING_TO_CLAIM = {
    "th": "ตอนนี้ไม่มีงานให้รับครับ",
    "en": "There is no job to take right now.",
}
SINGLE_SHOP = {
    "th": "บัญชีนี้อยู่ร้าน {company} ร้านเดียวครับ ถ้าจะเข้าร่วมร้านอื่น พิมพ์รหัสเชิญของร้านนั้นได้เลย",
    "en": "This account belongs to {company} only. To join another shop, type that shop's invite code.",
}
# The shipped roles in words (review, 6 Sep 2026, B15: "ร้าน: บริษัททดสอบ (sales)").
ROLE_LABELS = {
    "owner": {"th": "เจ้าของร้าน", "en": "owner"},
    "admin": {"th": "แอดมิน", "en": "admin"},
    "sales": {"th": "ฝ่ายขาย", "en": "sales"},
    "member": {"th": "พนักงานขาย", "en": "salesperson"},
    "cs": {"th": "ฝ่ายบริการลูกค้า", "en": "customer service"},
    "technician": {"th": "ช่าง", "en": "technician"},
    "customer": {"th": "ลูกค้า", "en": "customer"},
}
STAFF_PROFILE_TEXT = {
    "th": "ข้อมูลของคุณครับ\n\nชื่อ: {name}\nเบอร์: {phone}\nอีเมล: {email}\nร้าน: {shop} ({role})\n\nแก้ได้เลย เช่น \"แก้เบอร์เป็น 08x-xxx-xxxx\"",
    "en": "Your details\n\nName: {name}\nPhone: {phone}\nEmail: {email}\nShop: {shop} ({role})\n\nChange any of it, e.g. \"change my phone to 08x-xxx-xxxx\"",
}
TICKET_NEEDS_CODE = {
    "th": "ระบุเลขงานด้วย เช่น \"มอบหมาย T-2026-0001 ให้ทีม AC\"",
    "en": "Include the ticket number, e.g. \"assign T-2026-0001 to AC Team\".",
}
TICKET_NEEDS_TARGET = {
    "th": "ระบุด้วยว่ามอบหมายให้ใครหรือทีมไหน เช่น \"มอบหมาย T-2026-0001 ให้ทีม AC\"",
    "en": "Say who or which team, e.g. \"assign T-2026-0001 to AC Team\".",
}
TICKET_DISPATCH_BLOCKED = {
    "th": "ยังมอบหมายไม่ได้ ข้อมูลไม่ครบ: {missing}\n\nเติมข้อมูลก่อนแล้วค่อยมอบหมายอีกครั้ง",
    "en": "Cannot dispatch — still missing: {missing}",
}
TICKET_ASSIGNED = {
    "th": "มอบหมาย {code} ให้ {target} แล้ว",
    "en": "Assigned {code} to {target}.",
}
TICKET_CLAIMED = {
    "th": "รับงาน {code} แล้ว\n{customer}\n{address}\nนัด {when}",
    "en": "You took {code}.\n{customer}\n{address}\nScheduled {when}",
}
TICKET_CLAIM_FAILED = {
    "th": "รับงานไม่สำเร็จ: {detail}",
    "en": "Could not take that ticket: {detail}",
}


def _ticket_when(ticket: dict) -> str:
    from .thai_datetime import format_thai_date, format_thai_time

    raw_date = ticket.get("scheduled_date")
    raw_time = ticket.get("scheduled_time")
    parts = []
    if raw_date:
        try:
            parts.append(format_thai_date(date.fromisoformat(str(raw_date))))
        except ValueError:
            parts.append(str(raw_date))
    if raw_time:
        try:
            parts.append(format_thai_time(time.fromisoformat(str(raw_time))))
        except ValueError:
            parts.append(str(raw_time))
    return " ".join(parts) or "—"


TICKET_DETAIL_EMPTY = {
    "th": "ระบุเลขงานด้วยครับ เช่น \"ข้อมูลงาน T-2026-0001\"",
    "en": 'Which job? e.g. "ticket T-2026-0001".',
}


REPORT_LIST_PHRASES = (
    "รายงานของฉัน", "รายงานที่ส่ง", "ดูรายงาน", "my reports", "รายงานที่ส่งไป", "รายงานที่ส่งแล้ว", "รายงานที่เคยส่ง",
    "รายงานทั้งหมด", "ดูรายงานของฉัน", "reports", "my report", "รายงานซ่อมของฉัน", "รายงานฉัน",
)

# Phase 13.4/13.5 — the report as paper. Produced at approval; asked for
# here when it was not, or when a corrected copy is wanted.
REPORT_PDF_TRIGGERS = ("ออกรายงาน", "รายงาน pdf", "pdf รายงาน", "ขอ pdf", "ขอไฟล์รายงาน", "report pdf", "issue report")
REPORT_PDF_REISSUE = ("ออกรายงานใหม่", "ออกรายงานซ้ำ", "reissue report")
REPORT_PDF_READY = {
    "th": "รายงาน {code} (PDF)\nลิงก์ดาวน์โหลด (ใช้ได้ 7 วัน):\n{url}",
    "en": "Report {code} (PDF)\nDownload link (valid 7 days):\n{url}",
}
REPORT_PDF_NOT_APPROVED = {
    "th": "รายงาน {code} ยังไม่ผ่านการอนุมัติ PDF จะออกให้เมื่อ CS อนุมัติแล้ว",
    "en": "Report {code} is not approved yet — the PDF is produced on approval.",
}
REPORT_PDF_NEEDS_CODE = {
    "th": "ออกรายงานไหนครับ พิมพ์ \"ออกรายงาน SR-2026-0001\" (ดูเลขได้จาก \"รายงานของฉัน\")",
    "en": "Which report? Type \"issue report SR-2026-0001\" (see \"my reports\")",
}
REPORT_PDF_FAILED = {
    "th": "ออก PDF ไม่สำเร็จ: {detail}",
    "en": "Could not produce the PDF: {detail}",
}


async def _handle_report_pdf(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    from .report_issue import ReportAlreadyIssued, ReportNotApproved, issue_for_report

    if "service_report.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    match = SERVICE_REPORT_CODE_RE.search(message or "")
    code = match.group(1).upper() if match else ""
    reissue = any(t in (message or "").lower() for t in REPORT_PDF_REISSUE)
    try:
        rows = await client.list_service_reports(license_id)
    except Exception:
        log.exception("could not list reports")
        return ChatReply(text=unavailable_reply(language))
    if not code:
        # One approved report of mine and no code: that one. Otherwise ask.
        try:
            member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
        except Exception:
            member = None
        mine = [
            r for r in rows
            if str(r.get("status") or "") == "approved"
            and (not member or str(r.get("technician_member_id") or "") == str(member.get("id")))
        ]
        if len(mine) != 1:
            return ChatReply(
                text=_t(REPORT_PDF_NEEDS_CODE, language),
                quick_replies=[
                    (str(r.get("report_id"))[:20], f"ออกรายงาน {r.get('report_id')}") for r in mine[:4]
                ] or [("รายงานของฉัน", "รายงานของฉัน")],
            )
        code = str(mine[0].get("report_id") or "")
    report = next((r for r in rows if str(r.get("report_id") or "").upper() == code), None)
    if report is None:
        return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what="รายงาน", code=code))

    document_id = str(report.get("generated_document_id") or "")
    if document_id and not reissue:
        url = document_download_url(license_id, document_id)
    else:
        try:
            document = await issue_for_report(
                client, license_id=license_id, report_id=str(report["id"]),
                actor_id=ctx.chann_uid, allow_reissue=reissue,
            )
        except ReportNotApproved:
            return ChatReply(text=_t(REPORT_PDF_NOT_APPROVED, language).format(code=code))
        except ReportAlreadyIssued:
            url = document_download_url(license_id, document_id)
            document = None
        except Exception as exc:  # noqa: BLE001
            log.exception("report pdf failed")
            return ChatReply(text=_t(REPORT_PDF_FAILED, language).format(detail=str(exc)[:160]))
        if document is not None:
            url = document_download_url(license_id, str(document.get("id") or ""))
    if not url:
        return ChatReply(text=_t(QUOTE_ISSUED_NO_LINK, language).format(quote_id=code, sha=""))
    return ChatReply(
        text=_t(REPORT_PDF_READY, language).format(code=code, url=url),
        entity_type="service_report", entity_id=str(report.get("id") or ""),
        quick_replies=[("ออกรายงานใหม่", f"ออกรายงานใหม่ {code}")],
    )

REPORT_LIST_EMPTY = {
    "th": "ยังไม่มีรายงานการซ่อม",
    "en": "No service reports yet.",
}
REPORT_LIST_HEAD = {
    "th": "รายงานการซ่อม {count} รายการ",
    "en": "{count} service reports",
}
REPORT_STATUS_LABELS = {
    "submitted": {"th": "รอตรวจ", "en": "awaiting review"},
    "approved": {"th": "อนุมัติแล้ว", "en": "approved"},
    "rejected": {"th": "ตีกลับ", "en": "sent back"},
    "draft": {"th": "ร่าง", "en": "draft"},
}


async def _handle_report_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """What a technician has filed, and whether the office has read it.

    A report goes in and vanishes: the technician cannot see it again,
    and cannot tell whether it was approved or sent back — which is the
    one thing they need to know to act on it.
    """
    if "service_report.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        member = await client.get_member(str(license_id), ctx.chann_uid, channel=member_channel(ctx.oa))
        rows = await client.list_service_reports(str(license_id))
    except Exception:
        log.exception("report list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    mine = [
        r for r in rows
        if member and str(r.get("technician_member_id") or r.get("member_id") or "")
        == str(member.get("id"))
    ] or rows
    if not mine:
        return ChatReply(text=_t(REPORT_LIST_EMPTY, language))

    mine = sorted(mine, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    lines = []
    for r in mine[:LIST_LIMIT]:
        status = _label(REPORT_STATUS_LABELS, r.get("status"), language)
        found = str((r.get("report_data") or {}).get("found_issue") or "")[:40]
        lines.append(f"· {r.get('report_id')} · {status}{f' · {found}' if found else ''}")
    return ChatReply(
        text=_t(REPORT_LIST_HEAD, language).format(count=len(mine)) + "\n" + "\n".join(lines)
    )


async def _handle_ticket_detail(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """One job in full: who, where, what, when, and who is on it.

    TICKET_DETAIL_TRIGGERS was declared and never dispatched, so
    "ข้อมูลงาน T-2026-0001" fell through to the AI and, with the AI
    down, to an apology.
    """
    if "ticket.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    match = TICKET_CODE_RE.search(message or "")
    if not match:
        # No code: the single open job, if there is exactly one.
        member, ticket, _ = await _ticket_for_action(
            client, str(license_id), ctx, message,
            prefer_status=("assigned", "in_progress", "open"),
        )
        if ticket is None:
            return ChatReply(text=_t(TICKET_DETAIL_EMPTY, language))
    else:
        code = match.group(1).upper()
        try:
            member = await client.get_member(str(license_id), ctx.chann_uid, channel=member_channel(ctx.oa))
            tickets = await client.list_tickets(
                str(license_id), visible_to=str(member["id"]) if member else None,
            )
        except Exception:
            log.exception("ticket detail failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        ticket = next(
            (t for t in tickets if str(t.get("ticket_number", "")).upper() == code), None,
        )
        if ticket is None:
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code)
            )

    en = language == "en"
    lines = [f"{ticket.get('ticket_number')} · {_label(TICKET_STATUS_LABELS, ticket.get('status'), language)}"]
    if ticket.get("customer_name"):
        lines.append(f"{'Customer' if en else 'ลูกค้า'}: {ticket['customer_name']}")
    if ticket.get("customer_phone"):
        lines.append(f"{'Phone' if en else 'โทร'}: {ticket['customer_phone']}")
    if ticket.get("service_address"):
        lines.append(f"{'Address' if en else 'ที่อยู่'}: {ticket['service_address']}")
    if ticket.get("issue_description"):
        lines.append(f"{'Fault' if en else 'อาการ'}: {ticket['issue_description']}")
    # Which machine, and whether it is still covered — the reason for
    # linking a fault to a registered unit at all (owner, 10 ก.ย. 2569).
    # Silent on a ticket with no link: nothing is backfilled, and a label
    # with nothing after it is worse than no line.
    machine = ticket_machine.machine_line(
        (await ticket_machine.attach_to(client, str(license_id), [ticket]))[0], language,
    )
    if machine:
        lines.append(machine)
    when = _ticket_when(ticket)
    if when:
        lines.append(f"{'Scheduled' if en else 'นัด'}: {when}")
    if ticket.get("assigned_to_name"):
        lines.append(f"{'Technician' if en else 'ช่าง'}: {ticket['assigned_to_name']}")

    await _remember_entity(
        client, ctx, entity_type="ticket", entity_id=str(ticket["id"]),
        code=str(ticket.get("ticket_number") or ""),
    )
    return ChatReply(
        text="\n".join(lines),
        entity_type="ticket", entity_id=str(ticket["id"]),
    )


TICKET_OPEN_EMPTY = {
    "th": "ตอนนี้ไม่มีงานเปิดรับครับ",
    "en": "No open jobs right now.",
}


async def _handle_ticket_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str, mine: bool = False,
    open_only: bool = False, team_only: bool = False,
) -> ChatReply:
    if "ticket.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        if mine or open_only or team_only:
            member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
            if member is None:
                return ChatReply(text=_t(TICKET_EMPTY, language))
            # visible_to, not a plain list: a technician browsing without
            # it would read the address and phone number of every private
            # job in the tenant.
            tickets = await client.list_tickets(
                license_id, visible_to=str(member["id"]),
            )
            if mine and not (team_only or open_only):
                # Mine = assigned to me and not finished. The title said
                # "งานของฉัน" over every visible job in the tenant
                # (review, 6 Sep 2026).
                me = str(member["id"])
                tickets = [
                    t for t in tickets
                    if str(t.get("assigned_to_ref") or "") == me
                    and str(t.get("status") or "") not in ("completed", "cancelled")
                ]
            if team_only:
                # Accepted by the lead for a team I am on, not yet taken.
                tickets = [
                    t for t in tickets
                    if str(t.get("assigned_target_type") or "") == "technician_team"
                    and str(t.get("accept_status") or "") == "accepted"
                    and str(t.get("status") or "") not in ("completed", "cancelled")
                ]
            if open_only:
                me = str(member["id"])
                # Open = nobody has taken it yet. It used to exclude only
                # MY accepted jobs, so a colleague's job stayed "open" to
                # everyone else (owner, 3 Sep: a job in both lists).
                tickets = [
                    t for t in tickets
                    if str(t.get("status") or "") not in ("completed", "cancelled")
                    and str(t.get("accept_status") or "") != "accepted"
                ]
        else:
            tickets = await client.list_tickets(license_id)
    except Exception:
        log.exception("ticket list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not tickets:
        return ChatReply(text=_t(TICKET_TEAM_EMPTY if team_only else TICKET_OPEN_EMPTY if open_only else TICKET_EMPTY, language))

    shown = tickets[:LIST_LIMIT]
    lines = [
        f"{t.get('ticket_number')} · {_label(TICKET_STATUS_LABELS, t.get('status'), language)}"
        + (f" · {t.get('customer_name')}" if t.get("customer_name") else "")
        for t in shown
    ]
    return ChatReply(
        text="\n".join(lines) + _truncation_note(len(shown), len(tickets), language, "index"),
        list_card=_list_card(
            title="งานของทีม" if team_only else "งานที่เปิดรับ" if open_only else ("งานของฉัน" if mine else "งานซ่อม"),
            section="index", language=language, oa=ctx.oa,
            shown=len(shown), total=len(tickets),
            rows=[
                {
                    "title": str(t.get("ticket_number") or "-"),
                    "subtitle": " · ".join(
                        p for p in (
                            _label(TICKET_STATUS_LABELS, t.get("status"), language),
                            str(t.get("customer_name") or ""),
                        ) if p
                    ),
                    "stage": {"open": "new", "assigned": "proposed",
                              "in_progress": "proposed", "completed": "won",
                              "cancelled": "lost"}.get(str(t.get("status")), ""),
                    # On the open list the useful tap is to take the job.
                    "action_label": "รับงาน" if open_only else "ดู",
                    "action_text": (
                        f"รับงาน {t.get('ticket_number')}" if open_only
                        else f"ข้อมูลงาน {t.get('ticket_number')}"
                    ),
                }
                for t in shown
            ],
        ),
    )


# A running number on its own — "ticket 0004", "งาน 4", "มอบหมาย 0004 ให้ช่าง".
# Bounded at four digits and required to stand alone so a phone number, a
# price or a date cannot be read as a job (Thai letters are word
# characters, so the guard is explicit rather than \b).
_TICKET_RUN_RE = re.compile(
    r"(?:^|[\s:·])((?:ticket|job|งาน|ใบงาน|เลขงาน|no\.?|#)?\s*(\d{1,4}))(?=$|[\s:·.,!?])",
    re.IGNORECASE,
)

TICKET_ASSIGN_WHICH_TICKET = {
    "th": "มอบหมายงานไหนครับ",
    "en": "Which job should I assign?",
}
TICKET_ASSIGN_WHO = {
    "th": "งาน {code} มอบหมายให้ช่างคนไหนครับ",
    "en": "Who should job {code} go to?",
}
TICKET_ASSIGN_NO_TECHNICIAN = {
    "th": "ยังไม่มีช่างในร้านครับ เชิญช่างเข้าร้านก่อน แล้วค่อยมอบหมายงาน {code}",
    "en": "This shop has no technician yet — invite one, then assign {code}.",
}


def _ticket_run_numbers(message: str, trigger: str = "") -> list[tuple[str, str]]:
    """(running number as four digits, the text that said it).

    Owner's transcript, 10 ก.ย. 2569: "มอบหมาย ticket 0001 ให้ช่าง" was
    refused with "ระบุเลขงานด้วย" — the number WAS given, just not in the
    T-YYYY-NNNN shape nobody reads off a notification.

    The raw text comes back too so the caller can take exactly that out of
    the message before reading the rest as a person's name: blanking every
    digit run instead turned "ให้ CHN-TECH-1" into "CHN-TECH".
    """
    text = message or ""
    if trigger:
        lowered = text.lower()
        index = lowered.find(trigger.lower())
        if index >= 0:
            text = text[index + len(trigger):]
    text = TICKET_CODE_RE.sub(" ", text)
    if _looks_like_phone(text):
        return []
    return [(m.group(2).zfill(4), m.group(1)) for m in _TICKET_RUN_RE.finditer(text)]


async def _ticket_for_assignment(
    client: DataClient, license_id: str, ctx: ResolvedContext, message: str,
    trigger: str, tickets: list[dict],
) -> tuple[dict | None, str, str]:
    """The job a dispatch command is about: (ticket, code, text-consumed).

    Three ways in, in order of how certain each is, because the owner's
    10 ก.ย. transcript hit all three and got the same generic refusal:

    1. the full code, `T-2026-0004`, which always wins;
    2. a running number on its own — "ticket 0004", "งาน 0004" — matched
       on the number inside the licence's own codes, so 0004 means this
       shop's fourth job of the year and never another shop's;
    3. the job the conversation is already about: the last entity ref,
       which `handle_reply` seeds from the message being replied to. That
       is how "(quoting แจ้งซ่อมใหม่ T-2026-0004) มอบหมายให้ช่าง" resolves
       without the person retyping a code they are pointing at.

    (None, "") only when genuinely nothing identifies a job — which is the
    one case the old refusal was right about.
    """
    match = TICKET_CODE_RE.search(message or "")
    if match:
        code = match.group(1).upper()
        return next(
            (t for t in tickets if str(t.get("ticket_number", "")).upper() == code), None,
        ), code, ""

    for run, said in _ticket_run_numbers(message, trigger):
        hits = [
            t for t in tickets
            if str(t.get("ticket_number", "")).upper().endswith(f"-{run}")
        ]
        if len(hits) == 1:
            return hits[0], str(hits[0].get("ticket_number") or ""), said
        if len(hits) > 1:
            # The same running number in two years. Rather than guess,
            # fall through: the context below, or the question.
            break

    try:
        last = await _last_entity_ref(client, ctx)
    except Exception:
        log.exception("could not read the conversation's last record")
        last = None
    if last and str(last.get("entity_type") or "") in ("ticket", "service_ticket"):
        entity_id = str(last.get("entity_id") or "")
        row = next((t for t in tickets if str(t.get("id")) == entity_id), None)
        if row is not None:
            return row, str(row.get("ticket_number") or ""), ""

    # Nothing named and nothing in the conversation. The shop has just
    # been told about a new fault and is answering that — so the jobs
    # nobody has been given yet are the honest candidates: one is offered
    # by name, several are offered as buttons, none keeps the refusal.
    waiting = [
        t for t in tickets
        if str(t.get("status") or "") == "open" and not t.get("assigned_to_ref")
    ]
    if len(waiting) == 1:
        return waiting[0], str(waiting[0].get("ticket_number") or ""), ""
    return None, "", ""


def _names_no_technician(target_text: str) -> bool:
    """"ให้ช่าง" with nobody named — a request to dispatch, not a name.

    The stripper below removes "ให้ช่าง" as a preposition, so what arrives
    here is usually "" already; a bare "ช่าง"/"technician" that survived
    means the same thing and must not be looked up as a person's name.
    """
    return _normalise(target_text) in {"", "ช่าง", "ชาง", "technician", "tech", "ใคร", "who"}


async def _technicians_of(client: DataClient, license_id: str) -> list[dict]:
    """Active technicians, with the display name a person would type."""
    try:
        members = await client.list_members(str(license_id))
    except Exception:
        log.exception("could not list members to offer an assignment target")
        return []
    out = []
    for m in members:
        if str(m.get("status") or "active") != "active":
            continue
        role = str(m.get("role") or "").lower()
        if "technician" not in role and "ช่าง" not in str(m.get("role") or ""):
            continue
        try:
            profile = await client.get_profile(str(m.get("chann_uid") or "")) or {}
        except Exception:
            profile = {}
        name = " ".join(
            p for p in (profile.get("first_name"), profile.get("last_name")) if p
        ).strip()
        out.append({**m, "display_name": name or str(m.get("chann_uid") or "")})
    return out


async def _ask_who_to_assign(
    client: DataClient, *, license_id: str, code: str, trigger: str, language: str,
) -> ChatReply:
    """"ให้ช่างคนไหน" — with the shop's technicians as buttons.

    A shop with one technician is offered that one by name rather than
    asked an open question with a single possible answer; a shop with none
    is told so plainly instead of being refused for naming nobody.
    """
    technicians = await _technicians_of(client, license_id)
    if not technicians:
        teams = []
        try:
            teams = await client.list_technician_teams(license_id)
        except Exception:
            log.exception("could not list teams to offer an assignment target")
        if not teams:
            return ChatReply(
                text=_t(TICKET_ASSIGN_NO_TECHNICIAN, language).format(code=code),
                quick_replies=[("ขอรหัสเชิญช่าง", "ขอรหัสเชิญช่าง")],
            )
        return ChatReply(
            text=_t(TICKET_ASSIGN_WHO, language).format(code=code),
            quick_replies=[
                (str(t.get("team_name") or "")[:20], f"{trigger} {code} ให้ทีม {t.get('team_name')}")
                for t in teams[:4]
            ],
        )
    return ChatReply(
        text=_t(TICKET_ASSIGN_WHO, language).format(code=code),
        quick_replies=[
            (
                str(m.get("display_name") or m.get("chann_uid"))[:20],
                f"{trigger} {code} ให้ {m.get('chann_uid')}",
            )
            for m in technicians[:4]
        ],
    )


async def _handle_ticket_assign(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    trigger: str, permission_keys: list[str], language: str,
) -> ChatReply:
    # ticket.assign, not ticket.update: the catalogue and the permission
    # table both say dispatching is its own capability, and a CS role
    # granted assign-but-not-edit was refused here for no reason it could
    # see.
    if "ticket.assign" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    # Three things are being said at once — WHICH job, to WHOM, and that
    # it should be dispatched — and until 10 ก.ย. 2569 a message missing
    # either of the first two got the same refusal naming only the first.
    # They are resolved separately now, so each can be missing on its own
    # and asked for on its own.
    license_id = str(license_id)
    try:
        tickets = await client.list_tickets(license_id)
    except Exception:
        log.exception("ticket assign failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    ticket, code, said = await _ticket_for_assignment(
        client, license_id, ctx, message, trigger, tickets,
    )
    if ticket is None:
        if code:
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code)
            )
        waiting = [
            t for t in tickets
            if str(t.get("status") or "") == "open" and not t.get("assigned_to_ref")
        ]
        if waiting:
            # Several jobs waiting: which one, as buttons — not a lecture
            # about a code format (owner's transcript, 10 ก.ย. 2569).
            return ChatReply(
                text=_t(TICKET_ASSIGN_WHICH_TICKET, language),
                quick_replies=[
                    (
                        f"{t.get('ticket_number')} {t.get('customer_name') or ''}".strip()[:20],
                        f"{trigger} {t.get('ticket_number')} ให้ช่าง",
                    )
                    for t in waiting[:4]
                ],
            )
        # Nothing named, nothing in the conversation, nothing waiting: the
        # one case the old blanket refusal was right about.
        return ChatReply(
            text=_t(TICKET_NEEDS_CODE, language),
            quick_replies=[("รายการงาน", "รายการงาน")],
        )
    code = str(ticket.get("ticket_number") or code).upper()

    # Whatever follows the code, minus the code and the trigger, names the
    # target. Team names are tenant-chosen free text, so this cannot be a
    # closed vocabulary.
    lowered = message.lower()
    index = lowered.find(trigger.lower())
    target_text = message[index + len(trigger):] if index >= 0 else message
    target_text = TICKET_CODE_RE.sub("", target_text)
    for word in ("ให้ทีม", "ให้ช่าง", "ให้", "แก่", "to team", " to ", "ไปงาน", "ไปทำงาน", "ไปดู", "ไปซ่อม"):
        target_text = target_text.replace(word, " ")
    target_text = re.sub(r"^\s*(?:to|assign|ช่าง|คุณ|พี่)\s+", " ", target_text, flags=re.I)
    if said:
        # Exactly the words that named the job, and nothing else: a blanket
        # digit strip turned "ให้ CHN-TECH-1" into a member nobody is.
        target_text = target_text.replace(said, " ", 1)
    target_text = " ".join(target_text.split()).strip(" :·-")
    target_text = re.sub(r"(?:ไป|นะ|น่ะ|ครับ|ค่ะ|คะ|หน่อย|ด้วย|เลย|ที)+$", "", target_text).strip()
    target_text = _strip_polite_tail(target_text)
    # "มอบหมายงานช่าง" — the noun "งาน" belongs to the verb, not to the
    # target. Stripped only when what is left is the word for a technician
    # rather than a name, so a team called "งานหลวง" is still itself.
    if _names_no_technician(re.sub(r"^(?:งาน|ใบงาน|job)\s*", "", target_text, flags=re.I)):
        # "มอบหมายให้ช่าง" names the job and the intent but nobody in
        # particular. The shop's technicians are the answer to that, one
        # tap each — not a refusal telling them to type a code they gave.
        return await _ask_who_to_assign(
            client, license_id=license_id, code=code, trigger=trigger, language=language,
        )

    try:
        # "อัตโนมัติ" hands the choice to the Phase 11 engine, which is
        # what 12.5 describes: the dispatch gate checks completeness, then
        # the assignment rule decides WHO. Without this the engine existed
        # and nothing ever called it.
        if target_text.lower() in AUTO_ASSIGN_WORDS:
            return await _assign_ticket_automatically(
                client, ctx=ctx, license_id=license_id, ticket=ticket,
                code=code, language=language,
            )

        teams = await client.list_technician_teams(license_id)
        team = next(
            (t for t in teams
             if str(t.get("team_name", "")).lower() == target_text.lower()), None,
        )
        if team is not None:
            target_type, target_ref, label = "technician_team", str(team["id"]), target_text
        else:
            # A person, by name. Teams are the common case but a shop with
            # three technicians has no teams at all, and telling them to
            # create one before they can dispatch anything is bureaucracy
            # the product invented.
            person = await _find_member_by_name(client, license_id, target_text)
            if person is None:
                return ChatReply(text=_t(TICKET_NEEDS_TARGET, language))
            if isinstance(person, list):
                # Two technicians called สมชาย: one button each, re-sending
                # the command with the person's id in place of the name —
                # the id is what _find_member_by_name matches exactly, so
                # the tap cannot be ambiguous again (rule 3).
                return ChatReply(
                    text=_t(TICKET_TARGET_AMBIGUOUS, language),
                    quick_replies=[
                        (
                            f"{m.get('display_name') or m.get('chann_uid')}"[:20],
                            f"{trigger} {code} ให้ {m.get('chann_uid')}",
                        )
                        for m in person[:4]
                    ],
                )
            target_type = "technician"
            target_ref = str(person["id"])
            label = str(person.get("display_name") or person.get("chann_uid") or target_text)

        result = await client.assign_ticket(
            license_id, str(ticket["id"]),
            target_type=target_type, target_ref=target_ref, actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        # The gate's own answer, passed through: it names WHICH fields are
        # missing, and a generic failure would make the person guess.
        detail = exc.structured or {}
        if detail.get("error") == "dispatch_blocked":
            return ChatReply(
                text=_t(TICKET_DISPATCH_BLOCKED, language).format(
                    missing=", ".join(detail.get("missing") or [])
                ),
                quick_replies=[("ดูข้อมูลงาน", f"ข้อมูลงาน {code}")],
            )
        log.exception("ticket assign failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("ticket assign failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    # Tell the people who now have to do it. Without this a dispatched
    # ticket is only visible to someone who happens to open the dashboard,
    # which for a job with an appointment time is too late to be useful.
    await _notify_assigned_ticket(client, license_id, result, label, language)

    return ChatReply(
        text=_t(TICKET_ASSIGNED, language).format(code=code, target=label),
        entity_type="service_ticket", entity_id=str(result.get("id") or ""),
    )


AUTO_ASSIGN_WORDS = ("อัตโนมัติ", "ออโต้", "auto", "automatic")

TICKET_TARGET_AMBIGUOUS = {
    "th": "มีช่างหลายคนที่ชื่อตรงกัน เลือกคนที่ต้องการครับ",
    "en": "Several technicians match that name — pick one.",
}
TICKET_AUTO_ASSIGNED = {
    "th": "มอบหมาย {code} ให้ {name} แล้ว (เลือกโดยกฎมอบหมาย)\n{reason}",
    "en": "Assigned {code} to {name} by rule.\n{reason}",
}
TICKET_AUTO_FAILED = {
    "th": "เลือกช่างอัตโนมัติไม่ได้: {reason}\nลองระบุทีมหรือชื่อช่างแทน",
    "en": "Could not choose automatically: {reason}",
}


async def _find_member_by_name(client: DataClient, license_id: str, name: str):
    """A member whose profile name matches, the list of matches when more
    than one does (so the caller can offer them as buttons), or None.

    Matching on the profile rather than chann_uid: a CS person dispatching
    a job types "สมชาย", not an internal identifier they have never seen.
    """
    needle = _strip_polite_tail((name or "").strip()).lower()
    if len(needle) < 2:
        return None
    try:
        members = await client.list_members(license_id)
    except Exception:
        log.exception("could not list members to resolve an assignment target")
        return None

    matches = []
    for member in members:
        if str(member.get("status") or "active") != "active":
            continue
        try:
            profile = await client.get_profile(str(member.get("chann_uid") or ""))
        except Exception:
            profile = None
        display = " ".join(
            p for p in (
                (profile or {}).get("first_name"), (profile or {}).get("last_name"),
            ) if p
        ).strip()
        if needle in display.lower() or needle == str(member.get("chann_uid") or "").lower():
            matches.append({**member, "display_name": display})

    if not matches:
        return None
    # An exact chann_uid match wins outright: that is what the choose-one
    # buttons send back, and it must not be ambiguous a second time.
    exact = [m for m in matches if str(m.get("chann_uid") or "").lower() == needle]
    if len(exact) == 1:
        return exact[0]
    # Two people called สมชาย is not a reason to pick one — the job would
    # go to the wrong person's day.
    return matches[0] if len(matches) == 1 else matches


async def _assign_ticket_automatically(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, ticket: dict,
    code: str, language: str,
) -> ChatReply:
    """Let the Phase 11 engine choose, then dispatch to whoever it picked.

    The engine runs inside the Data tier's lock and returns a member id
    plus its reasoning; this only carries the result through the same
    dispatch gate an explicit assignment goes through, so "automatic" can
    never bypass the completeness check.
    """
    try:
        outcome = await client.execute_assignment(
            license_id, scope="technician",
            entity_type="service_ticket", entity_id=str(ticket["id"]),
            context={
                "product": {
                    "category": ticket.get("product_category"),
                    "name": ticket.get("product_name"),
                },
                "ticket": {"serial_number": ticket.get("serial_number")},
            },
            actor_id=ctx.chann_uid,
        )
    except Exception:
        log.exception("automatic assignment failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    member_id = outcome.get("member_id")
    if not member_id:
        return ChatReply(
            text=_t(TICKET_AUTO_FAILED, language).format(
                reason=outcome.get("reason") or ""
            )
        )

    try:
        result = await client.assign_ticket(
            license_id, str(ticket["id"]),
            target_type="technician", target_ref=str(member_id),
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        detail = exc.structured or {}
        if detail.get("error") == "dispatch_blocked":
            return ChatReply(
                text=_t(TICKET_DISPATCH_BLOCKED, language).format(
                    missing=", ".join(detail.get("missing") or [])
                )
            )
        log.exception("automatic assignment could not be applied")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    person = await _find_member_by_name(client, license_id, str(member_id))
    name = (
        person.get("display_name") if isinstance(person, dict) else None
    ) or str(member_id)[:8]

    await _notify_assigned_ticket(client, license_id, result, name, language)
    return ChatReply(
        text=_t(TICKET_AUTO_ASSIGNED, language).format(
            code=code, name=name, reason=outcome.get("reason") or "",
        ),
        entity_type="service_ticket", entity_id=str(result.get("id") or ""),
    )


async def _notify_assigned_ticket(
    client: DataClient, license_id: str, ticket: dict, target_label: str,
    language: str,
) -> None:
    """Notify whoever a ticket was just given to.

    A team assignment goes to every member of that team, because 12.4's
    team flow is that any of them may take it — telling only the lead
    would make the others' ability to claim it useless.

    Best-effort: the assignment succeeded, and failing to announce it must
    not undo that.
    """
    target_type = str(ticket.get("assigned_target_type") or "")
    target_ref = str(ticket.get("assigned_to_ref") or "")
    if not target_ref:
        return

    try:
        if target_type == "technician_team":
            members = await client.list_team_members(license_id, target_ref)
        else:
            members = [m for m in await client.list_members(license_id)
                       if str(m.get("id")) == target_ref]
    except Exception:
        log.exception("could not resolve who to notify for a ticket assignment")
        return

    # The technician is told which machine they are going to, and whether
    # it is still under warranty, before they set off (owner, 10 ก.ย. 2569)
    # — that is the operational reason a fault is linked to a unit at all.
    ticket = (await ticket_machine.attach_to(client, license_id, [ticket]))[0]
    machine_th = ticket_machine.machine_line(ticket, "th")
    machine_en = ticket_machine.machine_line(ticket, "en")
    text = (
        f"งานใหม่ {ticket.get('ticket_number')} ({target_label})\n"
        f"{ticket.get('customer_name') or '—'}\n"
        f"{ticket.get('service_address') or '—'}\n"
        f"{ticket.get('issue_description') or ''}"
        + (f"\n{machine_th}" if machine_th else "")
    )
    text_en = (
        f"New job {ticket.get('ticket_number')} ({target_label})\n"
        f"{ticket.get('customer_name') or '—'}\n"
        f"{ticket.get('service_address') or '—'}\n"
        f"{ticket.get('issue_description') or ''}"
        + (f"\n{machine_en}" if machine_en else "")
    )
    for member in members:
        chann_uid = str(member.get("chann_uid") or "")
        if not chann_uid:
            continue
        try:
            line_target = await client.line_target_of(chann_uid)
            await send_notification(
                client,
                license_id=license_id,
                target_chann_uid=chann_uid,
                target_line_user_id=line_target,
                type="ticket_assigned",
                message=text,
                message_en=text_en,
                entity_type="service_ticket",
                entity_id=str(ticket.get("id") or ""),
                # The technician OA, not sales: this is the channel they
                # actually work in.
                oa="technician",
            )
        except Exception:
            log.exception("could not notify %s about an assignment", chann_uid)
    when = _ticket_when(ticket)
    await _notify_customer(
        client, ticket,
        f"งาน {ticket.get('ticket_number')} ของคุณมอบหมายให้ช่างแล้ว" + (f" นัด {when}" if when else ""),
        f"Your job {ticket.get('ticket_number')} has been assigned to a technician" + (f" — {when}" if when else ""),
    )


async def _handle_ticket_claim(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    if "ticket.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    match = TICKET_CODE_RE.search(message or "")
    code = match.group(1).upper() if match else ""

    try:
        member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
        if member is None:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        tickets = await client.list_tickets(license_id, visible_to=str(member["id"]))

        if not code:
            # No code given. Claiming used to demand one, unlike check-in
            # and check-out which have inferred it since Phase 13 — so
            # "เดี๋ยวผมไปเอง" was refused while "ถึงแล้ว" worked, for no
            # reason a technician could see.
            #
            # Only when exactly one job is claimable: taking the wrong
            # one sends someone to the wrong address.
            claimable = [
                t for t in tickets
                if str(t.get("status") or "") in ("assigned", "dispatched", "open")
                # A job somebody has already accepted is not on offer —
                # unless it was accepted FOR a team, which opens it to the
                # team's members (review, 6 Sep 2026: the second technician's
                # bare "รับงาน" tried the taken job and got a 409).
                and not (
                    str(t.get("accept_status") or "") == "accepted"
                    and str(t.get("assigned_target_type") or "") != "technician_team"
                )
            ]
            if len(claimable) > 1:
                # The candidates are known: buttons, not a typed example.
                return ChatReply(
                    text=_t(TICKET_CLAIM_PICK, language),
                    quick_replies=[
                        (
                            f"{t.get('ticket_number')} {str(t.get('customer_name') or '')[:12]}".strip()[:20],
                            f"รับงาน {t.get('ticket_number')}",
                        )
                        for t in claimable[:4]
                    ],
                )
            if len(claimable) != 1:
                return ChatReply(
                    text=_t(TICKET_NOTHING_TO_CLAIM, language),
                    quick_replies=[("งานที่เปิดรับ", "งานที่เปิดรับ"), ("งานของฉัน", "งานของฉัน")],
                )
            code = str(claimable[0].get("ticket_number", "")).upper()

        ticket = next(
            (t for t in tickets if str(t.get("ticket_number", "")).upper() == code), None,
        )
        if ticket is None:
            # Not found OR not visible — deliberately the same message. A
            # distinct "you may not see this" would confirm the ticket
            # exists to someone who should not know that.
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code)
            )
        if (
            str(ticket.get("accept_status") or "") == "accepted"
            and str(ticket.get("assigned_target_type") or "") != "technician_team"
        ):
            if str(ticket.get("assigned_to_ref") or "") == str(member["id"]):
                return ChatReply(
                    text=_t(TICKET_ALREADY_MINE, language).format(code=code),
                    quick_replies=[(f"เช็คอิน {code}"[:20], f"เช็คอิน {code}")],
                )
            return ChatReply(
                text=_t(TICKET_ALREADY_TAKEN, language).format(code=code),
                quick_replies=[("งานที่เปิดรับ", "งานที่เปิดรับ")],
            )
        if str(ticket.get("status") or "") in ("completed", "cancelled"):
            return ChatReply(text=_t(TICKET_ALREADY_CLOSED, language).format(
                code=code, state=_label(TICKET_STATUS_LABELS, ticket.get("status"), language),
            ))
        claimed = await client.claim_ticket(
            license_id, str(ticket["id"]), str(member["id"]), actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        if "team lead accepts" in str(exc.detail or ""):
            return ChatReply(text=_t(TICKET_TEAM_LEAD_FIRST, language).format(code=code))
        return _field_service_failure(
            exc, code=code, language=language, template=TICKET_CLAIM_FAILED,
        )
    except Exception:
        log.exception("ticket claim failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if str(claimed.get("assigned_target_type") or "") == "technician_team":
        # 12.4: the lead accepted for the team. It is now open inside the
        # team; whoever goes types "รับงาน" again to take it.
        await _notify_team_open(client, license_id, claimed, language)
        return ChatReply(
            text=_t(TICKET_TEAM_ACCEPTED, language).format(code=code),
            entity_type="service_ticket", entity_id=str(claimed.get("id") or ""),
            quick_replies=[(f"รับงาน {code}"[:20], f"รับงาน {code}"), ("งานของทีม", "งานของทีม")],
        )
    await _notify_customer(
        client, {**ticket, **claimed},
        f"ช่างรับงาน {code} ของคุณแล้ว" + (f" นัด {_ticket_when(claimed)}" if _ticket_when(claimed) else ""),
        f"A technician has accepted your job {code}" + (f" — {_ticket_when(claimed)}" if _ticket_when(claimed) else ""),
    )
    return ChatReply(
        text=_t(TICKET_CLAIMED, language).format(
            code=code,
            customer=" ".join(
                p for p in (claimed.get("customer_name"), claimed.get("customer_phone")) if p
            ) or "—",
            address=claimed.get("service_address") or "—",
            when=_ticket_when(claimed),
        ) + "\n" + _t(TICKET_CLAIMED_NEXT, language),
        entity_type="service_ticket", entity_id=str(claimed.get("id") or ""),
        quick_replies=[(f"เช็คอิน {code}"[:20], f"เช็คอิน {code}")],
    )


TICKET_ALREADY_TAKEN = {
    "th": "งาน {code} มีช่างรับไปแล้วครับ",
    "en": "{code} has already been taken by another technician.",
}
TICKET_ALREADY_MINE = {
    "th": "คุณรับงาน {code} ไว้แล้วครับ ถึงหน้างานแล้วพิมพ์ \"เช็คอิน\"",
    "en": "You already hold {code} — type \"check in\" when you arrive.",
}
TICKET_ALREADY_CLOSED = {
    "th": "งาน {code} {state}แล้วครับ ทำรายการนี้ไม่ได้ พิมพ์ \"งานของฉัน\" เพื่อดูงานที่ยังเปิดอยู่",
    "en": "{code} is {state} — nothing more to do on it. \"my jobs\" shows what is still open.",
}
CHECKIN_ALREADY = {
    "th": "งาน {code} เช็คอินไว้แล้วครับ (กำลังทำ) เสร็จแล้วพิมพ์ \"ปิดงาน\"",
    "en": "{code} is already checked in (in progress). Type \"check out\" when done.",
}
CHECKIN_NOTHING = {
    "th": "ไม่มีงานที่รอเช็คอินครับ รับงานก่อนแล้วค่อยเช็คอินเมื่อถึงหน้างาน",
    "en": "No job is waiting for a check-in. Take a job first, then check in on site.",
}
CHECKIN_PICK_ONE = {
    "th": "มีหลายงาน เลือกงานที่ถึงหน้างานครับ (หรือพิมพ์ \"เช็คอิน T-2026-0001\")",
    "en": "Several jobs — which one are you at? (or type \"check in T-2026-0001\")",
}
TICKET_TEAM_ACCEPTED = {
    "th": "รับงาน {code} ให้ทีมแล้ว ตอนนี้เปิดให้คนในทีมรับต่อ — คนที่จะไปพิมพ์ \"รับงาน {code}\"",
    "en": "{code} accepted for the team — it is open inside the team; whoever goes types \"claim {code}\"",
}
TICKET_TEAM_LEAD_FIRST = {
    "th": "งาน {code} มอบหมายให้ทีม หัวหน้าทีมต้องกดรับให้ทีมก่อน แล้วสมาชิกจึงรับต่อได้",
    "en": "{code} was given to the team — the team lead accepts it first, then a member takes it",
}
TICKET_TEAM_PHRASES = ("งานของทีม", "งานทีม", "งานในทีม", "ทีมมีงานไหม", "ทีมมีงานอะไรบ้าง", "งานทีมมีไหม", "team jobs", "ทีมมีงาน", "งานของทีมฉัน", "งานทีมฉัน", "my team's jobs")
TICKET_TEAM_EMPTY = {"th": "ตอนนี้ไม่มีงานของทีมที่รอคนรับครับ", "en": "No team jobs waiting right now."}


async def _notify_team_open(client: DataClient, license_id: str, ticket: dict, language: str) -> None:
    """Every member of the team hears the lead accepted, so whoever is
    free can take it."""
    team_id = str(ticket.get("assigned_to_ref") or "")
    if not team_id:
        return
    try:
        members = await client.list_team_members(license_id, team_id)
    except Exception:
        log.exception("could not list the team to announce an accepted job")
        return
    code = str(ticket.get("ticket_number") or "")
    text = f"หัวหน้าทีมรับงาน {code} ให้ทีมแล้ว ใครไปพิมพ์ \"รับงาน {code}\"\n{ticket.get('service_address') or ''}".strip()
    text_en = f"Your team lead accepted job {code} for the team. Whoever goes: type \"claim {code}\"\n{ticket.get('service_address') or ''}".strip()
    for member in members:
        uid = str(member.get("chann_uid") or "")
        if not uid:
            continue
        try:
            line_uid = await client.line_target_of(uid)
            await send_notification(
                client, license_id=license_id, target_chann_uid=uid, target_line_user_id=line_uid,
                type="sla_warning", message=text, message_en=text_en, entity_type="service_ticket",
                entity_id=str(ticket.get("id") or ""), language=language, oa="technician",
            )
        except Exception:
            log.exception("could not tell %s about the team job", uid)


TICKET_CLAIMED_NEXT = {
    "th": "ถึงหน้างานแล้วพิมพ์ \"เช็คอิน\" ก่อน แล้วค่อย \"ปิดงาน\" พร้อมรายงาน",
    "en": "On site, type \"check in\" first; \"finish\" with the report comes after.",
}
CHECKIN_PHOTO_HINT = {
    "th": "\nส่งรูปหน้างานมาในแชทนี้ได้เลย ระบบจะแนบกับงาน",
    "en": "\nSend a photo of the site here and it is attached to the job.",
}
CHECKOUT_NEEDS_CHECKIN = {
    "th": "งาน {code} ยังไม่ได้เช็คอินครับ ถึงหน้างานแล้วพิมพ์ \"เช็คอิน {code}\" ก่อน แล้วค่อยปิดงาน",
    "en": "{code} is not checked in yet. On site, type \"check in {code}\" first, then finish.",
}
TICKET_REJECT_TRIGGERS = ("ปฏิเสธงาน", "ไม่รับงาน", "รับงานไม่ได้", "ไม่สะดวกรับงาน", "decline job", "ปฏิเสธ", "decline", "ขอไม่รับ", "reject job")
TICKET_REJECTED = {
    "th": "ปฏิเสธงาน {code} แล้ว งานกลับไปที่ CS เพื่อมอบหมายใหม่ (ไม่ส่งต่อให้ใครอัตโนมัติ)",
    "en": "{code} declined. It is back with CS to reassign — nothing is passed on automatically.",
}
TICKET_REJECT_NEEDS_CODE = {
    "th": "ปฏิเสธงานไหนครับ พิมพ์ \"ปฏิเสธงาน T-2026-0001 เหตุผล...\"",
    "en": "Which job? Type \"decline job T-2026-0001 reason...\"",
}


async def _handle_ticket_reject(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """12.4: the assignee says no. Back to the dispatcher, who is told,
    and nobody else is given the job by the system."""
    if "ticket.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    match = TICKET_CODE_RE.search(message or "")
    code = match.group(1).upper() if match else ""
    reason = (message or "")
    for trigger in sorted(TICKET_REJECT_TRIGGERS, key=len, reverse=True):
        reason = reason.replace(trigger, "")
    if code:
        reason = reason.replace(code, "").replace(code.lower(), "")
    reason = _strip_polite_tail(reason.strip(" :-—,\n"))
    if _normalise(reason) in BARE_DECLINE_WORDS and reason.replace(" ", "") in ("ไม่รับ", "decline"):
        reason = ""
    elif _normalise(reason) in _DECLINE_ONLY_WORDS:
        # "ไม่ว่าง" / "ไปไม่ได้" alone: a decline, not yet a reason.
        reason = ""
    try:
        member = await client.get_member(license_id, ctx.chann_uid, channel=member_channel(ctx.oa))
        if member is None:
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        tickets = await client.list_tickets(license_id, visible_to=str(member["id"]))
        me = str(member["id"])
        mine_pending = [
            t for t in tickets
            if str(t.get("assigned_to_ref") or "") == me
            # A visit already under way is closed, not declined.
            and str(t.get("status") or "") not in ("completed", "cancelled", "in_progress")
        ]
        if not code:
            if len(mine_pending) == 1:
                code = str(mine_pending[0].get("ticket_number") or "").upper()
            elif len(mine_pending) > 1:
                return ChatReply(
                    text=_t(TICKET_REJECT_NEEDS_CODE, language),
                    quick_replies=[
                        (f"{t.get('ticket_number')}"[:20], f"ปฏิเสธงาน {t.get('ticket_number')}")
                        for t in mine_pending[:4]
                    ],
                )
            else:
                return ChatReply(text=_t(TICKET_REJECT_NEEDS_CODE, language))
        ticket = next(
            (t for t in tickets if str(t.get("ticket_number", "")).upper() == code), None,
        )
        if ticket is None:
            return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code))
        if str(ticket.get("status") or "") in ("completed", "cancelled"):
            return ChatReply(text=_t(TICKET_ALREADY_CLOSED, language).format(
                code=code, state=_label(TICKET_STATUS_LABELS, ticket.get("status"), language),
            ))
    except DataTierError as exc:
        return _field_service_failure(
            exc, code=code, language=language, template=TICKET_CLAIM_FAILED,
        )
    except Exception:
        log.exception("ticket reject failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    if not reason:
        # Declining sends the job back to the dispatcher and is not undone
        # by typing again: confirm, and get the reason the dispatcher will
        # read (review, 6 Sep 2026 — neither was asked).
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="reject", entity="ticket_reject",
                fields={"ticket_id": str(ticket["id"]), "code": code}, missing=["reason"],
                ttl_seconds=TICKET_REJECT_TTL_S,
            )
        except Exception:
            log.exception("could not hold a decline for confirmation")
        return ChatReply(
            text=_t(TICKET_REJECT_CONFIRM, language).format(code=code, when=_ticket_when(ticket) or "-"),
            quick_replies=[(r, r) for r in _t(TICKET_REJECT_REASONS, language)] + [("ไม่ปฏิเสธ", "ยกเลิก")],
        )
    return await _do_ticket_reject(
        client, ctx=ctx, license_id=license_id, ticket_id=str(ticket["id"]), code=code,
        member_id=me, reason=reason, language=language,
    )


async def _do_ticket_reject(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, ticket_id: str, code: str,
    member_id: str, reason: str, language: str,
) -> ChatReply:
    try:
        row = await client.reject_ticket(license_id, ticket_id, member_id, actor_id=ctx.chann_uid)
    except DataTierError as exc:
        return _field_service_failure(
            exc, code=code, language=language, template=TICKET_CLAIM_FAILED,
        )
    except Exception:
        log.exception("ticket reject failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    await notify_ticket_rejected(client, license_id, row, reason=reason, language=language)
    return ChatReply(
        text=_t(TICKET_REJECTED, language).format(code=code),
        entity_type="service_ticket", entity_id=str(row.get("id") or ""),
    )


async def _resolve_ticket_reject_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """The answer to "ปฏิเสธงาน T-… ใช่ไหม บอกเหตุผลด้วย": a reason (which is
    the confirmation), or a no."""
    fields = pending.get("fields") or {}
    code = str(fields.get("code") or "")
    await _drop_pending_quietly(client, ctx)
    if _normalise(message) in _REJECT_ABORT_WORDS:
        return ChatReply(
            text=_t(TICKET_REJECT_ABORTED, language).format(code=code),
            quick_replies=[("งานของฉัน", "งานของฉัน")],
        )
    if "ticket.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    reason = (message or "").strip()
    for word in ("ยืนยันปฏิเสธ", "ยืนยัน", "confirm"):
        if reason.lower().startswith(word):
            reason = reason[len(word):].strip(" :-—,")
    try:
        member = await client.get_member(str(license_id), ctx.chann_uid, channel=member_channel(ctx.oa))
    except Exception:
        member = None
    if member is None:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    return await _do_ticket_reject(
        client, ctx=ctx, license_id=str(license_id), ticket_id=str(fields.get("ticket_id") or ""),
        code=code, member_id=str(member["id"]), reason=reason or _t(TICKET_REJECT_NO_REASON, language),
        language=language,
    )


TICKET_REJECT_TTL_S = 600
_REJECT_ABORT_WORDS = frozenset({"ยกเลิก", "ไม่ปฏิเสธ", "ไม่", "cancel", "no", "ไม่เอา", "ไม่ยกเลิก", "รับ", "รับงาน"})
TICKET_REJECT_CONFIRM = {
    "th": "ปฏิเสธงาน {code} (นัด {when}) ใช่ไหมครับ บอกเหตุผลสั้น ๆ ด้วย — ทางร้านจะเห็นและมอบหมายใหม่",
    "en": "Decline {code} (scheduled {when})? Give a short reason — the shop sees it and reassigns.",
}
TICKET_REJECT_REASONS = {
    "th": ("ไม่ว่างวันนั้น", "อยู่ไกลเกินไป", "งานเต็มแล้ว", "ไม่ถนัดงานนี้"),
    "en": ("not free that day", "too far away", "fully booked", "not my speciality"),
}
TICKET_REJECT_ABORTED = {
    "th": "ไม่ปฏิเสธงาน {code} ครับ งานยังอยู่กับคุณ",
    "en": "{code} stays with you.",
}
TICKET_REJECT_NO_REASON = {"th": "ไม่ได้ระบุเหตุผล", "en": "no reason given"}


async def notify_ticket_rejected(
    client: DataClient, license_id: str, row: dict, *, reason: str = "", language: str = "th",
) -> None:
    """The dispatcher hears it in LINE, with the reason — the whole point
    of not auto-reassigning is that a person decides next."""
    code = str(row.get("ticket_number") or "")
    text = f"ช่างปฏิเสธงาน {code}" + (f": {reason}" if reason else "") + "\nงานกลับมารอมอบหมายใหม่"
    text_en = f"The technician declined job {code}" + (f": {reason}" if reason else "") + "\nIt is back in the queue for reassignment"
    try:
        await _notify_ticket_change(client, str(license_id), str(row.get("id") or ""), text, language, text_en=text_en)
    except Exception:
        log.exception("could not announce a rejected ticket")


# ------------------------------------------------------ Phase 14-B approvals
#
# The chat side of the approval workflow. Every write goes through
# services/approval.py — the same function the dashboard routes call — so
# an approval from a phone and one from the queue page are the same
# transaction, the same notifications, the same survey.

APPROVAL_POLICY_TRIGGERS = (
    "ตั้งการอนุมัติ", "ตั้งขั้นตอนอนุมัติ", "ตั้งกฎอนุมัติ", "approval policy",
)
APPROVAL_POLICY_CONFIRM = ("ยืนยันการอนุมัติ", "ยืนยันขั้นตอนอนุมัติ", "confirm approval flow")
APPROVAL_POLICY_SHOW = (
    "ดูการอนุมัติปัจจุบัน", "การอนุมัติปัจจุบัน", "ดูขั้นตอนอนุมัติ", "ขั้นตอนอนุมัติ",
    "show approval flow",
)
APPROVAL_LIST_PHRASES = (
    "รายการรออนุมัติ", "รออนุมัติ", "งานรออนุมัติ", "รายงานรออนุมัติ", "รอตรวจ",
    "pending approvals", "pending", "อะไรรอฉัน", "อะไรรอฉันอยู่", "อะไรรอฉันอยู่บ้าง", "มีอะไรรอฉัน", "รอฉันอนุมัติ",
    "รอฉันตรวจ", "มีอะไรต้องอนุมัติ", "ต้องอนุมัติอะไรบ้าง", "รายการอนุมัติ", "approvals", "waiting for me", "to approve",
)
APPROVAL_REJECT_TRIGGERS = ("ไม่อนุมัติ", "ตีกลับ", "reject", "ไม่ผ่าน", "ไม่ให้ผ่าน", "ส่งกลับไปแก้", "rejected", "ให้แก้ใหม่")
APPROVAL_APPROVE_TRIGGERS = ("อนุมัติ", "approve", "ผ่าน", "โอเคผ่าน", "ผ่านได้", "approved", "ok approve", "ให้ผ่าน")


def _asks_for_approval_list(message: str) -> bool:
    """"มีอะไรรออนุมัติไหม", "ต้องอนุมัติอะไรบ้าง": a question about the
    queue, which used to approve the first report in it (review, 6 Sep
    2026). A sentence that names a record is a note, not this."""
    lowered = (message or "").lower()
    if re.search(r"[CDQT]-\d{4}-\d{4}", message or "", re.I):
        return False
    if any(w in lowered for w in ("รออนุมัติ", "รอตรวจ", "pending approval", "awaiting approval")):
        return _looks_like_a_question(message) or len(_normalise(message)) <= 24
    return _looks_like_a_question(message) and "อนุมัติ" in lowered and "ไม่อนุมัติ" not in lowered

APPROVAL_NOT_AN_APPROVER = {
    "th": "ไม่มีรายงานที่รอคุณอนุมัติครับ (บทบาทของคุณไม่ได้อยู่ในขั้นตอนอนุมัติ — เจ้าของร้านเพิ่มสิทธิ์ให้ได้ที่ แดชบอร์ด > บทบาทและทีม)",
    "en": "Nothing is waiting for your approval (your role is not an approval step — the owner can add that under dashboard > roles and team).",
}
APPROVAL_NONE_PENDING = {
    "th": "ไม่มีรายงานที่รอคุณอนุมัติตอนนี้ครับ",
    "en": "Nothing is waiting for your approval right now.",
}
APPROVAL_LIST_HEAD = {"th": "รายงานที่รอคุณตรวจ:", "en": "Waiting for your review:"}
APPROVAL_LIST_LINE = {
    "th": "· {report} — {ticket}{customer}{found}",
    "en": "· {report} — {ticket}{customer}{found}",
}
APPROVAL_PICK_ONE = {
    "th": "มีหลายรายการรออยู่ เลือกรายงานที่ต้องการครับ",
    "en": "Several are waiting — pick one.",
}
APPROVAL_NOT_YOURS = {
    "th": "ไม่มีรายงาน {code} ที่รอคุณอนุมัติครับ อาจอนุมัติไปแล้ว หรือยังไม่ถึงขั้นของคุณ",
    "en": "{code} is not waiting for you — it may be done already, or not at your step yet.",
}
APPROVAL_APPROVED = {
    "th": "อนุมัติ {code} แล้ว{next}",
    "en": "Approved {code}.{next}",
}
APPROVAL_NEXT_SURVEY_SENT = {
    "th": "\nครบทุกขั้นแล้ว ส่งแบบประเมินความพึงพอใจให้ลูกค้าแล้ว",
    "en": "\nAll steps passed — the customer has been sent the survey.",
}
APPROVAL_NEXT_SURVEY_NOT_SENT = {
    "th": "\nครบทุกขั้นแล้ว (ลูกค้าไม่มี LINE ที่ผูกไว้ จึงยังไม่ได้ส่งแบบประเมิน)",
    "en": "\nAll steps passed (the customer has no LINE on file, so no survey was sent).",
}
APPROVAL_NEXT_STEP = {
    "th": "\nส่งต่อให้ขั้นถัดไปแล้ว",
    "en": "\nPassed on to the next approver.",
}
APPROVAL_REJECTED = {
    "th": "ตีกลับ {code} แล้ว{reason}\nแจ้งช่างให้แก้แล้ว",
    "en": "Rejected {code}{reason}. The technician has been told.",
}
APPROVAL_REJECT_NEEDS_REASON = {
    "th": "บอกเหตุผลด้วยครับ เช่น \"ไม่อนุมัติ {code} รูปไม่ครบ\" — ช่างจะได้รู้ว่าต้องแก้อะไร",
    "en": "Add a reason, e.g. \"reject {code} photos missing\" — the technician needs to know what to fix.",
}
APPROVAL_ACT_CONFLICT = {
    "th": "ทำรายการกับ {code} ไม่ได้ครับ อาจมีคนอนุมัติไปแล้ว หรือยังไม่ถึงขั้นของคุณ",
    "en": "Could not act on {code} — someone may have already, or it is not at your step.",
}
APPROVAL_POLICY_NEEDS_TEXT = {
    "th": "พิมพ์นโยบายต่อท้ายด้วย เช่น \"ตั้งการอนุมัติ ให้ CS ก่อน แล้วต่อด้วย admin\"",
    "en": "Add the policy, e.g. \"approval policy: CS first, then admin\".",
}
APPROVAL_POLICY_NOT_UNDERSTOOD = {
    "th": "ยังแปลงเป็นขั้นตอนอนุมัติไม่ได้:\n{problems}\n\nบอกได้ว่าใครตรวจก่อน ใครตรวจต่อ เช่น \"ให้ CS ก่อน แล้วต่อด้วย admin\"",
    "en": "Could not turn that into an approval flow:\n{problems}",
}
APPROVAL_POLICY_REVIEW = {
    "th": "นี่คือขั้นตอนที่ได้ ตรวจดูก่อนบันทึก:\n\n{summary}\n\nถ้าถูกต้องพิมพ์ \"ยืนยันการอนุมัติ\"",
    "en": "Here is the flow — check it before saving:\n\n{summary}\n\nType \"confirm approval flow\" to save.",
}
APPROVAL_POLICY_SAVED = {
    "th": "บันทึกขั้นตอนอนุมัติแล้ว รายงานที่ปิดงานหลังจากนี้จะเดินตามนี้\n\n{summary}",
    "en": "Approval flow saved. Reports checked out from now on follow it.\n\n{summary}",
}
APPROVAL_POLICY_NOTHING_PENDING = {
    "th": "ยังไม่มีขั้นตอนที่รอยืนยัน พิมพ์ \"ตั้งการอนุมัติ ...\" ก่อน",
    "en": "No approval flow is waiting for confirmation.",
}
APPROVAL_POLICY_CURRENT = {
    "th": "ขั้นตอนอนุมัติปัจจุบัน:\n{summary}",
    "en": "Current approval flow:\n{summary}",
}
SURVEY_THANKS = {
    "th": "ขอบคุณครับ บันทึกคะแนน \"{label}\" ให้งาน {ticket} แล้ว",
    "en": "Thank you — \"{label}\" recorded for job {ticket}.",
}
SURVEY_ALREADY_ANSWERED = {
    "th": "ตอบแบบประเมินของงานนี้ไปแล้วครับ ขอบคุณครับ",
    "en": "This job's survey was already answered — thank you.",
}
SURVEY_OFF_SCALE = {
    "th": "เลือกได้เฉพาะ {scale} ครับ",
    "en": "Please pick one of {scale}.",
}
APPROVAL_DRAFT_TTL_S = 900


async def _approval_candidates(
    client: DataClient, *, ctx: ResolvedContext, license_id: str,
) -> list[tuple[dict, dict, dict]]:
    """(step, report, ticket) for every step waiting on this person."""
    from . import approval as approval_service

    steps = await approval_service.pending_for_actor(
        client, license_id=license_id, chann_uid=ctx.chann_uid,
    )
    if not steps:
        return []
    reports = {str(r.get("id")): r for r in await client.list_service_reports(license_id)}
    tickets = {str(t.get("id")): t for t in await client.list_tickets(license_id)}
    out = []
    for step in steps:
        report = reports.get(
            str(step.get("entity_id")), {"id": step.get("entity_id"), "report_id": ""},
        )
        ticket = tickets.get(str(report.get("ticket_id") or ""), {})
        out.append((step, report, ticket))
    return out


def _approval_line(report: dict, ticket: dict, language: str) -> str:
    found = str((report.get("report_data") or {}).get("found_issue") or "").strip()
    return _t(APPROVAL_LIST_LINE, language).format(
        report=report.get("report_id") or "",
        ticket=ticket.get("ticket_number") or "",
        customer=f" · {ticket['customer_name']}" if ticket.get("customer_name") else "",
        found=f"\n   {found[:60]}" if found else "",
    )


async def _handle_approval_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
) -> ChatReply:
    if "approval.view" not in set(permission_keys):
        # The tile is on every sales menu; a member who is not an approver
        # gets the truthful answer, not a permission wall (review, 6 Sep 2026).
        return ChatReply(
            text=_t(APPROVAL_NOT_AN_APPROVER, language),
            quick_replies=[("งานวันนี้", "งานวันนี้"), ("รายการดีล", "รายการดีล")],
        )
    try:
        candidates = await _approval_candidates(client, ctx=ctx, license_id=str(license_id))
    except Exception:
        log.exception("approval list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    if not candidates:
        return ChatReply(text=_t(APPROVAL_NONE_PENDING, language))
    lines = [_t(APPROVAL_LIST_HEAD, language)] + [
        _approval_line(report, ticket, language)
        for _, report, ticket in candidates[:LIST_LIMIT]
    ]
    return ChatReply(
        text="\n".join(lines),
        quick_replies=[
            (str(report.get("report_id") or ""), f"อนุมัติ {report.get('report_id')}")
            for _, report, _ in candidates[:4] if report.get("report_id")
        ],
    )


async def _handle_approval_act(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, approve: bool, trigger: str,
) -> ChatReply:
    """Approve or reject one report: by code, by the record just looked at
    (or replied to), or — when exactly one is waiting — by itself."""
    from . import approval as approval_service

    needed = "approval.approve" if approve else "approval.reject"
    if needed not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)

    match = SERVICE_REPORT_CODE_RE.search(message or "")
    code = match.group(1).upper() if match else ""

    try:
        candidates = await _approval_candidates(client, ctx=ctx, license_id=license_id)
    except Exception:
        log.exception("could not list pending approvals")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not code and len(candidates) != 1:
        # The report we were just looking at — or the one whose
        # notification this message replies to (handle_reply seeds it).
        # Only when the queue does not answer by itself: with ONE report
        # waiting, a bare "อนุมัติ" means that one, not the one rejected a
        # moment ago (review, 6 Sep 2026).
        try:
            ref = await _last_entity_ref(client, ctx)
        except Exception:
            ref = None
        if ref and ref.get("entity_type") == "service_report" and ref.get("code"):
            code = str(ref["code"]).upper()

    if code:
        chosen = next(
            (c for c in candidates if str(c[1].get("report_id") or "").upper() == code), None,
        )
        if chosen is None:
            return ChatReply(text=_t(APPROVAL_NOT_YOURS, language).format(code=code))
    elif len(candidates) == 1:
        chosen = candidates[0]
    elif not candidates:
        return ChatReply(text=_t(APPROVAL_NONE_PENDING, language))
    else:
        # Buttons, one per report, re-sending the same command with the
        # code — never a guess (rule 3).
        return ChatReply(
            text=_t(APPROVAL_PICK_ONE, language) + "\n"
            + "\n".join(_approval_line(r, t, language) for _, r, t in candidates[:LIST_LIMIT]),
            quick_replies=[
                (str(r.get("report_id") or ""), f"{trigger} {r.get('report_id')}")
                for _, r, _ in candidates[:4] if r.get("report_id")
            ],
        )

    step, report, _ticket = chosen
    report_code = str(report.get("report_id") or code)
    reason: str | None = None
    if not approve:
        rest = message or ""
        if code:
            rest = re.sub(re.escape(code), " ", rest, flags=re.IGNORECASE)
        index = rest.lower().find(trigger.lower())
        if index >= 0:
            rest = rest[index + len(trigger):]
        rest = re.sub(r"^\s*(?:ครับ|ค่ะ|คะ|คับ|นะ|จ้า|นะครับ|นะคะ)+", "", rest.strip())
        reason = rest.strip(" :·-\n,") or None
        if not reason:
            return ChatReply(
                text=_t(APPROVAL_REJECT_NEEDS_REASON, language).format(code=report_code),
            )

    try:
        result = await approval_service.act(
            client, license_id=license_id, step_id=str(step.get("id") or ""),
            approve=approve, actor_chann_uid=ctx.chann_uid, reason=reason, language=language,
        )
    except DataTierError as exc:
        if exc.status_code in (404, 409):
            return ChatReply(text=_t(APPROVAL_ACT_CONFLICT, language).format(code=report_code))
        log.exception("approval act refused: %s", exc.detail)
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("approval act failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _remember_entity(
        client, ctx, entity_type="service_report",
        entity_id=str(report.get("id") or ""), code=report_code,
    )
    if approve:
        status = result.get("report_status")
        if status == "approved":
            tail = APPROVAL_NEXT_SURVEY_SENT if result.get("survey_sent") else APPROVAL_NEXT_SURVEY_NOT_SENT
        else:
            tail = APPROVAL_NEXT_STEP
        text = _t(APPROVAL_APPROVED, language).format(code=report_code, next=_t(tail, language))
        if result.get("document_url"):
            text += f"\nPDF (7 วัน): {result['document_url']}"
    else:
        text = _t(APPROVAL_REJECTED, language).format(
            code=report_code, reason=f": {reason}" if reason else "",
        )
    return ChatReply(
        text=text, entity_type="service_report", entity_id=str(report.get("id") or ""),
        quick_replies=[("รายการรออนุมัติ", "รายการรออนุมัติ")],
    )


async def _handle_approval_policy(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    trigger: str, permission_keys: list[str], language: str, ai_client=None,
) -> ChatReply:
    """Owner decision 3 (PHASE14_PLAN): the flow is changed by typing it.
    Same shape as the assignment policy — model once, show back, confirm."""
    from . import approval as approval_service
    from .ai.approval_policy import policy_to_workflow

    if "approval.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    lowered = message.lower()
    index = lowered.find(trigger.lower())
    policy = message[index + len(trigger):].strip(" :·-") if index >= 0 else ""
    if not policy:
        return ChatReply(text=_t(APPROVAL_POLICY_NEEDS_TEXT, language))

    license_id = str(license_id)
    try:
        roles = [str(r.get("role_name")) for r in await client.list_roles(license_id) if r.get("role_name")]
    except Exception:
        log.exception("could not read roles for an approval policy")
        roles = []

    rules, problems = await policy_to_workflow(policy, roles=roles, client=ai_client)
    if rules is None:
        return ChatReply(
            text=_t(APPROVAL_POLICY_NOT_UNDERSTOOD, language).format(
                problems="\n".join(f"· {p}" for p in problems)
            )
        )
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa,
        action="confirm", entity="approval_workflow",
        fields={"rules": rules}, missing=[], ttl_seconds=APPROVAL_DRAFT_TTL_S,
    )
    return ChatReply(
        text=_t(APPROVAL_POLICY_REVIEW, language).format(
            summary=approval_service.describe_workflow(rules, language),
        ),
        quick_replies=[("ยืนยันการอนุมัติ", "ยืนยันการอนุมัติ")],
    )


async def _handle_approval_policy_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    pending: dict | None, permission_keys: list[str], language: str,
) -> ChatReply:
    from . import approval as approval_service

    if "approval.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not pending or pending.get("entity") != "approval_workflow":
        return ChatReply(text=_t(APPROVAL_POLICY_NOTHING_PENDING, language))
    rules = (pending.get("fields") or {}).get("rules")
    if not isinstance(rules, dict):
        return ChatReply(text=_t(APPROVAL_POLICY_NOTHING_PENDING, language))
    try:
        await approval_service.replace_workflow(
            client, license_id=str(license_id), rules_json=rules, actor_chann_uid=ctx.chann_uid,
        )
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        log.exception("saving an approval workflow failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return ChatReply(
        text=_t(APPROVAL_POLICY_SAVED, language).format(
            summary=approval_service.describe_workflow(rules, language),
        ),
    )


async def _handle_approval_policy_show(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    from . import approval as approval_service

    if not {"approval.manage", "approval.view"} & set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        workflow = await approval_service.current_workflow(client, license_id=str(license_id))
    except Exception:
        log.exception("could not read the approval workflow")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return ChatReply(
        text=_t(APPROVAL_POLICY_CURRENT, language).format(
            summary=approval_service.describe_workflow(
                (workflow or {}).get("rules_json") or {}, language,
            ),
        ),
    )


async def _maybe_answer_survey(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, language: str,
) -> ChatReply | None:
    """A customer's "2" (or "ดีเยี่ยม") when a survey is waiting for them.
    None when nothing is waiting or the text is not an answer, so the
    message falls through to whatever it was before."""
    from . import approval as approval_service

    text = (message or "").strip()
    score: int | None = None
    if text.isdigit() and len(text) == 1:
        score = int(text)
    else:
        for key, label in approval_service.DEFAULT_SCALE.items():
            if text == label:
                score = int(key)
    if score is None:
        return None
    try:
        survey, ticket = await approval_service.pending_survey_for_customer(
            client, license_id=str(license_id), customer_chann_uid=ctx.chann_uid,
        )
    except Exception:
        log.exception("could not look for a pending survey")
        return None
    if survey is None:
        return None
    scale = survey.get("scale_config_json") or approval_service.DEFAULT_SCALE
    if str(score) not in scale:
        return ChatReply(
            text=_t(SURVEY_OFF_SCALE, language).format(
                scale=", ".join(f"{k} ({v})" for k, v in sorted(scale.items())),
            ),
            quick_replies=[(f"{k} {v}", str(k)) for k, v in sorted(scale.items())],
        )
    try:
        await approval_service.answer_survey(
            client, license_id=str(license_id), survey_id=str(survey["id"]),
            score=score, comment=None, actor_chann_uid=ctx.chann_uid,
        )
    except DataTierError as exc:
        if exc.status_code == 409:
            return ChatReply(text=_t(SURVEY_ALREADY_ANSWERED, language))
        log.exception("survey answer refused: %s", exc.detail)
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("survey answer failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return ChatReply(
        text=_t(SURVEY_THANKS, language).format(
            label=scale.get(str(score), str(score)),
            ticket=(ticket or {}).get("ticket_number") or "",
        ),
    )


# ---------------------------------------------- Phase 11 assignment policy
#
# 11.6: an owner types a policy, the AI turns it into rule JSON, the rule
# is shown back in words, and only a confirmed rule is saved. The runtime
# engine never sees the prose — see chann_data/assignment_engine.py.

ASSIGN_POLICY_TRIGGERS = ("ตั้งกฎมอบหมาย", "กฎมอบหมาย", "ตั้งกฎงาน", "assignment rule")
ASSIGN_POLICY_SHOW = ("ดูกฎมอบหมาย", "กฎมอบหมายปัจจุบัน", "show assignment rule")
ASSIGN_CONFIRM = ("ยืนยันกฎ", "confirm rule")

POLICY_NEEDS_TEXT = {
    "th": "พิมพ์นโยบายต่อท้ายด้วย เช่น \"ตั้งกฎมอบหมาย ช่างที่รับผิดชอบแอร์ ให้ทีม AC ไม่เกินวันละ 5 งาน\"",
    "en": "Add the policy, e.g. \"assignment rule: AC work goes to the AC team, max 5 a day\".",
}
POLICY_NOT_UNDERSTOOD = {
    "th": "ยังแปลงนโยบายเป็นกฎไม่ได้:\n{problems}\n\nลองระบุให้ชัดขึ้นว่า งานประเภทไหน ให้ทีมไหน และจำกัดวันละกี่งาน",
    "en": "Could not turn that into a rule:\n{problems}",
}
POLICY_REVIEW = {
    "th": "นี่คือกฎที่ได้ ตรวจดูก่อนบันทึก:\n\n{summary}\n\nถ้าถูกต้องพิมพ์ \"ยืนยันกฎ\"",
    "en": "Here is the rule — check it before saving:\n\n{summary}\n\nType \"confirm rule\" to save.",
}
POLICY_SAVED = {
    "th": "บันทึกกฎมอบหมายแล้ว\n\n{summary}",
    "en": "Assignment rule saved.\n\n{summary}",
}
POLICY_NOTHING_PENDING = {
    "th": "ยังไม่มีกฎที่รอยืนยัน พิมพ์ \"ตั้งกฎมอบหมาย ...\" ก่อน",
    "en": "No rule is waiting for confirmation.",
}
POLICY_NONE_SET = {
    "th": "ยังไม่ได้ตั้งกฎมอบหมาย",
    "en": "No assignment rule set yet.",
}

# The draft waits here between "here is the rule" and "confirm". Same Redis
# pattern and TTL reasoning as pending_intent: a rule someone walked away
# from must not still be waiting an hour later.
ASSIGNMENT_DRAFT_TTL_S = 900


async def _handle_assignment_policy(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    trigger: str, permission_keys: list[str], language: str, ai_client=None,
) -> ChatReply:
    from .ai.assignment_policy import describe_rule, policy_to_rule

    # setting.manage, not a dedicated key: this is company configuration,
    # and the spec gives assignment rules no permission of their own.
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    lowered = message.lower()
    index = lowered.find(trigger.lower())
    policy = message[index + len(trigger):].strip(" :·-") if index >= 0 else ""
    if not policy:
        return ChatReply(text=_t(POLICY_NEEDS_TEXT, language))

    license_id = str(license_id)
    try:
        teams = await client.list_technician_teams(license_id)
    except Exception:
        log.exception("could not read teams for a policy translation")
        teams = []

    rule, problems = await policy_to_rule(
        policy,
        teams=[str(t.get("team_name")) for t in teams if t.get("team_name")],
        client=ai_client,
    )
    if rule is None:
        return ChatReply(
            text=_t(POLICY_NOT_UNDERSTOOD, language).format(
                problems="\n".join(f"· {p}" for p in problems)
            )
        )

    # Held, not saved. 11.6 requires the person to see it first — a rule
    # decides who gets work, and a model's reading of a sentence is not a
    # good enough reason to change that unseen.
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa,
        action="confirm", entity="assignment_rule",
        fields={"rule": rule}, missing=[],
        ttl_seconds=ASSIGNMENT_DRAFT_TTL_S,
    )
    return ChatReply(
        text=_t(POLICY_REVIEW, language).format(summary=describe_rule(rule, language)),
        quick_replies=[("ยืนยันกฎ", "ยืนยันกฎ")],
    )


async def _handle_assignment_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    pending: dict | None, permission_keys: list[str], language: str,
) -> ChatReply:
    from .ai.assignment_policy import describe_rule

    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not pending or pending.get("entity") != "assignment_rule":
        return ChatReply(text=_t(POLICY_NOTHING_PENDING, language))

    rule = (pending.get("fields") or {}).get("rule")
    if not isinstance(rule, dict):
        return ChatReply(text=_t(POLICY_NOTHING_PENDING, language))

    try:
        await client.upsert_assignment_rule(
            str(license_id), scope=rule.get("scope", "technician"),
            rules_json=rule, actor_id=ctx.chann_uid,
        )
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        log.exception("saving an assignment rule failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(POLICY_SAVED, language).format(summary=describe_rule(rule, language)),
    )


async def _handle_assignment_show(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    from .ai.assignment_policy import describe_rule

    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        rules = await client.get_assignment_rules(str(license_id))
    except Exception:
        log.exception("could not read assignment rules")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    active = [r for r in rules if r.get("is_active")]
    if not active:
        return ChatReply(text=_t(POLICY_NONE_SET, language))
    return ChatReply(
        text="\n\n".join(describe_rule(r.get("rules_json") or {}, language) for r in active)
    )


# ------------------------------------------------ Phase 10 issue a quote

QUOTE_ISSUE_TRIGGERS = ("ออกเอกสาร", "ออกใบเสนอราคา", "issue quote")
# Re-issuing is legitimate after a real correction, but must be asked for.
QUOTE_REISSUE_PHRASES = ("ออกเอกสารใหม่", "ออกซ้ำ", "reissue")

# 7 days: long enough that a customer who opens the message over a weekend
# still gets the file, short enough that a link forwarded on has stopped
# working well before the quote itself is stale.
QUOTE_LINK_TTL_SECONDS = 7 * 24 * 3600

QUOTE_ISSUED = {
    "th": "ออกเอกสาร {quote_id} เรียบร้อยแล้ว\nลิงก์ดาวน์โหลด (ใช้ได้ 7 วัน):\n{url}\n\nSHA-256: {sha}",
    "en": "Issued {quote_id}.\nDownload link (valid 7 days):\n{url}\n\nSHA-256: {sha}",
}
QUOTE_ISSUED_NO_LINK = {
    "th": "ออกเอกสาร {quote_id} เรียบร้อยแล้ว แต่สร้างลิงก์ดาวน์โหลดไม่สำเร็จ — เปิดจากหน้าแดชบอร์ดแทนได้\nSHA-256: {sha}",
    "en": "Issued {quote_id}, but could not create a download link — use the dashboard instead.\nSHA-256: {sha}",
}
QUOTE_ALREADY_ISSUED = {
    "th": "ใบเสนอราคา {quote_id} มีเอกสารที่ออกไปแล้ว ถ้าต้องการออกฉบับใหม่พิมพ์ \"ออกเอกสารใหม่ {quote_id}\"",
    "en": "Quote {quote_id} already has an issued document. To issue another, say \"reissue {quote_id}\".",
}
QUOTE_COMPANY_INCOMPLETE = {
    "th": "ยังออกเอกสารไม่ได้ — ข้อมูลบริษัทไม่ครบ ({detail})\nพิมพ์ \"ข้อมูลบริษัท\" เพื่อดูว่าขาดอะไร",
    "en": "Cannot issue yet — the company profile is incomplete ({detail}).",
}
QUOTE_ISSUE_FAILED = {
    "th": "ออกเอกสารไม่สำเร็จ: {detail}",
    "en": "Could not issue the document: {detail}",
}


def document_download_url(license_id: str, document_id: str) -> str | None:
    """A tappable link to an issued document, or None when it cannot be built.

    Returns None rather than a broken URL for the same reason dashboard_link
    does: a link that fails when tapped is worse than a message that admits
    it has none.
    """
    from ..auth.document_link import issue_document_token
    from ..config import settings

    base = (settings.public_base_url or "").rstrip("/")
    if not base or not document_id:
        return None
    try:
        token = issue_document_token(license_id, document_id)
    except Exception:
        log.exception("could not issue a document link token")
        return None
    return f"{base}/api/v1/documents/{token}"


async def _handle_quote_issue(
    client: DataClient, *, license_id, code: str, permission_keys: list[str],
    language: str, actor_id: str, allow_reissue: bool,
) -> ChatReply:
    from .documents.snapshot import QuoteNotRenderable
    from .quote_issue import QuoteAlreadyIssued, issue_quote_document
    from .storage.base import (
        DocumentStoreError, DocumentStoreNotConfigured, get_document_store,
    )

    if "quote.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not code:
        return ChatReply(text=_t(SEARCH_NEEDS_TERM, language))

    license_id = str(license_id)
    try:
        quotes = await client.list_quotes(license_id)
    except Exception:
        log.exception("quote lookup failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    wanted = code.strip().lower()
    quote = next((q for q in quotes if str(q.get("quote_id") or "").lower() == wanted), None)
    if quote is None:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(
                what="ใบเสนอราคา" if language == "th" else "quote", code=code
            )
        )

    try:
        deal = await client.get_deal(license_id, str(quote["deal_id"]))
        customer = await client.get_customer(license_id, str(deal["contact_id"]))
        company = await client.get_company_profile(license_id)
        document = await issue_quote_document(
            client, license_id=license_id, quote=quote, deal=deal, customer=customer,
            company=company, actor_id=actor_id, allow_reissue=allow_reissue,
        )
    except QuoteAlreadyIssued:
        return ChatReply(
            text=_t(QUOTE_ALREADY_ISSUED, language).format(quote_id=quote.get("quote_id")),
            quick_replies=[("ออกเอกสารใหม่", f"ออกเอกสารใหม่ {quote.get('quote_id')}")],
        )
    except QuoteNotRenderable as exc:
        return ChatReply(
            text=_t(QUOTE_COMPANY_INCOMPLETE, language).format(detail=str(exc)),
            quick_replies=[("ดูข้อมูลบริษัท", "ข้อมูลบริษัท")],
        )
    except DocumentStoreNotConfigured as exc:
        return ChatReply(text=_t(QUOTE_ISSUE_FAILED, language).format(detail=str(exc)))
    except Exception as exc:  # noqa: BLE001
        log.exception("quote issue failed")
        return ChatReply(text=_t(QUOTE_ISSUE_FAILED, language).format(detail=str(exc)[:160]))

    sha = str(document.get("sha256") or "")[:16]
    # A link served by this application, not a GCS signed URL. Signing one
    # needs iam.serviceAccounts.signBlob on the signing service account,
    # which roles/editor does not grant and this project does not add — in
    # production every issue ended with "could not create a download link"
    # and no file, for a document that existed and was simply unreachable.
    url = document_download_url(str(license_id), str(document.get("id") or ""))
    if not url:
        log.error("issued document has no id or no public base URL configured")
        return ChatReply(
            text=_t(QUOTE_ISSUED_NO_LINK, language).format(
                quote_id=quote.get("quote_id"), sha=sha
            ),
            entity_type="quote", entity_id=str(quote.get("id") or ""),
        )

    return ChatReply(
        text=_t(QUOTE_ISSUED, language).format(
            quote_id=quote.get("quote_id"), url=url, sha=sha
        ),
        entity_type="quote",
        entity_id=str(quote.get("id") or ""),
        quick_replies=[("รายการใบเสนอราคา", "รายการใบเสนอราคา")],
    )


# ------------------------------------------- Phase 10 list / detail views
#
# Master Spec 9.2 lists these, and every phase so far shipped only the
# `create` half of each entity: ACTION_PERMISSIONS already registers
# ("read", "customer") and ("read", "deal"), but no handler implemented
# them, so "ดูรายชื่อลูกค้า" passed the permission gate and then fell
# through to nothing. These are the reads that make the rest usable —
# without them a salesperson can enter data all day and never see it back.
#
# Matched deterministically, like the other closed-vocabulary commands:
# the point of a list command is that it always works, and routing "ดู
# รายชื่อลูกค้า" through a model that might mis-parse it to a create is a
# bad trade for no benefit.

# Ten is what fits in a LINE bubble without the person having to scroll
# past the reply to find the next message. Beyond that the list stops being
# readable in chat, so the answer is a link to the dashboard rather than a
# longer wall of text.
LIST_LIMIT = 10

# Deep-link sub-paths, RELATIVE to the LIFF app's configured endpoint URL.
#
# This is the part that is easy to get wrong and did get wrong: LINE
# resolves https://liff.line.me/{id}/{path} by APPENDING {path} to the
# endpoint URL. The Sales endpoint is already .../liff/sales, so a link
# built with the full "/liff/sales/customers" resolved to
# .../liff/sales/liff/sales/customers — a page that does not exist. Only
# the part after the endpoint belongs here.
DASHBOARD_PATHS = {
    "customers": "customers",
    "deals": "deals",
    "products": "products",
    "quotes": "quotes",
    "company": "company",
    "warranties": "warranties",
    "teams": "teams",
    # These pages exist under presentation/app/liff/sales and had no path
    # here, so every deep link to them came back None — the person was told
    # "try the dashboard" with nothing to tap and no page named.
    "tickets": "tickets",
    "reports": "reports",
    "approvals": "approvals",
    "roles": "roles",
    "members": "members",
    "guide": "guide",
    "chats": "chats",
    # The document-templates page, so a chat-designed draft can be opened,
    # previewed and published on screen as well as from the reply.
    "templates": "templates",
    "index": "",
}


# The uri tiles fall back to these texts when no LIFF id is configured
# (scripts/richmenu-apply.sh FALLBACK_*); without a handler they went to
# the model and came back "ระบบไม่พร้อม" (review, 6 Sep 2026).
DASHBOARD_OPEN_PHRASES = (
    "เปิดแดชบอร์ด", "แดชบอร์ด", "เปิดหน้าจอ", "เปิดหน้าจอช่าง", "เปิดหน้าจอลูกค้า", "หน้าจอช่าง", "หน้าจอลูกค้า",
    "แชทลูกค้า", "open dashboard", "open the dashboard", "dashboard", "customer chats",
)
DASHBOARD_OPEN = {
    "th": "เปิดหน้าจอได้ที่ลิงก์นี้ครับ\n{url}",
    "en": "Open it here:\n{url}",
}
DASHBOARD_NOT_CONFIGURED = {
    "th": "ยังไม่ได้ตั้งค่าหน้าจอ (LIFF) ของร้านนี้ครับ ทุกอย่างทำในแชทได้เหมือนกัน พิมพ์ \"วิธีใช้\" เพื่อดูคำสั่ง",
    "en": "The dashboard (LIFF) is not set up for this shop yet. Everything works in chat too — type \"help\" for the commands.",
}
_DASHBOARD_FALLBACK_BUTTONS = {
    "sales": [("งานวันนี้", "งานวันนี้"), ("รายชื่อลูกค้า", "รายชื่อลูกค้า"), ("วิธีใช้", "วิธีใช้")],
    "technician": [("งานของฉัน", "งานของฉัน"), ("งานที่เปิดรับ", "งานที่เปิดรับ"), ("วิธีใช้", "วิธีใช้")],
    "customer": [("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน"), ("วิธีใช้", "วิธีใช้")],
}


def _dashboard_open_reply(oa: str, message: str, language: str) -> ChatReply:
    section = "chats" if _matches_phrase(message, ("แชทลูกค้า", "customer chats")) else "index"
    url = dashboard_link(section, oa)
    if url:
        return ChatReply(
            text=_t(DASHBOARD_OPEN, language).format(url=url),
            quick_reply_url=(("เปิดหน้าจอ" if language != "en" else "Open"), url),
        )
    return ChatReply(
        text=_t(DASHBOARD_NOT_CONFIGURED, language),
        quick_replies=_DASHBOARD_FALLBACK_BUTTONS.get(oa, _DASHBOARD_FALLBACK_BUTTONS["sales"]),
    )


def dashboard_link(section: str, oa: str = "sales") -> str | None:
    """A LIFF deep link to a dashboard page, or None when no LIFF id is
    configured.

    Returning None rather than a half-formed URL is deliberate: a link that
    opens an error page is worse than no link, because the person taps it,
    waits, and ends up somewhere broken instead of just reading the list.

    Per OA: a technician's ticket list must open the technician LIFF app,
    not the sales one — the sales pages refuse a technician's token and
    the tap ended on an error page.
    """
    from ..config import settings

    liff_id = (
        {
            "sales": settings.liff_sales_id,
            "technician": settings.liff_technician_id,
            "customer": settings.liff_customer_id,
        }.get(oa) or ""
    ).strip()
    path = DASHBOARD_PATHS.get(section)
    if not liff_id or path is None:
        return None
    # The section paths are the sales dashboard's; the other two OAs have
    # a single home, which is where their deep link lands.
    if oa != "sales":
        return f"https://liff.line.me/{liff_id}"
    return f"https://liff.line.me/{liff_id}/{path}" if path else f"https://liff.line.me/{liff_id}"

CUSTOMER_LIST_PHRASES = (
    "รายชื่อลูกค้า", "รายการลูกค้า", "ดูลูกค้า", "ลูกค้าทั้งหมด", "รายชื่อลูกค้าทั้งหมด", "ลูกค้ามีใครบ้าง",
    "ลูกค้ามีอะไรบ้าง", "ลูกค้าทั้งหมดมีกี่คน", "มีลูกค้ากี่คน", "ดูรายชื่อลูกค้า", "customer list", "list customers",
    # Review, 6 Sep 2026 (B8): the synonyms and the English people mix in.
    "ลิสต์ลูกค้า", "list ลูกค้า", "ลูกค้ากี่คน", "ลูกค้าทั้งหมดมีใครบ้าง", "lead", "leads", "lead ทั้งหมด", "ดู lead",
    "รายชื่อ lead", "lead มีใครบ้าง", "lead list", "รายการ lead", "ลูกค้ามุ่งหวัง", "รายชื่อลูกค้ามุ่งหวัง", "customers",
    "all customers", "who are our customers",
)
BARE_CUSTOMER_WORDS = frozenset({"ลูกค้า", "customers", "customer", "lead", "leads"})
DEAL_LIST_PHRASES = (
    "รายการดีล", "รายชื่อดีล", "ดูดีล", "ดีลทั้งหมด", "ดีลมีอะไรบ้าง", "deal list", "deals",
    "pipeline", "ดู pipeline", "รายการ pipeline", "pipeline ทั้งหมด", "ดีลทั้งหมดมีอะไรบ้าง", "ดีลที่มี", "ดีลมีกี่ดีล",
    "all deals", "list deals",
)
BARE_DEAL_WORDS = frozenset({"ดีล", "deal", "pipeline"})
BARE_PRODUCT_WORDS = frozenset({"สินค้า", "products", "product", "catalogue", "catalog"})
# Trailing-name forms: "ดูดีลของจุใจ", "ดีลของ C-2026-0005". Matched
# separately from the bare phrases because _matches_phrase compares the
# whole message, so anything after the trigger stops it matching at all.
DEAL_FOR_CUSTOMER_TRIGGERS = (
    "ดูดีลของ", "รายการดีลของ", "ดีลของ", "deals of", "deals for",
)

DEAL_CUSTOMER_NOT_FOUND = {
    "th": "ไม่พบลูกค้าชื่อ \"{name}\"",
    "en": "No customer matching \"{name}\".",
}
DEAL_CUSTOMER_AMBIGUOUS = {
    "th": "มีลูกค้าหลายคนที่ตรงกัน ระบุให้ชัดขึ้นหรือใช้รหัส: {names}",
    "en": "Several customers match — be more specific or use a code: {names}",
}
DEAL_NONE_FOR_CUSTOMER = {
    "th": "{name} ยังไม่มีดีล",
    "en": "{name} has no deals yet.",
}
DEAL_OPEN_PHRASES = (
    "ดีลที่ยังไม่ปิด", "ดีลค้าง", "ดีลเปิดอยู่", "open deals", "ดีลไหนยังเปิดอยู่", "ดีลไหนยังเปิด", "ดีลที่เปิดอยู่",
    "ดีลยังเปิด", "ดีลที่ยังเปิด", "ดีลที่ยังไม่จบ", "ดีลที่ค้าง", "ดีลค้างอยู่", "ดีลรอปิด", "pending deals",
)
PRODUCT_LIST_PHRASES = (
    "รายการสินค้า", "รายชื่อสินค้า", "ดูสินค้า", "สินค้าทั้งหมด", "product list", "มีสินค้าอะไรบ้าง", "สินค้ามีอะไรบ้าง",
    "สินค้าที่ขาย", "ขายอะไรบ้าง", "มีขายอะไรบ้าง", "แคตตาล็อก", "ราคาสินค้า", "all products",
)
QUOTE_LIST_PHRASES = (
    "รายการใบเสนอราคา", "ใบเสนอราคาทั้งหมด", "ดูใบเสนอราคา", "quote list", "ใบเสนอราคาที่ยังไม่ตอบ", "ใบเสนอราคาค้าง",
    "ใบเสนอราคาที่รอ", "ใบเสนอราคารอตอบ", "ใบเสนอราคาที่ส่งไป", "ใบเสนอราคาที่ส่งแล้ว", "quotes", "pending quotes", "all quotes",
    "ใบเสนอราคามีอะไรบ้าง",
)
# "สมชายมีดีลอะไรบ้าง", "ดีลของสมชายมีไหม": the customer named first.
_DEAL_OWNER_ASKED_RE = re.compile(r"^(?:ลูกค้า)?([^\s]+(?:\s+[^\s]+)?)\s*มีดีล(?:อะไรบ้าง|อะไร|ไหม|มั้ย|กี่ดีล|บ้าง|อยู่ไหม|อยู่บ้าง)?\s*(?:ครับ|คะ|ค่ะ)?$")


def _deal_owner_asked(message: str) -> str | None:
    m = _DEAL_OWNER_ASKED_RE.match(_canonical(message))
    if not m:
        return None
    name = m.group(1).strip()
    if not name or re.search(r"\d", name) or name in _NOT_A_NAME:
        return None
    return name


# "ลูกค้าคนนี้มีดีลอะไรบ้าง": the one just looked at, not somebody called "คนนี้".
_NOT_A_NAME = frozenset({"ใคร", "ทั้งหมด", "ฉัน", "ผม", "คนนี้", "คนนั้น", "รายนี้", "รายนั้น", "นี้", "เขา", "เค้า", "ทุกคน", "แต่ละคน", "คนไหน", "ที่"})
_CONTEXT_CUSTOMER_DEALS_RE = re.compile(r"^(?:ลูกค้า)?(?:คนนี้|รายนี้|เขา|เค้า|คนเดิม|คนเมื่อกี้)\s*มีดีล")

CUSTOMER_SEARCH_TRIGGERS = ("ค้นหาลูกค้า", "หาลูกค้า", "find customer")
CUSTOMER_DETAIL_TRIGGERS = ("ข้อมูลลูกค้า", "รายละเอียดลูกค้า", "customer detail")
DEAL_DETAIL_TRIGGERS = ("ข้อมูลดีล", "รายละเอียดดีล", "deal detail")

EMPTY_LIST = {
    "th": "ยังไม่มี{what}ในระบบ",
    "en": "No {what} yet.",
}
LIST_TRUNCATED = {
    "th": "\n\nแสดง {shown} จากทั้งหมด {total} รายการ",
    "en": "\n\nShowing {shown} of {total}.",
}
LIST_SEE_ALL = {
    "th": "\nดูทั้งหมดในแดชบอร์ด:\n{url}",
    "en": "\nSee all in the dashboard:\n{url}",
}
LIST_SEE_ALL_NO_LINK = {
    "th": "\n(ยังเปิดแดชบอร์ดไม่ได้ — ยังไม่ได้ตั้งค่า LIFF)",
    "en": "\n(dashboard unavailable — LIFF is not configured)",
}
OPEN_DASHBOARD = {"th": "เปิดแดชบอร์ด", "en": "Open dashboard"}
NOT_FOUND_BY_CODE = {
    "th": "ไม่พบ{what}รหัส {code}",
    "en": "No {what} with code {code}.",
}
SEARCH_NEEDS_TERM = {
    "th": "พิมพ์ชื่อที่ต้องการค้นหาต่อท้ายด้วย เช่น \"ค้นหาลูกค้า สมชาย\"",
    "en": "Add a name to search for, e.g. \"find customer Somchai\".",
}
SEARCH_NO_MATCH = {
    "th": "ไม่พบลูกค้าที่ตรงกับ \"{term}\"",
    "en": "No customer matching \"{term}\".",
}

DEAL_STAGE_LABELS = {
    "new": {"th": "ใหม่", "en": "new"},
    "proposed": {"th": "เสนอราคาแล้ว", "en": "proposed"},
    "won": {"th": "สำเร็จ", "en": "won"},
    "lost": {"th": "ไม่สำเร็จ", "en": "lost"},
}
CUSTOMER_STAGE_LABELS = {
    "lead": {"th": "ลูกค้ามุ่งหวัง", "en": "lead"},
    "contact": {"th": "ลูกค้า", "en": "contact"},
}
QUOTE_STATUS_LABELS = {
    "draft": {"th": "ร่าง", "en": "draft"},
    "sent": {"th": "ส่งแล้ว", "en": "sent"},
    "accepted": {"th": "ตอบรับแล้ว", "en": "accepted"},
    "rejected": {"th": "ปฏิเสธ", "en": "rejected"},
    "expired": {"th": "หมดอายุ", "en": "expired"},
}


def _label(table: dict, key, language: str) -> str:
    entry = table.get(str(key or "").lower())
    return _t(entry, language) if entry else str(key or "")


def _entity_noun(entity_type, language: str) -> str:
    """The Thai/English noun for an entity type, for sentences like
    "ไม่พบ{what}รหัส …" — the raw type ("customer") read as a glitch."""
    table = GROUP_LABELS.get(str(entity_type or ""), {})
    noun = table.get(language) or table.get("th")
    return noun or str(entity_type or "")


def _iso_to_thai_date(value) -> str:
    """An ISO date or timestamp from the Data Tier as a Thai date.

    Every raw `2026-09-06` next to a `6 ก.ย. 2569` in the same reply is one
    more thing the reader has to convert in their head, and the two eras
    side by side is exactly what the era bug of 1 Sep looked like from
    the outside.
    """
    from .thai_datetime import format_thai_date

    if not value:
        return ""
    try:
        return format_thai_date(date.fromisoformat(str(value)[:10]))
    except ValueError:
        return str(value)


# Field-service refusals from the Data Tier (409 on a wrong-state ticket,
# 404 on a job that is not visible) arrive as English sentences with raw
# enums in them. The technician reading them is standing in a customer's
# house; they get Thai, and the code to look the job up.
TICKET_STATE_CONFLICT = {
    "th": "ทำรายการนี้กับงาน {code} ในสถานะปัจจุบันไม่ได้ครับ พิมพ์ \"ข้อมูลงาน {code}\" เพื่อดูสถานะ",
    "en": "That cannot be done to {code} in its current state. Type \"ticket {code}\" to see it.",
}


def _field_service_failure(exc, *, code: str, language: str, template: dict) -> ChatReply:
    structured = getattr(exc, "structured", None) or {}
    status = getattr(exc, "status_code", None)
    if structured.get("error") == "checkout_blocked":
        return ChatReply(
            text=_t(CHECKOUT_NEEDS_REPORT, language).format(
                missing=", ".join(structured.get("missing") or []), code=code,
            )
        )
    if status == 409:
        return ChatReply(
            text=_t(TICKET_STATE_CONFLICT, language).format(code=code or "—"),
            quick_replies=[("ดูข้อมูลงาน", f"ข้อมูลงาน {code}")] if code else [],
        )
    if status == 404:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code or "—")
        )
    log.warning("field-service call refused (%s): %s", status, getattr(exc, "detail", exc))
    return ChatReply(text=_t(template, language).format(detail=_t(COMPANY_SAVE_FAILED, language)))


# Politeness is not meaning. "รายชื่อลูกค้าครับ", "ดูรายชื่อลูกค้าหน่อย" and
# "ขอรายชื่อลูกค้า" are the same request as "รายชื่อลูกค้า"; before 6 Sep 2026
# only the bare form matched and the other three went to the model.
#
# Two layers, stripped in order and each at most once (review, 6 Sep 2026,
# B1: the old loop kept going — "สถานะครับ" → "สถานะ" → "สถา", and every
# phrase ending in ลย/ที/สิ/นะ/ครับ stopped matching):
#   1. the polite particle  ครับ / ค่ะ / คะ / จ้า / krub / ka / เด้อ / เจ้า …
#   2. one softener         นะ / หน่อย / ด้วย / ที / สิ / เลย
# then the particle again ("หน่อยนะครับ" is softener + particle + particle).
_POLITE_PARTICLES = (
    "นะครับผม", "นะครับ", "นะคะ", "นะค่ะ", "นะจ๊ะ", "นะจ้ะ", "นะเจ้า", "ครับผม", "คับผม", "ค้าบผม", "คร้าบผม",
    "ครับพี่", "ค่ะพี่", "คะพี่", "ครับ", "คร้าบ", "ค้าบ", "คับ", "ครัช", "ครัฟ", "ค่ะ", "คะ", "ค่า", "ค๊ะ", "งับ",
    "จ้า", "จ้ะ", "จ๊ะ", "จ๋า", "เด้อ", "เนาะ", "เจ้า", "หนา", "หนิ", "ฮะ", "ฮับ", "ขอรับ", "krub", "krab",
    "khrap", "kub", "ka", "kha", "please", "pls", "thanks", "thank you",
)
_POLITE_SOFTENERS = (
    "หน่อยนะ", "ด้วยนะ", "เลยนะ", "ทีนะ", "สินะ", "ให้หน่อย", "หน่อยสิ", "หน่อยได้ไหม", "หน่อยได้มั้ย", "ได้ไหม",
    "ได้มั้ย", "หน่อย", "ด้วย", "ที", "สิ", "ซิ", "เลย", "นะ", "น่ะ", "เถอะ", "เหอะ",
)
# Kept for the callers that only need "every particle we know".
_POLITE_TAIL = _POLITE_PARTICLES + _POLITE_SOFTENERS
# Words that END in a softener and must not lose it: "สถานะ" is not
# "สถา" + "นะ".
_PROTECTED_ENDINGS = (
    "สถานะ", "ธุระ", "ช่วยด้วย", "กรุณา", "ราคาเท่าไหร่", "ทันที", "ทุกที", "บางที", "สายที", "ปลอดภัย",
    "เยอะเลย", "ถ้วนที", "ครั้งที", "แม่ที", "โอกาส", "เก็บเลย", "เข้าที", "ประเทศ",
)
_POLITE_HEAD = (
    "ขอดู", "ช่วยดู", "อยากดู", "ขอเช็ค", "ขอถาม", "ขอทราบ", "อยากทราบ", "อยากรู้", "รบกวนขอ", "รบกวนดู", "ช่วยเช็ค",
    "ขอ", "ช่วย", "อยากจะ", "อยาก", "รบกวน", "please", "pls", "กรุณา", "เอา", "ให้",
)

# --------------------------------------------------------- canonical spelling
# The spellings people actually type, folded onto the one the phrase tables
# use. Applied to the MATCHING forms only — the message a handler reads is
# untouched, so a note or an address is saved as written (review, 6 Sep
# 2026, B2/B4/B9: "เชคอิน", "ฮอดแล้ว", "แอร์บ่เย็น", "sawasdee krub", "ถึงละ").
_SPELLING_MAP = (
    ("เช็คเอ้าท์", "เช็คเอาท์"), ("เช็คเอ้า", "เช็คเอา"), ("เช็ก", "เช็ค"), ("เชค", "เช็ค"), ("เช๊ค", "เช็ค"), ("เช็คค", "เช็ค"),
    ("ซ้อม", "ซ่อม"), ("ซอม", "ซ่อม"), ("เย้น", "เย็น"), ("เย๊น", "เย็น"), ("ไม่เยน", "ไม่เย็น"), ("ลุกค้า", "ลูกค้า"), ("ลูกคา", "ลูกค้า"),
    ("ลูกค่า", "ลูกค้า"), ("ดิล", "ดีล"), ("ขอบคุน", "ขอบคุณ"), ("ขอบคุง", "ขอบคุณ"), ("สวัดดี", "สวัสดี"), ("สวัสดร", "สวัสดี"),
    ("หวัดดร", "หวัดดี"), ("ฮอด", "ถึง"), ("แปง", "ซ่อม"), ("ไปป์ไลน์", "pipeline"), ("ไปป์ไลน", "pipeline"), ("ลีด", "lead"),
    ("ทะเบียร", "ทะเบียน"), ("ปะกัน", "ประกัน"), ("ประกัณ", "ประกัน"), ("โน๊ต", "โน้ต"), ("โน้ท", "โน้ต"), ("จ็อบ", "งาน"),
)
# "มีไรบ้าง" = "มีอะไรบ้าง" — but "อะไรบ้าง" already has it, and neither
# does "อย่างไร": folding that one turned "เช็คอินต้องทำอย่างไร" into
# "…อย่างอะไร", which no question marker matched, so the question was
# obeyed as a command (review v3, B03).
_SLANG_WHAT_RE = re.compile(r"(?<!อะ)(?<!เท่า)(?<!ท่า)(?<!อย่าง)ไร(?=บ้าง|มั่ง|ดี|กัน|$|\s)")
# Karaoke Thai and the English a Thai typist reaches for. Whole words only,
# longest first, so "wan nee" is read before "nee".
_KARAOKE_MAP = (
    ("set laew", "เสร็จแล้ว"), ("sed laew", "เสร็จแล้ว"), ("set leaw", "เสร็จแล้ว"), ("wan nee", "วันนี้"), ("wan ni", "วันนี้"),
    ("prung nee", "พรุ่งนี้"), ("khob khun", "ขอบคุณ"), ("kob kun", "ขอบคุณ"), ("kop kun", "ขอบคุณ"), ("air con", "แอร์"),
    ("rub ngan", "รับงาน"), ("rab ngan", "รับงาน"), ("mai yen", "ไม่เย็น"), ("not cold", "ไม่เย็น"), ("no cold", "ไม่เย็น"),
    ("sawasdee", "สวัสดี"), ("sawatdee", "สวัสดี"), ("sawaddee", "สวัสดี"), ("sawadee", "สวัสดี"), ("khobkhun", "ขอบคุณ"),
    ("kobkun", "ขอบคุณ"), ("wannee", "วันนี้"), ("aircon", "แอร์"), ("air", "แอร์"), ("krub", "ครับ"), ("krab", "ครับ"),
    ("khrap", "ครับ"), ("krup", "ครับ"), ("kub", "ครับ"), ("kha", "ค่ะ"), ("ka", "ค่ะ"), ("jaa", "จ้า"), ("ja", "จ้า"),
    ("mai", "ไม่"), ("yen", "เย็น"), ("sia", "เสีย"), ("sang", "แจ้ง"), ("jaeng", "แจ้ง"), ("som", "ซ่อม"), ("laew", "แล้ว"),
    ("leaw", "แล้ว"), ("laeo", "แล้ว"), ("teung", "ถึง"), ("thueng", "ถึง"), ("tueng", "ถึง"), ("rub", "รับ"),
    ("ngan", "งาน"), ("chek", "check"), ("chec", "check"), ("cheak", "check"), ("chk", "check"), ("eng", "english"),
)
_KARAOKE_RE = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(k) for k, _ in _KARAOKE_MAP) + r")(?![a-z])",
)
_KARAOKE_LOOKUP = dict(_KARAOKE_MAP)
# Pronouns: "ของผม" and "ของฉัน" are the same person (B8).
_PRONOUN_OF_RE = re.compile(r"ของ(?:ผม|ดิฉัน|หนู|เรา|กู|ข้อย|เฮา)")
_PRONOUN_RE = re.compile(r"(?<![ก-๙])(?:ดิฉัน|ผม|หนู|กู|เรา|ข้อย|เฮา)(?![ก-๙])|^(?:ดิฉัน|ผม|หนู|เรา)|(?:ดิฉัน|ผม|หนู|เรา)(?=มี|จะ|ขอ|รับ|เอา|ไป|ไม่|ว่าง|อยาก|เปลี่ยน|แก้|ชื่อ|เป็น|อยู่|ต้อง|ทำ)")
_PRONOUN_INNER_RE = re.compile(r"(?:ทีม|งาน|คิว|รายงาน|ข้อมูล|โปรไฟล์|สิทธิ์|ประกัน|เบอร์|ชื่อ|ที่อยู่|ร้าน|บริษัท|รอ|นัด|ดีล|ลูกค้า)(?:ดิฉัน|ผม|หนู|เรา|ฉัน)")
# Isan / Northern: บ่ = ไม่ (not before า อ น ง: "บ่าย", "บ่อ", "บ่น" stay),
# word-final เพ = เสีย, word-final ละ = แล้ว ("แหละ" stays).
_DIALECT_NOT_RE = re.compile(r"บ่(?![าอนงัิีึืุู])")
_DIALECT_BROKEN_RE = re.compile(r"(?<=[ก-๙])เพ(?=\s|$)")
_DIALECT_DONE_RE = re.compile(r"(?<=[ก-๙])(?<!แห)(?<!ที)ละ(?=ครับ|คับ|ค่ะ|คะ|เด้อ|เจ้า|\s|$)")
# "ลูกค้าาา", "งานนน", "เสร็จแล้วว": a held key, not another word.
_REPEAT_THAI_RE = re.compile(r"([ก-๙])\1{2,}")
_REPEAT_FINAL_THAI_RE = re.compile(r"(?<=[ก-๙])([ก-ฮ])\1(?=\s|$)")
# Real words that end in a doubled letter and must keep it.
_DOUBLE_ENDINGS_OK = ("ระบบ", "สรร", "กรรม", "ธรรม", "วรรค", "บรร", "อัตต", "ครรภ", "โปรแกรม")
_REPEAT_LATIN_RE = re.compile(r"([a-z])\1{2,}")
_LAUGH_RE = re.compile(r"(?<![0-9])(?:5{3,}\+*|ฮ่า+(?:ๆ)*|ฮะๆ|ha(?:ha)+|lol|lmao|เเ+)(?![0-9])")
_ARROW_RE = re.compile(r"[\u200b\u200c\u200d\ufe0f]")


def _canonical(message: str) -> str:
    """Lower-case text with the spellings folded: the form every matcher
    reads. Spaces are kept so word-level rules can see boundaries."""
    text = _ARROW_RE.sub("", (message or "")).strip().lower()
    if not text:
        return ""
    text = _LAUGH_RE.sub(" ", text)
    text = _REPEAT_THAI_RE.sub(r"\1", text)
    text = _REPEAT_FINAL_THAI_RE.sub(
        lambda m: m.group(0) if text[: m.end()].endswith(_DOUBLE_ENDINGS_OK) else m.group(1), text,
    )
    text = _REPEAT_LATIN_RE.sub(r"\1", text)
    for wrong, right in _SPELLING_MAP:
        if wrong in text:
            text = text.replace(wrong, right)
    text = _KARAOKE_RE.sub(lambda m: _KARAOKE_LOOKUP[m.group(1)], text)
    text = _SLANG_WHAT_RE.sub("อะไร", text)
    text = _DIALECT_NOT_RE.sub("ไม่", text)
    text = _DIALECT_BROKEN_RE.sub("เสีย", text)
    text = _DIALECT_DONE_RE.sub("แล้ว", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact(message: str) -> str:
    """Lower-case, no spaces, no punctuation — the particles kept."""
    return re.sub(r"[\s!?.,~ๆ。()\[\]\"'“”‘’:;]+", "", _canonical(message))


def _strip_one(compact: str, tails: tuple[str, ...]) -> str:
    if any(compact.endswith(p) for p in _PROTECTED_ENDINGS):
        return compact
    for tail in sorted(tails, key=len, reverse=True):
        if not (compact.endswith(tail) and len(compact) > len(tail) + 1):
            continue
        rest = compact[: -len(tail)]
        # "สถานะครับ" ends in "นะครับ", but the "นะ" belongs to "สถานะ":
        # a compound tail whose head completes a protected word is not
        # this tail — try the shorter one.
        if any((rest + tail[:k]).endswith(p) for p in _PROTECTED_ENDINGS for k in range(1, len(tail))):
            continue
        return rest
    return compact


def _strip_layers(compact: str) -> list[str]:
    """Every intermediate form, most-stripped last: particle, softener,
    particle again."""
    forms = [compact]
    for tails in (_POLITE_PARTICLES, _POLITE_SOFTENERS, _POLITE_PARTICLES, _POLITE_SOFTENERS):
        nxt = _strip_one(forms[-1], tails)
        if nxt != forms[-1]:
            forms.append(nxt)
    return forms


def _normalise(message: str) -> str:
    """Lower-case, no spaces, no trailing punctuation or polite particles."""
    compact = _compact(message)
    return _strip_layers(compact)[-1] if compact else compact


def _with_pronoun_forms(form: str) -> set[str]:
    out = {form}
    mapped = _PRONOUN_OF_RE.sub("ของฉัน", form)
    mapped = _PRONOUN_INNER_RE.sub(lambda m: m.group(0)[: -len(m.group(0)) + len(m.group(0)) - len(re.search(r"(ดิฉัน|ผม|หนู|เรา|ฉัน)$", m.group(0)).group(1))] + "ฉัน", mapped)
    mapped = _PRONOUN_RE.sub("ฉัน", mapped)
    out.add(mapped)
    # Without the pronoun altogether: "ทีมฉันมีงานอะไรบ้าง" → "ทีมมีงานอะไรบ้าง".
    bare = re.sub(r"ของฉัน|ฉัน", "", mapped)
    if len(bare) >= 3:
        out.add(bare)
    return out


# A question wrapped around a noun phrase: "มีงานว่างไหม" asks for "งานว่าง",
# "ดีลค้างมีไหม" for "ดีลค้าง", "ลูกค้าทั้งหมดมีใครบ้าง" for "ลูกค้าทั้งหมด" (B8).
_FRAME_TAILS = (
    "มีอะไรบ้าง", "มีใครบ้าง", "มีไหม", "มีมั้ย", "มีบ้างไหม", "มีบ้างมั้ย", "อะไรบ้าง", "ใครบ้าง", "บ้างไหม", "บ้างมั้ย",
    "หรือยัง", "หรือเปล่า", "รึเปล่า", "หรือไม่", "ไหม", "มั้ย", "มะ", "ป่าว", "เปล่า", "บ้าง", "ยัง", "แล้ว", "กี่คน",
    "กี่ดีล", "กี่งาน", "กี่รายการ", "กี่อัน", "กี่ใบ", "อยู่", "ล่ะ", "ละ", "หรอ", "เหรอ", "อ่ะ", "อะ",
)
_FRAME_HEADS = ("มี", "ขอดู", "ดู", "ขอ", "list", "ลิสต์", "แสดง", "โชว์", "show")


def _frame_stripped(form: str) -> set[str]:
    out = set()
    cur = form
    for _ in range(3):
        nxt = _strip_one(cur, _FRAME_TAILS) if not any(cur.endswith(p) for p in _PROTECTED_ENDINGS) else cur
        if nxt == cur:
            break
        cur = nxt
        if len(cur) >= 3:
            out.add(cur)
    for base in list(out) + [form]:
        for head in sorted(_FRAME_HEADS, key=len, reverse=True):
            if base.startswith(head) and len(base) > len(head) + 2:
                out.add(base[len(head):])
                break
    return out


def _bare_forms(message: str) -> set[str]:
    """The raw and particle-stripped forms plus the polite heads removed
    and the pronouns folded — but the question frame kept, so a bare
    word set ("งาน") is not matched by "มีงานไหม"."""
    compact = _compact(message)
    forms: set[str] = set()
    for layer in _strip_layers(compact):
        forms |= _with_pronoun_forms(layer)
    for base in list(forms):
        for head in _POLITE_HEAD:
            if base.startswith(head) and len(base) > len(head) + 1:
                forms |= _with_pronoun_forms(base[len(head):])
    forms.discard("")
    return forms


def _polite_forms(message: str) -> set[str]:
    # Both the raw and the particle-stripped form: "สถานะ" ends in "นะ".
    forms = _bare_forms(message)
    for base in list(forms):
        forms |= _frame_stripped(base)
    forms.discard("")
    return forms


def _is_bare_word(message: str, words) -> bool:
    """A single command word, in any of its polite dressings."""
    return bool(_bare_forms(message) & set(words))


def _matches_phrase(message: str, phrases: tuple[str, ...]) -> bool:
    forms = _polite_forms(message)
    forms.discard("")
    return bool(forms) and any(f == p.replace(" ", "").lower() for f in forms for p in phrases)


# "ช่วย…ให้หน่อยได้ไหม", "รบกวน…ที", "ขอ…หน่อย": Thai politeness wraps an
# order in a question. Only the leading words count — a sentence that merely
# contains ช่วย later ("ใครช่วยได้บ้าง") is still a question.
_POLITE_REQUEST_RE = re.compile(r"^(?:ช่วย|รบกวน|กรุณา|ขอความกรุณา|please|pls)")


_JOB_DISCLAIMER_RE = re.compile(
    r"(?:ยังไม่|ไม่ได้|ไม่ต้อง|อย่า|ห้าม).{0,10}(?:เช็คอิน|checkin|ถึง|ปิดงาน|เสร็จ)|"
    r"(?:ถ้า|หาก|พรุ่งนี้|ค่อย).{0,24}(?:เช็คอิน|checkin|ถึง|ปิดงาน)|"
    r"(?:บอกว่า|บอกให้|ถามว่า)"
)


_JOB_STEP_ACTIONS = frozenset({"check_in", "check_out", "close", "claim", "reject", "cancel"})
JOB_ACTION_NOT_REQUESTED = {
    "th": 'รับทราบครับ ยังไม่ได้บันทึกอะไรกับงานนี้ ถ้าต้องการให้บันทึก พิมพ์คำสั่งตรง ๆ เช่น "เช็คอิน T-2026-0001"',
    "en": 'Understood — nothing was recorded on this job. Send the command itself when you want it, e.g. "check in T-2026-0001".',
}


def _disclaims_a_job_action(message: str) -> bool:
    """The sentence says the job step has NOT happened, or is about someone
    else saying it happened.

    Held against the model's answer as well as the trigger table. The
    model reads "ยังไม่ถึงหน้างาน" as action=check_in about two times in
    three (real-model corpus, 9 Sep 2026), and the AI path executed it —
    the same wrong check-in the owner reported, arriving by the other
    road. A guard on only one of the two roads is not a guard.
    """
    return bool(_JOB_DISCLAIMER_RE.search(_normalise(message)))


# What the chat says when a sentence names an action without asking for it.
# One table, so a refusal reads the same wherever it comes from: the verb,
# the record it would have touched, and the command that WOULD do it — an
# acknowledgement that teaches, rather than a shrug.
_GUARD_ACTIONS: dict[str, dict[str, str]] = {
    "appointment_create": {
        "th": "ตั้งนัด", "en": "set the appointment",
        "th_eg": "ตั้งนัด {code} พรุ่งนี้ 14:00", "en_eg": "set an appointment for {code} tomorrow 14:00",
        "code": "C-2026-0001",
    },
    "appointment_move": {
        "th": "เลื่อนนัด", "en": "move the appointment",
        "th_eg": "เลื่อนนัด {code} เป็น 16:00", "en_eg": "move the appointment for {code} to 16:00",
        "code": "C-2026-0001",
    },
    "appointment_cancel": {
        "th": "ยกเลิกนัด", "en": "cancel the appointment",
        "th_eg": "ยกเลิกนัด {code}", "en_eg": "cancel the appointment for {code}",
        "code": "C-2026-0001",
    },
    "appointment_delete": {
        "th": "ลบนัด", "en": "remove the appointment",
        "th_eg": "ลบนัด {code}", "en_eg": "remove the appointment for {code}",
        "code": "C-2026-0001",
    },
    "quote_create": {
        "th": "สร้างใบเสนอราคา", "en": "create the quotation",
        "th_eg": "สร้างใบเสนอราคา {code}", "en_eg": "create a quotation for {code}",
        "code": "D-2026-0001",
    },
    "check_in": {
        "th": "เช็คอิน", "en": "check in",
        "th_eg": "เช็คอิน {code}", "en_eg": "check in {code}",
        "code": "T-2026-0001",
    },
    "check_out": {
        "th": "ปิดงาน", "en": "close the job",
        "th_eg": "ปิดงาน {code}", "en_eg": "close {code}",
        "code": "T-2026-0001",
    },
    "ticket_open": {
        "th": "เปิดงานซ่อม", "en": "open a repair job",
        "th_eg": "แจ้งซ่อม", "en_eg": "report a fault",
        "code": "",
    },
    "pending_flow": {
        "th": "บันทึก", "en": "save this",
        "th_eg": "เพิ่มลูกค้า สมชาย 0812345678", "en_eg": "add customer Somchai 0812345678",
        "code": "",
    },
    "line_item": {
        "th": "แก้รายการสินค้า", "en": "change the line items",
        "th_eg": "เพิ่มพัดลม 3 ตัว", "en_eg": "add 3 fans",
        "code": "",
    },
    # Anything else the model asks to remove. Deliberately vague, because
    # this is the catch-all for entities with no wording of their own.
    "record_delete": {
        "th": "ลบข้อมูลนี้", "en": "delete this record",
        "th_eg": "ลบ {code}", "en_eg": "delete {code}",
        "code": "C-2026-0001",
    },
    # The deterministic branches, added 10 ก.ย. 2569 — see the note on
    # ACTION_WORDS in intent_guard.py for what each of these was doing to
    # the database before it had a guard.
    "quote_status": {
        "th": "เปลี่ยนสถานะใบเสนอราคา", "en": "change the quotation's status",
        "th_eg": "ตอบรับใบเสนอราคา {code}", "en_eg": "accept quotation {code}",
        "code": "Q-2026-0001",
    },
    "quote_terms": {
        "th": "แก้ส่วนลด", "en": "change the discount",
        "th_eg": "ใบเสนอราคา {code} ส่วนลด 10%", "en_eg": "quotation {code} discount 10%",
        "code": "Q-2026-0001",
    },
    "deal_create": {
        "th": "สร้างดีล", "en": "create the deal",
        "th_eg": "เปิดดีลให้ {code}", "en_eg": "open a deal for {code}",
        "code": "C-2026-0001",
    },
    "deal_stage": {
        "th": "เปลี่ยนสถานะดีล", "en": "change the deal's stage",
        "th_eg": "ปิดดีล {code} สำเร็จ", "en_eg": "close deal {code} as won",
        "code": "D-2026-0001",
    },
    "note_write": {
        "th": "บันทึกข้อความนี้", "en": "save this note",
        "th_eg": "บันทึกว่า {code} สนใจรุ่นใหม่", "en_eg": "note that {code} is interested",
        "code": "C-2026-0001",
    },
    "invite_create": {
        "th": "ออกรหัสเชิญ", "en": "issue an invite code",
        "th_eg": "ขอรหัสเชิญช่าง", "en_eg": "technician invite code",
        "code": "",
    },
    "team_manage": {
        "th": "แก้ทีมช่าง", "en": "change the technician team",
        "th_eg": "สร้างทีมช่าง แอร์", "en_eg": "create technician team A/C",
        "code": "",
    },
    "warranty_register": {
        "th": "ลงทะเบียนสินค้า", "en": "register the product",
        "th_eg": "ลงทะเบียน SN12345678", "en_eg": "register SN12345678",
        "code": "",
    },
    "company_update": {
        "th": "แก้ข้อมูลบริษัท", "en": "change the company details",
        "th_eg": "ข้อมูลบริษัท ชื่อ บริษัท ก จำกัด", "en_eg": "company name Acme Co Ltd",
        "code": "",
    },
    "customer_bulk": {
        "th": "เพิ่มลูกค้า", "en": "add the customers",
        "th_eg": "เพิ่มลูกค้า สมชาย ใจดี 0812345678", "en_eg": "add customer John Doe 0812345678",
        "code": "",
    },
    "approval_act": {
        "th": "ตัดสินรายงานนี้", "en": "act on this report",
        "th_eg": "อนุมัติ {code}", "en_eg": "approve {code}",
        "code": "SR-2026-0001",
    },
    "document_issue": {
        "th": "ออกเอกสาร", "en": "issue the document",
        "th_eg": "ออกเอกสาร {code}", "en_eg": "issue document {code}",
        "code": "Q-2026-0001",
    },
    "template_publish": {
        "th": "เผยแพร่แบบฟอร์มนี้", "en": "publish this template",
        "th_eg": "ใช้เลย", "en_eg": "use it",
        "code": "",
    },
    "record_write": {
        "th": "แก้ข้อมูลนี้", "en": "change this record",
        "th_eg": "สร้างลูกค้า สมชาย ใจดี 0812345678", "en_eg": "add customer John Doe 0812345678",
        "code": "",
    },
    "ticket_cancel": {
        "th": "ยกเลิกงาน", "en": "cancel the job",
        "th_eg": "ยกเลิกงาน {code}", "en_eg": "cancel job {code}",
        "code": "T-2026-0001",
    },
    "shop_setting": {
        "th": "เปลี่ยนการตั้งค่าร้าน", "en": "change the shop setting",
        "th_eg": "ตั้งค่าลบ lead อัตโนมัติ 90 วัน", "en_eg": "auto-archive leads after 90 days",
        "code": "",
    },
    "profile_update": {
        "th": "แก้ข้อมูลของคุณ", "en": "change your details",
        "th_eg": "เบอร์ 0812345678", "en_eg": "phone 0812345678",
        "code": "",
    },
    "chat_open": {
        "th": "เปิดการสนทนากับร้าน", "en": "open a conversation with the shop",
        "th_eg": "คุยกับร้าน", "en_eg": "talk to the shop",
        "code": "",
    },
    "job_claim": {
        "th": "รับงาน", "en": "take the job",
        "th_eg": "รับงาน {code}", "en_eg": "claim {code}",
        "code": "T-2026-0001",
    },
    "job_reject": {
        "th": "ปฏิเสธงาน", "en": "decline the job",
        "th_eg": "ปฏิเสธงาน {code} ติดงานอื่น", "en_eg": "decline job {code} — booked elsewhere",
        "code": "T-2026-0001",
    },
    "job_assign": {
        "th": "มอบหมายงาน", "en": "assign the job",
        "th_eg": "มอบหมาย {code} ให้ทีมแอร์", "en_eg": "assign {code} to the A/C team",
        "code": "T-2026-0001",
    },
    "job_situation": {
        "th": "บันทึกเรื่องนี้ในงาน", "en": "record this on the job",
        "th_eg": "ขอเลื่อนนัด {code} พรุ่งนี้ 10 โมง", "en_eg": "reschedule {code} to tomorrow 10:00",
        "code": "T-2026-0001",
    },
}

# Everything the model can ask for that changes something. Used to decide
# whether an (entity, action) with no specific guard still needs the
# generic one — the answer is yes for every verb in here.
_MUTATING_ACTIONS = frozenset({
    "create", "update", "delete", "archive", "cancel", "close", "issue",
    "approve", "reject", "check_in", "check_out", "claim", "assign",
    "promote", "convert", "publish", "send",
})

# The model's reading is not a mandate either: every mutating (entity,
# action) the AI road can dispatch is named here, so ONE call in
# _execute_intent covers the whole road rather than each branch of it.
_AI_GUARDED: dict[tuple[str, str], str] = {
    ("followup", "create"): "appointment_create",
    ("followup", "update"): "appointment_move",
    ("followup", "cancel"): "appointment_cancel",
    ("followup", "delete"): "appointment_delete",
    ("quote", "create"): "quote_create",
    ("quote", "issue"): "quote_create",
    ("service_report", "check_in"): "check_in",
    ("service_report", "check_out"): "check_out",
    ("service_report", "create"): "check_out",
    ("service_report", "update"): "check_out",
    ("ticket", "create"): "ticket_open",
    ("ticket", "close"): "check_out",
    ("line_item", "create"): "line_item",
    ("line_item", "update"): "line_item",
    ("line_item", "delete"): "line_item",
    # The note road, which the model can reach with no trigger word in the
    # sentence at all: "ยังไม่ต้องแก้ที่จดไว้เมื่อกี้" and "ยังไม่ต้องเอาที่
    # จดไว้เมื่อกี้ออก" came back as update/delete on entity=note and were
    # carried out (adversarial sweep, 10 ก.ย. 2569). The generic
    # record_delete fallback LOOKED like cover and was inert: with no note
    # word to find, intent_to_act had nothing to negate and returned ACT.
    ("note", "create"): "note_write",
    ("note", "update"): "note_write",
    ("note", "delete"): "note_write",
    # The rest of the mutating pairs, mapped to the vocabulary that already
    # describes them, so the refusal names the actual thing rather than the
    # generic "แก้ข้อมูลนี้". Anything still not listed falls to
    # `record_write` / `record_delete` in _execute_intent — guarded, just
    # less specific (10 ก.ย. 2569).
    ("customer", "create"): "customer_bulk",
    # These two were bound to vocabularies that an edit sentence never
    # contains — customer_bulk is the paste-a-list wording, deal_create is
    # about opening deals — so intent_to_act looked for words that were
    # never there and returned ACT every time. "แก้เบอร์ลูกค้ายังไงครับ"
    # (a how-to) wrote update_customer, and "สมชายยืนยันเป็นลูกค้าไปหรือยัง"
    # (a question) promoted them (10 ก.ย. 2569). record_write is the
    # generic edit vocabulary and reads all three moods correctly.
    ("customer", "update"): "record_write",
    ("customer", "archive"): "record_delete",
    ("customer", "promote"): "record_write",
    ("deal", "create"): "deal_create",
    ("deal", "update"): "deal_stage",
    ("deal", "archive"): "record_delete",
    ("quote", "update"): "quote_terms",
    ("team", "create"): "team_manage",
    ("team", "update"): "team_manage",
    ("team", "delete"): "team_manage",
    ("warranty", "create"): "warranty_register",
    ("warranty", "update"): "warranty_register",
    ("setting", "update"): "shop_setting",
    ("approval", "approve"): "approval_act",
    ("approval", "reject"): "approval_act",
    ("approval", "update"): "approval_act",
    ("ticket", "claim"): "job_claim",
    ("ticket", "reject"): "job_reject",
    ("ticket", "assign"): "job_assign",
    ("service_report", "issue"): "document_issue",
}


def _guard_triggers(action: str) -> tuple[str, ...]:
    """The handler's OWN trigger tuple for a guarded action.

    A function, not a table, because the trigger constants are declared
    further down this module with their handlers; read at call time they
    are simply the same words the dispatcher matched on, which is the
    point — the guard and the dispatcher must never disagree about what
    the action is called.
    """
    return {
        "appointment_create": REMINDER_TRIGGERS,
        "appointment_move": REMINDER_MOVE_TRIGGERS,
        "appointment_cancel": REMINDER_CANCEL_TRIGGERS + _REMINDER_CANCEL_HEADS,
        "appointment_delete": REMINDER_CANCEL_TRIGGERS,
        "quote_create": QUOTE_CREATE_TRIGGERS,
        "check_in": CHECKIN_TRIGGERS,
        "check_out": CHECKOUT_TRIGGERS,
        "line_item": LINE_EDIT_TRIGGERS + LINE_REMOVE_TRIGGERS + DEAL_PRODUCT_ADD_TRIGGERS,
        "quote_status": QUOTE_VOID_TRIGGERS + QUOTE_ACCEPT_TRIGGERS,
        "quote_terms": QUOTE_DISCOUNT_TRIGGERS,
        "deal_create": DEAL_CREATE_TRIGGERS + DEAL_CREATE_BARE_TRIGGERS,
        # All three, because one action name covers create, edit and delete
        # — and the guard reads "does the sentence OPEN with this handler's
        # own word" to tell an order from a question. With only the create
        # triggers here, "ลบบันทึกของ C-2026-0001 ให้หน่อยได้ไหมครับ" did
        # not look imperative and was answered with a confirm prompt.
        "note_write": NOTE_TRIGGERS + NOTE_EDIT_TRIGGERS + NOTE_DELETE_TRIGGERS,
        "invite_create": TECHNICIAN_INVITE_TRIGGERS + SALES_INVITE_TRIGGERS + INVITE_AMBIGUOUS_TRIGGERS,
        "customer_bulk": BULK_CUSTOMER_TRIGGERS,
        "job_claim": TICKET_CLAIM_TRIGGERS,
        "job_reject": TICKET_REJECT_TRIGGERS,
        "job_assign": TICKET_ASSIGN_TRIGGERS,
        # deal_stage, team_manage, warranty_register and company_update
        # match on their own tables inline rather than a named constant;
        # ACTION_WORDS carries their vocabulary.
    }.get(action, ())


INTENT_NOT_A_COMMAND = {
    "th": 'รับทราบครับ ยังไม่ได้{what} ถ้าต้องการให้ทำ พิมพ์คำสั่งตรง ๆ เช่น "{example}"',
    "en": 'Understood — I did not {what}. Send the command itself when you want it, e.g. "{example}".',
}
INTENT_STATUS_ANSWER = {
    "th": 'ยังไม่ได้{what} ครับ ถ้าต้องการให้ทำ พิมพ์ "{example}"',
    "en": 'That has not happened — I did not {what}. Send "{example}" when you want it.',
}
# …and the same question when the answer is yes. "สร้างใบเสนอราคาไปหรือยัง"
# was answered "ยังไม่ได้สร้างใบเสนอราคา" without any lookup at all, so a
# shop with the quotation already issued was told the opposite of the truth
# (owner: "สร้างใบเสนอราคาไปหรือยัง ต้องเช็คสถานะ ไม่ใช่สร้าง", 10 ก.ย.
# 2569). Not creating it was right; not checking was not.
INTENT_STATUS_DONE = {
    "th": "{done}ครับ",
    "en": "Yes — {done}.",
}
STATUS_DONE_TEXT = {
    "quote_create": {
        "th": "ใบเสนอราคา {code} ออกไปแล้ว", "en": "quotation {code} has been issued",
    },
    "deal_create": {
        "th": "ดีล {code} เปิดไว้แล้ว", "en": "deal {code} is already open",
    },
}
# Only these two, deliberately. A status question can only be answered
# where the guard is reached through _guarded_in_context, which has the
# client; the appointment and warranty branches call the synchronous
# _intent_guard_reply and cannot look anything up. Listing them here would
# be the same defect this change is fixing — a capability the product
# claims and does not have — so they stay out until their call sites are
# async. Their "ยังไม่ได้…" answer is unchanged and still honest about not
# having been done by THIS message.
INTENT_HOW_TO = {
    "th": 'วิธี{verb}: พิมพ์ "{example}" ครับ — ตอนนี้ยังไม่ได้แก้ข้อมูลอะไร',
    "en": 'To {verb}: send "{example}". Nothing has been changed.',
}
INTENT_CONFIRM = {
    "th": 'ต้องการ{what} ใช่ไหมครับ? ถ้าใช่ พิมพ์ "{example}"',
    "en": 'Do you want me to {what}? If so, send "{example}".',
}
_GUARD_CODE_RE = re.compile(r"(?<![A-Za-z0-9])((?:SR|[CDQT])-\d{4}-\d{4})(?![0-9])", re.IGNORECASE)


def _intent_guard_reply(
    message: str, *, action: str, language: str,
    triggers: tuple[str, ...] | None = None, code: str | None = None,
    proposed: bool = False,
) -> ChatReply | None:
    """The answer to give INSTEAD of writing, or None to go ahead.

    The decision itself is `intent_guard.intent_to_act`; this only dresses
    it. Every path in this module that mutates anything calls this first,
    which is the whole point — the review of 9-10 Sep 2026 cancelled
    appointments, issued quotations, checked jobs in and opened repair
    tickets from sentences that refused, questioned or merely quoted the
    action, because each handler was left to notice for itself.
    """
    verdict = intent_to_act(
        message, action=action, canonical=_canonical(message),
        triggers=tuple(_guard_triggers(action) if triggers is None else triggers),
        proposed=proposed,
    )
    if verdict.acts:
        return None
    spec = _GUARD_ACTIONS[action]
    found = _GUARD_CODE_RE.search(_normalise_message(message) or "")
    named = (code or (found.group(1).upper() if found else "") or "").strip()
    verb = spec["th" if language != "en" else "en"]
    example = spec["th_eg" if language != "en" else "en_eg"].format(code=named or spec["code"])
    what = f"{verb}ของ {named}" if (named and language != "en") else (
        f"{verb} for {named}" if named else verb
    )
    if verdict.outcome == ASK:
        table = INTENT_CONFIRM
    elif verdict.reason == "status":
        table = INTENT_STATUS_ANSWER
    elif verdict.reason == "howto":
        table = INTENT_HOW_TO
    else:
        table = INTENT_NOT_A_COMMAND
    return ChatReply(text=_t(table, language).format(what=what, verb=verb, example=example))


async def _status_answer(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    action: str, language: str,
) -> ChatReply | None:
    """Has this already been done? — answered from the database.

    Returns a "yes, here it is" reply, or None to let the caller say "not
    yet" (which is then true, because we looked). Any failure returns
    None: a status question is not worth an error, and "ยังไม่ได้" is the
    safe half of the answer.
    """
    code = ""
    found = _GUARD_CODE_RE.search(_normalise_message(message) or "")
    if found:
        code = found.group(1).upper()
    try:
        if action == "quote_create":
            quotes = await client.list_quotes(str(license_id))
            # A quotation for the deal named, or for the deal just discussed.
            deal_code = code if code.startswith("D-") else ""
            if not deal_code:
                ref = await _last_entity_ref(client, ctx)
                if ref and str(ref.get("entity_type")) == "deal":
                    deal_code = str(await _code_for_entity(
                        client, str(license_id), "deal", str(ref.get("entity_id") or ""),
                    ) or "")
            if code.startswith("Q-"):
                hit = next((q for q in quotes if str(q.get("quote_id")) == code), None)
            elif deal_code:
                deals = await client.list_deals(str(license_id))
                deal = next((d for d in deals if str(d.get("deal_id")) == deal_code), None)
                hit = next(
                    (q for q in quotes if deal and str(q.get("deal_id")) == str(deal.get("id"))),
                    None,
                ) if deal else None
            else:
                hit = None
            shown = str((hit or {}).get("quote_id") or "")
        elif action == "deal_create":
            deals = await client.list_deals(str(license_id))
            hit = next((d for d in deals if str(d.get("deal_id")) == code), None) if code.startswith("D-") else None
            shown = str((hit or {}).get("deal_id") or "")
        else:
            return None
    except Exception:
        log.exception("could not answer a status question for %s", action)
        return None
    if not hit:
        return None
    done = _t(STATUS_DONE_TEXT[action], language).format(code=shown or code or "-")
    return ChatReply(text=_t(INTENT_STATUS_DONE, language).format(done=done))


async def _guarded_in_context(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    action: str, language: str,
) -> ChatReply | None:
    """_intent_guard_reply, naming the record in play.

    "ยังไม่เปลี่ยนเวลานัดเป็น 16:00" names no code — the appointment is the
    one they were just looking at, and a refusal that cannot say which
    record it is about is half an answer (owner rule 3: never guess, never
    go quiet). The lookup happens only when the guard has already decided
    to hold, so an ordinary command pays nothing for it.
    """
    reply = _intent_guard_reply(message, action=action, language=language)
    if reply is None:
        return None
    # "…ไปหรือยัง" is a question about the world, not an order. Answering it
    # from the sentence alone means asserting "ยังไม่ได้" about a record
    # nobody looked at. Look, when the question is that shape and the
    # action is one we can look up.
    verdict = intent_to_act(
        message, action=action, canonical=_canonical(message),
        triggers=tuple(_guard_triggers(action)),
    )
    if verdict.reason == "status" and action in STATUS_DONE_TEXT:
        answered = await _status_answer(
            client, ctx=ctx, license_id=license_id, message=message,
            action=action, language=language,
        )
        if answered is not None:
            return answered
    if _GUARD_CODE_RE.search(message or ""):
        return reply
    try:
        ref = await _last_entity_ref(client, ctx)
    except Exception:
        return reply
    code = await _code_for_entity(
        client, str(license_id), str((ref or {}).get("entity_type") or ""),
        str((ref or {}).get("entity_id") or ""),
    ) if ref else None
    if not code:
        return reply
    return _intent_guard_reply(message, action=action, language=language, code=code)


def _command_like(message: str, triggers: tuple[str, ...]) -> bool:
    """A command word at the START of a short message, or anywhere when a
    ticket code is named. "ลูกค้าบอกว่าถึงแล้วค่อยโทร" is a sentence about
    a customer, not a check-in — the old substring match took it as one."""
    compact = _normalise(message)
    if not compact or compact.startswith(("วิธีใช้", "help", "guide", "คู่มือ")):
        return False
    # A question does not command — except the polite Thai request, which is
    # shaped like one: "ช่วยเช็คอินให้หน่อยได้ไหมครับ" is an instruction, and
    # refusing it sent the technician the guide instead (owner, 9 Sep 2026).
    if _looks_like_a_question(message) and not _POLITE_REQUEST_RE.match(compact):
        return False
    if _disclaims_a_job_action(message):
        return False
    words = [t.replace(" ", "").lower() for t in triggers]
    if TICKET_CODE_RE.search(message or ""):
        return any(w in compact for w in words)
    forms = _bare_forms(message)
    return any(f.startswith(w) for f in forms for w in words) or (
        len(compact) <= 16 and any(w in compact for w in words)
    )


def _is_menu_tile(message: str, oa: str | None = None) -> bool:
    """A rich-menu tile or a menu command on its own — ANY OA's tile when
    oa is None, since a customer tapping a staff tile text still expects
    an answer about that word, not a repair job named after it."""
    forms = _polite_forms(message)
    forms.discard("")
    if not forms:
        return False
    texts = _ALL_TILE_TEXTS if oa is None else (
        frozenset(t.replace(" ", "").lower() for t in RICH_MENU_TILE_TEXTS.get(oa, ()))
        | frozenset(t.replace(" ", "").lower() for t in _MENU_COMMAND_TEXTS)
    )
    if any(f in texts for f in forms):
        return True
    # "วิธีใช้ 3", "วิธีใช้ทั้งหมด", "สลับภาษา" variants.
    return bool(_HELP_STEP_RE.match(message or "")) or _is_help_request(message, oa or "") or _matches_phrase(
        message, LANGUAGE_TOGGLE_PHRASES + CAPABILITY_PHRASES,
    )


def _action_command(message: str, triggers: tuple[str, ...], code_re: re.Pattern | None = None) -> bool:
    """An ACTION (approve, claim, assign, decline…) is only ever taken from
    a message that starts with its verb, or that names the record code
    with the verb next to it — never from a question, and never from a
    sentence that merely contains the verb.

    Review, 6 Sep 2026: "มีอะไรรออนุมัติไหม" approved a report,
    "ใครรับงาน T-2026-0001" claimed the job for the person asking, and
    "บันทึกว่า C-… ลูกค้ารออนุมัติงบ" was refused as an approval.
    """
    compact = _normalise(message)
    if not compact or compact.startswith(("วิธีใช้", "help", "guide", "คู่มือ")):
        return False
    if _looks_like_a_question(message):
        return False
    words = [t.replace(" ", "").lower() for t in triggers]
    forms = _polite_forms(message)
    if any(f.startswith(w) for f in forms for w in words):
        return True
    if (code_re or TICKET_CODE_RE).search(message or ""):
        # The code and the verb, in either order: "T-2026-0001 รับงาน".
        return any(w in compact for w in words) and len(compact) <= 40
    return False


_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_THAI_MAGNITUDES = (("ล้าน", 1_000_000), ("แสน", 100_000), ("หมื่น", 10_000), ("พัน", 1_000))
_RECORD_CODE_TAIL_RE = re.compile(r"((?:SR|[CDQT])-\d{4}-\d{4})[.,!;:。]+(?=\s|$)", re.IGNORECASE)


def _normalise_message(message: str) -> str:
    """What every handler reads: Thai numerals as Arabic ("T-๒๐๒๖-๐๐๐๑",
    "ดีลเกิน ๑๐๐๐๐"), no punctuation glued to a record code
    ("ข้อมูลลูกค้า C-2026-0001."), and "10k" / "1 หมื่น" as the number
    (review, 6 Sep 2026)."""
    text = (message or "").translate(_THAI_DIGITS)
    text = _RECORD_CODE_TAIL_RE.sub(r"\1", text)
    text = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)\s?k(?![\w])", lambda m: str(int(float(m.group(1)) * 1000)), text, flags=re.IGNORECASE)
    return text


def _thai_amount(number: str, unit: str | None) -> Decimal:
    """"10,000", "1.5 หมื่น", "1.2 ล้าน" as a number."""
    value = Decimal(number.replace(",", ""))
    factor = dict(_THAI_MAGNITUDES).get((unit or "").strip())
    return value * factor if factor else value


def _strip_polite_tail(name: str) -> str:
    """A name typed with a particle glued on: "สมชายหน่อย", "สมศักดิ์ครับ"
    (review, 6 Sep 2026: the particle became part of the name and the
    person was not found)."""
    text = (name or "").strip()
    changed = True
    while changed and text:
        changed = False
        for tail in sorted(_POLITE_TAIL, key=len, reverse=True):
            if text.lower().endswith(tail) and len(text) > len(tail) + 1:
                text = text[: -len(tail)].strip(" ,")
                changed = True
                break
    return text


def _menu_digit(message: str) -> int | None:
    """"2", "2.", "ข้อ 2", "หัวข้อ 2": the number someone typed to pick a
    menu item, or None."""
    m = re.match(r"^\s*(?:ข้อ|หัวข้อ|no\.?|item|#)?\s*(\d{1,2})\s*[.)]?\s*$", (message or ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


# Acknowledgements and thanks. Sending "ขอบคุณครับ" to the intent model and
# answering "ยังไม่แน่ใจว่าต้องการอะไร" is the conversational equivalent of
# not hearing someone say thank you.
SMALL_TALK_PHRASES = (
    "ขอบคุณ", "ขอบคุณมาก", "ขอบใจ", "thanks", "thank you", "thx", "โอเค", "ok", "okay", "okie", "โอเคเลย", "โอเคร",
    "ครับ", "ค่ะ", "คะ", "ครับผม", "รับทราบ", "ทราบแล้ว", "ทราบ", "ได้", "ได้เลย", "เยี่ยม", "ดีมาก", "เข้าใจแล้ว",
    "เข้าใจ", "noted", "good", "great", "nice", "จบ", "พอแล้ว", "พอก่อน", "แค่นี้", "แค่นี้ก่อน", "แค่นี้ก่อนนะ",
    "บาย", "บ๊ายบาย", "ลาก่อน", "bye", "goodbye", "see you", "ไว้จะติดต่อมาใหม่", "ไว้ติดต่อมาใหม่", "ไว้คุยใหม่",
    "แล้วเจอกัน", "ขอบคุณนะ", "ขอบคุณค่ะ", "ขอบคุณครับ", "โอเคขอบคุณ", "ขอบคุณโอเค", "โอเครับทราบ", "รับทราบโอเค",
    "ok thanks", "okay thanks", "เยส", "yes", "yep", "sure", "cool", "👍", "🙏",
)
SMALL_TALK_REPLY = {
    "th": "ยินดีครับ 🙂 มีอะไรพิมพ์มาได้เลย",
    "en": "You're welcome. Just type whenever you need something.",
}
_EMOJI_ONLY = ("👍", "🙏", "👌", "❤️", "😊", "🙂", "😀", "✅")
# An acknowledgement drawn rather than typed — with skin tones, variation
# selectors and repeats ("🙏🙏", "👍🏻") stripped first (review, 6 Sep 2026).
_ACK_EMOJI = frozenset("👍🙏👌❤😊🙂😀✅😁😄😃🥰💕👏😍💯✌🤝🫶🆗☺😉🤗💪")
_EMOJI_MODIFIER_RE = re.compile(r"[\U0001F3FB-\U0001F3FF\uFE0F\u200D\s]")


def _is_ack_emoji(raw: str) -> bool:
    stripped = _EMOJI_MODIFIER_RE.sub("", raw)
    return bool(stripped) and all(ch in _ACK_EMOJI for ch in stripped)


def _is_small_talk(message: str) -> bool:
    raw = (message or "").strip()
    if not raw:
        return False
    if (len(raw) >= 3 and all(ch in "5๕" for ch in raw)) or raw in _EMOJI_ONLY or _is_ack_emoji(raw):
        return True  # "555" laughs; "5" answers a survey
    if _is_only_a_greeting(raw):
        return False
    phrases = {p.replace(" ", "") for p in SMALL_TALK_PHRASES}
    if _bare_forms(message) & phrases:
        return True
    # "โอเคครับ รับทราบ", "พอแล้วครับ ขอบคุณ": every piece is small talk.
    pieces = [pc for pc in re.split(r"[\s,.!]+", _canonical(message)) if pc]
    return len(pieces) > 1 and all(
        (_bare_forms(pc) & phrases) or _is_ack_emoji(pc) or _is_only_a_greeting(pc) for pc in pieces
    )


# "How do I use this", in the many ways people say it. Exact phrases still
# match through HELP_TRIGGERS; these are the words inside a short message
# that mean the same thing. Short keys (≤3 chars) must start the message.
HELP_CONTAINS = (
    "ใช้ยังไง", "ใช้งานยังไง", "ใช้ไง", "ใช้อย่างไร", "ใช้งานอย่างไร", "วิธีใช้", "คู่มือ", "สอนใช้", "สอนหน่อย",
    "ทำยังไง", "ทํายังไง", "ทำไง", "เริ่มยังไง", "เริ่มต้นยังไง", "เริ่มใช้", "ช่วยด้วย", "ช่วยหน่อย", "ช่วยเหลือ",
    "แนะนำการใช้", "แนะนำหน่อย", "คำแนะนำ", "ไม่เข้าใจ", "งง", "ใช้ไม่เป็น", "ใช้งานไม่เป็น", "มีอะไรบ้าง",
    "มีฟังก์ชัน", "มีเมนู", "ทำอะไรได้", "ทําอะไรได้", "ช่วยอะไรได้", "มีคำสั่ง", "คำสั่งทั้งหมด", "พิมพ์อะไร",
    "พิมพ์ยังไง", "how to", "how do i", "what can you", "help", "guide", "tutorial", "menu", "มีอะไรให้ทำ", "ต้องกดอะไร",
    "กดตรงไหน", "ต้องทำไง", "ต้องทำยังไง", "เริ่มตรงไหน",
)
_HOW_TO_WORDS = ("ยังไง", "ทำไง", "อย่างไร", "ทำอย่างไร", "ต้องทำ", "how do", "how to", "ขั้นตอน", "กดตรงไหน")
# The generic ones above ("มีอะไรบ้าง", "ทำอะไรได้") are about the system only
# when no record type is named: "ดีลมีอะไรบ้าง" is a list request.
_HELP_GENERIC = ("มีอะไรบ้าง", "ทำอะไรได้", "ทําอะไรได้", "ช่วยอะไรได้", "มีฟังก์ชัน", "มีเมนู")
_ENTITY_WORDS = ("ลูกค้า", "ดีล", "งาน", "สินค้า", "ใบเสนอ", "นัด", "ทีม", "ช่าง", "ประกัน", "รายงาน", "บันทึก", "เตือน")


def _is_help_request(message: str, oa: str = "") -> bool:
    if (
        _matches_phrase(message, HELP_TRIGGERS) or _matches_phrase(message, HELP_EXAMPLES_PHRASES)
        or (message or "").strip() in ("?", "??", "???") or _is_general_capability_question(message)
    ):
        return True
    compact = _normalise(message)
    if not compact or len(compact) > 40:
        return False
    if re.search(r"[CDQT]-\d{4}-\d{4}", message or "", re.I):
        return False
    if oa == "customer" and _looks_like_fault(message) and not (
        # "จะแจ้งซ่อมต้องทำไง": how to report, not a report.
        any(w in compact for w in _HOW_TO_WORDS)
        and not _looks_like_fault(re.sub(r"แจ้งซ่อม|แจ้งเสีย|แจ้งปัญหา|ซ่อม", "", _canonical(message)))
    ):
        return False
    if compact in HELP_EXACT_SHORT or _compact(message) in HELP_EXACT_SHORT:
        return True
    names_entity = any(w in compact for w in _ENTITY_WORDS)
    # "วันนี้มีอะไรบ้าง" is about the day, not the system.
    names_day = any(w in compact for w in ("วันนี้", "พรุ่งนี้", "สัปดาห์นี้", "อาทิตย์นี้", "today", "tomorrow"))
    # "ช่วยเพิ่มลูกค้า สมชาย ใจดี 0812345678 ให้หน่อยครับ" — a complete
    # order, politely worded — was answered with the nine-topic guide.
    # HELP_CONTAINS carries "ช่วยหน่อย", which normalises to "ช่วย", and
    # the substring rule below then claimed every short sentence
    # containing it (10 ก.ย. 2569). A polite prefix in front of a create
    # verb is a command; the manual is not an answer to it.
    if _POLITE_REQUEST_RE.match(compact) and _is_create_command(
        _POLITE_REQUEST_RE.sub("", compact, count=1), oa
    ):
        return False
    for key in HELP_CONTAINS:
        k = _normalise(key)
        if key in _HELP_GENERIC and (names_entity or names_day):
            continue
        if len(k) <= 3:
            if compact.startswith(k) or compact == k:
                return True
        elif k in compact:
            return True
    return False


HELP_EXACT_SHORT = frozenset({"ช่วย", "สอน", "แนะนำ", "help", "งง", "เมนู", "menu", "?", "สอนที", "ช่วยที"})


def _parse_after_trigger(message: str, triggers: tuple[str, ...]) -> str | None:
    """The text following a trigger, or None if no trigger matched.

    An empty string is a real result meaning "trigger present, nothing
    after it" — the caller turns that into a prompt rather than guessing.
    """
    text = (message or "").strip()
    lowered = text.lower()
    for trigger in triggers:
        index = lowered.find(trigger.lower())
        if index == -1:
            continue
        return _strip_leading_connector(text[index + len(trigger):].strip())
    return None


def _customer_name(customer: dict) -> str:
    parts = [customer.get("first_name") or "", customer.get("last_name") or ""]
    return " ".join(p for p in parts if p).strip() or "-"


def _truncation_note(shown: int, total: int, language: str, section: str) -> str:
    """What to append when a list did not fit.

    Says the real total either way — knowing there are 240 customers is
    useful even when the link cannot be built — and adds the deep link only
    when there is one to add.
    """
    if total <= shown:
        return ""
    note = _t(LIST_TRUNCATED, language).format(shown=shown, total=total)
    url = dashboard_link(section)
    if url:
        return note + _t(LIST_SEE_ALL, language).format(url=url)
    return note + _t(LIST_SEE_ALL_NO_LINK, language)


def _list_card(
    *, title: str, rows: list[dict], section: str, language: str,
    shown: int, total: int, oa: str = "sales",
) -> dict:
    """The structured form of a list reply.

    Each row carries its own action, which is the fix for a real problem in
    the first version: a single "view details" quick reply had to guess
    which record was meant, and guessing the first one is wrong more often
    than not.
    """
    note = f"{shown}/{total}" if total > shown else str(total)
    card = {"title": title, "rows": rows, "note": note}
    url = dashboard_link(section, oa)
    if url:
        card["footer_label"] = _t(OPEN_DASHBOARD, language)
        card["footer_url"] = url
    return card


def _dashboard_button(section: str, language: str) -> tuple[str, str] | None:
    """The (label, url) pair for a dashboard quick-reply button.

    A uri action rather than a message action: tapping opens the page
    immediately instead of sending a message that the bot has to answer
    with a link the person then taps again.
    """
    url = dashboard_link(section)
    return (_t(OPEN_DASHBOARD, language), url) if url else None


async def _handle_customer_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
    search_term: str | None = None,
) -> ChatReply:
    if "customer.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        customers = await client.list_customers(str(license_id))
    except Exception:
        log.exception("customer list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if search_term:
        needle = search_term.lower()
        # Filtered here rather than in a Data-tier query: the tenant-scoped
        # list is already fetched, the volumes at SMB scale are small, and
        # adding a search endpoint for this would be a schema change for no
        # behavioural gain. Revisit if a tenant ever has thousands.
        customers = [
            c for c in customers
            if needle in _customer_name(c).lower()
            or needle in str(c.get("phone") or "")
            or needle in str(c.get("customer_id") or "").lower()
        ]
        if not customers:
            return ChatReply(text=_t(SEARCH_NO_MATCH, language).format(term=search_term))

    if not customers:
        return ChatReply(
            text=_t(EMPTY_LIST, language).format(what="ลูกค้า" if language == "th" else "customers"),
            quick_replies=[("เพิ่มลูกค้าใหม่", "สร้างลูกค้า")],
        )

    shown = customers[:LIST_LIMIT]
    lines = [
        f"{c.get('customer_id') or '-'} · {_customer_name(c)}"
        f" · {_label(CUSTOMER_STAGE_LABELS, c.get('stage'), language)}"
        + (f" · {c.get('phone')}" if c.get("phone") else "")
        for c in shown
    ]
    text = "\n".join(lines) + _truncation_note(len(shown), len(customers), language, "customers")
    return ChatReply(
        text=text,
        # Quick replies are now only "what to say next" — navigation moved
        # into the card, where a row's button knows which row it belongs to.
        quick_replies=[
            ("ค้นหาลูกค้า", "ค้นหาลูกค้า "),
            ("รายการดีล", "รายการดีล"),
        ],
        quick_reply_url=_dashboard_button("customers", language),
        list_card=_list_card(
            title="ลูกค้า", section="customers", language=language,
            shown=len(shown), total=len(customers),
            rows=[
                {
                    "title": _customer_name(c),
                    "subtitle": " · ".join(
                        p for p in (
                            str(c.get("customer_id") or ""),
                            _label(CUSTOMER_STAGE_LABELS, c.get("stage"), language),
                            str(c.get("phone") or ""),
                        ) if p
                    ),
                    "stage": c.get("stage"),
                    "action_label": "ดู",
                    "action_text": f"ข้อมูลลูกค้า {c.get('customer_id')}",
                }
                for c in shown
            ],
        ),
    )


async def _handle_customer_detail(
    client: DataClient, *, license_id, code: str, permission_keys: list[str], language: str,
    ctx: ResolvedContext | None = None,
) -> ChatReply:
    if "customer.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not code and ctx is not None:
        # Bare "ข้อมูลลูกค้า" as a REPLY to a message about someone, or
        # right after working on them, means that person — asking "ระบุ
        # คำค้น" about the record the conversation is already on was the
        # 21:49 dead end wearing different words.
        try:
            ref = await _last_entity_ref(client, ctx)
        except Exception:
            ref = None
        if ref and ref.get("entity_type") == "customer":
            code = str(ref.get("code") or "")
    if not code:
        return ChatReply(text=_t(SEARCH_NEEDS_TERM, language))
    try:
        customers = await client.list_customers(str(license_id))
    except Exception:
        log.exception("customer detail failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    wanted = code.strip().lower()
    customer = next(
        (c for c in customers if str(c.get("customer_id") or "").lower() == wanted), None
    )
    if customer is None:
        # Not a code — a name, or a phone number. "ข้อมูลลูกค้า สมชาย" is
        # how people ask; the code is something they would have to look
        # up first, which defeats the point of asking.
        digits = "".join(ch for ch in wanted if ch.isdigit())
        matches = [
            c for c in customers
            if wanted in _display_name(c).lower()
            or (digits and digits in "".join(ch for ch in str(c.get("phone") or "") if ch.isdigit()))
        ]
        if len(matches) == 1:
            customer = matches[0]
        elif len(matches) > 1:
            return ChatReply(
                text=_t(CUSTOMER_AMBIGUOUS_LEAD, language).format(name=code) + "\n"
                + "\n".join(
                    f"· {c.get('customer_id')} {_display_name(c)}" for c in matches[:LIST_LIMIT]
                ),
                quick_replies=[
                    (_display_name(c)[:20], f"ข้อมูลลูกค้า {c.get('customer_id')}")
                    for c in matches[:4]
                ],
            )
    if customer is None:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(
                what="ลูกค้า" if language == "th" else "customer", code=code
            )
        )

    if ctx is not None:
        await _remember_entity(
            client, ctx, entity_type="customer",
            entity_id=customer["id"], code=customer["customer_id"],
        )
        # BOTH refs. They are separate keys for good reasons (see
        # cache.k_last_entity_ref), but viewing a customer wrote only the
        # generic one — so "สร้างดีล" straight after opening a customer
        # asked which customer, seconds after showing them.
        await _remember_customer(client, ctx, customer)

    en = language == "en"
    rows = [
        f"{customer.get('customer_id')} · {_customer_name(customer)}",
        f"{'Stage' if en else 'สถานะ'}: {_label(CUSTOMER_STAGE_LABELS, customer.get('stage'), language)}",
    ]
    for field_name, label in (
        ("phone", "Phone" if en else "โทร"), ("email", "Email" if en else "อีเมล"),
        ("address", "Address" if en else "ที่อยู่"), ("notes", "Notes" if en else "บันทึก"),
    ):
        if customer.get(field_name):
            rows.append(f"{label}: {customer[field_name]}")

    return ChatReply(
        text="\n".join(rows),
        entity_type="customer",
        entity_id=str(customer.get("id") or ""),
        quick_replies=[
            ("สร้างดีล", f"สร้างดีลให้ {_customer_name(customer)}"),
            ("รายชื่อลูกค้า", "รายชื่อลูกค้า"),
        ],
    )


# The system writes this text itself, on a quick-reply button, in a shape
# it chose — so sending it back through a model to be interpreted is
# strange in principle and fragile in practice: it fails when the AI is
# down, fails when the AI labels the entity differently, and spends a
# model call on every tap of a button whose meaning was never in doubt.
DEAL_CREATE_TRIGGERS = (
    "สร้างดีลใหม่ให้", "เปิดดีลใหม่ให้", "สร้างดีลใหม่กับ", "สร้างดีลให้", "เปิดดีลให้", "สร้างดีลกับ", "เปิดดีลกับ",
    "create deal for", "open deal for", "new deal for", "open a deal for", "create a deal for",
)


DEAL_PRODUCT_ADD_TRIGGERS = (
    "เพิ่มสินค้าเข้าดีล", "ใส่สินค้าเข้าดีล", "เพิ่มสินค้า", "ใส่สินค้า",
    "add product to deal",
)

# "พัดลม 2 ตัว ราคา 1500" / "FAN001 x2 1500" — quantity and price pulled
# out wherever they appear, because nobody types a fixed field order.
# Anchored to a counting word, and only where one is present. An earlier
# version matched any bare number, which ate the "18" out of
# "พัดลมตั้งพื้น 18 นิ้ว 2 ตัว" and left the product called
# "พัดลมตั้งพื้น นิ้ว" — model numbers and sizes live inside product
# names constantly, so a number alone can never mean a quantity.
# The sign and the decimal point are part of the number and are carried
# through extraction. Dropping them is how "เพิ่มพัดลมอีก -1 ตัว" reached the
# positive-value guard as a plain 1 and added a fan, and how "อีก 1.5 ตัว"
# became five (review v3, B05). A minus only counts as one where nothing
# precedes it: the "-3" in "FAN-3 ตัว" is part of a model name.
_THAI_DIGIT_MAP = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_QTY_RE = re.compile(
    r"(?:x|×|จำนวน)\s*(-?\d+(?:\.\d+)?)"
    r"|(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:ตัว|ชิ้น|อัน|เครื่อง|ชุด)\b",
    re.I,
)


def _qty_number(text: str | None) -> float | None:
    """The quantity as written, Thai digits included, or None."""
    raw = str(text or "").strip().translate(_THAI_DIGIT_MAP)
    return float(raw) if re.fullmatch(r"-?\d+(?:\.\d+)?", raw) else None


def _whole_qty(text: str | None) -> int | None:
    """A quantity something can actually be counted in: a whole number,
    zero or above. None for a negative, a fraction, or nonsense — the
    caller says so rather than rounding it into something plausible."""
    value = _qty_number(text)
    if value is None or value < 0 or not value.is_integer():
        return None
    return int(value)


def _tidy_qty(value: float | None) -> int | float | None:
    """3.0 back to 3, so a rebuilt sentence says "เป็น 3"."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


_PRICE_RE = re.compile(r"(?:ราคา|@|฿)\s*([\d,]+(?:\.\d{1,2})?)\s*(?:บาท|฿|baht)?", re.I)

DEAL_PRODUCT_ADDED = {
    "th": "เพิ่ม {name} × {qty} ราคา {price} เข้าดีล {deal_id} แล้ว\nรวม {total}",
    "en": "Added {name} × {qty} at {price} to {deal_id}. Total {total}",
}
DEAL_PRODUCT_NEEDS_DEAL = {
    "th": "เพิ่มสินค้าเข้าดีลไหนครับ พิมพ์ \"เพิ่มสินค้า <ชื่อ> เข้าดีล D-2026-0001\"",
    "en": 'Which deal? Type "add product <name> to deal D-2026-0001".',
}
DEAL_PRODUCT_NEEDS_NAME = {
    "th": "ระบุชื่อสินค้าและราคาด้วยครับ เช่น \"เพิ่มสินค้า พัดลม 2 ตัว ราคา 1500\"",
    "en": 'Name and price please, e.g. "add product fan 2 at 1500".',
}
PRODUCT_UNKNOWN_NEEDS_PRICE = {
    "th": "ไม่มี \"{name}\" ในรายการสินค้า ระบุราคาด้วยครับ เช่น \"{name} 2 ตัว ราคา 1500\"\n(หรือเพิ่มเข้ารายการสินค้าก่อนด้วย \"สร้างสินค้า\")",
    "en": '"{name}" is not in the catalogue — give a price, e.g. "{name} 2 at 1500".',
}

DEAL_PRODUCT_AMBIGUOUS = {
    "th": "มีสินค้าหลายรายการที่ตรงกับ \"{name}\" เลือกอันไหนครับ\n{options}",
    "en": 'Several products match "{name}" — which one?\n{options}',
}

DEAL_PRODUCT_NEEDS_PRICE = {
    "th": "สินค้า \"{name}\" ราคาเท่าไหร่ครับ",
    "en": 'What is the price for "{name}"?',
}


def _distinguishing_part(name: str, others: list[str]) -> str:
    """What makes this option different from the ones beside it.

    Four products called "พัดลมตั้งพื้น 16/18/20/22 นิ้ว" produce four
    buttons that are identical once LINE truncates them to 20 characters.
    Dropping the shared prefix keeps the digits — the only part anyone is
    reading.
    """
    rest = [o for o in others if o and o != name]
    if not rest:
        return name

    shared = 0
    for index, char in enumerate(name):
        if all(index < len(o) and o[index] == char for o in rest):
            shared = index + 1
        else:
            break

    # Back off to the last space before the divergence. "16 นิ้ว" and
    # "18 นิ้ว" share everything up to the "1", so cutting at the raw
    # divergence point produces "6 นิ้ว" and "8 นิ้ว" — which are not the
    # sizes and would have someone order the wrong fan.
    boundary = name.rfind(" ", 0, shared)
    trimmed = name[boundary + 1:].lstrip() if boundary >= 0 else name[shared:].lstrip()
    if not trimmed or len(trimmed) < 2:
        return name
    return trimmed


# Editing a line already on a deal or a quote. Before this the only way
# to correct a quantity was to delete and retype it, which loses the
# line's position and on a deal with several similar products is easy to
# do to the wrong one.

LINE_EDIT_TRIGGERS = (
    "แก้ราคา", "เปลี่ยนราคา", "ลดราคา", "ปรับราคา", "ขึ้นราคา",
    "แก้จำนวน", "เปลี่ยนจำนวน", "เพิ่มจำนวน", "ลดจำนวน", "ปรับจำนวน",
)
LINE_REMOVE_TRIGGERS = ("ลบสินค้า", "เอาสินค้าออก", "ตัดสินค้า", "remove product")

LINE_NONE_YET = {
    "th": "{where} {code} ยังไม่มีสินค้าให้แก้",
    "en": "{code} has no lines to change.",
}
LINE_WHICH_ONE = {
    "th": "{where} {code} มีหลายรายการ จะแก้อันไหนครับ\n{options}",
    "en": "{code} has several lines — which one?\n{options}",
}
LINE_NOT_FOUND = {
    "th": "ไม่พบสินค้า \"{name}\" ใน{where} {code}",
    "en": 'No line called "{name}" on {code}',
}
LINE_UPDATED = {
    "th": "แก้ {name} เป็น {qty} × {price} = {total} ใน{where} {code} แล้ว",
    "en": "Updated {name} to {qty} × {price} = {total} on {code}.",
}
LINE_REMOVED = {
    "th": "ลบ {name} ออกจาก{where} {code} แล้ว",
    "en": "Removed {name} from {code}.",
}
LINE_NEEDS_TARGET = {
    "th": "แก้ของดีลหรือใบเสนอราคาไหนครับ พิมพ์รหัสด้วย เช่น \"ลดราคาพัดลมเหลือ 1400 ใน Q-2026-0001\"",
    "en": "Which deal or quote? Include the code.",
}
LINE_QUOTE_LOCKED = {
    "th": "ใบเสนอราคา {code} ออกเอกสารแล้ว แก้ไม่ได้ ถ้าต้องแก้ให้ยกเลิกแล้วสร้างใบใหม่",
    "en": "Quote {code} has been issued and cannot be changed.",
}

_NEW_PRICE_RE = re.compile(r"(?:เหลือ|เป็น|as|to)\s*([\d,]+(?:\.\d{1,2})?)\s*(?:บาท|฿|baht)?", re.I)


# ---------------------------------------------------------------- line items, as spoken
#
# Owner's live test (8 Sep 2026): "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ"
# was answered "ไม่พบสินค้า"; "เพิ่มพัดลมอีก 3 ตัว" SET the quantity to 3
# instead of adding 3; "ลบสินค้าพัดลมออก" looked for a product called
# "พัดลมออก". One normaliser for the words around a product's name, one
# parser for the shapes people type, and one handler behind both the
# typed path and the model's reading of the same sentence.

_ITEM_COUNT_UNITS = r"ตัว|ชิ้น|อัน|เครื่อง|ชุด|รายการ|units?|pcs|pieces?|items?"
# The same table the clock uses, so a number word understood in "บ่ายสอง" is
# understood in "อีกสามตัว" (review v3: "สาม" was not read as 3 at all).
_QTY_WORDS = thai_number_words(20)
_QTY_WORDS_RE = "|".join(sorted(_QTY_WORDS, key=len, reverse=True))
# Only where the word is unmistakably a count: at the start, after a space,
# or straight after one of the words that introduce a quantity. A number
# word buried in a name stays a name — "เก้าอี้" is a chair and "ชุดสามชิ้น"
# is what a three-piece set is called, not three of something.
_QTY_WORD_LEAD = r"(?:^|(?<=\s)|(?<=อีก)|(?<=เป็น)|(?<=เหลือ)|(?<=จำนวน))"
_QTY_WORD_BEFORE_UNIT_RE = re.compile(
    _QTY_WORD_LEAD + r"(" + _QTY_WORDS_RE + r")(?=\s*(?:" + _ITEM_COUNT_UNITS + r")(?![ก-๙]))",
    re.I,
)
_QTY_WORD_AS_TARGET_RE = re.compile(
    r"(?:(?<=เป็น)|(?<=เหลือ))\s*(" + _QTY_WORDS_RE + r")(?![ก-๙\w])",
)


def _fold_qty_words(text: str) -> str:
    """"เพิ่มพัดลมอีกสามตัว" -> "เพิ่มพัดลมอีก3ตัว"; "เป็นสาม" -> "เป็น3"."""
    folded = _QTY_WORD_BEFORE_UNIT_RE.sub(lambda m: str(_QTY_WORDS[m[1]]), text or "")
    return _QTY_WORD_AS_TARGET_RE.sub(lambda m: str(_QTY_WORDS[m[1]]), folded)


# "อีก 2", "เอาเพิ่ม 2", "2 more": the delta of an increment, with or
# without a counting word. A size ("อีก 18 นิ้ว") is never a delta.
_ITEM_MORE_QTY_RE = re.compile(
    r"(?:อีก|ไปอีก|เพิ่มอีก|เอาเพิ่ม|เพิ่มเติม|another)\s*(?:สัก)?\s*(-?\d+(?:\.\d+)?)"
    r"(?!\s*(?:นิ้ว|ตัน|ลิตร|btu|วัตต์|แรง))"
    r"|(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:more|extra|additional)\b",
    re.I,
)
_ITEM_MORE_WORDS_RE = re.compile(r"ไปอีก|เพิ่มอีก|เอาเพิ่ม|เพิ่มเติม|อีก|\bmore\b|\bextra\b|\badditional\b|\banother\b", re.I)
_ITEM_LESS_QTY_RE = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:less|fewer)\b"
    r"|(?:by|off)\s*(-?\d+(?:\.\d+)?)\b",
    re.I,
)
# A number followed by one of these is a SIZE inside the name, never a count.
_ITEM_SIZE_AFTER_NUMBER_RE = re.compile(r"\d+\s*(?:นิ้ว|ตัน|ลิตร|btu|วัตต์|แรง|ซม|มม|กก|kg|cm|mm|hp)", re.I)

# Words that join a product to the record, anywhere in the sentence.
_ITEM_INNER_PHRASES = (
    "ออกจากดีลนี้", "ออกจากดีล", "ออกจากใบเสนอราคา", "จากดีลนี้", "จากดีล", "ในดีลนี้", "ในดีล", "เข้าดีลนี้", "เข้าดีล",
    "ให้ดีลนี้", "ให้ดีล", "ของดีลนี้", "ของดีล", "ในใบเสนอราคา", "เข้าใบเสนอราคา", "ของใบเสนอราคา",
    "to this deal", "to the deal", "on this deal", "on the deal", "from this deal", "from the deal", "into the deal",
    "in the deal", "in this deal", "to the quote", "on the quote", "from the quote", "to deal", "from deal",
    "more", "extra", "additional",
)
# Verbs and fillers in front of the name.
_ITEM_LEAD_WORDS = (
    "เพิ่มสินค้า", "ใส่สินค้า", "ลบสินค้า", "เอาสินค้า", "ตัดสินค้า", "เพิ่มรายการ", "ใส่รายการ", "ลบรายการ", "เอารายการ",
    "เพิ่มจำนวน", "ลดจำนวน", "เพิ่ม", "บวก", "ใส่", "เอา", "ลบ", "ตัด", "ลด", "สินค้า", "รายการ", "ที่ชื่อ", "ชื่อ", "ที่เป็น",
    "add product", "add item", "add the", "add a", "add an", "add", "put in", "put", "insert", "remove product",
    "remove item", "remove the", "remove a", "remove", "delete the", "delete item", "delete", "drop the", "drop",
    "take the", "take", "the", "an", "a",
)
# Particles and fillers after it.
_ITEM_TAIL_WORDS = (
    "ให้หน่อยครับ", "ให้หน่อยค่ะ", "ให้หน่อย", "ให้ด้วย", "ด้วยนะครับ", "ด้วยนะคะ", "ด้วยนะ", "ด้วยครับ", "ด้วยค่ะ", "ด้วย",
    "หน่อยครับ", "หน่อยค่ะ", "หน่อยนะ", "หน่อย", "นะครับ", "นะคะ", "นะค่ะ", "ครับ", "ค่ะ", "คะ", "นะ", "เลย", "ที", "ออก",
    "ทิ้ง", "ทิ้งไป", "ไปเลย", "ไป", "ซะ", "เข้าไป", "ไปอีก", "เพิ่มอีก", "อีก", "เพิ่มเติม", "ราคา", "จำนวน", "ลง", "สัก",
    "ใน", "เข้า", "ให้", "จาก", "ของ",
    "please", "pls", "more", "extra", "additional", "off", "away", "out", "in", "into", "on", "to", "from",
)
_ITEM_LEAD_SORTED = tuple(sorted(_ITEM_LEAD_WORDS, key=len, reverse=True))
_ITEM_TAIL_SORTED = tuple(sorted(_ITEM_TAIL_WORDS, key=len, reverse=True))
# A name made of one of these is another feature's object, not a product.
_ITEM_EXCLUDED_NOUNS = (
    "ลูกค้า", "lead", "ลีด", "บันทึก", "โน้ต", "นัด", "เตือน", "ช่าง", "ทีม", "สมาชิก", "ผู้ใช้", "งานซ่อม", "ใบงาน",
    "ใบเสนอราคา", "สิทธิ์", "กฎ", "ประกัน", "ซีเรียล", "รูป", "ที่อยู่", "เบอร์", "โทร", "อีเมล", "ส่วนลด", "ดีล", "บริษัท",
    "ร้าน", "รหัส", "เอกสาร", "รายงาน", "แชท", "ภาษา", "ใหม่", "ทั้งหมด", "รายชื่อ", "ระบบ", "ทะเบียน", "ฐานข้อมูล",
    "customer", "note", "reminder", "appointment", "technician", "team", "member", "ticket", "job", "warranty",
    "serial", "photo", "address", "phone", "email", "discount", "deal", "quote", "company", "shop", "document", "report",
    "chat", "language", "new", "everything", "all", "list", "system", "database", "record",
)


def _strip_item_particles(name: str) -> str:
    """The product's name and nothing else.

    "ลบสินค้าพัดลมออก" -> "พัดลม"; "ใส่สินค้า พัดลม 18 นิ้ว อีก" ->
    "พัดลม 18 นิ้ว"; "ทีวี 40 นิ้ว ไปอีก" -> "ทีวี 40 นิ้ว". Sizes stay
    inside the name — "18 นิ้ว" is what the fan IS. Quantities and prices
    are taken out by the callers before this runs.
    """
    text = " ".join(str(name or "").split())
    text = re.sub(r"(?<![A-Za-z0-9])[QD]-\d{4}-\d{4}(?![0-9])", " ", text, flags=re.I)
    for phrase in sorted(_ITEM_INNER_PHRASES, key=len, reverse=True):
        if phrase.isascii():
            text = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", text, flags=re.I)
        else:
            text = text.replace(phrase, " ")
    text = " ".join(text.split()).strip(" :·-,")
    text = re.sub(
        r"^(?:ช่วย|รบกวน|ขอ|กรุณา|please)\s*(?=เพิ่ม|ใส่|ลบ|เอา|ตัด|ลด|add|remove|delete|put|insert|drop|take)",
        "", text, flags=re.I,
    )
    changed = True
    while changed and text:
        changed = False
        low = text.lower()
        for lead in _ITEM_LEAD_SORTED:
            if not low.startswith(lead) or len(text) <= len(lead):
                continue
            if lead.isascii() and not low[len(lead)].isspace():
                continue
            text = text[len(lead):].strip(" :·-,")
            changed = True
            break
        low = text.lower()
        for tail in _ITEM_TAIL_SORTED:
            if not low.endswith(tail) or len(text) <= len(tail):
                continue
            if tail.isascii() and not low[-len(tail) - 1].isspace():
                continue
            text = text[: -len(tail)].strip(" :·-,")
            changed = True
            break
    if text.lower() in _ITEM_TAIL_SORTED or text.lower() in _ITEM_LEAD_SORTED or text.lower() in ("product", "item"):
        # "เพิ่มอีก 1 ตัว" leaves only "อีก", "เอาเพิ่ม 2 ตัว" only "เพิ่ม": no
        # name was given at all.
        return ""
    return " ".join(text.split())


def _item_is_excluded(name: str) -> bool:
    low = (name or "").strip().lower()
    return not low or any(w in low for w in _ITEM_EXCLUDED_NOUNS)


def _line_code_in(message: str) -> str | None:
    m = re.search(r"(?<![A-Za-z0-9])([QD]-\d{4}-\d{4})(?![0-9])", message or "", re.I)
    return m.group(1).upper() if m else None


def _item_unit_word(message: str) -> str:
    """The counting word the person used ("ตัว", "ชิ้น" …), for the reply."""
    m = re.search(r"\d+\s*(ตัว|ชิ้น|อัน|เครื่อง|ชุด|รายการ)", message or "")
    return m.group(1) if m else "ตัว"


_ITEM_HEAD_RE = re.compile(
    r"^(?:ช่วย|รบกวน|ขอ|กรุณา|please)?\s*"
    r"(เพิ่มสินค้า|ใส่สินค้า|เพิ่มรายการ|ใส่รายการ|เพิ่มจำนวน|ลดจำนวน|ลบสินค้า|ลบรายการ|เอาสินค้าออก|ตัดสินค้า|เอาเพิ่ม|เอาออก|"
    r"เพิ่ม|บวก|ใส่|เอา|ลด|ลบ|ตัด|ไปอีก|อีก|add|put|insert|remove|delete|drop|take)",
    re.I,
)
_ITEM_SET_QTY_RE = re.compile(
    r"^(?:แก้ไข|แก้|เปลี่ยน|ปรับ|ตั้ง|set|change|update)\s*(?:สินค้า|รายการ|จำนวน|the)?\s*(.*?)\s*(?:เป็น|เหลือ|ให้เหลือ|to|=)\s*(-?\d+(?:\.\d+)?)\s*"
    + r"(?:" + _ITEM_COUNT_UNITS + r")?\s*(?:ครับ|ค่ะ|คะ|นะ)?$",
    re.I,
)
_ITEM_BARE_QTY_RE = re.compile(
    r"^(.+?)\s*(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:" + _ITEM_COUNT_UNITS + r")\s*(?:ครับ|ค่ะ|คะ|นะ)?$",
    re.I,
)


def _parse_line_item_command(message: str) -> dict | None:
    """What a sentence about a line item asks for, or None when it is not one.

    Returns {"op", "name", "qty", "price", "more", "code", "unit"} with op
    one of add / decrement / delete / set_qty / bare_qty:
      "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ" -> add, qty 2, price 4000
      "เพิ่มพัดลมอีก 3 ตัว" / "เพิ่มอีก 1 ตัว" / "อีก 1"  -> add with more=True
      "ลดพัดลม 1 ตัว" / "เอาออก 1 ตัว"                    -> decrement
      "ลบสินค้าพัดลมออก" / "เอาพัดลมออก" / "remove the fan" -> delete
      "แก้พัดลมเป็น 3 ตัว"                                -> set_qty
      "พัดลม 3 ตัว"                                       -> bare_qty (set, when the line exists)
    The price-and-quantity triggers ("แก้ราคา", "เปลี่ยนจำนวน") keep their
    own path; a sentence with "อีก" is an increment whatever verb it uses.
    """
    raw = _fold_qty_words(" ".join((message or "").split()))
    if not raw or "\n" in (message or "") or len(raw) > 120:
        return None
    lowered = raw.lower()
    if any(w in lowered for w in ("ส่วนลด", "discount", "%")):
        return None
    more = bool(_ITEM_MORE_WORDS_RE.search(raw))
    code = _line_code_in(raw)
    body = _strip_polite_tail(" ".join(re.sub(r"(?<![A-Za-z0-9])[QD]-\d{4}-\d{4}(?![0-9])", " ", raw, flags=re.I).split()))
    low = body.lower()

    if not more:
        if any(t in lowered for t in LINE_EDIT_TRIGGERS):
            return None
        m = _ITEM_SET_QTY_RE.match(body)
        if m and not any(w in low for w in ("ราคา", "price", "บาท")):
            name = _strip_item_particles(m.group(1))
            if name and _item_is_excluded(name):
                return None
            return {"op": "set_qty", "name": name, "qty": _tidy_qty(_qty_number(m.group(2))),
                    "price": None, "more": False, "code": code, "unit": _item_unit_word(body)}

    price = None
    pm = _PRICE_RE.search(body)
    if pm:
        price = pm.group(1).replace(",", "")
        body = body.replace(pm.group(0), " ")
        low = body.lower()

    head = _ITEM_HEAD_RE.match(body)
    verb = head.group(1).lower() if head else ""
    unit = _item_unit_word(body)

    qty = None
    rest = body
    # The unit-anchored count first ("3 ตัว"), then a bare "อีก 3" — the
    # other order left " ตัว" behind as part of the name.
    qm = _QTY_RE.search(body) or _ITEM_MORE_QTY_RE.search(body)
    if qm:
        qty = _qty_number(qm.group(1) or qm.group(2))
        rest = body.replace(qm.group(0), " ")
    elif head:
        # "add 2 heaters", "remove 1 fan": the count right after the verb.
        lead = re.match(
            r"\s*(-?\d+(?:\.\d+)?)\s+(?!\d)(?!(?:นิ้ว|ตัน|ลิตร|btu|วัตต์|แรง|more|extra)\b)",
            body[head.end():], re.I,
        )
        if lead:
            qty = _qty_number(lead.group(1))
            rest = body[: head.end()] + " " + body[head.end() + lead.end():]
    rest = re.sub(r"(?:^|(?<=\s))(?:" + _ITEM_COUNT_UNITS + r")(?=\s|$)", " ", rest, flags=re.I)

    if verb in ("เพิ่มสินค้า", "ใส่สินค้า", "เพิ่มรายการ", "ใส่รายการ", "เพิ่มจำนวน", "เพิ่ม", "บวก", "ใส่", "เอาเพิ่ม", "ไปอีก", "อีก",
                "add", "put", "insert") or (verb == "เอา" and more):
        if verb == "เพิ่มจำนวน" and not more:
            return None
        name = _strip_item_particles(rest)
        if name and _item_is_excluded(name):
            return None
        if not name and not more:
            return None
        if not name and qty is None:
            return None
        return {"op": "add", "name": name or None, "qty": 1 if qty is None else _tidy_qty(qty),
                "price": price, "more": more, "code": code, "unit": unit}

    if verb in ("ลด", "ลดจำนวน"):
        if price is not None or any(w in low for w in ("ราคา", "price", "บาท")):
            return None
        if re.search(r"(?:เหลือ|เป็น)\s*\d", low) and not more:
            return None
        if qty is None:
            lm = _ITEM_LESS_QTY_RE.search(body)
            if lm:
                qty = _qty_number(lm.group(1) or lm.group(2))
                rest = body.replace(lm.group(0), " ")
        if qty is None:
            return None
        name = _strip_item_particles(rest)
        if name and _item_is_excluded(name):
            return None
        return {"op": "decrement", "name": name or None, "qty": _tidy_qty(qty), "price": None,
                "more": False, "code": code, "unit": unit}

    if verb in ("ลบสินค้า", "ลบรายการ", "เอาสินค้าออก", "ตัดสินค้า", "เอาออก", "เอา", "ลบ", "ตัด", "remove", "delete", "drop", "take"):
        has_out = "ออก" in low or "ทิ้ง" in low or bool(re.search(r"\b(?:off|out|away)\b", low))
        if qty is None and verb in ("remove", "take"):
            lm = _ITEM_LESS_QTY_RE.search(body)
            if lm:
                qty = _qty_number(lm.group(1) or lm.group(2))
                rest = body.replace(lm.group(0), " ")
        if qty is not None and "ทั้งหมด" not in low and not re.search(r"\ball\b", low):
            # "เอาพัดลมออก 1 ตัว", "remove 1 fan": fewer, not gone.
            name = _strip_item_particles(rest)
            if name and _item_is_excluded(name):
                return None
            return {"op": "decrement", "name": name or None, "qty": _tidy_qty(qty), "price": None,
                    "more": False, "code": code, "unit": unit}
        explicit = verb in ("ลบสินค้า", "ลบรายการ", "เอาสินค้าออก", "ตัดสินค้า", "remove", "delete", "drop")
        if not (explicit or has_out):
            return None
        name = _strip_item_particles(rest)
        if name and _item_is_excluded(name):
            return None
        if not name and verb in ("ลบ", "delete", "drop", "take") and not has_out:
            return None
        return {"op": "delete", "name": name or None, "qty": None, "price": None, "more": False, "code": code, "unit": unit,
                "explicit": explicit or "สินค้า" in low or "รายการ" in low}

    if verb:
        return None
    if _looks_like_a_question(raw) or price is not None:
        return None
    m = _ITEM_BARE_QTY_RE.match(body)
    if not m:
        return None
    name = _strip_item_particles(m.group(1))
    if _item_is_excluded(name) or re.search(r"\d\s*$", name) or len(name) < 2:
        return None
    if re.match(r"^(?:สร้าง|เปิด|ดู|ขอ|มี|create|open|show|view)", name.lower()):
        return None
    return {"op": "bare_qty", "name": name, "qty": _tidy_qty(_qty_number(m.group(2))), "price": None,
            "more": False, "code": code, "unit": unit}


# A quantity the person wrote that nothing can be done with. Said plainly,
# with what the record still holds, because the alternative — reading it as
# the nearest plausible positive number — changes a document that goes to a
# customer without anyone being told (review v3, B05).
LINE_QTY_NOT_POSITIVE = {
    "th": "จำนวนที่เพิ่มหรือลดต้องมากกว่า 0 ครับ รายการเดิมยังอยู่เท่าเดิม",
    "en": "The quantity to add or remove must be greater than 0. The items are unchanged.",
}
LINE_QTY_NOT_WHOLE = {
    "th": "จำนวนต้องเป็นจำนวนเต็มครับ เช่น \"2 ตัว\" รายการเดิมยังอยู่เท่าเดิม",
    "en": "A quantity has to be a whole number, e.g. \"2\". The items are unchanged.",
}
LINE_INCREMENTED = {
    "th": "เพิ่ม{name}อีก {delta} {unit}แล้ว ตอนนี้{name}รวมเป็น {qty} × {price} = {total} ใน{where} {code}{record_total}",
    "en": "Added {delta} more {name}. Now {qty} × {price} = {total} on {code}{record_total}",
}
LINE_DECREMENTED = {
    "th": "ลด{name}ลง {delta} {unit}แล้ว ตอนนี้{name}เหลือ {qty} × {price} = {total} ใน{where} {code}{record_total}",
    "en": "Took {delta} {name} off. Now {qty} × {price} = {total} on {code}{record_total}",
}
LINE_DECREMENTED_TO_ZERO = {
    "th": "ลด{name}ลง {delta} {unit} เหลือ 0 จึงลบ{name}ออกจาก{where} {code} แล้ว{record_total}",
    "en": "Took {delta} {name} off — none left, so the line is off {code}{record_total}",
}
LINE_NEW_ASK_PRICE = {
    "th": "ยังไม่มี {name} ใน{where} {code} ต้องการเพิ่มเป็นรายการใหม่ใช่ไหมครับ? กรุณาระบุราคาต่อชิ้น เช่น \"1500\"",
    "en": '"{name}" is not on {code} yet — add it as a new line? Send the unit price, e.g. "1500".',
}
LINE_NEW_OFFER = {
    "th": "ไม่พบสินค้า \"{name}\" ใน{where} {code} ต้องการเพิ่มเป็นรายการใหม่ ({qty} × {price}) ใช่ไหมครับ?",
    "en": 'No line called "{name}" on {code} — add it as a new line ({qty} × {price})?',
}
LINE_NEW_ADDED = {
    "th": "รับทราบครับ เพิ่มเป็นสินค้ารายการใหม่: {name} × {qty} ราคา {price} ใน{where} {code} แล้ว รวม {total}{record_total}",
    "en": "Added as a new line: {name} × {qty} at {price} on {code}. Line total {total}{record_total}",
}
LINE_NEW_STILL_NEEDS_PRICE = {
    "th": "รับทราบครับ จะเพิ่ม {name} × {qty} เป็นสินค้ารายการใหม่ใน{where} {code} กรุณาระบุราคาต่อชิ้น เช่น \"1500\"",
    "en": 'Got it — {name} × {qty} goes on {code} as a new line. What is the unit price? e.g. "1500"',
}
LINE_NEW_CANCELLED = {"th": "ยังไม่ได้เพิ่ม {name} ครับ", "en": "{name} was not added."}
LINE_NEW_YES_LABEL = {"th": "ใช่ เพิ่มเลย", "en": "Yes, add it"}
LINE_NEW_NO_LABEL = {"th": "ไม่ใช่", "en": "No"}
LINE_WHICH_LAST = {
    "th": "{where} {code} มีหลายรายการ หมายถึงรายการไหนครับ\n{options}",
    "en": "{code} has several lines — which one?\n{options}",
}
RECORD_TOTAL_SUFFIX = {"th": " · ยอดรวม{where} {total}", "en": " · {where} total {total}"}
DEAL_ZERO_TOTAL = {"th": "รวม: 0.00 บาท", "en": "Total: 0.00 THB"}

_LINE_NEW_YES_WORDS = frozenset({
    "ใช่", "ใช่เลย", "ใช่ครับ", "ใช่ค่ะ", "ใช่เพิ่มเลย", "เพิ่มเลย", "เพิ่ม", "ตกลง", "โอเค", "ได้", "ได้เลย", "เอาเลย",
    "รายการใหม่", "เป็นรายการใหม่", "เป็นสินค้ารายการใหม่", "สินค้ารายการใหม่", "เพิ่มเป็นรายการใหม่", "เพิ่มเป็นสินค้ารายการใหม่",
    "สินค้าใหม่", "เป็นสินค้าใหม่", "รายการใหม่เลย", "yes", "ok", "okay", "sure", "yes add it", "add it", "new item", "its new",
    "it's new", "new line", "as a new line", "as new",
})
_LINE_NEW_YES_NORM = frozenset(w.replace(" ", "").replace("'", "") for w in _LINE_NEW_YES_WORDS)
_LINE_NEW_NO_WORDS = frozenset({"ไม่", "ไม่ใช่", "ไม่เอา", "ไม่ต้อง", "no", "nope", "cancel", "ยกเลิก", "ไม่เพิ่ม"})
_LINE_PRICE_ANSWER_RE = re.compile(
    r"^(?:ราคา|@|฿|price)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:บาท|฿|baht|ต่อชิ้น|ต่อตัว|/ชิ้น|/ตัว|each|per unit)?\s*(?:ครับ|ค่ะ|คะ|นะ)?$",
    re.I,
)


def _where_label(kind: str, language: str) -> str:
    if language == "en":
        return "quote" if kind == "quote" else "deal"
    return "ใบเสนอราคา" if kind == "quote" else "ดีล"


async def _record_lines(client: DataClient, license_id: str, kind: str, entity_id: str) -> list[dict]:
    """The lines on the deal or quote, re-read after a change."""
    try:
        if kind == "quote":
            return list(await client.list_quote_products(license_id, entity_id) or [])
        deals = await client.list_deals(license_id)
        row = next((d for d in deals if str(d.get("id")) == str(entity_id)), None)
        return list((row or {}).get("products") or [])
    except Exception:
        log.exception("could not re-read the lines for a total")
        return []


def _lines_total(lines: list[dict]) -> Decimal:
    return sum(
        (Decimal(str(l.get("quoted_unit_price") or 0)) * int(l.get("qty") or 0) for l in lines),
        Decimal("0"),
    )


async def _record_total_suffix(client: DataClient, license_id: str, kind: str, entity_id: str, language: str) -> str:
    """" · ยอดรวมดีล 8,000.00": every line reply ends with where the record
    stands, so nobody has to open it to check (owner test, 8 Sep 2026)."""
    lines = await _record_lines(client, license_id, kind, entity_id)
    return _t(RECORD_TOTAL_SUFFIX, language).format(
        where=_where_label(kind, language), total=f"{_lines_total(lines):,.2f}",
    )


async def _remember_line(client: DataClient, ctx: ResolvedContext, *, kind: str, entity_id: str, code: str, line: dict | None) -> None:
    """The record AND the line just touched, so "เพิ่มอีก 1 ตัว" with no
    name means this one."""
    extra = None
    if line:
        extra = {"line_id": str(line.get("id") or ""), "line_name": str(line.get("product_name") or "")}
    await _remember_entity(client, ctx, entity_type=kind, entity_id=entity_id, code=code, extra=extra)


def _match_lines_loosely(lines: list[dict], name: str) -> list[dict]:
    """_match_lines, then the English plural dropped: "fans" means the "fan" line."""
    found = _match_lines(lines, name)
    if not found and name and name.isascii() and name.lower().endswith("s"):
        found = _match_lines(lines, name[:-1])
    return found


async def _line_from_context(client: DataClient, ctx: ResolvedContext, lines: list[dict], entity_id: str) -> dict | None:
    """The line "เพิ่มอีก 1 ตัว" refers to: the one last added or edited on
    this record, else the only line there is."""
    try:
        ref = await _last_entity_ref(client, ctx)
    except Exception:
        ref = None
    extra = (ref or {}).get("extra") or {}
    if ref and str(ref.get("entity_id") or "") == str(entity_id) and extra.get("line_id"):
        hit = next((l for l in lines if str(l.get("id")) == str(extra["line_id"])), None)
        if hit is not None:
            return hit
    if len(lines) == 1:
        return lines[0]
    return None


def _which_line_reply(lines: list[dict], kind: str, code: str, language: str, template: dict, trigger: str) -> ChatReply:
    return ChatReply(
        text=_t(template, language).format(
            where=_where_label(kind, language), code=code or "",
            options="\n".join(f"· {l.get('product_name')}" for l in lines[:LIST_LIMIT]),
        ),
        quick_replies=[(str(l.get("product_name"))[:20], f"{trigger}{l.get('product_name')} ") for l in lines[:4]],
    )


async def _apply_line_qty_change(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, kind: str, code: str, entity_id: str,
    line: dict, delta: int, language: str, unit: str = "ตัว",
) -> ChatReply:
    """old + delta (or old − delta), never "set to delta". A line that
    reaches zero comes off the record, and the reply says so."""
    old_qty = int(line.get("qty") or 1)
    new_qty = old_qty + delta
    name = str(line.get("product_name") or "")
    unit_price = Decimal(str(line.get("quoted_unit_price") or 0))
    where = _where_label(kind, language)
    try:
        if new_qty <= 0:
            if kind == "quote":
                await client.remove_quote_product(license_id, entity_id, str(line["id"]), actor_id=ctx.chann_uid)
            else:
                await client.remove_deal_product(license_id, entity_id, str(line["id"]), actor_id=ctx.chann_uid)
            await _remember_line(client, ctx, kind=kind, entity_id=entity_id, code=code, line=None)
            return ChatReply(
                text=_t(LINE_DECREMENTED_TO_ZERO, language).format(
                    name=name, delta=-delta, unit=unit, where=where, code=code,
                    record_total=await _record_total_suffix(client, license_id, kind, entity_id, language),
                ),
                entity_type=kind, entity_id=entity_id,
            )
        if kind == "quote":
            updated = await client.update_quote_product(license_id, entity_id, str(line["id"]), {"qty": new_qty}, actor_id=ctx.chann_uid)
        else:
            updated = await client.update_deal_product(license_id, entity_id, str(line["id"]), {"qty": new_qty}, actor_id=ctx.chann_uid)
    except DataTierError as exc:
        if kind == "quote" and "can no longer be edited" in str(exc.detail):
            return ChatReply(text=_t(LINE_QUOTE_LOCKED, language).format(code=code))
        log.exception("line quantity change failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("line quantity change failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    unit_price = Decimal(str((updated or {}).get("quoted_unit_price") or unit_price))
    qty = int((updated or {}).get("qty") or new_qty)
    await _remember_line(client, ctx, kind=kind, entity_id=entity_id, code=code, line={**line, **(updated or {})})
    template = LINE_INCREMENTED if delta > 0 else LINE_DECREMENTED
    return ChatReply(
        text=_t(template, language).format(
            name=name, delta=abs(delta), unit=unit, qty=qty, price=f"{unit_price:,.2f}",
            total=f"{unit_price * qty:,.2f}", where=where, code=code,
            record_total=await _record_total_suffix(client, license_id, kind, entity_id, language),
        ),
        entity_type=kind, entity_id=entity_id,
    )


async def _apply_new_line(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, kind: str, code: str, entity_id: str,
    name: str, qty: int, price: str, product_id: str | None, language: str, as_new: bool = False,
    quick_replies: list | None = None,
) -> ChatReply:
    """Put a line the record does not have yet on it, and say where the
    record stands afterwards."""
    payload = {"product_name": name, "quoted_unit_price": price, "qty": qty}
    try:
        if kind == "quote":
            row = await client.add_quote_product(license_id, entity_id, payload, actor_id=ctx.chann_uid)
        else:
            row = await client.add_deal_product(license_id, entity_id, {**payload, "product_id": product_id}, actor_id=ctx.chann_uid)
    except DataTierError as exc:
        if kind == "quote" and "can no longer be edited" in str(exc.detail):
            return ChatReply(text=_t(LINE_QUOTE_LOCKED, language).format(code=code))
        log.exception("adding a line failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("adding a line failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    row = row or {}
    await _remember_line(client, ctx, kind=kind, entity_id=entity_id, code=code, line={**payload, **row})
    unit = Decimal(str(row.get("quoted_unit_price") or price))
    record_total = await _record_total_suffix(client, license_id, kind, entity_id, language)
    if as_new:
        text = _t(LINE_NEW_ADDED, language).format(
            name=row.get("product_name") or name, qty=qty, price=f"{unit:,.2f}", total=f"{unit * qty:,.2f}",
            where=_where_label(kind, language), code=code, record_total=record_total,
        )
    else:
        text = _t(DEAL_PRODUCT_ADDED, language).format(
            name=row.get("product_name") or name, qty=qty, deal_id=code, price=f"{unit:,.2f}",
            total=f"{unit * qty:,.2f}",
        ) + record_total
    return ChatReply(
        text=text, entity_type=kind, entity_id=entity_id,
        quick_replies=quick_replies if quick_replies is not None else (
            [("สร้างใบเสนอราคา", f"สร้างใบเสนอราคาจากดีล {code}")] if kind == "deal" else []
        ),
    )


async def _hold_new_line(
    client: DataClient, *, ctx: ResolvedContext, kind: str, code: str, entity_id: str,
    name: str, qty: int, price: str | None, language: str,
) -> ChatReply:
    """Ask about a line the record does not have, remembering everything
    already said so "1500" / "ใช่" / "เป็นสินค้ารายการใหม่" finishes it."""
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="add", entity="line_item_add",
        fields={"kind": kind, "code": code, "entity_id": str(entity_id), "name": name, "qty": int(qty), "price": price},
        missing=["price"] if price is None else [], ttl_seconds=PENDING_INTENT_TTL_S,
    )
    where = _where_label(kind, language)
    if price is None:
        return ChatReply(
            text=_t(LINE_NEW_ASK_PRICE, language).format(name=name, where=where, code=code),
            quick_replies=[("ยกเลิก", "ยกเลิก")],
        )
    return ChatReply(
        text=_t(LINE_NEW_OFFER, language).format(
            name=name, where=where, code=code, qty=qty, price=f"{Decimal(str(price)):,.2f}",
        ),
        quick_replies=[(_t(LINE_NEW_YES_LABEL, language), "ใช่"), (_t(LINE_NEW_NO_LABEL, language), "ไม่ใช่")],
    )


async def _resolve_line_item_add(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    """The answer to "add it as a new line?" — a price, a yes, a no. Any
    other message is a new request: the question is dropped and the
    router goes on."""
    fields = pending.get("fields") or {}
    name = str(fields.get("name") or "")
    kind = str(fields.get("kind") or "deal")
    code = str(fields.get("code") or "")
    entity_id = str(fields.get("entity_id") or "")
    qty = int(fields.get("qty") or 1)
    price = fields.get("price")
    norm = _normalise(message)
    stripped = (message or "").strip().lower()
    if norm in _SLOT_FILL_ABORT_WORDS or norm in _LINE_NEW_NO_WORDS or stripped in _LINE_NEW_NO_WORDS:
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(LINE_NEW_CANCELLED, language).format(name=name))
    answered_price = None
    pm = _LINE_PRICE_ANSWER_RE.match((message or "").strip())
    if pm:
        answered_price = pm.group(1).replace(",", "")
    said_yes = norm in _LINE_NEW_YES_NORM or stripped in _LINE_NEW_YES_WORDS
    if answered_price is None and not said_yes:
        await _drop_pending_quietly(client, ctx)
        return None
    needed = "quote.update" if kind == "quote" else "deal.update"
    if needed not in set(permission_keys):
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    final_price = answered_price or price
    if final_price is None:
        # "ใช่" without a price: the line is agreed, the price still is not.
        return ChatReply(
            text=_t(LINE_NEW_STILL_NEEDS_PRICE, language).format(
                name=name, qty=qty, where=_where_label(kind, language), code=code,
            ),
        )
    await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    return await _apply_new_line(
        client, ctx=ctx, license_id=str(license_id), kind=kind, code=code, entity_id=entity_id,
        name=name, qty=qty, price=str(final_price), product_id=None, language=language, as_new=True,
    )


async def _handle_line_item_command(
    client: DataClient, *, ctx: ResolvedContext, license_id, cmd: dict, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    """One handler for every way of changing what is on a deal or quote —
    the typed forms and the model's reading of them both land here.

    Returns None when the sentence turns out not to be about a line at all
    (a bare "พัดลม 3 ตัว" with no such line, an "เพิ่ม …" with no deal in
    play), so the router carries on with the other readings.
    """
    license_id = str(license_id)
    op = str(cmd.get("op") or "")
    kind, code, entity_id, lines = await _resolve_line_target(client, license_id, ctx, message)
    if kind is None or entity_id is None:
        if code:
            needed = "quote.update" if code.upper().startswith("Q") else "deal.update"
            if needed not in set(permission_keys):
                return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
            return ChatReply(text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=code))
        # No deal or quote in play: "เอาสมชายออก" is about a lead, "เพิ่มพัดลม
        # 2 ตัว" about the catalogue — the other readings get the sentence.
        return None
    needed = "quote.update" if kind == "quote" else "deal.update"
    if needed not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    where = _where_label(kind, language)
    code = code or ""
    name = str(cmd.get("name") or "").strip()
    # The number the person actually wrote reaches the guard now, so the
    # guard can do its job: "-1" is refused instead of adding one, and
    # "1.5" instead of quietly becoming 5 (review v3, B05).
    raw_qty = cmd.get("qty")
    qty_value = float(raw_qty) if raw_qty is not None else 1.0
    if not qty_value.is_integer():
        return ChatReply(text=_t(LINE_QTY_NOT_WHOLE, language))
    qty = int(qty_value)
    if op in ("add", "decrement") and qty <= 0:
        return ChatReply(text=_t(LINE_QTY_NOT_POSITIVE, language))
    if op in ("set_qty", "bare_qty") and qty < 0:
        return ChatReply(text=_t(LINE_QTY_NOT_POSITIVE, language))
    unit = str(cmd.get("unit") or "ตัว")
    trigger = {"delete": "ลบสินค้า", "decrement": "ลด", "add": "เพิ่ม", "set_qty": "แก้จำนวน", "bare_qty": "แก้จำนวน"}.get(op, "แก้จำนวน")

    line = None
    if name and not _is_generic_product_word(name):
        matches = _match_lines_loosely(lines, name)
        exact = [l for l in matches if str(l.get("product_name") or "").lower() == name.lower()]
        if len(matches) == 1:
            line = matches[0]
        elif len(matches) > 1:
            if op == "add" and not cmd.get("more") and not exact:
                line = None
            else:
                return _which_line_reply(matches, kind, code, language, LINE_WHICH_ONE, trigger)
        if op == "add" and line is not None and not exact and not cmd.get("more"):
            # "เพิ่มพัดลม 2 ตัว" on a deal holding "พัดลม 16 นิ้ว" means a
            # new line; only "อีก" (or the exact name) means more of that one.
            line = None
    elif lines:
        line = await _line_from_context(client, ctx, lines, entity_id)
        if line is None and op != "add":
            return _which_line_reply(lines, kind, code, language, LINE_WHICH_LAST, trigger)

    if op == "add":
        if line is not None:
            return await _apply_line_qty_change(
                client, ctx=ctx, license_id=license_id, kind=kind, code=code, entity_id=entity_id,
                line=line, delta=qty, language=language, unit=unit,
            )
        if not name:
            if lines:
                return _which_line_reply(lines, kind, code, language, LINE_WHICH_LAST, trigger)
            return ChatReply(text=_t(DEAL_PRODUCT_NEEDS_NAME, language))
        if kind == "deal":
            return await _handle_deal_product_add(
                client, ctx=ctx, license_id=license_id,
                message=_joined_words("เพิ่มสินค้า", name, f"{qty} ตัว", f"ราคา {cmd['price']}" if cmd.get("price") else "", f"เข้าดีล {code}"),
                trigger="เพิ่มสินค้า", permission_keys=permission_keys, language=language,
            )
        price = cmd.get("price")
        product = None
        if price is None:
            product, problem = await _catalogue_product(client, license_id, name, qty=qty, code=code, language=language, kind=kind)
            if problem is not None:
                return problem
            if product is not None and product.get("unit_price") is not None:
                price = str(product["unit_price"])
                name = str(product.get("product_name") or name)
        if price is None:
            return await _hold_new_line(client, ctx=ctx, kind=kind, code=code, entity_id=entity_id, name=name, qty=qty, price=None, language=language)
        return await _apply_new_line(
            client, ctx=ctx, license_id=license_id, kind=kind, code=code, entity_id=entity_id,
            name=name, qty=qty, price=str(price), product_id=str(product["id"]) if product else None, language=language,
            quick_replies=[],
        )

    if op == "bare_qty" and line is None:
        return None
    if line is None and op == "delete" and not cmd.get("explicit"):
        # "เอาสมชายออกไปเลย" with a deal in play: not a line here, and the
        # sentence may be about something else entirely.
        return None
    if line is None:
        if not lines:
            return ChatReply(text=_t(LINE_NONE_YET, language).format(where=where, code=code))
        return ChatReply(text=_t(LINE_NOT_FOUND, language).format(name=name or "—", where=where, code=code))

    if op == "delete":
        return await _handle_line_edit(
            client, ctx=ctx, license_id=license_id,
            message=_joined_words("ลบสินค้า", str(line.get("product_name") or ""), code),
            trigger="ลบสินค้า", permission_keys=permission_keys, language=language, remove=True,
        )
    if op == "decrement":
        return await _apply_line_qty_change(
            client, ctx=ctx, license_id=license_id, kind=kind, code=code, entity_id=entity_id,
            line=line, delta=-qty, language=language, unit=unit,
        )
    # set_qty / bare_qty: the number IS the quantity.
    return await _handle_line_edit(
        client, ctx=ctx, license_id=license_id,
        message=_joined_words("แก้จำนวน", str(line.get("product_name") or ""), code, f"เป็น {qty}"),
        trigger="แก้จำนวน", permission_keys=permission_keys, language=language,
    )


def _joined_words(*parts) -> str:
    return " ".join(str(p) for p in parts if str(p or "").strip())


async def _catalogue_product(
    client: DataClient, license_id: str, name: str, *, qty: int, code: str, language: str, kind: str = "deal",
) -> tuple[dict | None, ChatReply | None]:
    """The catalogue product a name means: (product, None), (None, None)
    when there is none, or (None, reply) when several could be meant.

    Exact first — a product code or a full name is an unambiguous answer
    and must not be beaten by a partial match on something else. Then
    partial, because "พัดลม" is what someone types and "พัดลมตั้งพื้น 16
    นิ้ว" is what the catalogue calls it.
    """
    try:
        products = await client.list_products(license_id)
    except Exception:
        products = []
    needle = name.lower()
    exact = [
        p for p in products
        if str(p.get("product_id") or "").lower() == needle or str(p.get("product_name") or "").lower() == needle
    ]
    candidates = exact or [
        p for p in products
        if needle in str(p.get("product_name") or "").lower() or needle in str(p.get("product_id") or "").lower()
    ]
    if len(candidates) > 1:
        # Several models of the same thing at different prices. Picking one
        # silently puts the wrong price on a document that goes to a
        # customer — the one place a quiet guess is least acceptable.
        shown = candidates[:LIST_LIMIT]
        options = "\n".join(
            f"· {c.get('product_name')}"
            + (f" — {Decimal(str(c['unit_price'])):,.2f}" if c.get("unit_price") is not None else "")
            for c in shown
        )
        # Labels drop the part every candidate shares: LINE's 20-character
        # limit would otherwise cut off the only bit that differs.
        send = (
            (lambda c: f"เพิ่มสินค้าในใบเสนอราคา {c.get('product_name')} {qty} ตัว") if kind == "quote"
            else (lambda c: f"เพิ่มสินค้า {c.get('product_name')} {qty} ตัว เข้าดีล {code}")
        )
        return None, ChatReply(
            text=_t(DEAL_PRODUCT_AMBIGUOUS, language).format(name=name, options=options),
            quick_replies=[
                (_distinguishing_part(str(c.get("product_name") or ""), [str(o.get("product_name") or "") for o in shown]), send(c))
                for c in shown[:4]
            ],
        )
    return (candidates[0] if candidates else None), None


async def _resolve_line_target(
    client: DataClient, license_id: str, ctx: ResolvedContext, message: str,
):
    """(kind, code, id, lines) for the quote or deal a line edit targets.

    An explicit code wins; otherwise the record just discussed. Quotes are
    checked before deals because someone editing prices is usually working
    on the offer, not the pipeline entry behind it.
    """
    quote_match = re.search(r"(?<![A-Za-z0-9])(Q-\d{4}-\d{4})(?![0-9])", message or "", re.I)
    deal_match = re.search(r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", message or "", re.I)

    if quote_match:
        code = quote_match.group(1).upper()
        quotes = await client.list_quotes(license_id)
        row = next((q for q in quotes if str(q.get("quote_id", "")).upper() == code), None)
        if row is None:
            return None, code, None, []
        lines = await client.list_quote_products(license_id, str(row["id"]))
        return "quote", code, str(row["id"]), lines

    if deal_match:
        code = deal_match.group(1).upper()
        deals = await client.list_deals(license_id)
        row = next((d for d in deals if str(d.get("deal_id", "")).upper() == code), None)
        if row is None:
            return None, code, None, []
        return "deal", code, str(row["id"]), list(row.get("products") or [])

    last_ref = await _last_entity_ref(client, ctx)
    if not last_ref:
        return None, None, None, []
    kind = str(last_ref.get("entity_type") or "")
    code = str(last_ref.get("code") or "")
    entity_id = str(last_ref.get("entity_id") or "")
    if kind == "quote":
        return "quote", code, entity_id, await client.list_quote_products(
            license_id, entity_id,
        )
    if kind == "deal":
        deals = await client.list_deals(license_id)
        row = next((d for d in deals if str(d.get("id")) == entity_id), None)
        return "deal", code, entity_id, list((row or {}).get("products") or [])
    return None, None, None, []


def _is_generic_product_word(name: str) -> bool:
    """Is this the WORD for a product rather than the name of one?

    Someone editing a deal with one line says "แก้ราคาสินค้าเป็น 1800",
    meaning "change the product's price" — and the parser reads "สินค้า"
    as a name and finds nothing.
    """
    return (name or "").strip().lower() in {
        "สินค้า", "ของ", "รายการ", "อัน", "ตัว", "product", "item", "it",
    }


def _match_lines(lines: list[dict], name: str) -> list[dict]:
    """Every line the name could mean — exact first, then partial.

    Returned as a list so the caller can tell "nothing" from "several":
    the old version collapsed both to None and told someone editing
    "พัดลม" on a quote holding two fans that no such product existed.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return []
    exact = [l for l in lines if str(l.get("product_name") or "").lower() == needle]
    if exact:
        return exact[:1]
    return [l for l in lines if needle in str(l.get("product_name") or "").lower()]


def _match_line(lines: list[dict], name: str) -> dict | None:
    matches = _match_lines(lines, name)
    # One match or nothing: two lines matching the same words means the
    # edit could land on either, and changing the wrong price on a quote
    # is worse than being asked again.
    return matches[0] if len(matches) == 1 else None


STAFF_TICKET_CREATED = {
    # {machine} is the registered unit the job is about, or empty when the
    # customer has none on file (owner, 10 ก.ย. 2569).
    "th": "เปิดงานซ่อม {code} ให้ {name} แล้ว\nอาการ: {issue}{machine}",
    "en": "Opened {code} for {name}: {issue}{machine}",
}
STAFF_TICKET_NEEDS_ISSUE = {
    "th": "แจ้งซ่อมเรื่องอะไรครับ พิมพ์อาการมาได้เลย",
    "en": "What is the problem?",
}
STAFF_TICKET_WHICH_PRODUCT = {
    # The customer flow's own question, asked of the staff member instead:
    # one device-choice pattern, not two (REPORT_WHICH_PRODUCT above).
    "th": "{name} ลงทะเบียนไว้หลายเครื่อง งานนี้เครื่องไหนครับ",
    "en": "{name} has several registered units — which one is this job about?",
}
STAFF_TICKET_NO_MACHINE_CHOICE = {
    "th": "ไม่ระบุเครื่อง",
    "en": "No machine",
}
STAFF_TICKET_DEVICE_TTL_S = 900


async def _registered_units_of(
    client: DataClient, license_id: str, customer: dict,
) -> list[dict]:
    """The machines this contact has registered with the shop.

    Matched through the LINE identity on the contact row, because that is
    what a warranty records when a customer claims a unit. A contact who
    has never linked LINE has no claimed units, which is a real answer,
    not an error — the job is opened without a machine.
    """
    uid = str((customer or {}).get("customer_chann_uid") or "").strip()
    if not uid:
        return []
    try:
        rows = await client.list_warranties(str(license_id), customer_chann_uid=uid)
    except Exception:
        log.exception("could not read the registered units of %s", uid)
        return []
    return [
        w for w in (rows or [])
        if str(w.get("status") or "") != "void" and w.get("serial_number")
    ]


async def _ask_staff_which_device(
    client: DataClient, *, ctx: ResolvedContext, customer: dict, fields: dict,
    registered: list[dict], language: str,
) -> ChatReply:
    """Which of the customer's machines, as buttons.

    The customer flow's device choice (REPORT_WHICH_PRODUCT), asked of the
    staff member: same shape, same escape hatch, same "one tap sends the
    serial back". The half-finished request is held so the tap finishes
    the ORIGINAL sentence rather than being re-parsed as a bare serial.
    """
    candidates = registered[:4]
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa,
            action="create", entity="staff_ticket_device",
            fields={
                "resume_fields": dict(fields),
                "serials": [str(w.get("serial_number") or "") for w in candidates],
            },
            missing=["serial"], ttl_seconds=STAFF_TICKET_DEVICE_TTL_S,
        )
    except Exception:
        log.exception("could not hold a staff ticket while asking which machine")
    no_machine = _t(STAFF_TICKET_NO_MACHINE_CHOICE, language)
    return ChatReply(
        text=_t(STAFF_TICKET_WHICH_PRODUCT, language).format(name=_display_name(customer)),
        quick_replies=[
            (
                f"{w.get('product_name') or ''} {w.get('serial_number')}".strip()[:20],
                str(w.get("serial_number") or ""),
            )
            for w in candidates
        ] + [(no_machine, no_machine)],
    )


async def _resolve_staff_ticket_device(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    pending: dict, permission_keys: list[str], language: str,
) -> ChatReply | None:
    """Consumes the answer to "which machine?" on the staff path.

    Returns None when the reply is neither one of the offered serials nor
    the way out, so an unrelated command is still an unrelated command —
    the same rule the line-item and template prompts follow.
    """
    held = pending.get("fields") or {}
    resume = dict(held.get("resume_fields") or {})
    serials = [str(x).upper() for x in (held.get("serials") or [])]
    text = (message or "").strip()
    upper = text.upper()

    if upper in serials:
        resume["serial_number"] = text
    elif _matches_phrase(text, NO_SERIAL_PHRASES) or _normalise(text) == _normalise(
        _t(STAFF_TICKET_NO_MACHINE_CHOICE, language)
    ):
        resume["no_machine"] = True
    else:
        return None

    await _drop_pending_quietly(client, ctx)
    return await _handle_staff_ticket_create(
        client, ctx=ctx, license_id=license_id, fields=resume,
        permission_keys=permission_keys, language=language,
    )


async def _handle_staff_ticket_create(
    client: DataClient, *, ctx: ResolvedContext, license_id, fields: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """A salesperson logging a fault on a customer's behalf.

    "ลูกค้าสมชายแจ้งแอร์ไม่เย็น" — the customer phoned the shop rather
    than the bot. The AI was taught to emit this and nothing received
    it, so it fell to the suggestion path and was reported as a
    permission problem it was not.

    The customer is resolved by name the same way a deal's is, and the
    ticket carries their details so the dispatch gate has something to
    check. Address and time are asked for afterwards, as they are for a
    customer's own report.
    """
    if "ticket.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    issue = str(fields.get("issue_description") or "").strip()
    if not issue:
        return ChatReply(text=_t(STAFF_TICKET_NEEDS_ISSUE, language))

    license_id = str(license_id)
    target = str(fields.get("target_name") or "").strip()
    customer = None
    if target:
        customer, problem = await _find_one_customer_by_name(
            client, license_id, target, language,
            ctx=ctx, resume_entity="ticket", resume_action="create",
            resume_fields=dict(fields),
        )
        if problem is not None:
            return problem
    if customer is None:
        last_ref = await _last_customer_ref(client, ctx)
        if last_ref:
            customer = {"id": last_ref["customer_id"], "first_name": last_ref["name"]}
    if customer is None:
        return ChatReply(text=_t(TICKET_NEEDS_TARGET_NAME, language))

    # Owner, 10 ก.ย. 2569: a job opened by hand is about a registered unit
    # just as much as one the customer reported themselves. One unit is
    # linked and named; several are asked about rather than guessed at
    # (rule 3 — never guess between candidates, never go silent); none
    # leaves the flow exactly as it was.
    serial = str(fields.get("serial_number") or "").strip()
    if not serial and not fields.get("no_machine"):
        registered = await _registered_units_of(client, license_id, customer)
        if len(registered) == 1:
            serial = str(registered[0].get("serial_number") or "")
        elif len(registered) > 1:
            return await _ask_staff_which_device(
                client, ctx=ctx, customer=customer, fields=fields,
                registered=registered, language=language,
            )

    warranty = await ticket_machine.warranty_for_serial(client, license_id, serial) if serial else None

    try:
        ticket = await client.create_ticket(
            license_id,
            {
                "issue_description": issue,
                "owner_member_id": await _member_id_of(client, license_id, ctx),
                # contact_id, not customer_id: TicketIn has no customer_id
                # field, so the link to the customer this job is for was
                # being dropped by pydantic without a word.
                "contact_id": str(customer.get("id") or ""),
                "customer_name": _display_name(customer),
                "customer_phone": customer.get("phone"),
                "service_address": fields.get("service_address") or customer.get("address"),
                "scheduled_date": fields.get("scheduled_date"),
                "scheduled_time": fields.get("scheduled_time"),
                **ticket_machine.link_fields(warranty, serial),
            },
            actor_id=ctx.chann_uid,
        )
    except Exception:
        log.exception("staff ticket creation failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    code = str(ticket.get("ticket_number") or "")
    await _remember_entity(
        client, ctx, entity_type="ticket", entity_id=str(ticket["id"]), code=code,
    )
    machine_line = ticket_machine.machine_line(
        ticket_machine.attach([ticket], _by_serial(warranty))[0], language,
    )
    return ChatReply(
        text=_t(STAFF_TICKET_CREATED, language).format(
            code=code, name=_display_name(customer), issue=issue,
            machine=f"\n{machine_line}" if machine_line else "",
        ),
        entity_type="ticket", entity_id=str(ticket["id"]),
        quick_replies=[
            ("มอบหมายอัตโนมัติ", f"มอบหมาย {code} ให้อัตโนมัติ"),
            ("ดูงาน", f"ข้อมูลงาน {code}"),
        ],
    )


SALES_SUMMARY_PHRASES = (
    "ยอดขาย", "สรุปยอด", "ยอดเดือนนี้", "สรุปการขาย", "ภาพรวมการขาย", "sales summary",
    "ขายได้เท่าไหร่", "ขายได้เท่าไร", "ขายได้กี่บาท", "ขายไปเท่าไหร่", "ขายไปกี่บาท", "ยอดรวม", "ปิดได้เท่าไหร่",
    "ปิดได้กี่ดีล", "sales this month", "how much did we sell", "revenue", "sales total",
)

SALES_SUMMARY = {
    "th": (
        "สรุปการขาย\n"
        "· ดีลเปิดอยู่ {open_count} ดีล มูลค่า {open_value}\n"
        "· คาดว่าจะปิดเดือนนี้ {closing}\n"
        "· ปิดสำเร็จแล้ว {won_count} ดีล มูลค่า {won_value}"
    ),
    "en": (
        "Sales summary\n"
        "· {open_count} open, worth {open_value}\n"
        "· {closing} forecast to close this month\n"
        "· {won_count} won, worth {won_value}"
    ),
}
SALES_SUMMARY_CAVEAT = {
    "th": "\n({overdue} ดีลเลยกำหนด · {undated} ดีลยังไม่ระบุวันปิด)",
    "en": "\n({overdue} overdue · {undated} with no close date)",
}


async def _handle_sales_summary(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    """The numbers, asked for in words.

    The AI was taught to emit entity="report" and nothing received it, so
    "ยอดขายเดือนนี้เท่าไหร่" was answered as a permission problem it was
    not. Uses the same pipeline summary the dashboard shows, so the two
    cannot disagree about the shop's own numbers.
    """
    if "deal.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        summary = await client.pipeline_summary(str(license_id))
    except Exception:
        log.exception("sales summary failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    by_stage = summary.get("by_stage") or {}
    open_count = sum(
        int((by_stage.get(s) or {}).get("count") or 0) for s in ("new", "proposed")
    )
    won = by_stage.get("won") or {}

    def money(value) -> str:
        return f"{Decimal(str(value or 0)):,.0f}"

    text = _t(SALES_SUMMARY, language).format(
        open_count=open_count,
        open_value=money(summary.get("open_value")),
        closing=money(summary.get("closing_this_month")),
        won_count=int(won.get("count") or 0),
        won_value=money(won.get("value")),
    )
    # The two things that make the forecast mean less, said rather than
    # folded in — a number built on half-dated deals should say so.
    overdue = int(summary.get("overdue_count") or 0)
    undated = int(summary.get("undated_open_count") or 0)
    if overdue or undated:
        text += _t(SALES_SUMMARY_CAVEAT, language).format(overdue=overdue, undated=undated)

    return ChatReply(
        text=text,
        quick_replies=[
            # The same numbers as a picture, one tap away (owner, 8 Sep 2026).
            (_t(CHART_AS_CHART_BUTTON, language), _t(CHART_AS_CHART_SAYS, language)),
            ("ดีลเดือนนี้", "ดีลเดือนนี้"),
            ("ดีลเลยกำหนด", "ดีลเลยกำหนด"),
        ],
    )


async def _handle_ai_understood_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str, message: str = "",
) -> ChatReply:
    """Route a request the AI recognised to the handler that already does it.

    The triggers were never meant to be the vocabulary. They are the fast
    path for the phrasings people use most and for the text the system
    writes on its own buttons — but a shop that says "เดี๋ยวผมไปเอง"
    instead of "รับงาน" is asking for the same thing, and until now got
    told the feature did not exist.

    Each branch rebuilds a sentence the existing handler parses, rather
    than reimplementing it. One set of rules about finding the ticket, one
    about the dispatch gate, one about permissions — and no way for the
    typed path and the spoken one to disagree about any of them.
    """
    entity = str(intent.get("entity") or "")
    action = str(intent.get("action") or "")
    fields = intent.get("fields") or {}

    # The model's reading does not outrank what the sentence says. It
    # answers check_in to "ยังไม่ถึงหน้างาน" about twice in three tries,
    # and acting on that is the wrong check-in the owner reported — the
    # trigger table has refused it since 8 Sep, so this road must too.
    if action in _JOB_STEP_ACTIONS and _disclaims_a_job_action(message):
        return ChatReply(text=_t(JOB_ACTION_NOT_REQUESTED, language))

    def _joined(*parts) -> str:
        return " ".join(str(p) for p in parts if str(p or "").strip())

    code = str(fields.get("code") or "").strip()
    target = str(fields.get("target_name") or "").strip()

    if entity == "ticket":
        if action == "create":
            return await _handle_staff_ticket_create(
                client, ctx=ctx, license_id=license_id, fields=fields,
                permission_keys=permission_keys, language=language,
            )
        if action == "claim":
            return await _handle_ticket_claim(
                client, ctx=ctx, license_id=license_id,
                message=_joined("รับงาน", code),
                permission_keys=permission_keys, language=language,
            )
        if action == "assign":
            return await _handle_ticket_assign(
                client, ctx=ctx, license_id=license_id,
                message=_joined("มอบหมาย", code, "ให้", target),
                trigger="มอบหมาย", permission_keys=permission_keys,
                language=language,
            )
        if action in READ_ACTIONS:
            # "งานนี้เป็นยังไงบ้าง" / "ขอดูงาน T-2026-0001": ticket.read has
            # been registered since Phase 6 and reached nothing — the model
            # understood, the gate passed, and the router answered with a
            # list of permissions instead of the job.
            if ctx.oa == "customer":
                # ticket.read is in scope for the Customer OA, but it means
                # "my own repair", never the shop's job list.
                # _handle_customer_report answers that long before anything
                # reaches the model; this is the belt to that brace.
                return _customer_fallback(message, language)
            if code or TICKET_CODE_RE.search(message or ""):
                return await _handle_ticket_detail(
                    client, ctx=ctx, license_id=license_id,
                    message=_joined(code, message),
                    permission_keys=permission_keys, language=language,
                )
            return await _handle_ticket_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
                mine=(ctx.oa == "technician"),
            )
        if action == "reject":
            return await _handle_ticket_reject(
                client, ctx=ctx, license_id=license_id,
                message=_joined("ไม่รับงาน", code, fields.get("reason")),
                permission_keys=permission_keys, language=language,
            )
        if action == "close":
            # Closing a job in this product IS the technician's check-out:
            # the visit ends with a report, and there is no other way to
            # finish one. Same handler as the typed "ปิดงาน".
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id,
                message=_joined("ปิดงาน", code),
                permission_keys=permission_keys, language=language,
            )
        if action == "update" and ctx.oa == "technician":
            # "เลื่อนไปพรุ่งนี้บ่าย", "ลูกค้าขอเปลี่ยนที่อยู่" — the same
            # situation handler the technician's own words already reach.
            # Sales has no equivalent handler, so a sales OA update falls
            # through to the honest reply with the Jobs page on it.
            return await _handle_technician_situation(
                client, ctx=ctx, license_id=license_id, kind="reschedule",
                message=_joined(code, fields.get("scheduled_date"), fields.get("scheduled_time"),
                                fields.get("service_address"), message),
                permission_keys=permission_keys, language=language,
            )

    if entity == "service_report":
        if action == "check_in":
            return await _handle_check_in(
                client, ctx=ctx, license_id=license_id,
                message=_joined("เช็คอิน", code),
                permission_keys=permission_keys, language=language,
            )
        if action == "check_out":
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id,
                message=_joined("ปิดงาน", code),
                permission_keys=permission_keys, language=language,
            )
        if action in READ_ACTIONS:
            found = SERVICE_REPORT_CODE_RE.search(_joined(code, message))
            if found:
                return await _handle_report_detail(
                    client, ctx=ctx, license_id=license_id, code=found.group(1).upper(),
                    permission_keys=permission_keys, language=language,
                )
            return await _handle_report_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        if action in ("create", "update"):
            # "คอมเพรสเซอร์เสีย เปลี่ยนให้แล้ว": a report is filed by
            # closing the job, so the model's fields are rewritten into the
            # marker form _handle_check_out already parses — one parser,
            # one set of rules about which ticket it belongs to.
            written = _joined(
                "ปิดงาน", code,
                f"พบ: {fields.get('found_issue')}" if fields.get("found_issue") else "",
                f"แก้: {fields.get('work_done')}" if fields.get("work_done") else "",
                fields.get("parts_changed"), fields.get("notes"),
            )
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id,
                message=written if fields.get("found_issue") or fields.get("work_done") else _joined("ปิดงาน", code, message),
                permission_keys=permission_keys, language=language,
            )
        if action == "issue":
            return await _handle_report_pdf(
                client, ctx=ctx, license_id=license_id,
                message=_joined("ออกรายงาน", code),
                permission_keys=permission_keys, language=language,
            )

    if entity == "approval":
        # "ผ่านได้เลย SR-2026-0001" / "รายงานนี้ไม่ผ่าน ภาพไม่ครบ": the
        # model's reading, re-synthesised into the sentence the typed
        # handler already parses, so both paths share one set of rules.
        if action in ("approve", "reject"):
            approve = action == "approve"
            return await _handle_approval_act(
                client, ctx=ctx, license_id=license_id,
                message=_joined(
                    "อนุมัติ" if approve else "ไม่อนุมัติ", code,
                    None if approve else fields.get("reason"),
                ),
                permission_keys=permission_keys, language=language,
                approve=approve, trigger="อนุมัติ" if approve else "ไม่อนุมัติ",
            )
        if action == "read":
            return await _handle_approval_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )

    if entity == "followup" and action == "create":
        due = _joined(fields.get("due_date"), fields.get("due_time"))
        # The person's own sentence goes in too, not just the model's
        # extracted fields: "วันที่ 7" lives in the message even when the
        # model forgets to put it in due_date, and the parser reads it
        # perfectly well (12:03, 2 Sep — the model returned no date and the
        # assistant answered "ไม่เข้าใจวันที่" to a message containing one).
        text = _joined("เตือน", due, target, fields.get("notes"), message)
        resolved = None
        if target and _find_entity_code(message) is None:
            # A named customer is a target in its own right — waiting for a
            # code the person has never seen is not how an assistant works.
            row, err = await _find_one_customer_by_name(
                client, str(license_id), target, language,
                ctx=ctx, resume_entity="followup", resume_action="create",
                resume_fields=fields,
            )
            if row is not None:
                resolved = ("customer", str(row["id"]), str(row.get("customer_id") or ""))
            elif not await _last_entity_ref(client, ctx):
                # Unknown name and nothing in context: the not-found reply
                # (which offers the candidates or a way to add them) is more
                # use than booking against the wrong record.
                return err
        return await _handle_reminder_create(
            client, ctx=ctx, license_id=license_id, message=text,
            permission_keys=permission_keys, language=language,
            actor_id=ctx.chann_uid, target=resolved,
        )

    if entity == "followup" and action == "read":
        # "ดูนักหมายของสมบัติ" — a typo the triggers cannot catch but the
        # model reads fine. Routes to the same scoped list, target and all.
        return await _handle_reminder_list(
            client, ctx=ctx, license_id=license_id,
            message=_joined(code, target, message),
            permission_keys=permission_keys, language=language,
        )

    if entity == "followup" and action == "update":
        # Registered in ACTION_PERMISSIONS since Phase 6 and unhandled ever
        # since: the intent resolved, the permission passed, and the router
        # fell through to the capability list.
        return await _handle_reminder_move(
            client, ctx=ctx, license_id=license_id,
            message=_joined("เลื่อนนัด", code, target,
                            fields.get("due_date"), fields.get("due_time"), message),
            permission_keys=permission_keys, language=language,
            actor_id=ctx.chann_uid,
        )

    if entity == "followup" and action == "delete":
        # Only the model's explicit "remove it" gets here. Everything the
        # deterministic triggers catch (ยกเลิกนัด, ลบนัด, cancel reminder)
        # still cancels, because a cancelled appointment is still a record
        # that something was planned and dropped.
        return await _handle_reminder_delete(
            client, ctx=ctx, license_id=license_id,
            message=_joined("ลบนัด", code, target),
            permission_keys=permission_keys, language=language,
            actor_id=ctx.chann_uid,
        )

    if entity == "followup" and action == "cancel":
        # The handler falls back to "the record we were just looking at"
        # when neither a code nor a name was given, same as creating one.
        return await _handle_reminder_cancel(
            client, ctx=ctx, license_id=license_id,
            message=_joined("ยกเลิกเตือน", code, target),
            permission_keys=permission_keys, language=language,
            actor_id=ctx.chann_uid,
        )

    if entity == "warranty" and action in READ_ACTIONS:
        serial = str(fields.get("serial_number") or "").strip()
        if serial:
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id,
                message=_joined("เช็คประกัน", serial), language=language,
            )
        if ctx.oa != "customer":
            # "ดูรายการรับประกัน" with no serial: the shop's own book, the
            # same list "สมุดรับประกัน" shows. A customer asking this means
            # their own units, which _handle_warranty_mine answers on the
            # customer OA long before anything reaches the model.
            return await _handle_warranty_book(
                client, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )

    if entity == "warranty" and action == "create":
        return await _handle_warranty_register(
            client, ctx=ctx, license_id=license_id,
            message=_joined(fields.get("serial_number"), fields.get("product_name"), target, message),
            language=language, permission_keys=permission_keys,
        )

    # Understood as a category but not as something with a handler behind
    # it. Saying what IS possible beats "not a feature", which is wrong —
    # the feature exists, this particular shape of it does not.
    try:
        catalog = await client.permission_catalog()
    except Exception:
        log.exception("could not read the permission catalogue")
        catalog = []
    return ChatReply(
        text=suggest_what_you_can_do(
            _filter_by_oa(permission_keys, ctx.oa), catalog, language, oa=ctx.oa,
            requested_action=action, requested_entity=entity,
        ),
        quick_reply_url=_entity_page_button(entity, language, ctx.oa) or _guide_button(ctx.oa, language),
    )


async def _handle_line_item_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str, message: str = "",
) -> ChatReply:
    """A line edit the AI understood, rather than one a trigger matched.

    The triggers are a shortcut for the phrasings people use most and for
    the text the system writes on its own buttons. They are not the
    vocabulary — "ทำให้ถูกลงหน่อยเป็น 1200" means the same as "ลดราคา
    เหลือ 1200" and a shop should not have to learn which words we
    happened to list.

    Both paths converge on the same handler: the AI's job here is to
    recognise the request and pull out the numbers, not to reimplement
    what happens next.
    """
    fields = intent.get("fields") or {}
    action = str(intent.get("action") or "update")

    # The person's own sentence first: the shared parser reads "อีก" as an
    # increment and "ออก" as a delete where the model's fields cannot say
    # which (owner test, 8 Sep 2026). Then the model's reading, rebuilt as
    # a sentence the same handler parses.
    parsed = _parse_line_item_command(message) if message else None
    if parsed is not None:
        handled = await _handle_line_item_command(
            client, ctx=ctx, license_id=license_id, cmd=parsed, message=message,
            permission_keys=permission_keys, language=language,
        )
        if handled is not None:
            return handled

    parts: list[str] = []
    name = _strip_item_particles(str(fields.get("target_name") or "").strip())
    if name:
        parts.append(name)
    code = str(fields.get("code") or "").strip()
    if code:
        parts.append(code)
    qty = fields.get("qty")
    price = fields.get("quoted_unit_price")

    if action == "delete":
        return await _handle_line_edit(
            client, ctx=ctx, license_id=license_id,
            message="ลบสินค้า " + " ".join(parts),
            trigger="ลบสินค้า", permission_keys=permission_keys,
            language=language, remove=True,
        )

    if price is None and qty is None:
        # Recognised as a line edit but with nothing to change. Asking
        # beats guessing which of price or quantity was meant.
        return ChatReply(text=_t(LINE_NEEDS_TARGET, language))

    if qty is not None and message and _ITEM_MORE_WORDS_RE.search(message):
        # "เพิ่มพัดลมอีก 3 ตัว" read by the model as qty=3: more, not set.
        handled = await _handle_line_item_command(
            client, ctx=ctx, license_id=license_id,
            cmd={"op": "add", "name": name or None, "qty": int(qty), "price": price, "more": True,
                 "code": code or None, "unit": _item_unit_word(message)},
            message=message, permission_keys=permission_keys, language=language,
        )
        if handled is not None:
            return handled

    if price is not None:
        trigger = "แก้ราคา"
        parts.append(f"เป็น {price}")
    else:
        trigger = "แก้จำนวน"
        parts.append(f"เป็น {qty}")

    return await _handle_line_edit(
        client, ctx=ctx, license_id=license_id,
        message=trigger + " " + " ".join(parts),
        trigger=trigger, permission_keys=permission_keys, language=language,
    )


QUOTE_DISCOUNT_TRIGGERS = ("ลดราคาทั้งใบ", "ส่วนลด", "ให้ส่วนลด", "discount")


def _is_whole_quote_discount(message: str) -> bool:
    """"ลดราคา Q-2026-0001 500 บาท": a quote code, a discount verb and an
    amount, and nothing else — the whole quote, not a line called "500
    บาท" (review, 6 Sep 2026)."""
    text = message or ""
    if not re.search(r"(?<![A-Za-z0-9])Q-\d{4}-\d{4}(?![0-9])", text, re.I):
        return False
    if not re.search(r"ลดราคา|ลด\s|^ลด|discount|ส่วนลด", text.lower()):
        return False
    rest = re.sub(r"(?<![A-Za-z0-9])Q-\d{4}-\d{4}(?![0-9])", " ", text, flags=re.I)
    rest = re.sub(r"ลดราคาทั้งใบ|ลดราคา|ส่วนลด|ให้ส่วนลด|discount|\bลด\b|ลด", " ", rest.lower())
    rest = re.sub(r"[\d,]+(?:\.\d+)?\s*(?:%|เปอร์เซ็นต์|บาท|baht)?", " ", rest)
    rest = re.sub(r"ให้|ทั้งใบ|เหลือ|เป็น|ครับ|ค่ะ|คะ|หน่อย|นะ|ด้วย|ใบ|เสนอราคา|quote|by|to|for", " ", rest)
    return not rest.strip()

QUOTE_DISCOUNT_SET = {
    "th": "ตั้งส่วนลด {discount} ให้ {code} แล้ว\nยอดสุทธิ {total}",
    "en": "{code} discounted by {discount}. Net {total}.",
}
QUOTE_DISCOUNT_NEEDS = {
    "th": "ลดเท่าไหร่ครับ เช่น \"ส่วนลด 10%\" หรือ \"ส่วนลด 500\"",
    "en": 'How much? e.g. "discount 10%" or "discount 500".',
}


QUOTE_VOID_TRIGGERS = ("ยกเลิกใบเสนอราคา", "ยกเลิก quote", "void quote", "ใบเสนอราคาไม่ใช้")
QUOTE_ACCEPT_TRIGGERS = (
    "ลูกค้าตกลง", "ลูกค้ารับใบเสนอราคา", "ตอบรับใบเสนอราคา", "quote accepted", "ลูกค้าโอเคใบเสนอราคา", "ลูกค้ายอมรับใบเสนอราคา",
    "ลูกค้ารับราคา", "ลูกค้าโอเคราคา", "ลูกค้าตกลงราคา", "accepted the quote", "customer accepted",
    "ลูกค้าโอเค q-", "ลูกค้าตกลง q-",
)

QUOTE_NONE_ON_DEAL = {
    "th": "ดีล {code} ยังไม่มีใบเสนอราคาครับ ถ้าลูกค้าตกลงแล้ว ปิดดีลได้เลย หรือสร้างใบเสนอราคาก่อน",
    "en": "Deal {code} has no quote yet — close the deal, or create a quote first.",
}
QUOTE_STATUS_SET = {
    "th": "เปลี่ยนสถานะ {code} เป็น {status} แล้ว",
    "en": "{code} is now {status}.",
}


async def _handle_quote_status(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    target: str, permission_keys: list[str], language: str,
) -> ChatReply:
    """Void or accept a quote in chat — the same buttons the dashboard has.

    Voiding is how a quote issued with the wrong contents is retired: it
    cannot be edited once issued, so the honest path is to close this one
    and issue another. Neither was possible from chat.
    """
    if "quote.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    match = re.search(r"(?<![A-Za-z0-9])(Q-\d{4}-\d{4})(?![0-9])", message or "", re.I)
    code = match.group(1).upper() if match else None
    deal_match = re.search(r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", message or "", re.I)
    if not code and deal_match:
        # "ดีล D-2026-0001 ลูกค้าตกลงแล้ว": the deal's latest quote is the
        # one accepted (review, 6 Sep 2026 — it asked for a code that
        # was in the message).
        deal_code = deal_match.group(1).upper()
        try:
            deals = await client.list_deals(license_id)
            deal = next((d for d in deals if str(d.get("deal_id", "")).upper() == deal_code), None)
            if deal is None:
                return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what="ดีล", code=deal_code))
            quotes_of_deal = [q for q in await client.list_quotes(license_id) if str(q.get("deal_id")) == str(deal["id"])]
        except Exception:
            log.exception("could not resolve the deal's quote")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        if not quotes_of_deal and target == "accepted" and "deal.update" in set(permission_keys):
            # No quote to accept: "ลูกค้าตกลงแล้ว" on a deal with none is the
            # deal won (review, 6 Sep 2026 — it answered "no quote" only).
            return await _handle_deal_stage_command(
                client, license_id=license_id, deal_code=deal_code, target_stage="won",
                permission_keys=permission_keys, language=language, actor_id=ctx.chann_uid, message=message,
            )
        if not quotes_of_deal:
            return ChatReply(
                text=_t(QUOTE_NONE_ON_DEAL, language).format(code=deal_code),
                quick_replies=[("ข้อมูลดีล", f"ข้อมูลดีล {deal_code}"), ("สร้างใบเสนอราคา", f"สร้างใบเสนอราคาจากดีล {deal_code}")],
            )
        code = str(quotes_of_deal[-1].get("quote_id") or "").upper()
    if not code:
        last_ref = await _last_entity_ref(client, ctx)
        if last_ref and last_ref.get("entity_type") == "quote":
            code = str(last_ref.get("code") or "")
    if not code:
        return ChatReply(text=_t(LINE_NEEDS_TARGET, language))
    try:
        quotes = await client.list_quotes(license_id)
        quote = next((q for q in quotes if str(q.get("quote_id", "")).upper() == code), None)
        if quote is None:
            return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what="ใบเสนอราคา", code=code))
        await client.set_quote_status(license_id, str(quote["id"]), target, actor_id=ctx.chann_uid)
    except DataTierError as exc:
        if _is_conflict(exc):
            return ChatReply(text=_t(DEAL_STAGE_ILLEGAL, language).format(deal_id=code))
        log.exception("quote status change failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("quote status change failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return ChatReply(
        text=_t(QUOTE_STATUS_SET, language).format(
            code=code, status=_label(QUOTE_STATUS_LABELS, target, language),
        ),
        entity_type="quote", entity_id=str(quote["id"]),
        quick_replies=(
            [("สร้างใบใหม่", "สร้างใบเสนอราคา")] if target == "rejected" else []
        ),
    )


async def _handle_quote_discount(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """A discount on the whole quote, said in chat.

    "ลดราคา 10%" was being read as a LINE edit and looked for a product
    called "10%". A percentage or a bare amount with no product named is
    the quote's discount, not a line's price.
    """
    if "quote.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    # The code is stripped first: "ส่วนลด Q-2026-0001 500 บาท" used to read
    # 2026 as the discount (review, 6 Sep 2026).
    scrubbed = re.sub(r"(?<![A-Za-z0-9])[CDQT]-\d{4}-\d{4}(?![0-9])", " ", message or "", flags=re.I)
    amount = re.search(r"([\d,]+(?:\.\d{1,2})?)\s*(%|เปอร์เซ็นต์|บาท)?", scrubbed)
    if not amount:
        return ChatReply(text=_t(QUOTE_DISCOUNT_NEEDS, language))
    number = amount.group(1).replace(",", "")
    is_percent = amount.group(2) in ("%", "เปอร์เซ็นต์")

    match = re.search(r"(?<![A-Za-z0-9])(Q-\d{4}-\d{4})(?![0-9])", message or "", re.I)
    code = match.group(1).upper() if match else None
    if not code:
        last_ref = await _last_entity_ref(client, ctx)
        if last_ref and last_ref.get("entity_type") == "quote":
            code = str(last_ref.get("code") or "")
    if not code:
        return ChatReply(text=_t(LINE_NEEDS_TARGET, language))

    try:
        quotes = await client.list_quotes(license_id)
        quote = next((q for q in quotes if str(q.get("quote_id", "")).upper() == code), None)
        if quote is None:
            return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what="ใบเสนอราคา", code=code))
        fields = {"discount_percent": number} if is_percent else {"discount_amount": number}
        await client.set_quote_terms(license_id, str(quote["id"]), fields, actor_id=ctx.chann_uid)
        lines = await client.list_quote_products(license_id, str(quote["id"]))
    except DataTierError as exc:
        if "can no longer be edited" in str(exc.detail):
            return ChatReply(text=_t(LINE_QUOTE_LOCKED, language).format(code=code))
        log.exception("quote discount failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("quote discount failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    subtotal = sum(
        (Decimal(str(l.get("quoted_unit_price") or 0)) * int(l.get("qty") or 0) for l in lines),
        Decimal("0"),
    )
    cut = (subtotal * Decimal(number) / 100) if is_percent else Decimal(number)
    net = max(Decimal("0"), subtotal - cut)
    return ChatReply(
        text=_t(QUOTE_DISCOUNT_SET, language).format(
            code=code, discount=f"{number}%" if is_percent else f"{Decimal(number):,.2f}",
            total=f"{net:,.2f}",
        ),
        entity_type="quote", entity_id=str(quote["id"]),
        quick_replies=[("ออกเอกสาร", f"ออกเอกสาร {code}")],
    )


async def _handle_line_edit(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    trigger: str, permission_keys: list[str], language: str, remove: bool = False,
) -> ChatReply:
    license_id = str(license_id)
    kind, code, entity_id, lines = await _resolve_line_target(
        client, license_id, ctx, message,
    )
    if kind is None or entity_id is None:
        return ChatReply(text=_t(LINE_NEEDS_TARGET, language))

    needed = "quote.update" if kind == "quote" else "deal.update"
    if needed not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    # Everything after the trigger, minus the codes and the new value, is
    # the product being pointed at.
    lowered = message.lower()
    index = lowered.find(trigger.lower())
    text = message[index + len(trigger):] if index >= 0 else message
    text = re.sub(r"\b[QD]-\d{4}-\d{4}\b", " ", text, flags=re.I)
    # "เปลี่ยนจำนวนเป็นสาม" says three as plainly as "เป็น 3" does.
    text = _fold_qty_words(text)

    new_price = None
    new_qty = None
    price_match = _NEW_PRICE_RE.search(text)
    if price_match and "ราคา" in trigger:
        new_price = price_match.group(1).replace(",", "")
        text = text.replace(price_match.group(0), " ")
    elif price_match and "จำนวน" in trigger:
        new_qty = _whole_qty(price_match.group(1).replace(",", ""))
        if new_qty is None:
            return ChatReply(text=_t(LINE_QTY_NOT_WHOLE, language))
        text = text.replace(price_match.group(0), " ")
    else:
        qty_match = _QTY_RE.search(text)
        if qty_match and "จำนวน" in trigger:
            counted = _whole_qty(qty_match.group(1) or qty_match.group(2))
            if counted is None:
                return ChatReply(text=_t(LINE_QTY_NOT_WHOLE, language))
            new_qty = max(1, counted)
            text = text.replace(qty_match.group(0), " ")

    for word in ("ใน", "ของ", "ออกจาก", "จาก", "on", "from"):
        text = text.replace(word, " ")
    # "ลบสินค้าพัดลมออก" pointed at a product called "พัดลมออก" (owner test,
    # 8 Sep 2026): the words around the name come off first.
    name = _strip_item_particles(" ".join(text.split()).strip(" :·-,"))

    if not lines and not remove and name and not _is_generic_product_word(name) and (new_price is not None or new_qty is not None):
        # Nothing on the record yet and a price or quantity in hand: the
        # line is new, and the person is asked so rather than told "no
        # lines to change".
        return await _hold_new_line(
            client, ctx=ctx, kind=kind, code=code or "", entity_id=entity_id, name=name,
            qty=new_qty or 1, price=new_price, language=language,
        )
    if not lines:
        return ChatReply(
            text=_t(LINE_NONE_YET, language).format(
                where="ใบเสนอราคา" if kind == "quote" else "ดีล", code=code or "",
            )
        )

    line = _match_line(lines, _strip_item_particles(name) if name else name)
    if line is None and name and name.isascii() and name.lower().endswith("s"):
        line = _match_line(lines, name[:-1])
    if line is None and len(lines) == 1 and (
        not name or _is_generic_product_word(name)
    ):
        # One line on the deal, and nothing that identifies a different
        # one. "แก้ราคาสินค้าเป็น 1800" uses the WORD for product rather
        # than a name; "เพิ่มจำนวนเป็น 5" names nothing at all. Both are
        # unambiguous when there is only one line to mean.
        #
        # Only when there is exactly one: with several, guessing would
        # put a price on the wrong product.
        line = lines[0]
    if line is None and not name:
        # Several lines and nothing said which. Listing them is the
        # answer — asking "which one?" without showing the options makes
        # someone go and look the deal up.
        return ChatReply(
            text=_t(LINE_WHICH_ONE, language).format(
                where="ใบเสนอราคา" if kind == "quote" else "ดีล",
                code=code or "",
                options="\n".join(
                    f"· {l.get('product_name')}" for l in lines[:LIST_LIMIT]
                ),
            ),
            quick_replies=[
                (
                    str(l.get("product_name"))[:20],
                    f"{trigger}{l.get('product_name')} เป็น ",
                )
                for l in lines[:4]
            ],
        )
    if line is None and name and len(_match_lines(lines, name)) > 1:
        # Several lines match. Offering them beats "not found", which is
        # false, and beats guessing, which puts a price on the wrong one.
        candidates = _match_lines(lines, name)
        return ChatReply(
            text=_t(LINE_WHICH_ONE, language).format(
                where="ใบเสนอราคา" if kind == "quote" else "ดีล", code=code or "",
                options="\n".join(f"· {l.get('product_name')}" for l in candidates[:LIST_LIMIT]),
            ),
            quick_replies=[
                (str(l.get("product_name"))[:20], f"{trigger}{l.get('product_name')} เป็น ")
                for l in candidates[:4]
            ],
        )
    if line is None and not remove and name and (new_price is not None or new_qty is not None):
        # Not on the record, but a price or a quantity was given: this is a
        # NEW line, and "ไม่พบสินค้า" is the wrong answer to it. Offered,
        # with everything said so far remembered (owner test, 8 Sep 2026).
        return await _hold_new_line(
            client, ctx=ctx, kind=kind, code=code or "", entity_id=entity_id, name=name,
            qty=new_qty or 1, price=new_price, language=language,
        )
    if line is None:
        return ChatReply(
            text=_t(LINE_NOT_FOUND, language).format(
                name=name or "—",
                where="ใบเสนอราคา" if kind == "quote" else "ดีล",
                code=code or "",
            )
        )

    where = "ใบเสนอราคา" if kind == "quote" else "ดีล"
    try:
        if remove:
            if kind == "quote":
                await client.remove_quote_product(
                    license_id, entity_id, str(line["id"]), actor_id=ctx.chann_uid,
                )
            else:
                await client.remove_deal_product(
                    license_id, entity_id, str(line["id"]), actor_id=ctx.chann_uid,
                )
            await _remember_line(client, ctx, kind=kind, entity_id=entity_id, code=code or "", line=None)
            return ChatReply(
                text=_t(LINE_REMOVED, language).format(
                    name=line.get("product_name"), where=where, code=code,
                ) + await _record_total_suffix(client, license_id, kind, entity_id, language),
                entity_type=kind, entity_id=entity_id,
            )

        fields: dict = {}
        if new_price is not None:
            fields["quoted_unit_price"] = new_price
        if new_qty is not None:
            fields["qty"] = new_qty
        if not fields:
            return ChatReply(text=_t(LINE_NEEDS_TARGET, language))

        if kind == "quote":
            updated = await client.update_quote_product(
                license_id, entity_id, str(line["id"]), fields, actor_id=ctx.chann_uid,
            )
        else:
            updated = await client.update_deal_product(
                license_id, entity_id, str(line["id"]), fields, actor_id=ctx.chann_uid,
            )
    except DataTierError as exc:
        if kind == "quote" and "can no longer be edited" in str(exc.detail):
            return ChatReply(text=_t(LINE_QUOTE_LOCKED, language).format(code=code))
        log.exception("line edit failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception:
        log.exception("line edit failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    unit = Decimal(str(updated.get("quoted_unit_price") or 0))
    qty = int(updated.get("qty") or 1)
    await _remember_line(client, ctx, kind=kind, entity_id=entity_id, code=code or "", line={**line, **(updated or {})})
    return ChatReply(
        text=_t(LINE_UPDATED, language).format(
            name=updated.get("product_name") or line.get("product_name"), qty=qty,
            price=f"{unit:,.2f}", total=f"{unit * qty:,.2f}",
            where=where, code=code,
        ) + await _record_total_suffix(client, license_id, kind, entity_id, language),
        entity_type=kind, entity_id=entity_id,
    )


async def _handle_deal_product_add(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    trigger: str, permission_keys: list[str], language: str,
) -> ChatReply:
    """Put a line item on a deal, from chat.

    Needed the moment quoting started requiring one: the refusal told
    people to add a product and there was no way to do it here, which is
    worse than the original confusion — they now knew what to do and
    still could not do it.

    Falls back to the deal just discussed, like quoting does. Price comes
    from the catalogue when the product is one we know, so "เพิ่มสินค้า
    FAN001" is enough for a shop that has set its prices up.
    """
    if "deal.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    text = (message or "")

    deal_code = None
    match = re.search(r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", text, re.IGNORECASE)
    if match:
        deal_code = match.group(1).upper()
        text = text.replace(match.group(1), " ")
    else:
        last_ref = await _last_entity_ref(client, ctx)
        if last_ref and last_ref.get("entity_type") == "deal":
            deal_code = str(last_ref.get("code") or "")
    if not deal_code:
        return ChatReply(text=_t(DEAL_PRODUCT_NEEDS_DEAL, language))

    # Strip the trigger and the joining words, leaving the product itself.
    lowered = text.lower()
    index = lowered.find(trigger.lower())
    if index >= 0:
        text = text[index + len(trigger):]
    for word in ("เข้าดีล", "ในดีล", "ให้ดีล", "to deal", "เข้า"):
        text = text.replace(word, " ")

    qty = 1
    qty_match = _QTY_RE.search(text)
    price = None
    price_match = _PRICE_RE.search(text)
    if price_match:
        price = price_match.group(1).replace(",", "")
        text = text.replace(price_match.group(0), " ")
    if qty_match:
        counted = _whole_qty(qty_match.group(1) or qty_match.group(2))
        if counted is None:
            # "-1 ตัว" / "1.5 ตัว": said, not silently rounded into one.
            return ChatReply(text=_t(LINE_QTY_NOT_WHOLE, language))
        qty = max(1, counted)
        text = text.replace(qty_match.group(0), " ")

    name = _strip_item_particles(" ".join(text.split()).strip(" :·-,"))
    if not name or _is_generic_product_word(name):
        return ChatReply(text=_t(DEAL_PRODUCT_NEEDS_NAME, language))

    try:
        deals = await client.list_deals(license_id)
        deal = next(
            (d for d in deals if str(d.get("deal_id", "")).upper() == deal_code), None,
        )
    except Exception:
        log.exception("adding a product to a deal failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    if deal is None:
        return ChatReply(
            text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code)
        )
    deal_id = str(deal["id"])
    lines = list(deal.get("products") or [])

    product = None
    if price is None:
        # Look it up rather than asking: a shop that has entered its
        # catalogue should not have to retype prices it already knows.
        product, problem = await _catalogue_product(
            client, license_id, name, qty=qty, code=deal_code, language=language,
        )
        if problem is not None:
            return problem
        if product and product.get("unit_price") is not None:
            price = str(product["unit_price"])
            name = str(product.get("product_name") or name)

    existing = next(
        (l for l in lines if str(l.get("product_name") or "").lower() == name.lower()), None,
    )
    if existing is not None:
        # Already on the deal: "เพิ่มพัดลม 3 ตัว" means three MORE, not a
        # second line called the same thing (owner test, 8 Sep 2026).
        return await _apply_line_qty_change(
            client, ctx=ctx, license_id=license_id, kind="deal", code=deal_code, entity_id=deal_id,
            line=existing, delta=qty, language=language, unit=_item_unit_word(message),
        )

    if price is None:
        # Not in the catalogue and no price given. The line is held with
        # its name and quantity, so "1500" on its own finishes it.
        return await _hold_new_line(
            client, ctx=ctx, kind="deal", code=deal_code, entity_id=deal_id, name=name, qty=qty,
            price=None, language=language,
        )

    return await _apply_new_line(
        client, ctx=ctx, license_id=license_id, kind="deal", code=deal_code, entity_id=deal_id,
        name=name, qty=qty, price=str(price), product_id=str(product["id"]) if product else None,
        language=language,
    )


QUOTE_CREATE_TRIGGERS = (
    "สร้างใบเสนอราคา", "ออกใบเสนอราคา", "ทำใบเสนอราคา", "create quote", "ขอใบเสนอราคา", "เปิดใบเสนอราคา",
    "สร้าง quote", "ทำ quote", "ออก quote", "quotation", "new quote", "make a quote", "create quotation",
)  # "ใบเสนอราคาสำหรับ" removed: a noun phrase, not an order.


def _is_typed_quote_create(message: str) -> bool:
    """A create-quote ORDER, not a sentence containing the noun.

    The branch used to fire on the words appearing anywhere, so
    "ลูกค้าขอใบเสนอราคา" — reporting what a customer asked for — issued a
    real Q- row, and "ลูกค้าอยากได้ใบเสนอราคาสำหรับพัดลม 2 ตัว" issued one
    with the deal's line destroyed and nothing put back (10 ก.ย. 2569).

    It stays deterministic for the two shapes that answer themselves: a
    D- code (every button carries one) or a sentence that OPENS with the
    verb, politeness allowed in front. Anything else is a sentence to be
    read, and the model decides.
    """
    text = message or ""
    if not any(t in text.lower() for t in QUOTE_CREATE_TRIGGERS):
        return False
    if re.search(r"(?<![A-Za-z0-9])D-\d{4}-\d{4}(?![0-9])", text, re.I):
        return True
    compact = _normalise(text)
    heads = [compact]
    if _POLITE_REQUEST_RE.match(compact):
        # "ช่วยออกใบเสนอราคาให้หน่อย" is the same order, said politely.
        heads.append(_POLITE_REQUEST_RE.sub("", compact, count=1))
    return any(h.startswith(t.lower().replace(" ", "")) for h in heads for t in QUOTE_CREATE_TRIGGERS)

DEAL_ALREADY_OPEN = {
    "th": "ลูกค้ารายนี้มีดีล {code} เปิดอยู่แล้ว\nปิดดีลเดิมก่อน (ปิดสำเร็จหรือปิดไม่สำเร็จ) แล้วค่อยเปิดใหม่",
    "en": "This customer already has {code} open. Close it before starting another.",
}

CUSTOMER_ALREADY_EXISTS = {
    "th": "เบอร์นี้มีลูกค้า {code} อยู่แล้ว ใช้รายเดิมได้เลย",
    "en": "That number already belongs to {code}.",
}

QUOTE_NEEDS_DEAL = {
    "th": "สร้างใบเสนอราคาจากดีลไหนครับ พิมพ์ \"สร้างใบเสนอราคาจากดีล D-2026-0001\"",
    "en": 'Which deal? Type "create quote from deal D-2026-0001".',
}
QUOTE_DEAL_EMPTY = {
    "th": "ดีล {deal_id} ยังไม่มีสินค้า เพิ่มสินค้าก่อนแล้วค่อยออกใบเสนอราคา\nพิมพ์ \"ข้อมูลดีล {deal_id}\" เพื่อดูรายละเอียด",
    "en": "Deal {deal_id} has no products yet — add one before quoting.",
}


async def _handle_quote_create_direct(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, deal_code: str | None = None,
) -> ChatReply:
    """Create a quote from a named deal, or from the deal just discussed.

    Deterministic for the same reason deal creation is: the quick-reply
    button writes this text itself. And it falls back to context because
    quoting is what someone does immediately after making a deal — being
    asked which one, seconds after being told which one, reads as the
    system not paying attention.
    """
    if "quote.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    # A caller may already have the deal — the model road passes what it
    # read. Otherwise the code in the sentence, otherwise the deal this
    # conversation is already on.
    if not deal_code:
        match = re.search(r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", message or "", re.IGNORECASE)
        if match:
            deal_code = match.group(1).upper()
        else:
            last_ref = await _last_entity_ref(client, ctx)
            if last_ref and last_ref.get("entity_type") == "deal":
                deal_code = str(last_ref.get("code") or "")

    if not deal_code:
        return ChatReply(text=_t(QUOTE_NEEDS_DEAL, language))

    try:
        deals = await client.list_deals(license_id)
        deal = next(
            (d for d in deals if str(d.get("deal_id", "")).upper() == deal_code), None,
        )
        if deal is None:
            return ChatReply(
                text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code)
            )
        row = await client.create_quote(
            license_id, {"deal_id": deal["id"], "owner_member_id": await _member_id_of(client, license_id, ctx)},
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        duplicate = exc.structured or {}
        if duplicate.get("error") == "duplicate":
            return ChatReply(
                text=_t(DEAL_ALREADY_OPEN, language).format(
                    code=duplicate.get("existing_code", ""),
                ),
                quick_replies=[
                    ("ดูดีล", f"ข้อมูลดีล {duplicate.get('existing_code','')}"),
                ],
            )
        # The "no products" rule, said in terms the person can act on
        # rather than as a raw conflict from the data tier.
        if "no products" in str(exc.detail).lower():
            return ChatReply(
                text=_t(QUOTE_DEAL_EMPTY, language).format(deal_id=deal_code),
                quick_replies=[("ดูข้อมูลดีล", f"ข้อมูลดีล {deal_code}")],
            )
        if _is_not_found(exc):
            # The deal vanished between listing and creating, or the list
            # call itself failed. Either way "not found" is the honest
            # answer and the generic save-failed message is not.
            return ChatReply(
                text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code)
            )
        log.exception("quote creation failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return ChatReply(
                text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code)
            )
        log.exception("quote creation failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _remember_entity(
        client, ctx, entity_type="quote", entity_id=row["id"], code=row["quote_id"],
    )

    # A product named in the same breath REPLACES what was copied from the
    # deal. "ออกใบเสนอราคา พัดลม 2 ตัว" means quote two fans; copying one
    # from the deal and ignoring the rest of the sentence produced a
    # document that quietly said something the person did not ask for.
    #
    # Said nothing about products? Keep the deal's, which is the whole
    # point of copying them.
    override = _trailing_product_for_quote(message)
    if override:
        # The replacement goes on FIRST. Clearing before knowing the new
        # line could be added left the quotation empty whenever the
        # product was not in the catalogue — "ใบเสนอราคาสำหรับพัดลม 2 ตัว"
        # produced a quote with the deal's line deleted and nothing put
        # back, and a reply saying the product does not exist
        # (10 ก.ย. 2569).
        try:
            copied = [str(line["id"]) for line in
                      await client.list_quote_products(license_id, str(row["id"]))]
        except Exception:
            log.exception("could not read the copied lines")
            copied = []
        added = await _add_line_from_text(
            client, ctx=ctx, license_id=license_id, quote_id=str(row["id"]),
            text=override, language=language,
        )
        if added is None:
            # The replacement is on. Now, and only now, the copied lines go.
            for line_id in copied:
                try:
                    await client.remove_quote_product(
                        license_id, str(row["id"]), line_id, actor_id=ctx.chann_uid,
                    )
                except Exception:
                    log.exception("could not remove a copied line after an override")
        if added is not None:
            # The add failed or needs a choice; its reply explains why, and
            # the quote exists either way so it is named here too.
            return ChatReply(
                text=f"{_t(QUOTE_CREATED, language).format(quote_id=row['quote_id'], deal_id=deal_code)}\n{added.text}",
                entity_type="quote", entity_id=str(row["id"]),
                quick_replies=added.quick_replies,
            )

    return ChatReply(
        text=_t(QUOTE_CREATED, language).format(
            quote_id=row["quote_id"], deal_id=deal_code,
        ),
        entity_type="quote", entity_id=str(row["id"]),
        quick_replies=[("ออกเอกสาร", f"ออกเอกสาร {row['quote_id']}")],
    )


def _trailing_product_for_quote(message: str) -> str | None:
    """A product line named alongside a create-quote command."""
    for trigger in QUOTE_CREATE_TRIGGERS:
        index = (message or "").lower().find(trigger.lower())
        if index < 0:
            continue
        rest = message[index + len(trigger):]
        rest = re.sub(r"\bจากดีล\b|\bD-\d{4}-\d{4}\b", " ", rest, flags=re.I)
        rest = " ".join(rest.split()).strip(" :·-,")
        if rest and (_QTY_RE.search(rest) or _PRICE_RE.search(rest)):
            return rest
    return None


async def _add_line_from_text(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, quote_id: str,
    text: str, language: str,
) -> ChatReply | None:
    """Put one line on a quote, parsed from free text.

    Returns None when it worked, or a ChatReply explaining what is needed
    — an ambiguous name, an unknown product with no price. The caller
    prefixes it, so the person always learns the quote exists even when
    the line did not land.
    """
    qty = 1
    price = None
    name = text

    price_match = _PRICE_RE.search(name)
    if price_match:
        price = price_match.group(1).replace(",", "")
        name = name.replace(price_match.group(0), " ")
    qty_match = _QTY_RE.search(name)
    if qty_match:
        counted = _whole_qty(qty_match.group(1) or qty_match.group(2))
        if counted is None:
            return ChatReply(text=_t(LINE_QTY_NOT_WHOLE, language))
        qty = max(1, counted)
        name = name.replace(qty_match.group(0), " ")
    name = " ".join(name.split()).strip(" :·-,")
    if not name:
        return ChatReply(text=_t(DEAL_PRODUCT_NEEDS_NAME, language))

    product = None
    if price is None:
        try:
            products = await client.list_products(license_id)
        except Exception:
            products = []
        needle = name.lower()
        exact = [
            p for p in products
            if str(p.get("product_id") or "").lower() == needle
            or str(p.get("product_name") or "").lower() == needle
        ]
        candidates = exact or [
            p for p in products
            if needle in str(p.get("product_name") or "").lower()
            or needle in str(p.get("product_id") or "").lower()
        ]
        if len(candidates) > 1:
            shown = candidates[:LIST_LIMIT]
            lines = "\n".join(
                f"· {c.get('product_name')}"
                + (f" — {Decimal(str(c['unit_price'])):,.2f}"
                   if c.get("unit_price") is not None else "")
                for c in shown
            )
            return ChatReply(
                text=_t(DEAL_PRODUCT_AMBIGUOUS, language).format(name=name, options=lines),
                quick_replies=[
                    (
                        _distinguishing_part(
                            str(c.get("product_name") or ""),
                            [str(o.get("product_name") or "") for o in shown],
                        ),
                        f"เพิ่มสินค้าในใบเสนอราคา {c.get('product_name')} {qty} ตัว",
                    )
                    for c in shown[:4]
                ],
            )
        product = candidates[0] if candidates else None
        if product and product.get("unit_price") is not None:
            price = str(product["unit_price"])
            name = str(product.get("product_name") or name)

    if price is None:
        # Not in the catalogue and no price given. Saying so plainly beats
        # inventing a zero, which would render a document offering to do
        # the work for nothing.
        return ChatReply(text=_t(PRODUCT_UNKNOWN_NEEDS_PRICE, language).format(name=name))

    try:
        await client.add_quote_product(
            license_id, quote_id,
            {
                "product_name": name,
                "quoted_unit_price": price,
                "qty": qty,
            },
            actor_id=ctx.chann_uid,
        )
    except Exception:
        log.exception("could not add a line to a quote")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    return None


# "เปิดดีล" is deliberately NOT here: "เปิดดีล D-2026-0001 ใหม่" means
# REOPEN a closed deal, which is a different action with a different
# permission. Eight tests caught it the moment it was added — the same
# substring trap as ไม่สำเร็จ/สำเร็จ, ออกเอกสารใหม่/ออกเอกสาร and
# ตั้งกฎมอบหมาย/มอบหมาย before it.
DEAL_CREATE_BARE_TRIGGERS = ("สร้างดีล", "create deal")

# "สร้างดีลและเพิ่มสินค้าพัดลม 2 ตัว" — one sentence, two actions, which
# is how people talk. The conjunction is where the second one starts.
_DEAL_CONJUNCTION_RE = re.compile(
    r"(?:\s*(?:และ|แล้ว|พร้อม|,|\+|and)\s*)(เพิ่มสินค้า|ใส่สินค้า|add product)",
    re.IGNORECASE,
)

def _trailing_product(message: str) -> str | None:
    """A product named after a bare create-deal command, with no "และ".

    "สร้างดีล พัดลม 2 ตัว" is one instruction with two parts and no
    conjunction — people leave it out constantly — so the remainder after
    the trigger is treated as a product line when it looks like one.

    Returns it phrased as an add-product command so the existing handler
    parses it, keeping one parser for product lines rather than two that
    can disagree about what "2 ตัว" means.
    """
    for trigger in DEAL_CREATE_BARE_TRIGGERS:
        index = message.lower().find(trigger.lower())
        if index < 0:
            continue
        rest = message[index + len(trigger):].strip(" :·-,")
        # A name alone is not enough; a product line has a quantity or a
        # price, and without one this is more likely a customer's name.
        if rest and (_QTY_RE.search(rest) or _PRICE_RE.search(rest)):
            return f"เพิ่มสินค้า {rest}"
    return None


def _after_deal_conjunction(message: str) -> str | None:
    """The second half of a compound instruction, if there is one.

    Returned as text rather than parsed here, so the add-product handler
    stays the single place that knows how to read a product line — one
    parser, one set of quirks, one thing to fix.
    """
    match = _DEAL_CONJUNCTION_RE.search(message or "")
    return message[match.start(1):].strip() if match else None


# Buttons the system writes that ask for something rather than doing it.
# Tapping one must not spend an AI call to be told what the system already
# knows: there is nothing to interpret in "สร้างลูกค้า" on its own.
BARE_CREATE_PROMPTS: dict[str, tuple[tuple[str, ...], str, dict]] = {
    "customer": (("สร้างลูกค้า", "เพิ่มลูกค้า", "add customer"), "customer.create", {
        "th": "สร้างลูกค้าใหม่ พิมพ์ชื่อกับเบอร์มาได้เลยครับ\n"
              'เช่น "จุใจ มาติกา 0812345678"',
        "en": 'New customer — send a name and phone, e.g. "Jujai 0812345678".',
    }),
    "product": (("สร้างสินค้า", "เพิ่มสินค้าใหม่", "add product"), "product.manage", {
        "th": "เพิ่มสินค้าใหม่ พิมพ์รหัส ชื่อ และราคามาได้เลยครับ\n"
              'เช่น "FAN001 พัดลมตั้งพื้น 16 นิ้ว ราคา 1500"',
        "en": 'New product — code, name and price, e.g. "FAN001 Fan 1500".',
    }),
}


async def _handle_bare_create_prompt(
    message: str, permission_keys: list[str], language: str,
) -> ChatReply | None:
    """The reply to a button that only asks for details.

    Returns None when the message carries more than the bare phrase — at
    that point there IS something to interpret, and the AI should have it.
    """
    lowered = (message or "").strip().lower()
    for _, (phrases, needed, prompt) in BARE_CREATE_PROMPTS.items():
        if lowered not in {p.lower() for p in phrases}:
            continue
        if needed not in set(permission_keys):
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        return ChatReply(text=_t(prompt, language))
    return None


_DEAL_NAME_STOP_WORDS = (
    "มูลค่า", "ราคา", "ยอด", "ปิด", "คาดว่า", "ภายใน", "วงเงิน", "งบ", "ดีล", "ประมาณ", "เบอร์", "โทร",
    "สิ้น", "ปลาย", "ต้นเดือน", "กำหนด", "worth", "amount", "closing", "close", "about", "around",
)


def _deal_name_only(text: str | None) -> str | None:
    """"อาทิตย์ มูลค่า 250,000 ปิดสิ้นเดือนนี้" -> "อาทิตย์": the name is the
    leading words up to the first number, money or timing word."""
    if not text:
        return text
    kept: list[str] = []
    for token in text.strip().split():
        lowered = token.lower()
        if re.search(r"\d", token) or any(lowered.startswith(w) for w in _DEAL_NAME_STOP_WORDS):
            break
        kept.append(token)
    return " ".join(kept).strip(" ,:") or None


_DEAL_NAME_IN_MESSAGE_RE = re.compile(r"(?:ของ|ให้กับ|ให้|กับ|สำหรับ|for)\s*(?:คุณ)?\s*([^\s,]+(?:\s+[^\s,\d]+)?)")


def _deal_name_from_message(message: str | None) -> str | None:
    """"ดีลนี้ของอาทิตย์ มูลค่า …" names a person even without the
    "สร้างดีลให้" prefix. User review (4 Sep 2026): a bare deal command
    used to fall through to "the last customer mentioned" and put the deal
    on the wrong person; the name in the sentence wins."""
    match = _DEAL_NAME_IN_MESSAGE_RE.search(message or "")
    if not match:
        return None
    return _deal_name_only(match.group(1))


async def _customer_still_there(
    client: DataClient, ctx: ResolvedContext, license_id,
) -> dict | None:
    """The customer in context, re-read from this shop's own rows.

    The cache holds a name and an id from up to an hour ago. In that hour
    the person can be archived, renamed, or erased under PDPA — and
    "สร้างดีล" with no name still made a deal against the id and echoed
    the cached name back: "สร้างดีล D-2026-0001 สำหรับ สมชาย ใจดี (ลูกค้า
    ที่เพิ่งคุยถึง) เรียบร้อยแล้ว" over a shop with no สมชาย in it
    (10 ก.ย. 2569). The id is authoritative; the name has to be re-read
    before it is spoken.
    """
    ref = await _last_customer_ref(client, ctx)
    if not ref or not ref.get("customer_id"):
        return None
    try:
        rows = await client.list_customers(str(license_id))
    except Exception:  # noqa: BLE001
        log.exception("could not re-read the customer in context")
        return None
    return next((r for r in rows if str(r.get("id")) == str(ref["customer_id"])), None)


async def _handle_deal_create_direct(
    client: DataClient, *, ctx: ResolvedContext, license_id, name: str | None,
    permission_keys: list[str], language: str, rest: str | None = None, message: str | None = None,
    abandoned: dict | None = None,
) -> ChatReply:
    """Create a deal, then optionally act on it in the same breath.

    Reuses the same resolver and creation helper the AI path uses, so the
    two cannot drift; the only difference is how the customer was found.

    `name=None` means "the customer we were just looking at" — the record
    someone opened seconds ago is almost always the one they mean.
    """
    if "deal.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)

    if name:
        contact, problem = await _find_one_customer_by_name(
            client, license_id, name, language,
            ctx=ctx, resume_entity="deal", resume_action="create", resume_fields={},
        )
        if problem is not None:
            if _draft_matches_name(abandoned, name) and problem.text == _t(CUSTOMER_NOT_FOUND, language).format(name=name):
                deal_fields, _ambiguous = _deal_fields_from_message(message or "", None)
                return await _offer_draft_customer_deal(
                    client, ctx=ctx, draft=abandoned, deal_fields=deal_fields, language=language,
                )
            return problem
        if contact is None:
            return ChatReply(
                text=_t(DEAL_CUSTOMER_NOT_FOUND, language).format(name=name)
            )
        used_context = False
    else:
        # last_customer_ref, not last_entity_ref: the AI path has used it
        # for exactly this since Phase 9, and a second mechanism for "the
        # customer we were just discussing" would drift from the first and
        # give different answers to the same question.
        # Re-read, not remembered: the cached name is up to an hour old and
        # the person may have been archived or erased since.
        contact = await _customer_still_there(client, ctx, license_id)
        if contact is None:
            return ChatReply(text=_t(DEAL_NEEDS_TARGET_NAME, language))
        used_context = True

    deal_fields, ambiguous = _deal_fields_from_message(message or "", None)
    if ambiguous:
        return await _ask_deal_ambiguity(
            client, ctx=ctx, message=message or "", target_name=name, fields=deal_fields,
            ambiguous=ambiguous, language=language,
        )
    # The bare "สร้างดีล" button (and "สร้างดีล พร้อมเพิ่มสินค้า …") after
    # opening a customer means that customer — created as before. A name
    # inside the sentence was already extracted by the caller; the
    # "is it really them?" question lives on the AI path, where the model
    # may have dropped the name (user review, 4 Sep 2026).
    reply = await _apply_deal_create(
        client, contact=contact, fields=deal_fields, ctx=ctx,
        license_id=license_id, language=language, used_context=used_context,
    )

    if not rest or not reply.entity_id:
        return reply

    # The deal exists; now do the second half against it. Its own reply is
    # returned, prefixed with the first — one message describing both
    # things, because that is how the person asked for them.
    follow_on = await _handle_deal_product_add(
        client, ctx=ctx, license_id=license_id, message=rest,
        trigger=next(
            (t for t in DEAL_PRODUCT_ADD_TRIGGERS if t in rest.lower()), "เพิ่มสินค้า",
        ),
        permission_keys=permission_keys, language=language,
    )
    return ChatReply(
        text=f"{reply.text}\n{follow_on.text}",
        entity_type=follow_on.entity_type or reply.entity_type,
        entity_id=follow_on.entity_id or reply.entity_id,
        quick_replies=follow_on.quick_replies,
    )


# Asking about the pipeline in words. Every one of these existed as a
# column after migration 0020 and could be filtered on a screen; none
# could be asked for in chat, which is where a salesperson actually is.
DEAL_QUERY_PHRASES = {
    "closing_this_month": (
        "ดีลเดือนนี้", "ดีลที่จะปิดเดือนนี้", "ปิดเดือนนี้", "เดือนนี้ปิดได้เท่าไหร่",
        "closing this month",
    ),
    "closing_this_week": ("ดีลสัปดาห์นี้", "ดีลอาทิตย์นี้", "ปิดสัปดาห์นี้", "closing this week"),
    "overdue": ("ดีลเลยกำหนด", "ดีลค้างเกินกำหนด", "ดีลที่เลยวันปิด", "overdue deals"),
    "undated": ("ดีลที่ยังไม่มีวันปิด", "ดีลไม่มีกำหนด", "deals with no date"),
    "biggest": ("ดีลใหญ่สุด", "ดีลมูลค่าสูงสุด", "ดีลที่แพงที่สุด", "biggest deals"),
    "lost": ("ดีลที่แพ้", "ดีลไม่สำเร็จ", "ดีลที่เสียไป", "lost deals"),
    "won": ("ดีลที่ชนะ", "ดีลสำเร็จ", "ดีลที่ปิดได้", "won deals"),
}

# "ดีลเกิน 5000" / "ดีลมากกว่า 10,000" — a value threshold.
_DEAL_VALUE_RE = re.compile(
    r"ดีล\s*(?:ที่)?\s*(?:เกิน|มากกว่า|สูงกว่า|over|above)\s*([\d,]+(?:\.\d+)?)\s*(ล้าน|แสน|หมื่น|พัน)?", re.I,
)


def _deal_value(deal: dict) -> Decimal:
    return sum(
        (Decimal(str(p.get("quoted_unit_price") or 0)) * int(p.get("qty") or 0)
         for p in (deal.get("products") or [])),
        Decimal("0"),
    )


# Setting the two fields a deal has that nothing in chat could touch.
DEAL_CLOSE_DATE_TRIGGERS = ("คาดว่าจะปิด", "วันปิดดีล", "ตั้งวันปิด", "จะปิดวันที่", "expected close")

DEAL_CLOSE_DATE_SET = {
    "th": "ตั้งวันปิดคาดการณ์ของ {code} เป็น {date} แล้ว",
    "en": "{code} now expected to close {date}.",
}
DEAL_CLOSE_DATE_NEEDS = {
    "th": "ระบุวันด้วยครับ เช่น \"ดีล D-2026-0001 คาดว่าจะปิดวันศุกร์\"",
    "en": 'Which day? e.g. "D-2026-0001 expected close Friday".',
}


async def _handle_deal_close_date(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """"ดีล D-2026-0001 คาดว่าจะปิดวันศุกร์" — the forecast, from chat.

    Uses the deal named, or the one just discussed. The date parser is
    the same one reminders use, so every phrasing that works for "เตือน"
    works here too.
    """
    if "deal.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    from .thai_datetime import format_thai_date, parse_thai_date

    license_id = str(license_id)
    match = re.search(r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", message or "", re.I)
    code = match.group(1).upper() if match else None
    if not code:
        last_ref = await _last_entity_ref(client, ctx)
        if last_ref and last_ref.get("entity_type") == "deal":
            code = str(last_ref.get("code") or "")
    if not code:
        return ChatReply(text=_t(QUOTE_NEEDS_DEAL, language))

    when = parse_thai_date(message, local_today())
    if when is None:
        return ChatReply(text=_t(DEAL_CLOSE_DATE_NEEDS, language))

    try:
        deals = await client.list_deals(license_id)
        deal = next((d for d in deals if str(d.get("deal_id", "")).upper() == code), None)
        if deal is None:
            return ChatReply(text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=code))
        await client.update_deal(
            license_id, str(deal["id"]),
            {"expected_close_date": when.isoformat()}, actor_id=ctx.chann_uid,
        )
    except Exception:
        log.exception("could not set expected close date")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _remember_entity(
        client, ctx, entity_type="deal", entity_id=str(deal["id"]), code=code,
    )
    return ChatReply(
        text=_t(DEAL_CLOSE_DATE_SET, language).format(
            code=code, date=format_thai_date(when),
        ),
        entity_type="deal", entity_id=str(deal["id"]),
    )


async def _handle_deal_query(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
    kind: str, threshold: Decimal | None = None,
) -> ChatReply:
    """A filtered view of the pipeline, in chat.

    The same questions the dashboard's sort and filter controls answer,
    asked in words. A salesperson between calls does not open a screen to
    learn which deals are due this week; they ask.
    """
    if "deal.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    from datetime import date as _date, timedelta

    try:
        deals = await client.list_deals(str(license_id))
    except Exception:
        log.exception("deal query failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    today = local_today()
    open_stages = ("new", "proposed")

    def close_of(d: dict):
        raw = d.get("expected_close_date")
        try:
            return _date.fromisoformat(str(raw)) if raw else None
        except ValueError:
            return None

    if kind == "closing_this_month":
        month_end = (_date(today.year + (today.month == 12), (today.month % 12) + 1, 1))
        rows = [d for d in deals if d.get("stage") in open_stages
                and close_of(d) and today <= close_of(d) < month_end]
        title = "ดีลที่คาดว่าจะปิดเดือนนี้"
    elif kind == "closing_this_week":
        week_end = today + timedelta(days=7 - today.weekday())
        rows = [d for d in deals if d.get("stage") in open_stages
                and close_of(d) and today <= close_of(d) < week_end]
        title = "ดีลที่คาดว่าจะปิดสัปดาห์นี้"
    elif kind == "overdue":
        rows = [d for d in deals if d.get("stage") in open_stages
                and close_of(d) and close_of(d) < today]
        title = "ดีลที่เลยวันปิดคาดการณ์"
    elif kind == "undated":
        rows = [d for d in deals if d.get("stage") in open_stages and not close_of(d)]
        title = "ดีลเปิดอยู่ที่ยังไม่ระบุวันปิด"
    elif kind == "biggest":
        rows = sorted(
            [d for d in deals if d.get("stage") in open_stages],
            key=_deal_value, reverse=True,
        )[:LIST_LIMIT]
        title = "ดีลเปิดอยู่ มูลค่าสูงสุด"
    elif kind == "lost":
        rows = [d for d in deals if d.get("stage") == "lost"]
        title = "ดีลที่ไม่สำเร็จ"
    elif kind == "won":
        rows = [d for d in deals if d.get("stage") == "won"]
        title = "ดีลที่ปิดสำเร็จ"
    elif kind == "over_value" and threshold is not None:
        rows = [d for d in deals if _deal_value(d) >= threshold]
        title = f"ดีลมูลค่าตั้งแต่ {threshold:,.0f}"
    else:
        rows, title = [], "ดีล"

    if not rows:
        return ChatReply(text=f"{title}: ไม่มี")

    rows = sorted(rows, key=lambda d: (close_of(d) or _date.max, -_deal_value(d)))
    total = sum((_deal_value(d) for d in rows), Decimal("0"))
    lines = []
    for d in rows[:LIST_LIMIT]:
        value = _deal_value(d)
        when = _iso_to_thai_date(d.get("expected_close_date")) if d.get("expected_close_date") else ""
        extra = f" {value:,.0f}" if value else ""
        extra += f" · {when}" if when else ""
        if kind == "lost" and d.get("lost_reason"):
            extra += f" · {d['lost_reason']}"
        lines.append(f"· {d.get('deal_id')} {d.get('customer_name') or ''}{extra}".rstrip())

    head = f"{title} {len(rows)} ดีล"
    if total:
        head += f" รวม {total:,.0f}"
    return ChatReply(text=head + "\n" + "\n".join(lines))


async def _handle_deal_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
    open_only: bool = False, for_customer: str | None = None,
) -> ChatReply:
    if not set(permission_keys) & DEAL_VIEW_KEYS:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    try:
        deals = await client.list_deals(license_id)
    except Exception:
        log.exception("deal list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    matched_customer = None
    if for_customer:
        # "ดูดีลของจุใจ" — the deals belonging to one person, which is how
        # someone actually thinks about a customer they are working. Resolved
        # by name or code against the customer list, then filtered on
        # contact_id, because a deal stores who it belongs to and nothing
        # else in the deal row names them.
        try:
            customers = await client.list_customers(license_id)
        except Exception:
            log.exception("customer lookup for a deal filter failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

        needle = for_customer.strip().lower()
        matches = [
            c for c in customers
            if needle in _customer_name(c).lower()
            or needle == str(c.get("customer_id") or "").lower()
            or needle in str(c.get("phone") or "")
        ]
        if not matches:
            return ChatReply(
                text=_t(DEAL_CUSTOMER_NOT_FOUND, language).format(name=for_customer)
            )
        if len(matches) > 1:
            # Guessing which "สมชาย" was meant would attach the answer to the
            # wrong person's pipeline — so the choice, as buttons that
            # re-send this very command with the name swapped for a code
            # (owner requirement 2 Sep: a duplicate name must be pickable,
            # not just listed).
            names = ", ".join(
                f"{_customer_name(c)} ({c.get('customer_id')})" for c in matches[:5]
            )
            return ChatReply(
                text=_t(DEAL_CUSTOMER_AMBIGUOUS, language).format(names=names),
                quick_replies=[
                    (_customer_name(c)[:20], f"ดูดีลของ {c.get('customer_id')}")
                    for c in matches[:4]
                ],
            )
        matched_customer = matches[0]
        deals = [
            d for d in deals
            if str(d.get("contact_id") or "") == str(matched_customer.get("id"))
        ]

    if open_only:
        # "Open" means not yet resolved either way. Filtering on the two
        # terminal stages rather than listing the open ones means a stage
        # added later is treated as open by default, which is the safer
        # direction to be wrong in for a work queue.
        deals = [d for d in deals if str(d.get("stage") or "").lower() not in ("won", "lost")]

    if not deals:
        if matched_customer is not None:
            # The reply that resolved a person must carry that person:
            # this one did not, so replying to "สมบัติ ยังไม่มีดีล" with a
            # follow-up question answered "ไม่พบข้อความต้นฉบับ" (21:49).
            name = _customer_name(matched_customer)
            await _remember_entity(
                client, ctx, entity_type="customer",
                entity_id=str(matched_customer["id"]),
                code=str(matched_customer.get("customer_id") or ""),
            )
            return ChatReply(
                text=_t(DEAL_NONE_FOR_CUSTOMER, language).format(name=name),
                entity_type="customer", entity_id=matched_customer["id"],
                quick_replies=[("สร้างดีล", f"สร้างดีลให้ {name}")],
            )
        return ChatReply(
            text=_t(EMPTY_LIST, language).format(what="ดีล" if language == "th" else "deals"),
            quick_replies=[("สร้างดีล", "สร้างดีล")],
        )

    shown = deals[:LIST_LIMIT]
    lines = [
        f"{d.get('deal_id') or '-'} · {_label(DEAL_STAGE_LABELS, d.get('stage'), language)}"
        + (f" · {len(d.get('products') or [])} รายการ" if d.get("products") else "")
        for d in shown
    ]
    text = "\n".join(lines) + _truncation_note(len(shown), len(deals), language, "deals")
    return ChatReply(
        text=text,
        quick_replies=[
            ("ดีลที่ยังไม่ปิด", "ดีลที่ยังไม่ปิด"),
            ("รายชื่อลูกค้า", "รายชื่อลูกค้า"),
        ],
        quick_reply_url=_dashboard_button("deals", language),
        list_card=_list_card(
            title="ดีลที่ยังไม่ปิด" if open_only else "ดีล",
            section="deals", language=language,
            shown=len(shown), total=len(deals),
            rows=[
                {
                    "title": str(d.get("deal_id") or "-"),
                    "subtitle": " · ".join(
                        p for p in (
                            _label(DEAL_STAGE_LABELS, d.get("stage"), language),
                            f"{len(d.get('products') or [])} รายการ" if d.get("products") else "",
                        ) if p
                    ),
                    "stage": d.get("stage"),
                    "action_label": "ดู",
                    "action_text": f"ข้อมูลดีล {d.get('deal_id')}",
                }
                for d in shown
            ],
        ),
    )


async def _handle_deal_detail(
    client: DataClient, *, license_id, code: str, permission_keys: list[str], language: str,
    ctx: ResolvedContext | None = None,
) -> ChatReply:
    if "deal.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not code:
        return ChatReply(text=_t(SEARCH_NEEDS_TERM, language))
    try:
        deals = await client.list_deals(str(license_id))
    except Exception:
        log.exception("deal detail failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    wanted = code.strip().lower()
    deal = next((d for d in deals if str(d.get("deal_id") or "").lower() == wanted), None)
    if deal is None:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(
                what="ดีล" if language == "th" else "deal", code=code
            )
        )

    if ctx is not None:
        await _remember_entity(
            client, ctx, entity_type="deal", entity_id=deal["id"], code=deal["deal_id"],
        )

    rows = [
        f"{deal.get('deal_id')} · {_label(DEAL_STAGE_LABELS, deal.get('stage'), language)}",
    ]
    if deal.get("notes"):
        rows.append(f"บันทึก: {deal['notes']}")

    products = deal.get("products") or []
    if products:
        rows.append("")
        rows.append("รายการสินค้า:")
        # The same deterministic arithmetic the document uses, so what a
        # salesperson reads in chat can never disagree with what the
        # customer receives on the PDF.
        items = build_line_items(products)
        for item in items:
            rows.append(
                f"  {item['line_no']}. {item['product_name']}"
                f" × {item['qty']} = {Decimal(item['line_total']):,.2f}"
            )
        subtotal = sum(Decimal(i["line_total"]) for i in items)
        rows.append(
            f"Total: {subtotal:,.2f} THB (before tax)" if language == "en" else f"รวม: {subtotal:,.2f} บาท (ยังไม่รวมภาษี)"
        )
    else:
        rows.append("No line items on this deal yet" if language == "en" else "ยังไม่มีรายการสินค้าในดีลนี้")
        rows.append(_t(DEAL_ZERO_TOTAL, language))

    return ChatReply(
        text="\n".join(rows),
        entity_type="deal",
        entity_id=str(deal.get("id") or ""),
        quick_replies=[
            ("สร้างใบเสนอราคา", f"สร้างใบเสนอราคาจากดีล {deal.get('deal_id')}"),
            ("รายการดีล", "รายการดีล"),
        ],
        quick_reply_url=_dashboard_button("deals", language),
    )


_PRODUCT_SEARCH_RES = (
    re.compile(
        r"^(?:ขอ|ช่วย|รบกวน)?\s*(?:ค้นหา|ค้น|หา|search(?: for)?|find|look ?up)\s*(?:สินค้า|products?|items?|catalogue|catalog)"
        r"\s*(?:ที่)?(?:ชื่อ|เป็น|เกี่ยวกับ|ว่า|called|named|matching|like|for)?\s*(.+)$",
        re.I,
    ),
    re.compile(
        r"^(?:มี|ขอดู|ดู|show|list|which)\s*(?:สินค้า|products?|items?)\s*(?:อะไรบ้าง|ไหนบ้าง|อะไร)?\s*(?:ที่)?"
        r"(?:เป็น|ชื่อ|เกี่ยวกับ|ประเภท|หมวด|ว่า|matching|like|called|named|for)\s*(.+?)\s*(?:บ้าง|ไหม|มั้ย|หน่อย|ครับ|ค่ะ|คะ)*$",
        re.I,
    ),
    re.compile(r"^มี\s*(.+?)\s*(?:รุ่น|แบบ|ยี่ห้อ)?(?:อะไรบ้าง|ไหนบ้าง|บ้างไหม|บ้างมั้ย|บ้าง)\s*(?:ครับ|ค่ะ|คะ)?$"),
    re.compile(r"^(?:สินค้า|products?)\s*(.+?)\s*(?:มีไหม|มีมั้ย|มีบ้างไหม|มีรึเปล่า|มีหรือเปล่า)\s*(?:ครับ|ค่ะ|คะ)?$"),
    re.compile(r"^what\s+(.+?)\s+do (?:we|you) (?:have|sell|stock)\??$", re.I),
)
_PRODUCT_SEARCH_STOP = (
    "ดีล", "ลูกค้า", "งาน", "นัด", "ใบเสนอราคา", "ทีม", "ช่าง", "สิทธิ์", "รายงาน", "เมนู", "คำสั่ง", "ฟังก์ชัน", "สินค้า", "ของ",
    "อะไร", "ทั้งหมด", "ประกัน", "ใคร", "เตือน", "บันทึก", "โน้ต", "อนุมัติ", "ร้าน", "บริษัท", "ตั้งค่า",
    "deal", "customer", "job", "ticket", "quote", "team", "product", "products", "everything", "all", "warranty",
    "reminder", "note", "report", "shop", "menu",
)


def _product_search_term(message: str) -> str | None:
    """"พัดลม" out of "มีสินค้าอะไรบ้างที่เป็น พัดลม" / "ค้นหาสินค้า พัดลม" /
    "มีพัดลมอะไรบ้าง" — a catalogue search by name, which used to reach
    the model and come back "ในแชทยังทำรายการนี้ไม่ได้" (owner test, 8 Sep 2026)."""
    text = " ".join((message or "").split())
    if not text or len(text) > 60 or re.search(r"(?:SR|[CDQT])-\d{4}-\d{4}", text, re.I):
        return None
    for rx in _PRODUCT_SEARCH_RES:
        m = rx.match(text)
        if not m:
            continue
        term = _strip_polite_tail(m.group(1).strip(" :?\"'"))
        term = re.sub(r"^(?:ที่ชื่อ|ที่เป็น|ชื่อว่า|ชื่อ|ว่า|ที่)\s*", "", term).strip()
        low = term.lower()
        if not term or len(term) > 40 or re.search(r"\d{5,}", term):
            return None
        if low in _PRODUCT_SEARCH_STOP or any(low.startswith(w) or low.endswith(w) for w in _PRODUCT_SEARCH_STOP):
            return None
        return term
    return None


PRODUCT_SEARCH_NONE = {
    "th": "ไม่พบสินค้าที่ตรงกับ \"{query}\" ในรายการสินค้า",
    "en": 'No product matching "{query}" in the catalogue.',
}


async def _handle_product_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str, query: str | None = None,
) -> ChatReply:
    """The catalogue — all of it, or the products matching a name
    ("มีสินค้าอะไรบ้างที่เป็น พัดลม", "ค้นหาสินค้า พัดลม")."""
    if not set(permission_keys) & PRODUCT_VIEW_KEYS:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        products = await client.list_products(str(license_id))
    except Exception:
        log.exception("product list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    query = (query or "").strip()
    if query and products:
        needle = query.lower()
        products = [
            p for p in products
            if needle in str(p.get("product_name") or "").lower()
            or needle in str(p.get("product_id") or "").lower()
            or needle in str(p.get("sku") or "").lower()
            or needle in str(p.get("category") or "").lower()
        ]
        if not products:
            return ChatReply(
                text=_t(PRODUCT_SEARCH_NONE, language).format(query=query),
                quick_replies=[("รายการสินค้า", "รายการสินค้า"), ("เพิ่มสินค้า", "สร้างสินค้า")],
            )

    if not products:
        return ChatReply(
            text=_t(EMPTY_LIST, language).format(what="สินค้า" if language == "th" else "products"),
            quick_replies=[("เพิ่มสินค้า", "สร้างสินค้า")],
        )

    shown = products[:LIST_LIMIT]
    lines = []
    for p in shown:
        price = p.get("unit_price")
        price_text = f" · {Decimal(str(price)):,.2f}" if price not in (None, "") else ""
        lines.append(f"{p.get('sku') or p.get('product_id') or '-'} · {p.get('product_name') or p.get('name') or '-'}{price_text}")
    text = "\n".join(lines) + _truncation_note(len(shown), len(products), language, "products")
    return ChatReply(
        text=text,
        quick_replies=[("รายการดีล", "รายการดีล")],
        quick_reply_url=_dashboard_button("products", language),
        list_card=_list_card(
            title=f"สินค้า: {query}" if query else "สินค้า", section="products", language=language,
            shown=len(shown), total=len(products),
            rows=[
                {
                    # ProductOut names it product_name; "name" was never a
                    # key, so every row read "-" (owner test, 8 Sep 2026).
                    "title": str(p.get("product_name") or p.get("name") or "-"),
                    "subtitle": " · ".join(
                        x for x in (
                            str(p.get("sku") or p.get("product_id") or ""),
                            f"{Decimal(str(p['unit_price'])):,.2f}"
                            if p.get("unit_price") not in (None, "") else "",
                        ) if x
                    ),
                }
                for p in shown
            ],
        ),
    )


async def _handle_quote_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    if "quote.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        quotes = await client.list_quotes(str(license_id))
    except Exception:
        log.exception("quote list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not quotes:
        return ChatReply(
            text=_t(EMPTY_LIST, language).format(
                what="ใบเสนอราคา" if language == "th" else "quotes"
            ),
            quick_replies=[("รายการดีล", "รายการดีล")],
        )

    shown = quotes[:LIST_LIMIT]
    lines = [
        f"{q.get('quote_id') or '-'} · {_label(QUOTE_STATUS_LABELS, q.get('status'), language)}"
        + (" · มีเอกสารแล้ว" if q.get("generated_document_id") else "")
        for q in shown
    ]
    text = "\n".join(lines) + _truncation_note(len(shown), len(quotes), language, "quotes")
    return ChatReply(
        text=text,
        quick_replies=[("รายการดีล", "รายการดีล")],
        quick_reply_url=_dashboard_button("quotes", language),
        list_card=_list_card(
            title="ใบเสนอราคา", section="quotes", language=language,
            shown=len(shown), total=len(quotes),
            rows=[
                {
                    "title": str(q.get("quote_id") or "-"),
                    "subtitle": _label(QUOTE_STATUS_LABELS, q.get("status"), language)
                    + (" · มีเอกสารแล้ว" if q.get("generated_document_id") else ""),
                    "stage": q.get("status"),
                    # Issuing is the action a quote list exists for, and it
                    # is per-row for the same reason viewing is.
                    "action_label": "ออกเอกสาร",
                    "action_text": f"ออกเอกสาร {q.get('quote_id')}",
                }
                for q in shown
            ],
        ),
    )


DEAL_STAGE_UPDATED = {
    "th": "อัปเดตดีล {deal_id} เป็นสถานะ {stage} เรียบร้อยแล้ว",
    "en": "Deal {deal_id} is now {stage}.",
}
DEAL_STAGE_NOT_FOUND = {
    "th": "ไม่พบดีลรหัส {deal_id} ในบริษัทนี้",
    "en": "No deal {deal_id} was found in this company.",
}
DEAL_STAGE_ILLEGAL = {
    "th": "ไม่สามารถเปลี่ยนสถานะดีล {deal_id} ได้ในตอนนี้",
    "en": "Deal {deal_id} cannot move to that stage right now.",
}
DEAL_REOPEN_DENIED = {
    "th": "การเปิดดีลที่ปิดแล้วใหม่ต้องมีสิทธิ์ deal.reopen",
    "en": "Reopening a closed deal requires deal.reopen permission",
}


async def _handle_deal_stage_command(
    client: DataClient, *, license_id, deal_code: str, target_stage: str,
    permission_keys: list[str], language: str, actor_id: str,
    message: str = "",
) -> ChatReply:
    license_id = str(license_id)
    deals = await client.list_deals(license_id)
    match = next((d for d in deals if d["deal_id"].upper() == deal_code), None)
    if match is None:
        return ChatReply(text=_t(DEAL_STAGE_NOT_FOUND, language).format(deal_id=deal_code))

    allow_reopen = "deal.reopen" in set(permission_keys)
    if match["stage"] in ("won", "lost") and target_stage == "new" and not allow_reopen:
        return ChatReply(text=_t(DEAL_REOPEN_DENIED, language))

    # "ปิดไม่สำเร็จ D-2026-0001 เพราะราคาสูงไป" — the reason, when one is
    # given in the same breath. Recorded so the shop can see later that it
    # loses on price and not on response time; never demanded.
    lost_reason = None
    if target_stage == "lost":
        reason_match = re.search(
            r"(?:เพราะ|เนื่องจาก|เหตุผล[:：]?|because|reason[:：]?)\s*(.+)$",
            message or "", re.I,
        )
        if reason_match:
            lost_reason = reason_match.group(1).strip()[:500]

    try:
        row = await client.transition_deal_stage(
            license_id, match["id"], target_stage,
            allow_reopen=allow_reopen, actor_id=actor_id, lost_reason=lost_reason,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_conflict(exc):
            return ChatReply(text=_t(DEAL_STAGE_ILLEGAL, language).format(deal_id=deal_code))
        if _is_not_found(exc):
            return ChatReply(text=_t(DEAL_STAGE_NOT_FOUND, language).format(deal_id=deal_code))
        raise
    await _notify_deal_owner(client, license_id, row, actor_id=actor_id, language=language)
    return ChatReply(
        text=_t(DEAL_STAGE_UPDATED, language).format(deal_id=row["deal_id"], stage=_label(DEAL_STAGE_LABELS, row["stage"], language)),
        entity_type="deal", entity_id=row["id"],
    )


async def _notify_deal_owner(client: DataClient, license_id: str, deal: dict, *, actor_id: str, language: str) -> None:
    """Spec §9: a stage change is a notification to the deal's owner — when
    someone else moved it. Best-effort; the transition already stands."""
    import uuid as _uuid

    owner_ref = str(deal.get("owner_member_id") or "")
    if not owner_ref:
        return
    try:
        members = await client.list_members(license_id)
        owner = next((m for m in members if str(m.get("id")) == owner_ref), None)
        uid = str((owner or {}).get("chann_uid") or "")
        if not uid or uid == str(actor_id):
            return
        try:
            entity_id: str | None = str(_uuid.UUID(str(deal.get("id"))))
        except (ValueError, TypeError):
            entity_id = None
        code = str(deal.get("deal_id") or "")
        stage = str(deal.get("stage") or "")
        await send_notification(
            client, license_id=license_id, target_chann_uid=uid,
            target_line_user_id=await client.line_target_of(uid),
            type="deal_stage_changed",
            message=f"ดีล {code} เปลี่ยนสถานะเป็น {stage}",
            message_en=f"Deal {code} moved to {stage}",
            entity_type="deal", entity_id=entity_id, oa="sales",
        )
    except Exception:  # noqa: BLE001
        log.exception("could not tell the deal owner about a stage change")


def required_permission(action: str, entity: str | None) -> str | None:
    """The permission key an intent needs, or None if the system cannot do it.

    None means two different things that deliberately get the same treatment:
    an unknown entity, and a known entity with an action it does not support.
    Neither is executable, so neither should be answered with "coming soon".
    """
    if not entity:
        return None
    act = ACTION_ALIASES.get((action or "").strip().lower(), (action or "").strip().lower())
    return ACTION_PERMISSIONS.get((act, str(entity).strip().lower()))

log = logging.getLogger(__name__)

# Thailand does not observe daylight saving, so a fixed offset is exact
# rather than an approximation. Reminders are parsed against the tenant's
# own day: at 23:00 in Bangkok, UTC is still yesterday, and "พรุ่งนี้"
# would otherwise land on today.
BANGKOK_TZ = timezone(timedelta(hours=7))

# Cap on how many capabilities a "what can I do" reply lists. A member with a
# broad role can hold 40+ permissions, and a LINE bubble that long is unusable.
SUGGEST_LIMIT = 8
# When the request names a group, that group is shown in full — capped
# separately so a role with a huge group (owner, ~10 keys in one group) does
# not itself blow past a reasonable message length.
SUGGEST_GROUP_LIMIT = 10
# How many OTHER groups to show after the priority one, so a broad role like
# owner still gets something more useful than one enormous flat list.
SUGGEST_OTHER_GROUPS = 2

REPLY_NO_SOURCE_MESSAGE = {
    "th": "ไม่พบข้อความต้นฉบับที่ตอบกลับ",
    "en": "Could not find the original message you replied to",
}

REPLY_NOT_REGISTERED = {
    "th": "ยังไม่พบบริษัทที่ผูกไว้ กรุณาลงทะเบียน",
    "en": "No company is linked to this account yet — please register",
}

REPLY_CHOOSE_TENANT = {
    "th": "บัญชีนี้อยู่กับหลายร้าน กดเลือกร้านที่ต้องการคุยด้วยได้เลย (เปลี่ยนทีหลังได้ด้วย \"เปลี่ยนร้าน\"):\n{names}",
    "en": "This account belongs to several shops — pick the one to talk to (change later with \"switch shop\"):\n{names}",
}
SWITCH_TENANT_PHRASES = (
    "เปลี่ยนร้าน", "สลับร้าน", "เปลี่ยนบริษัท", "สลับบริษัท", "เลือกร้าน", "เลือกบริษัท",
    "switch shop", "switch company", "change shop", "change company",
)
_USE_TENANT_PREFIXES = ("ใช้ร้าน", "ใช้บริษัท", "เลือกร้าน", "เลือกบริษัท", "ร้าน", "บริษัท", "use shop", "use ")
TENANT_SWITCHED = {
    "th": "ตอนนี้คุยในนาม {name} แล้วครับ",
    "en": "Now talking as {name}.",
}
HELD_UNTIL_SHOP_CHOSEN = {
    "th": "รับเรื่องไว้แล้วครับ เลือกร้านก่อน แล้วผมจะแจ้งซ่อมให้ที่ร้านนั้นทันที",
    "en": "Noted — pick the shop first and I will file it there right away.",
}


def _membership_named(
    message: str, memberships: list[dict], *, explicit_only: bool = False,
) -> dict | None:
    """The membership this message names — by company name, licence
    code, or list number ("2"). explicit_only: only "ใช้ร้าน X" forms and
    codes count, so an ordinary sentence that happens to contain a shop's
    name does not switch companies mid-conversation."""
    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()
    stripped = lowered
    explicit = False
    for prefix in _USE_TENANT_PREFIXES:
        if stripped.startswith(prefix) and len(stripped) > len(prefix):
            stripped = stripped[len(prefix):].strip()
            explicit = True
            break
    if not explicit_only and stripped.isdigit():
        index = int(stripped) - 1
        if 0 <= index < len(memberships):
            return memberships[index]
    for m in memberships:
        code = str(m.get("license_code") or "").lower()
        name = str(m.get("company_name") or "").lower()
        if code and stripped == code:
            return m
        if not name:
            continue
        if stripped == name or (explicit and (name in stripped or stripped in name)):
            return m
        if not explicit_only and not explicit and (name in lowered):
            return m
    return None


def _tenant_chooser(ctx: ResolvedContext, language: str, *, include_current: bool = False) -> ChatReply:
    options = list(ctx.memberships) + (list(ctx.alternatives) if include_current else [])
    if not include_current and ctx.resolution is not TenantResolution.MULTIPLE:
        options = list(ctx.memberships) + list(ctx.alternatives)
    names = "\n".join(
        f"{i}. {m.get('company_name') or m.get('license_code') or '—'}"
        for i, m in enumerate(options, start=1)
    )
    return ChatReply(
        text=_t(REPLY_CHOOSE_TENANT, language).format(names=names),
        quick_replies=[
            (str(m.get("company_name") or m.get("license_code") or "")[:20],
             f"ใช้ร้าน {m.get('license_code') or m.get('company_name')}")
            for m in options[:13]
        ],
    )


async def _switch_tenant(
    client: DataClient, *, ctx: ResolvedContext, membership: dict, language: str,
) -> ChatReply:
    try:
        await client.set_active_tenant(ctx.chann_uid, ctx.oa, str(membership["license_id"]))
    except Exception:
        log.exception("could not store the active tenant")
        return ChatReply(text=unavailable_reply(language))
    name = membership.get("company_name") or membership.get("license_code") or ""
    if ctx.oa == "customer":
        try:
            pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
        except Exception:
            pending = None
        held = str(((pending or {}).get("fields") or {}).get("message") or "")
        if held and (pending or {}).get("entity") == "pending_customer_message" and membership.get("license_id"):
            reply = await _handle_customer_report(
                client, ctx=ctx, license_id=str(membership["license_id"]), message=held, language=language,
            )
            reply.text = _t(TENANT_SWITCHED, language).format(name=name) + "\n\n" + reply.text
            return reply
    quick = (
        [("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน"), ("ข้อมูลของฉัน", "ข้อมูลของฉัน")]
        if ctx.oa == "customer"
        else [("งานของฉัน", "งานของฉัน"), ("งานที่เปิดรับ", "งานที่เปิดรับ")]
        if ctx.oa == "technician"
        else [("งานวันนี้", "งานวันนี้"), ("รายชื่อลูกค้า", "รายชื่อลูกค้า"), ("วิธีใช้", "วิธีใช้")]
    )
    return ChatReply(text=_t(TENANT_SWITCHED, language).format(name=name), quick_replies=quick)

ASK_MISSING = {
    "th": "กรุณาระบุ{fields}",
    "en": "Please provide {fields}",
}
# What to say instead of a field name the build never saw — the model is
# free to invent one, and a raw key is not a sentence.
ASK_MISSING_REST = {
    "th": "รายละเอียดที่เหลือ",
    "en": "the remaining details",
}

SUGGEST_HEADER = {
    "th": (
        "ยังไม่แน่ใจว่าต้องการอะไรครับ ลองพิมพ์ให้ชัดขึ้น เช่น \"ดูลูกค้า สมชาย\" \"งานวันนี้\" "
        "\"รายชื่อช่าง\" \"ข้อมูลร้าน\" หรือพิมพ์ \"วิธีใช้\" เพื่อเปิดคู่มือ"
    ),
    "en": (
        "Not sure what you need — try something more specific, e.g. \"show customer Somchai\", "
        "\"today\", \"technicians\", \"shop info\" — or type \"help\" for the guide"
    ),
}
# The technician OA's examples are its own — "ดูลูกค้า สมชาย" and "รายชื่อช่าง"
# do nothing there (review, 6 Sep 2026).
SUGGEST_HEADER_BY_OA = {
    "sales": SUGGEST_HEADER,
    "technician": {
        "th": (
            "ยังไม่แน่ใจว่าต้องการอะไรครับ ลองพิมพ์ให้ชัดขึ้น เช่น \"งานของฉัน\" \"งานที่เปิดรับ\" "
            "\"เช็คอิน\" \"ปิดงาน\" หรือพิมพ์ \"วิธีใช้\" เพื่อเปิดคู่มือ"
        ),
        "en": (
            "Not sure what you need — try something more specific, e.g. \"my jobs\", \"open jobs\", "
            "\"check in\", \"check out\" — or type \"help\" for the guide"
        ),
    },
    "customer": {
        "th": "ยังไม่แน่ใจว่าต้องการอะไรครับ แจ้งซ่อมได้เลย พิมพ์อาการ เช่น \"แอร์ไม่เย็น\" หรือพิมพ์ \"งานของฉัน\"",
        "en": "Not sure what you need — describe a fault, e.g. \"air con not cooling\", or type \"my jobs\".",
    },
}
SUGGEST_HELD_UNHANDLED = {
    "th": "เข้าใจว่าต้องการทำอะไรครับ แต่ยังทำจากประโยคนี้ไม่ได้ ลองพิมพ์ให้ตรงรูปแบบ เช่น {example}",
    "en": "I understand what you want, but not from that sentence — try the form the system knows, e.g. {example}",
}
SUGGEST_WRONG_OA = {
    "th": "คำสั่งนี้ใช้ในไลน์ฝ่ายขาย/แอดมินของร้านครับ ไม่ได้เปิดในช่องทางนี้",
    "en": "That command lives on the shop's sales/admin LINE, not on this channel.",
}
_SUGGEST_EXAMPLES: dict[str, dict[str, tuple[str, ...]]] = {
    "sales": {
        "ticket": ("ข้อมูลงาน T-2026-0001", "มอบหมาย T-2026-0001 ให้ สมชาย", "รายการงาน"),
        "service_report": ("รายงานของฉัน", "ออกรายงาน SR-2026-0001"),
        "followup": ("เตือน C-2026-0001 พรุ่งนี้ 10 โมง", "นัดหมาย"),
        "warranty": ("เช็คประกัน SN12345678", "รายการประกัน"),
        "approval": ("รายการรออนุมัติ", "อนุมัติ SR-2026-0001"),
        "customer": ("ค้นหาลูกค้า สมชาย", "ข้อมูลลูกค้า C-2026-0001"),
        "deal": ("ข้อมูลดีล D-2026-0001", "ปิดสำเร็จ D-2026-0001"),
        "quote": ("ออกเอกสาร Q-2026-0001", "ส่วนลด Q-2026-0001 10%"),
        "product": ("รายการสินค้า", "สร้างสินค้า พัดลม ราคา 1200"),
        "note": ("บันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด", "ดูบันทึก C-2026-0001"),
    },
    "technician": {
        "ticket": ("รับงาน T-2026-0001", "ข้อมูลงาน T-2026-0001", "งานของฉัน"),
        "service_report": ("เช็คอิน T-2026-0001", "ปิดงาน T-2026-0001", "รายงานของฉัน"),
        "warranty": ("เช็คประกัน SN12345678",),
    },
}


_SUGGEST_EXAMPLES_EN: dict[str, dict[str, tuple[str, ...]]] = {
    "sales": {
        "ticket": ("ticket T-2026-0001", "assign T-2026-0001 to Somchai", "tickets"),
        "service_report": ("my reports", "report pdf SR-2026-0001"),
        "followup": ("remind C-2026-0001 tomorrow 10am", "my reminders"),
        "warranty": ("check warranty SN12345678", "warranties"),
        "approval": ("pending approvals", "approve SR-2026-0001"),
        "customer": ("find customer Somchai", "customer detail C-2026-0001"),
        "deal": ("deal detail D-2026-0001", "won D-2026-0001"),
        "quote": ("issue quote Q-2026-0001", "discount Q-2026-0001 10%"),
        "product": ("product list", "create product Fan price 1200"),
        "note": ("note C-2026-0001 asked for a discount", "notes C-2026-0001"),
    },
    "technician": {
        "ticket": ("claim T-2026-0001", "ticket T-2026-0001", "my jobs"),
        "service_report": ("check in T-2026-0001", "check out T-2026-0001", "my reports"),
        "warranty": ("check warranty SN12345678",),
    },
}


def _suggest_example(oa: str, entity: str, language: str) -> str:
    tables = _SUGGEST_EXAMPLES_EN if language == "en" else _SUGGEST_EXAMPLES
    table = tables.get(oa) or tables["sales"]
    examples = table.get(entity) or tables["sales"].get(entity) or (("help",) if language == "en" else ("วิธีใช้",))
    return " · ".join(f"\"{e}\"" for e in examples)


# Owner (4 Sep 2026): a reply that lists "things you can do" is not an answer
# anyone can act on — the guide is. Every refusal and every "what can I do"
# points at the guide instead.
GUIDE_POINTER = {
    "th": "ดูวิธีใช้ทั้งหมด: พิมพ์ \"วิธีใช้\" หรือกดปุ่มเปิดคู่มือ",
    "en": "See how everything works: type \"help\" or open the guide",
}

# Two different reasons land here, and users need to hear the right one:
# not knowing a feature exists reads very differently from being denied it.
SUGGEST_NO_PERMISSION_LEAD = {
    "th": (
        "⛔ คุณยังไม่มีสิทธิ์ทำสิ่งนี้\n"
        "ขอสิทธิ์ได้จากเจ้าของร้านหรือแอดมิน (แดชบอร์ด > บทบาทและทีม)\n"
        "พิมพ์ \"วิธีใช้\" เพื่อเปิดคู่มือการใช้งาน"
    ),
    "en": (
        "⛔ You do not have permission for that\n"
        "Ask the shop owner or an admin (dashboard > roles and team)\n"
        "Type \"help\" to open the guide"
    ),
}
SUGGEST_NO_PERMISSION_NAMED = {
    "th": "⛔ คุณยังไม่มีสิทธิ์ทำสิ่งนี้ — ต้องมีสิทธิ์ «{needed}»\nขอได้จากเจ้าของร้านหรือแอดมิน (แดชบอร์ด > บทบาทและทีม)",
    "en": "⛔ Not allowed — this needs «{needed}»\nAsk the owner or an admin (dashboard > roles and team)",
}
SUGGEST_UNKNOWN_FEATURE_LEAD = {
    "th": "ระบบยังไม่มีฟังก์ชันนี้ครับ",
    "en": "That is not a feature yet.",
}

# Thai/English group headers, keyed to the catalogue's "group" field
# (permission_key.split(".", 1)[0], or "general" for a dotless key).
GROUP_LABELS: dict[str, dict[str, str]] = {
    "customer": {"th": "ลูกค้า", "en": "Customers"},
    "deal": {"th": "ดีล", "en": "Deals"},
    "note": {"th": "บันทึก", "en": "Notes"},
    "followup": {"th": "การติดตาม", "en": "Follow-ups"},
    "product": {"th": "สินค้า", "en": "Products"},
    "team": {"th": "ทีมและกลุ่ม", "en": "Teams & groups"},
    "assignment_rule": {"th": "กฎการมอบหมายงาน", "en": "Assignment rules"},
    "ticket": {"th": "ใบงาน", "en": "Tickets"},
    "quote": {"th": "ใบเสนอราคา", "en": "Quotes"},
    "service_report": {"th": "รายงานบริการ", "en": "Service reports"},
    "approval": {"th": "การอนุมัติ", "en": "Approvals"},
    "chat_session": {"th": "ห้องแชท", "en": "Chat sessions"},
    "role": {"th": "บทบาทและสิทธิ์", "en": "Roles & permissions"},
    "member": {"th": "สมาชิก", "en": "Members"},
    "setting": {"th": "การตั้งค่า", "en": "Settings"},
    "warranty": {"th": "ใบรับประกัน", "en": "Warranties"},
    "audit_log": {"th": "ประวัติการใช้งาน", "en": "Audit log"},
    "pdpa": {"th": "คำขอ PDPA", "en": "PDPA requests"},
    "billing": {"th": "การเรียกเก็บเงิน", "en": "Billing"},
    "general": {"th": "ทั่วไป", "en": "General"},
}


def _group_label(group: str, language: str) -> str:
    entry = GROUP_LABELS.get(group)
    if entry is None:
        return group
    return entry.get(language) or entry["th"]

SUGGEST_NOTHING = {
    "th": "ตอนนี้บัญชีของคุณยังไม่มีสิทธิ์ใช้งานใด ๆ กรุณาติดต่อผู้ดูแลบริษัท",
    "en": "Your account has no permissions yet — please contact your company admin",
}

NOT_UNDERSTOOD = {
    "th": "ขออภัย ไม่เข้าใจคำสั่ง ลองพิมพ์ให้ชัดขึ้น หรือพิมพ์ \"วิธีใช้\" เพื่อเปิดคู่มือ",
    "en": 'Sorry, I did not understand. Try again more specifically, or type "help" for the guide',
}


# Phase 16.5 — a person's rights over their own data, on every OA.
PDPA_EXPORT_PHRASES = (
    "ขอข้อมูลของฉัน", "ขอสำเนาข้อมูล", "ขอสำเนาข้อมูลของฉัน", "ขอข้อมูลส่วนตัว", "ดาวน์โหลดข้อมูลของฉัน",
    "my data", "export my data", "copy of my data", "download my data",
)
PDPA_ERASE_PHRASES = (
    "ขอลบข้อมูล", "ลบข้อมูลของฉัน", "ขอลบข้อมูลของฉัน", "ลบข้อมูลส่วนตัว", "ขอให้ลืมฉัน",
    "delete my data", "erase my data", "forget me",
)
PDPA_ERASE_CONFIRM_PHRASES = ("ยืนยันลบข้อมูล", "confirm delete my data", "confirm erase my data")

TENANT_SUSPENDED = {
    "th": "ร้าน {company} ถูกระงับการใช้งานชั่วคราวโดยผู้ดูแลระบบ ยังดูข้อมูลเดิมได้จากหน้าจอ แต่ทำรายการใหม่ไม่ได้ ติดต่อ Chann CRM AI เพื่อเปิดใช้งานอีกครั้ง",
    "en": "{company} is suspended by the platform operator. Existing records stay readable on the dashboard, but nothing new can be done. Contact Chann CRM AI to reopen.",
}



# Phase 17 — ad-hoc reports in plain language. A message that starts with
# one of these (and is not one of the fixed report commands above) goes to
# the AI report engine; the model returns a whitelisted spec, never SQL.
AI_REPORT_TRIGGERS = (
    "รายงาน", "ขอรายงาน", "สรุป", "ขอสรุป", "ดูยอด", "นับ", "มีกี่", "จำนวน", "สถิติ",
    "report ", "how many ", "summary of ", "summarize ", "count ",
)
AI_REPORT_UNAVAILABLE = {
    "th": "ตอนนี้สร้างรายงานด้วย AI ไม่ได้ครับ ลองใหม่อีกครั้ง หรือเปิด \"รายงาน AI\" บนหน้าจอ",
    "en": "AI reports are not available right now — try again, or open \"AI reports\" on the dashboard.",
}

# Phase 17's second output shape — ตาราง/กราฟ. The owner asked for it by
# name on 8 Sep 2026 ("อยากดู report ยอดขายเป็นกราฟ"): the chat could say
# the numbers and could hand over a CSV, but a picture of them was the one
# thing the spec listed and the chat could not do. LINE has no table and no
# chart component, so the chart is a PNG this tier draws and sends as an
# image message ahead of the summary (services/charts.py, sales_charts.py).
CHART_WORDS = ("กราฟ", "แผนภูมิ", "ชาร์ต", "chart", "graph")
# Which of the four sales pictures they meant. Checked in this order:
# "รายคน" and "ขายดี" name a chart outright, "เดือน" names a period, and a
# request that names none of them is the pipeline — the same numbers the
# text "สรุปการขาย" already answers with, so the button on that reply and
# a bare "ขอกราฟยอดขาย" land on the same picture.
CHART_OWNER_WORDS = ("รายคน", "แต่ละคน", "ต่อคน", "รายบุคคล", "แยกตามคน", "แยกตามพนักงาน", "แยกตามเซล",
                     "ของพนักงาน", "by person", "per person", "by owner", "by rep", "by salesperson")
CHART_PRODUCT_WORDS = ("สินค้าขายดี", "ขายดี", "สินค้า", "รุ่นไหนขายดี", "top product", "best seller",
                       "best-selling", "best selling", "product")
CHART_MONTHLY_WORDS = ("รายเดือน", "แต่ละเดือน", "ต่อเดือน", "เดือน", "monthly", "by month", "per month", "month")
_CHART_MONTHS_RE = re.compile(r"(\d{1,2})\s*(?:เดือน|months?\b)")
_CHART_TOP_RE = re.compile(r"(?:top|อันดับ)\s*(\d{1,2})|(\d{1,2})\s*อันดับ", re.I)

CHART_AS_CHART_BUTTON = {"th": "ดูเป็นกราฟ", "en": "View as chart"}
CHART_AS_CHART_SAYS = {"th": "ขอกราฟยอดขาย", "en": "sales chart"}
CHART_OTHER_BUTTONS = (
    ("monthly", {"th": "กราฟรายเดือน", "en": "By month"}, {"th": "กราฟยอดขายรายเดือน", "en": "monthly sales chart"}),
    ("products", {"th": "สินค้าขายดี", "en": "Best sellers"}, {"th": "กราฟสินค้าขายดี", "en": "top products chart"}),
    ("owner", {"th": "ยอดขายรายคน", "en": "By person"}, {"th": "กราฟยอดขายรายคน", "en": "sales chart per person"}),
    ("pipeline", {"th": "ดีลแต่ละสถานะ", "en": "By stage"}, {"th": "กราฟดีลแต่ละสถานะ", "en": "deals by stage chart"}),
)
# Storage is not configured in dev and can fail in production. The numbers
# are the answer; the picture is an extra, and its absence is said plainly
# rather than swallowed or turned into an apology for the whole request.
CHART_TEXT_ONLY = {
    "th": "\n(ยังส่งรูปกราฟไม่ได้ตอนนี้ — ที่เก็บไฟล์ยังไม่พร้อม ตัวเลขด้านบนถูกต้องครับ)",
    "en": "\n(The chart picture cannot be sent right now — file storage is not ready. The numbers above are correct.)",
}




# ============================================================ user review, 4 Sep 2026
# Issue 2 — a duplicate customer is a conversation, not a dead end.
DUPLICATE_FOUND = {
    "th": "มีลูกค้าคนนี้อยู่แล้ว: {name} ({code}) — {field} {value} ตรงกัน\nจะทำอย่างไรต่อ?\n1 ใช้รายชื่อเดิม · 2 อัปเดตข้อมูลเดิมด้วยข้อมูลใหม่ · 3 ยกเลิก",
    "en": "This customer already exists: {name} ({code}) — same {field} {value}\nWhat next?\n1 use the existing record · 2 update it with the new details · 3 cancel",
}
DUPLICATE_FIELD_LABEL = {"phone": {"th": "เบอร์", "en": "phone"}, "email": {"th": "อีเมล", "en": "email"}}
DUPLICATE_USE_PHRASES = ("ใช้รายชื่อเดิม", "ใช้ของเดิม", "ใช้อันเดิม", "use existing", "use the existing record", "1")
DUPLICATE_MERGE_PHRASES = ("อัปเดตข้อมูลเดิม", "อัปเดตของเดิม", "รวมข้อมูล", "merge", "update existing", "update the existing record", "2")
DUPLICATE_CANCEL_PHRASES = ("ยกเลิก", "cancel", "ไม่ต้อง", "3")
DUPLICATE_USED = {
    "th": "ใช้รายชื่อเดิม {name} ({code}) ต่อได้เลย ไม่ได้สร้างรายชื่อใหม่",
    "en": "Using the existing record {name} ({code}). Nothing new was created.",
}
DUPLICATE_MERGED = {
    "th": "อัปเดต {name} ({code}) แล้ว: {fields}",
    "en": "Updated {name} ({code}): {fields}",
}
DUPLICATE_NOTHING_TO_MERGE = {
    "th": "{name} ({code}) มีข้อมูลครบเหมือนที่ให้มาอยู่แล้ว ไม่มีอะไรต้องอัปเดต",
    "en": "{name} ({code}) already has exactly these details — nothing to update.",
}
DUPLICATE_CONFLICTS = {
    "th": "{name} ({code}) มีข้อมูลต่างจากที่ให้มา:\n{lines}\nจะแทนที่ด้วยข้อมูลใหม่ไหม? (ข้อมูลที่ว่างอยู่เติมให้แล้ว)",
    "en": "{name} ({code}) has different details on file:\n{lines}\nReplace them with the new values? (Empty fields were filled in already.)",
}
MERGE_REPLACE_PHRASES = ("แทนที่ทั้งหมด", "แทนที่", "replace", "ใช้ข้อมูลใหม่", "yes")
MERGE_KEEP_PHRASES = ("เก็บของเดิม", "เก็บเดิม", "keep", "ไม่แทนที่", "no")
MERGE_REPLACED = {"th": "แทนที่ข้อมูลของ {name} แล้ว: {fields}", "en": "Replaced on {name}: {fields}"}
MERGE_KEPT = {"th": "เก็บข้อมูลเดิมของ {name} ไว้ ไม่ได้เปลี่ยนอะไร", "en": "Kept {name}'s existing details unchanged."}
DUPLICATE_CANCELLED = {"th": "ยกเลิกแล้ว ไม่ได้สร้างรายชื่อใหม่", "en": "Cancelled — no new record was created."}
DUPLICATE_CHOICE_INVALID = {
    "th": "ตอบ 1 ใช้รายชื่อเดิม · 2 อัปเดตข้อมูลเดิม · 3 ยกเลิก",
    "en": "Reply 1 to use the existing record, 2 to update it, or 3 to cancel.",
}
CUSTOMER_FIELD_LABEL = {
    "first_name": {"th": "ชื่อ", "en": "first name"}, "last_name": {"th": "นามสกุล", "en": "last name"},
    "phone": {"th": "เบอร์", "en": "phone"}, "email": {"th": "อีเมล", "en": "email"},
    "address": {"th": "ที่อยู่", "en": "address"}, "notes": {"th": "บันทึก", "en": "notes"},
}
DUPLICATE_TTL_S = 900

# Issue 3 — a lead can be deleted: the platform's soft delete (archive),
# behind customer.archive and a confirmation.
LEAD_DELETE_TRIGGERS = (
    "ลบ lead", "ลบ Lead", "ลบลีด", "ลบลูกค้า", "ลบรายชื่อ", "เก็บถาวรลูกค้า", "เก็บถาวร lead", "ลบออกจาก lead",
    "delete lead", "archive lead", "remove lead", "delete customer", "archive customer",
)
LEAD_DELETE_NAME_STRIP = ("นี้", "รายนี้", "คนนี้", "ออกจาก lead", "ออกจากlead", "ออก", "ทิ้ง", "this lead", "this customer", "this")
ARCHIVE_CONFIRM = {
    "th": "ลบ {name} ({code}, สถานะ {stage}) ออกจากรายชื่อ? ข้อมูลจะถูกเก็บถาวร ไม่แสดงในรายชื่ออีก แต่ประวัติงาน/ดีลยังอยู่\nพิมพ์ \"ยืนยันลบ\" หรือ \"ยกเลิก\"",
    "en": "Remove {name} ({code}, stage {stage}) from the list? The record is archived — gone from every list, history kept.\nReply \"confirm delete\" or \"cancel\".",
}
ARCHIVE_CONFIRM_PHRASES = ("ยืนยันลบ", "ยืนยัน", "confirm delete", "confirm", "yes")
ARCHIVE_DONE = {"th": "ลบ {name} ({code}) ออกจากรายชื่อแล้ว (เก็บถาวร)", "en": "Removed {name} ({code}) from the list (archived)."}
ARCHIVE_CANCELLED = {"th": "ยกเลิกแล้ว {name} ยังอยู่ในรายชื่อ", "en": "Cancelled — {name} stays on the list."}
ARCHIVE_NEEDS_NAME = {
    "th": "ลบใครครับ พิมพ์ \"ลบ Lead สมชาย\" หรือเปิดดูลูกค้าก่อนแล้วพิมพ์ \"ลบ Lead นี้\"",
    "en": "Delete whom? Type \"delete lead Somchai\", or open a customer first and say \"delete this lead\".",
}
ARCHIVE_CHOICE_INVALID = {"th": "พิมพ์ \"ยืนยันลบ\" เพื่อลบ หรือ \"ยกเลิก\"", "en": "Reply \"confirm delete\" or \"cancel\"."}
STAGE_LABEL = {"lead": {"th": "Lead", "en": "lead"}, "contact": {"th": "ลูกค้า", "en": "contact"}}

# Issue 3b — inactive-lead cleanup, off by default, per tenant.
LEAD_CLEANUP_SET_PHRASES = (
    "ตั้งค่าลบ lead อัตโนมัติ", "ตั้งค่าลบลีดอัตโนมัติ", "ตั้งค่าเก็บถาวร lead", "ลบ lead ที่ไม่มีการเคลื่อนไหวเกิน",
    "ลบลีดที่ไม่มีการเคลื่อนไหวเกิน", "auto archive leads after", "auto-archive leads after",
)
LEAD_CLEANUP_OFF_PHRASES = ("ปิดการลบ lead อัตโนมัติ", "ปิดลบ lead อัตโนมัติ", "ยกเลิกลบ lead อัตโนมัติ", "turn off lead cleanup")
LEAD_CLEANUP_VIEW_PHRASES = ("การตั้งค่าลบ lead", "ดูการตั้งค่าลบ lead", "ตั้งค่าลบ lead", "lead cleanup setting")
LEAD_CLEANUP_STATE = {
    "th": "ลบ Lead ที่ไม่มีการเคลื่อนไหวอัตโนมัติ: {state}\nตั้งได้ด้วย \"ตั้งค่าลบ lead อัตโนมัติ 90 วัน\" หรือปิดด้วย \"ปิดการลบ lead อัตโนมัติ\" (นับจากการอัปเดตล่าสุดของลูกค้า ดีล งาน บันทึก หรือนัด; เก็บถาวร ไม่ลบทิ้ง)",
    "en": "Automatic cleanup of inactive leads: {state}\nSet it with \"auto archive leads after 90 days\" or turn it off with \"turn off lead cleanup\" (counted from the last update on the customer, their deals, jobs, notes or follow-ups; archived, never deleted).",
}
LEAD_CLEANUP_ON = {"th": "เปิด — เกิน {days} วัน", "en": "on — after {days} days"}
LEAD_CLEANUP_OFF = {"th": "ปิด (ค่าเริ่มต้น)", "en": "off (default)"}
LEAD_CLEANUP_BAD_NUMBER = {"th": "ระบุจำนวนวัน 1–3650 เช่น \"ตั้งค่าลบ lead อัตโนมัติ 90 วัน\"", "en": "Give a number of days from 1 to 3650, e.g. \"auto archive leads after 90 days\"."}

# Issue 4 — a deal for "the customer we were just talking about" is
# confirmed, never assumed, when the message names nobody.
DEAL_CONTEXT_CONFIRM = {
    "th": "สร้างดีลให้ {name} (ลูกค้าที่เพิ่งคุยถึง) ใช่ไหม{details}",
    "en": "Create the deal for {name} (the customer just mentioned)?{details}",
}
DEAL_CONTEXT_YES = ("ใช่", "ใช่ สร้างเลย", "yes", "ตกลง", "สร้างเลย")
DEAL_CONTEXT_NO = ("ไม่ใช่", "ไม่ใช่ ระบุชื่อ", "no", "ไม่")
DEAL_CONTEXT_CANCELLED = {"th": "ยังไม่ได้สร้างดีล พิมพ์ชื่อลูกค้าที่ต้องการ เช่น \"สร้างดีลให้ อาทิตย์\"", "en": "No deal created. Name the customer, e.g. \"create a deal for Arthit\"."}
DEAL_CONTEXT_CHOICE_INVALID = {"th": "ตอบ \"ใช่\" เพื่อสร้าง หรือ \"ไม่ใช่\" แล้วระบุชื่อ", "en": "Reply \"yes\" to create it, or \"no\" and name the customer."}
DEAL_AMOUNT_AMBIGUOUS = {
    "th": "มูลค่าดีลคือเท่าไหร่ครับ เห็นตัวเลขหลายค่า: {values}",
    "en": "Which is the deal amount? I see several: {values}",
}
DEAL_DATE_AMBIGUOUS = {
    "th": "คาดว่าจะปิดดีลวันไหนครับ ระบุวันที่ เช่น 30/09 หรือ \"สิ้นเดือนนี้\"",
    "en": "When is the deal expected to close? Give a date, e.g. 30/09 or \"end of this month\".",
}
DEAL_DETAILS_LINE = {"th": "\nมูลค่า {amount} · คาดว่าจะปิด {close}", "en": "\nAmount {amount} · expected close {close}"}
DEAL_AMOUNT_ONLY_LINE = {"th": "\nมูลค่า {amount}", "en": "\nAmount {amount}"}
DEAL_DATE_ONLY_LINE = {"th": "\nคาดว่าจะปิด {close}", "en": "\nExpected close {close}"}
DEAL_CONTEXT_TTL_S = 600

# Issue 1 — "what can I do with X?" answers from what this person holds.
CAPABILITY_GROUP_ALIASES = {
    "customer": ("lead", "ลีด", "ลูกค้า", "contact", "คอนแทค", "รายชื่อ", "customer"),
    "deal": ("deal", "ดีล", "การขาย", "ยอดขาย"),
    "quote": ("quote", "ใบเสนอราคา", "เสนอราคา"),
    "ticket": ("ticket", "ใบงาน", "งานซ่อม", "งานช่าง", "job"),
    "product": ("product", "สินค้า", "แคตตาล็อก", "catalogue", "catalog"),
    "team": ("team", "ทีม", "ทีมช่าง"),
    "warranty": ("warranty", "ประกัน", "รับประกัน"),
    "service_report": ("service report", "รายงานบริการ", "รายงานการซ่อม", "รายงานช่าง"),
    "note": ("note", "บันทึก", "โน้ต"),
    "followup": ("follow", "ติดตาม", "นัด", "เตือน", "reminder"),
    "role": ("role", "บทบาท", "สิทธิ์", "permission"),
    "setting": ("setting", "ตั้งค่า", "การตั้งค่า"),
    "chat_session": ("แชทลูกค้า", "chat session", "live chat"),
    "approval": ("approval", "อนุมัติ"),
}
CAPABILITY_GROUP_PAGE = {
    "customer": "customers", "deal": "deals", "quote": "quotes", "ticket": "tickets", "product": "products",
    "team": "teams", "warranty": "warranties", "service_report": "reports", "role": "roles", "setting": "company",
    "chat_session": "chats", "approval": "approvals",
}
CAPABILITY_ASK_MARKERS = ("ทำอะไร", "ทําอะไร", "ได้บ้าง", "ทำได้", "what can", "capabilit", "able to")
PERMISSION_ASK_PHRASES = (
    "ฉันมีสิทธิ์ทำอะไร", "ฉันมีสิทธิ์อะไร", "สิทธิ์ของฉัน", "มีสิทธิ์ทำอะไรบ้าง", "ผมมีสิทธิ์อะไรบ้าง", "ผมมีสิทธิ์อะไร",
    "มีสิทธิ์อะไรบ้าง", "เรามีสิทธิ์อะไรบ้าง", "ผมมีสิทธิ์ทำอะไร", "my permissions", "what am i allowed",
)
CAPABILITY_DETAIL_HEADER = {"th": "{group} — สิ่งที่คุณทำได้ตอนนี้:", "en": "{group} — what you can do now:"}
CAPABILITY_DETAIL_NONE = {
    "th": "{group} — บัญชีของคุณยังไม่มีสิทธิ์ในหมวดนี้ ขอได้จากเจ้าของร้านหรือแอดมิน (แดชบอร์ด > บทบาทและทีม)",
    "en": "{group} — your account has no permission in this area yet. Ask the shop owner or an admin (dashboard > roles and team).",
}
CAPABILITY_HELD = {"th": "สิทธิ์ที่มี: {labels}", "en": "Permissions held: {labels}"}
CAPABILITY_MORE_COMMANDS = {
    "th": "…และอีก {n} คำสั่ง — พิมพ์ \"วิธีใช้\" เพื่อดูทั้งหมด",
    "en": "…and {n} more — type \"help\" for all of them",
}
CAPABILITY_NOT_HELD = {"th": "ยังไม่มีสิทธิ์: {labels} (ขอจากเจ้าของร้าน)", "en": "Not yet allowed: {labels} (ask the owner)"}
CAPABILITY_FOLLOW_UP = {
    "th": "ถามต่อได้ เช่น \"ทำอะไรกับดีลได้บ้าง\" หรือพิมพ์ \"วิธีใช้\" เพื่อเปิดคู่มือทั้งหมด",
    "en": "Ask on, e.g. \"what can I do with deals?\", or type \"help\" for the full guide",
}
CAPABILITY_OVERVIEW = {
    "th": "หมวดที่คุณใช้ได้: {groups}\nถามรายละเอียดทีละหมวดได้ เช่น \"ทำอะไรกับ Lead ได้บ้าง\" หรือ \"ฉันมีสิทธิ์ทำอะไร\"",
    "en": "Areas open to you: {groups}\nAsk about one, e.g. \"what can I do with leads?\" or \"what am I allowed to do?\"",
}
CAPABILITY_OPEN_PAGE = {"th": "เปิดหน้า{group}", "en": "Open {group}"}
PERMISSION_SUMMARY_HEADER = {"th": "สิทธิ์ของคุณตามหมวด:", "en": "Your permissions by area:"}
PERMISSION_SUMMARY_NONE = {"th": "บัญชีของคุณยังไม่มีสิทธิ์ใช้งานใด ๆ ติดต่อเจ้าของบริษัท", "en": "Your account holds no permissions yet — ask the company owner."}




# ============================================================ user review, batch 2 (4 Sep 2026)
# A phone number is digits. The reason is named so the person can fix it.
PHONE_INVALID = {
    "letters": {
        "th": "เบอร์โทร \"{value}\" มีตัวอักษรอยู่ บันทึกไม่ได้ครับ พิมพ์เป็นตัวเลข เช่น 0812345678",
        "en": "The phone number \"{value}\" contains letters and cannot be saved. Use digits, e.g. 0812345678.",
    },
    "length": {
        "th": "เบอร์โทร \"{value}\" ต้องมี 9–15 หลัก เช่น 0812345678",
        "en": "The phone number \"{value}\" must have 9–15 digits, e.g. 0812345678.",
    },
}
# Several customers in one message: one per line (or ";"), each "ชื่อ นามสกุล เบอร์ [อีเมล]".
BULK_CUSTOMER_TRIGGERS = (
    "เพิ่มลูกค้าหลายคน", "เพิ่มลูกค้าหลายราย", "ลูกค้าใหม่หลายคน", "เพิ่มลูกค้าใหม่", "ลูกค้าใหม่",
    "เพิ่มลูกค้า", "สร้างลูกค้า", "add customers", "add customer", "new customers",
)
BULK_CUSTOMER_SUMMARY = {
    "th": "เพิ่มลูกค้าแล้ว {saved} ราย{skipped_line}{failed_line}",
    "en": "Added {saved} customer(s){skipped_line}{failed_line}",
}
BULK_SKIPPED_LINE = {"th": "\nข้ามเพราะมีอยู่แล้ว {n} ราย: {items}", "en": "\nSkipped as existing ({n}): {items}"}
BULK_FAILED_LINE = {"th": "\nไม่สำเร็จ {n} ราย: {items}", "en": "\nFailed ({n}): {items}"}
BULK_NEEDS_PHONE = {"th": "ไม่มีเบอร์โทร", "en": "no phone"}
BULK_BAD_PHONE = {"th": "เบอร์มีตัวอักษรหรือหลักไม่ครบ", "en": "phone has letters or wrong length"}
BULK_HINT = {
    "th": "เพิ่มหลายคนได้ในข้อความเดียว: หนึ่งคนต่อบรรทัด \"ชื่อ นามสกุล เบอร์ อีเมล(ถ้ามี)\" หรือใช้ปุ่ม \"นำเข้า CSV\" บนหน้ารายชื่อลูกค้า",
    "en": "Add several at once: one per line \"first last phone email(optional)\", or use \"Import CSV\" on the customers page.",
}


def _t(table: dict[str, str], language: str) -> str:
    """Thai-first fallback, matching Phase 5."""
    return table.get(language) or table["th"]


@dataclass
class ChatReply:
    """What to send back, plus what it was about.

    entity_type/entity_id travel with the reply so the caller can record a
    line_message_entity_map row once LINE returns the sent message's ID —
    the mapping cannot be written before the message exists.
    """

    text: str
    # Owner (3 Sep): guidance that is clearer with a picture carries one.
    # https URLs the LINE adapter sends as image messages ahead of the
    # text; empty for the ordinary reply.
    images: list[str] = field(default_factory=list)
    entity_type: str | None = None
    entity_id: str | None = None
    intent: dict | None = field(default=None, repr=False)
    # Phase 10 — suggested next actions, rendered as LINE quick-reply
    # buttons. Plain (label, text_to_send) pairs rather than LINE's wire
    # format, so this module stays a channel-agnostic domain layer and the
    # LINE adapter owns the JSON shape. A caller on another channel can
    # render the same list however it likes, or ignore it.
    quick_replies: list[tuple[str, str]] = field(default_factory=list)
    # A single (label, url) button that opens a link directly, kept apart
    # from quick_replies because it is a different LINE action type and
    # because there is only ever one destination worth offering: the
    # dashboard page showing the same thing the reply just summarised.
    quick_reply_url: tuple[str, str] | None = None
    # Phase 10 — a structured list the channel may render richly (LINE turns
    # this into a Flex bubble with per-row buttons). Deliberately not LINE's
    # JSON: this module stays channel-agnostic, and `text` remains a
    # complete answer on its own so any channel that cannot render a card,
    # and every notification preview, still says something useful.
    list_card: dict | None = None


def greet(ctx: ResolvedContext, language: str = "th") -> str:
    """Master Spec 6.9 test_greeting.

    The name always comes from the LINE profile. There is deliberately no
    per-tenant display name to prefer: display_name lives on chann_identities
    (one per person), not on license_members, so "the name colleagues know
    them by" does not exist as a separate field yet. A per-tenant name would
    belong to Phase 8 (profiles); until then, reading one here would be dead
    code that looks like a feature.
    """
    if ctx.resolution is TenantResolution.SINGLE:
        member = ctx.memberships[0]
        name = ctx.display_name or ctx.chann_uid
        company = member.get("company_name", "")
        # Points at the help rather than embedding it: a greeting is not the
        # place for a twenty-line command list, and greet() has no permission
        # set to filter one by anyway — showing commands someone cannot run
        # is worse than not showing them. One sentence gets them to the
        # filtered version.
        if language == "en":
            return (
                f"Hello {name} — connected to {company}\n"
                'Type "help" to see what you can do.'
            )
        return (
            f"สวัสดีคุณ{name} — เชื่อมต่อกับ {company} แล้ว\n"
            'พิมพ์ "วิธีใช้" เพื่อดูคำสั่งที่ใช้ได้'
        )

    if ctx.resolution is TenantResolution.MULTIPLE:
        names = ", ".join(m.get("company_name", "") for m in ctx.memberships)
        return _t(REPLY_CHOOSE_TENANT, language).format(names=names)

    # Not registered: LINE display name is all we have.
    name = ctx.display_name or ctx.chann_uid
    if language == "en":
        return f"Hello {name} — {_t(REPLY_NOT_REGISTERED, 'en')}"
    return f"สวัสดีคุณ{name} — {_t(REPLY_NOT_REGISTERED, 'th')}"


# Field-name -> human label, for ask_for_missing below. "missing" lists are
# populated two ways: the AI's own JSON output (told in the prompt to keep
# machine-facing keys in English, e.g. "last_name") and this project's own
# code-side validation (e.g. the last_name+phone hard check in
# _handle_customer_intent) — both use the same raw field-key vocabulary, so
# one lookup table covers both sources rather than needing two.
MISSING_FIELD_LABELS = {
    "first_name": {"th": "ชื่อ", "en": "first name"},
    "last_name": {"th": "นามสกุล", "en": "last name"},
    "phone": {"th": "เบอร์โทร", "en": "phone number"},
    "email": {"th": "อีเมล", "en": "email"},
    "address": {"th": "ที่อยู่", "en": "address"},
    "target_name": {"th": "ชื่อลูกค้า", "en": "the customer's name"},
    "amount": {"th": "มูลค่าดีล", "en": "the deal amount"},
    "expected_close_date": {"th": "วันที่คาดว่าจะปิด", "en": "the expected closing date"},
    "product_id": {"th": "รหัสสินค้า", "en": "product code"},
    "product_name": {"th": "ชื่อสินค้า", "en": "product name"},
    "unit_price": {"th": "ราคา", "en": "price"},
    # Ticket fields — reported live as "กรุณาระบุservice_address", the raw
    # key straight from the AI's JSON shown to a person mid-conversation.
    "service_address": {"th": "ที่อยู่หน้างาน", "en": "the service address"},
    "scheduled_date": {"th": "วันนัดหมาย", "en": "the appointment date"},
    "scheduled_time": {"th": "เวลานัดหมาย", "en": "the appointment time"},
    "issue_description": {"th": "อาการหรือรายละเอียดงาน", "en": "what the job is"},
    # Reminder fields — "กรุณาระบุdue_time" was shown to the owner twice in
    # one minute (12:03/12:04, 2 Sep). Labels are the backstop; the real
    # fix is _prune_missing below, which stops most of these being asked.
    "due_date": {"th": "วันที่ต้องการให้เตือน", "en": "the reminder date"},
    "due_time": {"th": "เวลา", "en": "the time"},
    "notes": {"th": "รายละเอียด", "en": "the details"},
    # The rest of what a flow can still be waiting for. Every one of these
    # was reachable and printed as its raw key — "กรุณาระบุteam_name" — the
    # same defect as the two above, three years of it, because the table is
    # kept by hand and nothing checked it. scripts/dev/check-ask-labels.py
    # now fails the build when a field that can reach ask_for_missing has no
    # label here (owner's review of the message path, 10 ก.ย. 2569).
    "team_name": {"th": "ชื่อทีม", "en": "the team name"},
    "name": {"th": "ชื่อ", "en": "the name"},
    "title": {"th": "ชื่อเรื่อง", "en": "the title"},
    "customer_ref": {"th": "ชื่อหรือรหัสลูกค้า", "en": "the customer's name or code"},
    "deal_code": {"th": "รหัสดีล", "en": "the deal code"},
    "entity_code": {"th": "รหัสรายการ", "en": "the record code"},
    "quantity": {"th": "จำนวน", "en": "the quantity"},
    "price": {"th": "ราคา", "en": "the price"},
    "role": {"th": "บทบาท", "en": "the role"},
    "reason": {"th": "เหตุผล", "en": "the reason"},
    "serial": {"th": "หมายเลขเครื่อง", "en": "the serial number"},
    "issue": {"th": "อาการที่พบ", "en": "what is wrong"},
    "schedule": {"th": "วันและเวลาที่สะดวก", "en": "a convenient date and time"},
    "company": {"th": "ชื่อบริษัทหรือร้าน", "en": "the company or shop name"},
    "document_type": {"th": "ชนิดเอกสาร", "en": "the document type"},
    "instruction": {"th": "สิ่งที่ต้องการให้ทำ", "en": "what you want done"},
    "message": {"th": "ข้อความ", "en": "the message"},
    "body": {"th": "เนื้อหา", "en": "the content"},
    "choice": {"th": "ตัวเลือกที่ต้องการ", "en": "which one you want"},
    "currency": {"th": "สกุลเงิน", "en": "the currency"},
    "note": {"th": "รายละเอียด", "en": "the details"},
}


#: Which reader answers a field the model reported as missing. One entry
#: per name in a Capability.parser_supplies; check-fields keeps them paired.
_PARSER_SEES = {"due_date": lambda message: _reads_as_a_date(message)}


def _prune_missing(missing: list[str], intent: dict, message: str) -> list[str]:
    """Drop anything we can answer ourselves before asking a person.

    The 12:03 loop (2 Sep): "สมบัติ ราชเทวี 0879707586 อยากนัดดู demo
    สินค้าวันที่ 7" came back with missing=["due_time"], so the assistant
    demanded a raw key instead of booking a perfectly complete request —
    and answering "9.00" produced the same demand again, because the model
    kept reporting the field it had just been given. Two rules:

    * A reminder's time is never worth asking for: it defaults to 09:00,
      echoed in the confirmation.
    * A date already present in the sentence ("วันที่ 7") is not missing,
      whatever the model says — the parser, not the model, decides that.

    Asking a person for something already on screen is the fastest way to
    make an assistant feel like a form.
    """
    known = capability(str(intent.get("entity") or ""), str(intent.get("action") or ""))
    if known is None:
        return missing
    # The registry decides, so this cannot drift from what the handler asks
    # for. It used to drop last_name as well as email/address — and the
    # create handler then asked for the surname a turn later, two questions
    # for what the model had already answered in one (10 ก.ย. 2569).
    pruned = known.prune_missing(missing)
    # What the parser can see beats what the model thinks it is missing.
    for field_name in known.parser_supplies:
        if field_name in pruned and _PARSER_SEES[field_name](message):
            pruned = [m for m in pruned if m != field_name]
    return pruned


def _slot_fill_still_open(pending: dict, language: str = "th") -> ChatReply:
    """Where the half-finished flow stands, as an answer to a question
    asked in the middle of it. Names what is held and what is still
    wanted, so "ต้องกรอกอะไรบ้าง" gets a real answer and the flow keeps
    its place."""
    fields = {k: v for k, v in (pending.get("fields") or {}).items() if v not in (None, "")}
    have = ", ".join(
        f"{MISSING_FIELD_LABELS.get(k, {}).get(language) or k} {v}" for k, v in fields.items()
    ) or _t(SLOT_FILL_NOTHING_YET, language)
    missing = [m for m in (pending.get("missing") or [])]
    wanted = ", ".join(
        MISSING_FIELD_LABELS.get(m, {}).get(language) or _t(ASK_MISSING_REST, language)
        for m in missing
    ) or _t(ASK_MISSING_REST, language)
    return ChatReply(
        text=_t(SLOT_FILL_STILL_OPEN, language).format(have=have, missing=wanted),
        intent=pending,
    )


def ask_for_missing(missing: list[str], language: str = "th") -> str:
    """Spec 6.4 — ask only for what is actually absent.

    Reported live: an unrecognised field name (e.g. "last_name" straight
    from the AI's own JSON) was shown to the user verbatim —
    "กรุณาระบุlast_name, phone" — because this only ever joined the raw
    keys. Everything the system itself can ask for is in
    MISSING_FIELD_LABELS, and check-ask-labels.py keeps it that way.

    The model, though, can put any string it likes in `missing`, and that
    string is not a field name the build could have checked. Rather than
    print `กรุณาระบุcustomer_tax_identification` at a person, an unknown
    key is folded into one plain request for the rest — the field is still
    asked for, in words, and nothing is silently dropped.
    """
    known = [MISSING_FIELD_LABELS[m][language] for m in missing if m in MISSING_FIELD_LABELS]
    unknown = [m for m in missing if m not in MISSING_FIELD_LABELS]
    if unknown:
        known.append(_t(ASK_MISSING_REST, language))
    labels = ", ".join(known) or _t(ASK_MISSING_REST, language)
    return _t(ASK_MISSING, language).format(fields=labels)


CUSTOMER_HELP = {
    "th": (
        "พิมพ์คุยได้เลยครับ\n\n"
        "· ลงทะเบียนสินค้า (ทำครั้งแรกก่อนแจ้งซ่อม) — พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) มาได้เลย "
        "(ร้านต้องบันทึกเครื่องไว้ก่อน ถ้าระบบยังไม่รู้จักหมายเลข ให้ติดต่อร้าน)\n"
        "· แจ้งซ่อม — พิมพ์อาการที่เสียมาได้เลย เช่น \"แอร์ไม่เย็น\"\n"
        "· ดูสถานะงาน — พิมพ์ \"งานของฉัน\"\n"
        "· เลื่อนหรือยกเลิกนัด — พิมพ์ \"เลื่อนนัด พรุ่งนี้ 10 โมง\" หรือ \"ยกเลิกนัด\"\n"
        "· ลงทะเบียนรับประกัน — พิมพ์ \"ลงทะเบียนรับประกัน\" ตามด้วยหมายเลขเครื่อง\n"
        "· เช็คประกัน — พิมพ์ \"เช็คประกัน\" ตามด้วยหมายเลขเครื่อง หรือ \"ประกันของฉัน\"\n"
        "· ติดต่อร้าน — พิมพ์ \"ติดต่อร้าน\"\n\n"
        "ผมจะถามที่อยู่และวันเวลาที่สะดวกต่อ แล้วส่งเรื่องให้ทางร้าน"
    ),
    "en": (
        "Just type.\n\n"
        "· Register your product first (once) — type the serial number (S/N on the sticker)\n"
        "· Report a fault — describe the problem, e.g. \"air con not cooling\"\n"
        "· Check a job — type \"my jobs\"\n"
        "· Move or cancel a visit — \"reschedule tomorrow 10am\" or \"cancel job\"\n"
        "· Register a product — \"register product\" followed by the serial\n"
        "· Check warranty — \"check warranty\" + serial, or \"my warranties\"\n"
        "· Contact the shop — \"contact\"\n\n"
        "I will ask for an address and a time, then pass it to the shop."
    ),
}

HELP_TRIGGERS = (
    "ช่วยเหลือ", "วิธีใช้", "วิธีใช้งาน", "ใช้ยังไง", "ทำอะไรได้บ้าง", "เมนู", "คู่มือ",
    "help", "how to use", "menu", "guide", "?",
)
# The guide's steps
# answer "how does this work"; this answers "what exactly can I type".
HELP_EXAMPLES_PHRASES = ("ตัวอย่างคำสั่ง", "คำสั่งทั้งหมด", "ดูคำสั่ง", "examples", "commands")
# "What am I allowed to do" — the grouped permission list, without a model
# call: it used to reach suggest_what_you_can_do only through the AI.
CAPABILITY_PHRASES = ("สิทธิ์ของฉัน", "ฉันมีสิทธิ์อะไรบ้าง", "ดูสิทธิ์", "my permissions", "what am i allowed to do")


HELP_MENU_TTL_S = 900
HELP_ALL_PHRASES = ("วิธีใช้ทั้งหมด", "ดูทั้งหมด", "help all", "full guide", "คู่มือทั้งหมด", "ทุกขั้น")
_HELP_STEP_RE = re.compile(r"^\s*(?:วิธีใช้|help|guide|คู่มือ)\s*(?:ข้อ|ขั้น|step)?\s*(\d{1,2})\s*$", re.I)


def _is_help_step_request(message: str) -> bool:
    return bool(_HELP_STEP_RE.match(message or "")) or _matches_phrase(message, HELP_ALL_PHRASES)


def _help_oa(ctx: ResolvedContext) -> str:
    return ctx.oa if ctx.oa in ("sales", "technician", "customer") else "customer"


async def _remember_help_menu(client: DataClient, ctx: ResolvedContext) -> None:
    """The menu is remembered so a bare digit picks a topic — but never
    over a flow that is waiting for an answer (review, 6 Sep 2026: "วิธีใช้"
    mid-report dropped the address prompt, and the phone number typed
    next hit the help menu's digit reader)."""
    try:
        current = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:  # noqa: BLE001
        current = None
    if current and current.get("entity") != "help_menu":
        return
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="help", entity="help_menu", fields={}, missing=[],
            ttl_seconds=HELP_MENU_TTL_S,
        )
    except Exception:  # noqa: BLE001
        log.exception("could not remember the help menu")


async def _help_step_reply(
    client: DataClient, *, ctx: ResolvedContext, n: int, language: str,
) -> ChatReply | None:
    oa = _help_oa(ctx)
    rendered = render_help_step(oa, n, language)
    if rendered is None:
        return None
    text, image = rendered
    await _remember_help_menu(client, ctx)
    buttons: list[tuple[str, str]] = []
    if n < help_step_count(oa):
        buttons.append((("ถัดไป" if language != "en" else "Next") + f" {n + 1}", f"วิธีใช้ {n + 1}"))
    buttons.append(("หัวข้อทั้งหมด" if language != "en" else "Topics", "วิธีใช้"))
    return ChatReply(
        text=text, images=[image] if image else [], quick_replies=buttons,
        quick_reply_url=_guide_button(oa, language),
    )


async def _help_step_from_pending(
    client: DataClient, *, ctx: ResolvedContext, message: str, language: str,
    permission_keys: list[str] | None = None,
) -> ChatReply | None:
    """A bare digit while the help menu is open: that topic. None
    otherwise, so a "1" answering some other question is left alone.

    The caller's real permission keys are passed on: an out-of-range
    number ("0", "99") re-renders the menu, and with an empty key list the
    owner was told they had no permissions at all (review, 6 Sep 2026)."""
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:  # noqa: BLE001
        return None
    if not pending or pending.get("entity") != "help_menu":
        return None
    n = _menu_digit(message)
    if n is None:
        return None
    reply = await _help_step_reply(client, ctx=ctx, n=n, language=language)
    if reply is None:
        return await _help_reply(
            client, ctx=ctx, permission_keys=list(permission_keys or []), language=language, message="วิธีใช้",
        )
    return reply


async def _help_reply(
    client: DataClient, *, ctx: ResolvedContext, permission_keys: list[str], language: str, message: str,
) -> ChatReply:
    """Owner (6 Sep 2026): help in layers. The menu names the topics with a
    button each; a topic comes with its picture; "วิธีใช้ทั้งหมด" is the
    old single message. Every layer carries the illustrated-guide link."""
    oa = _help_oa(ctx)
    m = _HELP_STEP_RE.match(message or "")
    if m:
        reply = await _help_step_reply(client, ctx=ctx, n=int(m.group(1)), language=language)
        if reply is not None:
            return reply
    else:
        n = help_step_by_text(oa, message, language)
        if n is not None and not _matches_phrase(message, HELP_TRIGGERS):
            reply = await _help_step_reply(client, ctx=ctx, n=n, language=language)
            if reply is not None:
                return reply
    lead = ""
    areas = ""
    if oa == "sales":
        # Someone with no permission at all is told who to ask first.
        lead = "" if _filter_by_oa(permission_keys, oa) else _t(HELP_NOTHING, language) + "\n\n"
        try:
            areas = capability_overview_line(_filter_by_oa(permission_keys, oa), await client.permission_catalog(), language)
        except Exception:  # noqa: BLE001
            log.exception("could not read the permission catalogue for the areas line")
    if _matches_phrase(message, HELP_ALL_PHRASES):
        return ChatReply(
            text=lead + render_help_text(oa, language) + ("\n\n" + areas if areas else ""),
            quick_replies=help_menu_quick_replies(oa, language),
            quick_reply_url=_guide_button(oa, language),
        )
    await _remember_help_menu(client, ctx)
    areas = areas.split("\n", 1)[0] if areas else ""
    return ChatReply(
        text=lead + render_help_menu(oa, language) + ("\n" + areas if areas else ""),
        quick_replies=help_menu_quick_replies(oa, language),
        quick_reply_url=_guide_button(oa, language),
    )


def _guide_button(oa: str, language: str) -> tuple[str, str] | None:
    url = dashboard_link("guide", oa)
    return (("คู่มือพร้อมรูป" if language != "en" else "Illustrated guide"), url) if url else None

# "Who am I here?" for a customer: their own details, the shop they belong
# to, what is registered. The chat twin of the home screen's profile card.
CUSTOMER_PROFILE_PHRASES = (
    "ข้อมูลของฉัน", "ข้อมูลส่วนตัว", "โปรไฟล์", "โปรไฟล์ของฉัน", "ดูข้อมูลของฉัน",
    "my profile", "my details", "ข้อมูลตัวเอง", "ดูข้อมูลตัวเอง", "ข้อมูลฉัน", "ฉันชื่ออะไรในระบบ", "ฉันชื่ออะไร",
    "ชื่ออะไรในระบบ", "profile", "my info", "who am i", "my account", "บัญชีของฉัน", "ข้อมูลส่วนตัวของฉัน",
)
# The customer-only half: "which shop am I a customer of" — on a staff OA
# "ร้านของเรา" is the shop's own record (SHOP_INFO_PHRASES).
CUSTOMER_SHOP_PHRASES = (
    "ลูกค้าร้านไหน", "ร้านของฉัน", "เป็นลูกค้าร้านไหน", "ลูกค้าร้านไหนอยู่", "เป็นลูกค้าร้านไหนอยู่", "ร้านไหน", "ผูกร้านไหน",
    "which shop", "my shop",
)
CUSTOMER_PROFILE_TEXT = {
    "th": (
        "ข้อมูลของคุณครับ\n\n"
        "ชื่อ: {name}\nเบอร์: {phone}\nที่อยู่: {address}\n"
        "ลูกค้าของ: {shop}\nสินค้าที่ลงทะเบียน: {products}\n\n"
        "แก้ได้เลย เช่น \"แก้เบอร์เป็น 08x-xxx-xxxx\" หรือ \"แก้ที่อยู่เป็น ...\""
    ),
    "en": (
        "Your details\n\n"
        "Name: {name}\nPhone: {phone}\nAddress: {address}\n"
        "Customer of: {shop}\nRegistered products: {products}\n\n"
        "Change any of it, e.g. \"change my phone to 08x-xxx-xxxx\""
    ),
}
_NOT_SET = {"th": "ยังไม่ระบุ", "en": "not set"}


async def _member_id_of(client: DataClient, license_id, ctx: ResolvedContext) -> str | None:
    """The caller's member id for owner_member_id on records they create
    (principle 6) — nothing set it until 6 Sep 2026."""
    try:
        member = await client.get_member(str(license_id), ctx.chann_uid, channel=member_channel(ctx.oa))
    except Exception:  # noqa: BLE001
        return None
    return str((member or {}).get("id") or "") or None


async def _handle_staff_profile_view(
    client: DataClient, *, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    try:
        profile = await client.get_profile(ctx.chann_uid) or {}
    except Exception:
        log.exception("could not read a member's profile")
        profile = {}
    member = next(
        (m for m in ctx.memberships if str(m.get("license_id")) == str(license_id)), {},
    )
    blank = _t(_NOT_SET, language)
    name = " ".join(p for p in (profile.get("first_name"), profile.get("last_name")) if p) or ctx.display_name or blank
    # The card invites an edit, so hold what is still blank: the next message
    # is often the value on its own ("0869768057"), which is not a command on
    # any OA and would otherwise read as nothing at all.
    blanks = [f for f in ("first_name", "phone", "email") if not profile.get(f)]
    if blanks:
        try:
            # Never over a flow that is already waiting: this card is a tile,
            # and a tile pressed in the middle of a service report must leave
            # the draft alone (review A1).
            open_flow = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
            if open_flow is None or open_flow.get("entity") == "profile_edit":
                await client.set_pending_intent(
                    ctx.chann_uid, ctx.oa, action="update", entity="profile_edit",
                    fields={}, missing=blanks, ttl_seconds=PENDING_INTENT_TTL_S,
                )
        except Exception:  # noqa: BLE001
            log.exception("could not hold the profile fields still blank")
    return ChatReply(
        text=_t(STAFF_PROFILE_TEXT, language).format(
            name=name, phone=profile.get("phone") or blank, email=profile.get("email") or blank,
            shop=member.get("company_name") or blank, role=_label(ROLE_LABELS, member.get("role"), language) if member.get("role") else "-",
        ),
    )


async def _handle_customer_profile_view(
    client: DataClient, *, ctx: ResolvedContext, license_id, language: str,
) -> ChatReply:
    try:
        profile = await client.get_profile(ctx.chann_uid) or {}
    except Exception:
        log.exception("could not read a customer's profile")
        profile = {}
    try:
        products = [
            w for w in await client.list_warranties(
                str(license_id), customer_chann_uid=ctx.chann_uid,
            )
            if str(w.get("status") or "") != "void"
        ]
    except Exception:
        products = []
    blank = _t(_NOT_SET, language)
    name = " ".join(
        p for p in (profile.get("first_name"), profile.get("last_name")) if p
    ) or ctx.display_name or blank
    shop = next(
        (
            m.get("company_name") for m in ctx.memberships
            if str(m.get("license_id")) == str(license_id)
        ),
        None,
    ) or blank
    listed = ", ".join(
        f"{w.get('product_name') or ''} {w.get('serial_number') or ''}".strip()
        for w in products[:5]
    )
    return ChatReply(
        text=_t(CUSTOMER_PROFILE_TEXT, language).format(
            name=name, phone=profile.get("phone") or blank,
            address=profile.get("address") or blank, shop=shop,
            products=listed or ("ยังไม่มี" if language == "th" else "none yet"),
        ),
        quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("ลงทะเบียนสินค้า", "ลงทะเบียนสินค้า")],
    )

# The technician's "วิธีใช้": a day's order of operations, not a catalogue.
# The sales help sections below list customers, quotes and diaries a
# technician never touches — the owner
# asked for a guide per OA that "อ่านแล้วเข้าใจว่าทำอะไรได้" (3 Sep).
TECHNICIAN_HELP = {
    "th": (
        "งานช่างเดินตามลำดับนี้ครับ\n\n"
        "1. รับงาน — พิมพ์ \"งานที่เปิดรับ\" แล้วเลือกงาน หรือพิมพ์ \"รับงาน T-000123\"\n"
        "2. ถึงหน้างาน — พิมพ์ \"เช็คอิน\" (ถ้ามีงานเดียวระบบรู้เอง)\n"
        "3. ซ่อมเสร็จ — พิมพ์ \"ปิดงาน\" แล้วตอบ 3 อย่าง: ปัญหาที่พบ / สิ่งที่แก้ไข / อะไหล่ (ถ้ามี)\n"
        "4. รอ CS ตรวจ — ผ่านหรือตีกลับจะแจ้งมาที่แชทนี้ ตีกลับแก้แล้วส่งใหม่ได้\n\n"
        "รับงานไม่ได้: พิมพ์ \"ปฏิเสธงาน T-000123 เหตุผล\" งานจะกลับไปที่ CS\n"
        "ดูอื่นๆ: \"งานของฉัน\" · \"งานวันนี้\" · \"รายงานของฉัน\" · \"ออกรายงาน SR-…\" (PDF เมื่อ CS ตรวจผ่านแล้ว)\n"
        "แก้ข้อมูลตัวเอง: \"แก้เบอร์เป็น 08x-xxx-xxxx\"\n"
        "เปิดหน้าจอเต็มได้จากเมนูด้านล่าง (เปิดหน้าจอช่าง)"
    ),
    "en": (
        "A technician's day, in order\n\n"
        "1. Take a job — type \"open jobs\" and pick one, or \"claim T-000123\"\n"
        "2. On site — type \"check in\" (one job open: it knows which)\n"
        "3. Done — type \"finish\" and answer three things: what you found / what you did / parts (if any)\n"
        "4. CS reviews — approved or sent back, you hear here; fix and resend\n\n"
        "Also: \"my jobs\" · \"today\" · \"my reports\"\n"
        "Your own details: \"change my phone to 08x-xxx-xxxx\"\n"
        "The full screen is on the menu below (Open the dashboard)"
    ),
}

# What to show, grouped the way a salesperson's day is shaped rather than the
# way the permission catalogue is organised. Each entry is
# (permission key, example command, what it does) — the example is the point:
# a list of capabilities tells someone what exists, an example tells them what
# to type, and only one of those gets used.
HELP_SECTIONS = (
    ("ร้านและทีม", (
        ("ticket.read", "ข้อมูลร้าน", "ชื่อ รหัสร้าน (ให้ลูกค้าใช้ผูกร้าน) และช่องทางติดต่อ"),
        ("ticket.read", "รายชื่อช่าง", "ช่างในร้านและเบอร์ติดต่อ"),
        ("ticket.read", "ทีมช่าง", "ทีมและสมาชิก (หัวหน้าทีม)"),
        ("team.manage", "สร้างทีมช่าง แอร์", "ตั้งทีมใหม่"),
        ("team.manage", "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า", "ใส่ช่างเข้าทีม / ตั้งหัวหน้า"),
        ("warranty.create", "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย", "บันทึกเครื่องที่ขาย ลูกค้าจะพิมพ์ S/N เพื่อผูกเอง"),
        ("warranty.read", "รายการประกัน", "เครื่องที่ลงทะเบียนไว้ทั้งหมด"),
    )),
    ("ลูกค้า", (
        ("customer.read", "รายชื่อลูกค้า", "ดูลูกค้าทั้งหมด"),
        ("customer.read", "ค้นหาลูกค้า สมชาย", "ค้นหาด้วยชื่อหรือเบอร์"),
        ("customer.archive", "ลบ Lead สมชาย", "เอาออกจากรายชื่อ (เก็บถาวร ประวัติยังอยู่) ระบบถามยืนยันก่อน"),
        ("setting.manage", "ตั้งค่าลบ lead อัตโนมัติ 90 วัน", "เก็บถาวร Lead ที่ไม่มีการเคลื่อนไหวเกินกำหนดเอง (ปิดเป็นค่าเริ่มต้น)"),
        ("customer.read", "ข้อมูลลูกค้า C-2026-0001", "ดูรายละเอียด"),
        ("customer.create", "สร้างลูกค้า สมชาย ใจดี 0812345678", "เพิ่มลูกค้าใหม่"),
    )),
    ("ดีลและใบเสนอราคา", (
        ("deal.read", "รายการดีล", "ดูดีลทั้งหมด"),
        ("deal.read", "ดีลที่ยังไม่ปิด", "เฉพาะที่ยังไม่จบ"),
        ("deal.read", "ข้อมูลดีล D-2026-0001", "ดูรายละเอียดพร้อมยอดรวม"),
        ("deal.create", "สร้างดีลให้ สมชาย", "เปิดดีลใหม่"),
        ("deal.create", "สร้างดีลให้ สมชาย มูลค่า 250,000 ปิดสิ้นเดือนนี้", "เปิดดีลพร้อมมูลค่าและวันคาดว่าจะปิด"),
        ("quote.create", "สร้างใบเสนอราคาจากดีล D-2026-0001", "ออกใบเสนอราคา"),
        ("quote.update", "ออกเอกสาร Q-2026-0001", "สร้าง PDF ส่งลูกค้า"),
    )),
    ("บันทึกและการติดตาม", (
        ("note.create", "บันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด", "จดบันทึก"),
        ("note.read", "ดูบันทึก C-2026-0001", "ดูบันทึกย้อนหลัง"),
        ("followup.create", "เตือน D-2026-0001 พรุ่งนี้", "ตั้งเตือน"),
        ("followup.create", "นัดดูสินค้า C-2026-0001 วันศุกร์ บ่าย 2", "นัดหมายพร้อมเวลา (ระบุรหัสลูกค้า/ดีล หรือเปิดดูข้อมูลก่อน)"),
        ("followup.read", "งานวันนี้", "ดูงานที่ต้องทำ"),
    )),
    ("งานซ่อม", (
        ("ticket.read", "รายการงาน", "ดูงานซ่อมทั้งหมด"),
        ("ticket.read", "งานของฉัน", "เฉพาะงานที่รับไว้"),
        ("ticket.read", "งานที่เปิดรับ", "งานที่ยังไม่มีคนรับ"),
        ("ticket.update", "มอบหมาย T-2026-0001 ให้ทีม AC", "จ่ายงานให้ทีมช่าง"),
        ("ticket.update", "รับงาน T-2026-0001", "ช่างกดรับงาน"),
        ("ticket.update", "เช็คอิน T-2026-0001", "แจ้งว่าถึงหน้างานแล้ว"),
        ("ticket.update", "ปิดงาน T-2026-0001\nพบ: ...\nแก้: ...", "ปิดงานพร้อมรายงาน"),
    )),
    ("การอนุมัติ", (
        ("approval.view", "รายการรออนุมัติ", "รายงานบริการที่รอคุณตรวจ"),
        ("approval.approve", "อนุมัติ SR-2026-0001", "อนุมัติรายงาน (ครบทุกขั้น = ส่งแบบประเมินให้ลูกค้า)"),
        ("approval.reject", "ไม่อนุมัติ SR-2026-0001 รูปไม่ครบ", "ตีกลับพร้อมเหตุผล แจ้งช่างให้แก้"),
        ("approval.manage", "ตั้งการอนุมัติ ให้ CS ก่อน แล้วต่อด้วย admin", "กำหนดขั้นตอนอนุมัติด้วยภาษาคน"),
        ("approval.manage", "ดูการอนุมัติปัจจุบัน", "ดูขั้นตอนที่ตั้งไว้"),
    )),
    ("ทีมงาน", (
        ("member.manage", "เพิ่มช่าง", "ขอรหัสเชิญให้ช่างเข้าร่วมบริษัท"),
        ("setting.manage", "ตั้งกฎมอบหมาย ช่างแอร์ให้ทีม AC วันละ 5 งาน", "ตั้งกฎจ่ายงานอัตโนมัติ"),
        ("setting.manage", "ดูกฎมอบหมาย", "ดูกฎที่ตั้งไว้"),
    )),
    ("ตั้งค่า", (
        ("setting.manage", "ข้อมูลบริษัท", "ดูข้อมูลที่พิมพ์บนเอกสาร"),
        ("setting.manage", "ตั้งเลขผู้เสียภาษี 0105558123456", "แก้ทีละช่อง"),
        ("setting.manage", "ออกแบบใบเสนอราคา",
         "ให้ AI ร่างแบบฟอร์มเอกสารให้ (ใบเสนอราคา / ใบรายงานการซ่อม) "
         "ดูตัวอย่างและแก้ได้ก่อน แล้วค่อยกดใช้จริง"),
    )),
)

HELP_NOTHING = {
    "th": "ยังไม่มีสิทธิ์ใช้งานคำสั่งใด ๆ — ติดต่อเจ้าของบริษัทเพื่อขอสิทธิ์",
    "en": "You do not have permission to use any commands yet — ask the company owner.",
}


# usage_help() (the permission-filtered example list) was retired on 4 Sep 2026:
# help answers with the guide, and capability_detail reads HELP_SECTIONS directly.


def suggest_what_you_can_do(
    permission_keys,
    catalog: list[dict],
    language: str = "th",
    *,
    requested_action: str | None = None,
    requested_entity: str | None = None,
    oa: str = "sales",
) -> str:
    """What to say when a request cannot be carried out.

    Until 4 Sep 2026 this listed the member's permissions, grouped and
    capped. The owner's verdict: "คุณสามารถทำสิ่งเหล่านี้ได้…" is not an
    answer anyone can act on — the guide is. So this now says only WHY
    (no such feature / not allowed, and who grants it / not understood)
    and points at the guide. The permission set still decides one thing:
    a member with nothing at all is told to ask, not sent to a guide of
    things they cannot do. `catalog` is used only to name the missing
    permission in the member's language.
    """
    held = set(permission_keys)
    if not held:
        return _t(SUGGEST_NOTHING, language)

    needed = required_permission(requested_action or "", requested_entity)
    if requested_entity and needed is None:
        lead = _t(SUGGEST_UNKNOWN_FEATURE_LEAD, language)
    elif requested_entity and needed is not None and needed in held:
        # They HOLD it: the feature exists and this phrasing is what is not
        # understood — say what to type (review, 6 Sep 2026: a technician
        # with ticket.update was told they lacked «แก้ไขใบงาน»).
        example = _suggest_example(oa, str(requested_entity), language)
        return _t(SUGGEST_HELD_UNHANDLED, language).format(example=example) + "\n\n" + _t(GUIDE_POINTER, language)
    elif requested_entity and needed is not None and not _oa_allows(oa, needed):
        return _t(SUGGEST_WRONG_OA, language) + "\n\n" + _t(GUIDE_POINTER, language)
    elif requested_entity and needed is not None:
        needed_label = next(
            (
                ((e.get("label") or {}).get(language) or (e.get("label") or {}).get("th"))
                for e in catalog if e.get("key") == needed
            ),
            needed,
        ) or needed
        lead = _t(SUGGEST_NO_PERMISSION_NAMED, language).format(needed=needed_label)
    else:
        # The plain fallback already says how to open the guide.
        return _t(SUGGEST_HEADER_BY_OA.get(oa, SUGGEST_HEADER), language)
    return lead + "\n\n" + _t(GUIDE_POINTER, language)


# Phase 8 — the fields a profile edit is allowed to touch through chat.
# Mirrors data/chann_data/repositories/profile.py's EDITABLE_FIELDS; kept as
# a separate constant rather than imported, since the Application tier has
# no dependency on the Data tier's Python package (only its HTTP API).
# The profile card ends with 'แก้ได้เลย เช่น "แก้เบอร์เป็น …"', so the next
# message is very often nothing but a field and a value. Without this the
# technician's "ชื่อ ทดสอบ1 มีทดสอบ" was read as a new CUSTOMER, the flow
# asked for a phone number, and the phone was then refused as a sales-only
# command — a dead end that started with our own invitation (owner, 10 Sep
# 2026).
_PROFILE_FIELD_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("last_name", ("นามสกุล", "last name", "surname")),
    ("first_name", ("ชื่อจริง", "ชื่อ", "first name", "name")),
    ("phone", ("เบอร์โทรศัพท์", "เบอร์โทร", "เบอร์", "โทรศัพท์", "โทร", "phone", "tel", "mobile")),
    ("email", ("อีเมล", "อีเมล์", "email", "e-mail", "mail")),
    ("address", ("ที่อยู่", "address")),
)


def _bare_profile_value(message: str, blanks: list[str]) -> dict | None:
    """A value with no label, matched to a field that is still empty.

    Only shapes that cannot be a command anywhere: a phone number, an email
    address. A bare word is NOT taken as a name — too much else is a bare
    word — so the label form above stays the way to set one.
    """
    text = _strip_polite_tail(" ".join((message or "").split())).strip()
    if not text or len(text) > 60:
        return None
    if "@" in text and "email" in blanks and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return {"email": text}
    digits = re.sub(r"[^0-9]", "", text)
    if "phone" in blanks and re.fullmatch(r"[0-9 ()+.-]+", text) and 9 <= len(digits) <= 11:
        return {"phone": text}
    return None


_NOT_MINE_QUALIFIERS = (
    "บริษัท", "ร้าน", "ลูกค้า", "ช่าง", "ทีม", "งาน", "ดีล", "ผู้ติดต่อ", "สาขา",
)


def _profile_field_edit(message: str) -> dict | None:
    """{field: value} when the message is a field label and a value, else None.

    "ชื่อ ทดสอบ1 มีทดสอบ" gives both names, the way the card's own example
    reads. A label with nothing after it is not an edit — it is a question.
    """
    text = " ".join((message or "").split())
    if not text or len(text) > 120:
        return None
    # The established edit phrasings ("เปลี่ยนเบอร์เป็น …", "เบอร์ใหม่ …") have
    # their own parser, which reads the value out of a whole sentence. This
    # one only handles a bare label and value; anything richer defers.
    if _looks_like_profile_edit(text):
        return None
    lowered = text.lower()
    for field, labels in _PROFILE_FIELD_LABELS:
        for label in labels:
            if not lowered.startswith(label):
                continue
            rest = text[len(label):].lstrip(" :：=")
            # "ที่อยู่บริษัท 99 …" is the shop's address, "ชื่อลูกค้า …" is
            # somebody else's: a qualifier right after the label means this
            # is not the caller's own field.
            if any(rest.startswith(q) for q in _NOT_MINE_QUALIFIERS):
                return None
            for lead in ("เป็น", "คือ", "ใหม่", "is", "to"):
                if rest.lower().startswith(lead):
                    rest = rest[len(lead):].lstrip(" :：=")
            rest = _strip_polite_tail(rest).strip()
            if not rest:
                return None
            if field == "first_name":
                parts = rest.split()
                if len(parts) >= 2:
                    return {"first_name": parts[0], "last_name": " ".join(parts[1:])}
                return {"first_name": rest}
            if field == "phone":
                digits = re.sub(r"[^0-9]", "", rest)
                return {"phone": rest} if digits else None
            return {field: rest}
    return None


PROFILE_EDITABLE_FIELDS = frozenset(
    {"first_name", "last_name", "phone", "email", "address"}
)

PROFILE_UPDATED = {
    "th": "แก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว",
    "en": "Your profile has been updated.",
}
PROFILE_INVALID_VALUE = {
    "th": "ข้อมูลที่ให้มาไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง",
    "en": "That value doesn't look right — please check and try again.",
}
PROFILE_NOTHING_TO_UPDATE = {
    "th": "กรุณาระบุข้อมูลที่ต้องการแก้ไข เช่น ชื่อ เบอร์โทร อีเมล หรือที่อยู่",
    "en": "Please say what to update — name, phone, email, or address.",
}

# Master Spec 8.1 lists "ลงทะเบียน profile ตัวเอง" under the Customer and
# Technician OA activity tables only. Sales OA is deliberately excluded: that
# same channel is where leads, deals and quotes are discussed, so "แก้เบอร์
# เป็น 08x" becomes genuinely ambiguous there once Phase 9 exists — whose
# phone number, the sender's or the customer they were just talking about?
# Keyed on the CURRENT message's OA, never on primary_role, which is fixed
# at first contact and goes stale the moment the same LINE account messages
# a different channel.
PROFILE_ELIGIBLE_ROLES = frozenset({"technician", "customer"})

PROFILE_NOT_ELIGIBLE = {
    "th": (
        "การแก้ไขข้อมูลส่วนตัวผ่านแชทใช้ได้เฉพาะบัญชีช่างและลูกค้าเท่านั้น "
        "กรุณาแก้ไขผ่าน Dashboard"
    ),
    "en": (
        "Editing your own profile through chat is available to technician and "
        "customer accounts only — please use the Dashboard instead."
    ),
}


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 409 or "409" in str(exc)


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 or "404" in str(exc)


async def _handle_profile_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, language: str
) -> ChatReply:
    """Phase 8 self-edit through chat (Master Spec 8.4).

    Self-edit only: resolving "แก้ลูกค้าชื่อสมชาย" to a real chann_uid needs a
    customer directory search, which is Phase 9. The on-behalf path exists
    and is fully authorized (data/chann_data/repositories/profile.py's
    may_edit_on_behalf, exercised via DataClient.check_profile_edit) but is
    not yet reachable from free-text chat for that reason — wiring it in is
    a Phase 9 follow-up, not a missing feature here.
    """
    if ctx.oa not in PROFILE_ELIGIBLE_ROLES:
        return ChatReply(text=_t(PROFILE_NOT_ELIGIBLE, language), intent=intent)

    raw_fields = intent.get("fields") or {}
    fields = {
        k: v for k, v in raw_fields.items()
        if k in PROFILE_EDITABLE_FIELDS and v not in (None, "")
    }
    if not fields:
        return ChatReply(text=_t(PROFILE_NOTHING_TO_UPDATE, language), intent=intent)

    try:
        await client.update_profile(ctx.chann_uid, fields, actor_id=ctx.chann_uid)
    except Exception as exc:  # noqa: BLE001
        if _is_conflict(exc):
            return ChatReply(text=_t(PROFILE_INVALID_VALUE, language), intent=intent)
        raise

    return ChatReply(
        text=_t(PROFILE_UPDATED, language),
        entity_type="profile", entity_id=ctx.chann_uid, intent=intent,
    )


# ---------------------------------------------------------------- Phase 9 CRM

APPOINTMENT_OFFER = {
    "th": "เห็นว่ามีนัดวันที่ {date} — ตั้งเตือนไว้ไหมครับ",
    "en": "Looks like {date} — shall I set a reminder?",
}

CUSTOMER_AMBIGUOUS_LEAD = {
    "th": "มีลูกค้าหลายคนที่ตรงกับ \"{name}\" หมายถึงคนไหนครับ",
    "en": 'Several customers match "{name}" — which one?',
}

CUSTOMER_CREATED = {
    "th": "เพิ่มลูกค้า{name}เรียบร้อยแล้ว",
    "en": "Added customer {name}.",
}
CUSTOMER_NEEDS_SOMETHING = {
    "th": "กรุณาระบุอย่างน้อยชื่อ เบอร์โทร หรืออีเมลของลูกค้า",
    "en": "Please provide at least a name, phone, or email for the customer.",
}
CUSTOMER_UPDATED = {
    "th": "แก้ไขข้อมูลลูกค้า{name}เรียบร้อยแล้ว",
    "en": "Updated customer {name}.",
}
CUSTOMER_PROMOTED = {
    "th": "ยืนยัน{name}เป็นลูกค้าจริง (Contact) แล้ว",
    "en": "{name} is now a confirmed Contact.",
}
CUSTOMER_NOT_FOUND = {
    "th": "ไม่พบลูกค้าชื่อ {name} ในบริษัทนี้",
    "en": "No customer named {name} was found in this company.",
}
CUSTOMER_NEEDS_TARGET_NAME = {
    "th": "กรุณาระบุชื่อลูกค้าที่ต้องการแก้ไขหรือยืนยัน",
    "en": "Please say which customer's name you mean.",
}

DEAL_CREATED = {
    "th": "สร้างดีล {deal_id} สำหรับ {name} เรียบร้อยแล้ว",
    "en": "Created deal {deal_id} for {name}.",
}
DEAL_NEEDS_TARGET_NAME = {
    "th": "กรุณาระบุชื่อลูกค้าที่จะสร้างดีลด้วย",
    "en": "Please say which customer this deal is for.",
}
# The same question in the job flow. _handle_staff_ticket_create used the
# deal wording, so someone opening a repair job by hand was asked which
# customer the DEAL was for — a word from a different part of the product,
# in the middle of a different task (owner's review of the message path,
# 10 ก.ย. 2569).
TICKET_NEEDS_TARGET_NAME = {
    "th": "กรุณาระบุชื่อลูกค้าที่จะเปิดงานซ่อมให้ด้วยครับ",
    "en": "Please say which customer this job is for.",
}


def _display_name(row: dict) -> str:
    name = " ".join(p for p in (row.get("first_name"), row.get("last_name")) if p).strip()
    return name or row.get("phone") or row.get("email") or "(ไม่มีชื่อ)"


CUSTOMER_DISAMBIGUATION_HEADER = {
    "th": "พบลูกค้าชื่อ {name} หลายคน กรุณาพิมพ์หมายเลขเพื่อเลือก:",
    "en": "Found several customers named {name} — reply with the number to choose:",
}
CUSTOMER_DISAMBIGUATION_INVALID = {
    "th": "กรุณาพิมพ์หมายเลข 1-{n} จากรายการที่แนะนำ",
    "en": "Please type a number from 1 to {n} from the list shown",
}
# Long enough that picking up a Word doc, checking with a colleague, or
# just pausing mid-conversation doesn't lose the list; short enough that a
# stale unanswered "which one?" goes cold before it could be answered
# against the wrong context days later. Matches the storefront selection
# TTL (STOREFRONT_PENDING_TTL_S) for the same reasoning.
CUSTOMER_DISAMBIGUATION_TTL_S = 300
# How many candidates to offer — long enough to almost never truncate a
# real disambiguation (shared first+last name AND same tenant is already
# rare), short enough that a LINE reply listing them stays readable.
CUSTOMER_DISAMBIGUATION_MAX = 9


def _format_customer_candidates(candidates: list[dict], language: str, name: str) -> str:
    lines = [_t(CUSTOMER_DISAMBIGUATION_HEADER, language).format(name=name)]
    for i, m in enumerate(candidates, start=1):
        phone = m.get("phone") or "-"
        lines.append(f"{i}. {_display_name(m)} ({phone})")
    return "\n".join(lines)


async def _find_one_customer_by_name(
    client: DataClient, license_id: str, name: str, language: str, *,
    ctx: ResolvedContext | None = None, resume_entity: str | None = None,
    resume_action: str | None = None, resume_fields: dict | None = None,
) -> tuple[dict | None, ChatReply | None]:
    """Name-based lookup, because a chat message names a customer by name,
    never by the internal id nobody but the system ever sees.

    Returns (row, None) on exactly one match, or (None, ChatReply) with a
    not-found/ambiguous reply the caller should return as-is otherwise.

    Reported live: an ambiguous match ("มีสมชายหลายคน") used to just list
    the candidates as text and ask the user to type something more
    specific — no way to simply pick one. When ctx/resume_* are given (the
    normal case from every real caller), an ambiguous match now also
    stores a pending_intent carrying enough to finish the ORIGINAL
    action once the user replies with a bare number — see
    _resolve_customer_disambiguation, which is what actually consumes it.
    """
    name = (name or "").strip()
    if not name:
        return None, ChatReply(text=_t(CUSTOMER_NEEDS_TARGET_NAME, language))
    rows = await client.list_customers(license_id)
    matches = [
        r for r in rows
        if name.lower() in " ".join(
            p for p in (r.get("first_name"), r.get("last_name")) if p
        ).lower()
    ]
    if not matches:
        return None, ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(name=name))
    if len(matches) > 1:
        candidates = matches[:CUSTOMER_DISAMBIGUATION_MAX]
        if ctx is not None:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa,
                action="resolve", entity="customer_disambiguation",
                fields={
                    "resume_entity": resume_entity, "resume_action": resume_action,
                    "resume_fields": resume_fields or {}, "candidates": candidates,
                },
                missing=[], ttl_seconds=CUSTOMER_DISAMBIGUATION_TTL_S,
            )
        return None, ChatReply(text=_format_customer_candidates(candidates, language, name))
    return matches[0], None


async def _resolve_customer_disambiguation(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    pending: dict, permission_keys: list[str], language: str,
) -> ChatReply:
    """Consumes the pending_intent _find_one_customer_by_name stores on an
    ambiguous match — a bare number reply here finishes whatever the
    original request was (update/promote a customer, or create a deal
    naming one), never re-asks the AI to re-parse a lone digit."""
    fields = pending.get("fields") or {}
    candidates = fields.get("candidates") or []
    text = (message or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= len(candidates)):
        return ChatReply(
            text=_t(CUSTOMER_DISAMBIGUATION_INVALID, language).format(n=len(candidates))
        )
    chosen = candidates[int(text) - 1]
    await client.clear_pending_intent(ctx.chann_uid, ctx.oa)

    resume_entity = fields.get("resume_entity")
    resume_action = fields.get("resume_action")
    resume_fields = fields.get("resume_fields") or {}
    license_id = str(license_id)

    if resume_entity == "customer":
        needed = required_permission(resume_action or "", "customer")
        if needed is None or needed not in set(permission_keys) or not _oa_allows(ctx.oa, needed):
            catalog = await client.permission_catalog()
            return ChatReply(text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language, oa=ctx.oa,
                requested_action=resume_action, requested_entity="customer",
            ))
        if resume_action == "archive":
            return await _ask_archive_confirmation(client, ctx=ctx, row=chosen, language=language)
        return await _apply_customer_action(
            client, chosen_row=chosen, action=resume_action or "", fields=resume_fields,
            ctx=ctx, license_id=license_id, language=language,
        )
    if resume_entity == "deal":
        needed = required_permission(resume_action or "", "deal")
        if needed is None or needed not in set(permission_keys) or not _oa_allows(ctx.oa, needed):
            catalog = await client.permission_catalog()
            return ChatReply(text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language, oa=ctx.oa,
                requested_action=resume_action, requested_entity="deal",
            ))
        return await _apply_deal_create(
            client, contact=chosen, fields=resume_fields,
            ctx=ctx, license_id=license_id, language=language,
        )
    # Not a shape this function ever wrote itself — never crash on it.
    return ChatReply(text=unavailable_reply(language))


async def _handle_customer_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext,
    license_id, language: str, permission_keys: list[str] | None = None,
) -> ChatReply:
    action = intent.get("action")
    fields = intent.get("fields") or {}
    license_id = str(license_id)

    if action == "create":
        editable = {
            k: v for k, v in fields.items()
            if k in ("first_name", "last_name", "phone", "email", "address", "notes")
            and v not in (None, "")
        }
        # Owner's explicit rule: a walk-in customer record must have at
        # least a last name AND a phone number — a first name alone is not
        # enough to reliably identify someone later (very common shared
        # first names), and a phone is how staff actually follow up.
        #
        # This check exists precisely because the AI's own "missing" list
        # cannot be trusted to always catch it — but when it doesn't, the
        # conversation must still continue naturally: register a
        # pending_intent here too, the same as spec 6.4's generic
        # slot-filling path does, so a bare follow-up answer ("ใจดี") is
        # understood as completing THIS request rather than parsed as a
        # new, meaningless message. Without this, the hard check would
        # silently break the exact continuity Phase 6 was built to provide.
        from .phone import phone_problem

        problem = phone_problem(editable.get("phone"))
        if problem:
            # Ask for the phone again with the reason, keeping everything else typed.
            editable.pop("phone", None)
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa,
                action="create", entity="customer", fields={**editable, **_private_fields(fields)},
                missing=["phone"], ttl_seconds=PENDING_INTENT_TTL_S,
            )
            return _phone_reply(problem, str(fields.get("phone")), language)
        still_missing = [f for f in CUSTOMER_CREATE.required if not editable.get(f)]
        if still_missing:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa,
                action="create", entity="customer", fields={**editable, **_private_fields(fields)},
                missing=still_missing, ttl_seconds=PENDING_INTENT_TTL_S,
            )
            return ChatReply(text=ask_for_missing(still_missing, language), intent=intent)
        try:
            owner = await _member_id_of(client, license_id, ctx)
            if owner:
                editable = {**editable, "owner_member_id": owner}
            row = await client.create_customer(license_id, editable, actor_id=ctx.chann_uid)
        except Exception as exc:  # noqa: BLE001
            structured = getattr(exc, "structured", None) or {}
            if structured.get("error") == "duplicate":
                # User review (4 Sep 2026): say which record, offer the moves.
                return await _handle_customer_duplicate(
                    client, ctx=ctx, license_id=license_id, language=language,
                    duplicate=structured, new_fields=editable,
                )
            if _is_conflict(exc):
                log.warning("customer create refused (409) for %s: %s", ctx.chann_uid, exc)
                return ChatReply(text=_t(CUSTOMER_NEEDS_SOMETHING, language), intent=intent)
            raise
        await _remember_customer(client, ctx, row)

        then_deal = fields.get("_then_deal")
        if then_deal is not None:
            # The customer was finished so a deal could follow ("ใส่เบอร์ก่อน"
            # to _offer_draft_customer_deal): both, in one reply.
            deal_reply = await _apply_deal_create(
                client, contact=row, fields=dict(then_deal), ctx=ctx, license_id=license_id, language=language,
            )
            return ChatReply(
                text=_t(CUSTOMER_CREATED, language).format(name=f" {_display_name(row)} ") + "\n" + deal_reply.text,
                entity_type=deal_reply.entity_type or "customer", entity_id=deal_reply.entity_id or row["id"],
                intent=intent, quick_replies=deal_reply.quick_replies,
            )

        # A date in what they said is almost always an appointment.
        # Offered rather than made: guessing wrong puts a reminder in
        # someone's diary they did not ask for, and the cost of asking is
        # one tap.
        reply_text = _t(CUSTOMER_CREATED, language).format(
            name=f" {_display_name(row)} "
        )
        # From the note, which after recover_free_text holds the person's
        # own words rather than the model's retyping of them.
        from .thai_datetime import parse_thai_date

        suggested = parse_thai_date(
            str(fields.get("notes") or ""), local_today(),
        )
        quick: list[tuple[str, str]] = []
        if suggested:
            from .thai_datetime import format_thai_date

            # Shown in Thai; the button still carries ISO because that
            # text is parsed back when tapped.
            reply_text += "\n" + _t(APPOINTMENT_OFFER, language).format(
                date=format_thai_date(suggested),
            )
            quick = [
                ("ตั้งนัด", f"เตือน {row['customer_id']} {suggested.isoformat()}"),
                ("ไม่ต้อง", "ดูนัดหมาย"),
            ]

        return ChatReply(
            text=reply_text,
            entity_type="customer", entity_id=row["id"], intent=intent,
            quick_replies=quick,
        )

    if action == "archive":
        return await _handle_lead_archive_request(
            client, ctx=ctx, license_id=license_id, name=(fields.get("target_name") or "").strip() or None,
            permission_keys=list(permission_keys or []), language=language,
        )
    if action in ("update", "promote"):
        target_name = fields.get("target_name")
        row, err = await _find_one_customer_by_name(
            client, license_id, target_name, language,
            ctx=ctx, resume_entity="customer", resume_action=action, resume_fields=fields,
        )
        if err is not None:
            return err
        return await _apply_customer_action(
            client, chosen_row=row, action=action, fields=fields,
            ctx=ctx, license_id=license_id, language=language,
        )

    if action in READ_ACTIONS:
        # "ขอดูข้อมูลคุณสมหมาย" (owner, 8 Sep 2026). The typed "ดูลูกค้า
        # สมชาย" has worked since Phase 9; the same sentence understood by
        # the model fell off the end of this function and was answered
        # "ในแชทยังทำรายการนี้ไม่ได้" about a thing the chat plainly does.
        #
        # One name, one lookup: _handle_customer_detail already reads a
        # code, a name or a phone number, and already owns the "not found"
        # and "several match" replies with the buttons that pick a person —
        # so a read routed here can never disagree with a read typed.
        if ctx.oa == "customer":
            # customer.read is in scope for the Customer OA (spec §6), but
            # it means "my own record" there, never the shop's book — a
            # customer must never be handed the tenant's whole contact
            # list because the model labelled their question a read.
            return await _handle_customer_profile_view(
                client, ctx=ctx, license_id=license_id, language=language,
            )
        held = list(permission_keys or []) or ["customer.read"]
        who = _named_customer(fields)
        if who:
            return await _handle_customer_detail(
                client, license_id=license_id, code=who,
                permission_keys=held, language=language, ctx=ctx,
            )
        return await _handle_customer_list(
            client, license_id=license_id, permission_keys=held, language=language,
        )

    return _no_handler_reply(intent, language, ctx.oa)


# "คุณสมหมาย", "นายสมชาย", "Mr Somchai": the model copies the honorific
# the person typed, and a substring lookup for "คุณสมหมาย" matches nobody
# because the record holds "สมหมาย". Stripped only from the FRONT and only
# when something is left, so a customer actually called "คุณ" survives.
_HONORIFICS = ("คุณ", "คุน", "นางสาว", "น.ส.", "นาย", "นาง", "ท่าน", "พี่", "น้อง",
               "mr.", "mr", "mrs.", "mrs", "ms.", "ms", "miss", "khun", "k.")


def _strip_honorific(name: str) -> str:
    text = (name or "").strip()
    for lead in sorted(_HONORIFICS, key=len, reverse=True):
        if text.lower().startswith(lead) and len(text) > len(lead):
            return text[len(lead):].strip(" .")
    return text


def _named_customer(fields: dict) -> str:
    """Who a read intent is about — a code, a name or a phone number, in
    whichever of the model's field names it landed in. Empty when the
    person named nobody, which means "the list"."""
    for key in ("target_name", "customer_id", "code", "name", "query", "search",
                "first_name", "last_name", "phone"):
        value = str((fields or {}).get(key) or "").strip()
        if value:
            return _strip_polite_tail(_strip_honorific(value))
    return ""


def _keys_this_oa_can_use(permission_keys, oa: str) -> list[str]:
    """The keys the person holds AND this channel can act on.

    The prompt used to list every key the person held, whatever channel
    they were on: a shop owner messaging the technician OA was described
    to the model as holding 46 keys, 38 of which that channel forbids —
    approval.approve, chat_session.reply, audit_log.view and the rest
    (measured 10 ก.ย. 2569). Every one of those invites a proposal the
    gate refuses a moment later, and reads to the model as capability the
    conversation does not have.

    The gate is unchanged; this only stops advertising what it will
    refuse. On the sales OA nothing is filtered, because nothing there is
    out of scope.
    """
    return [k for k in (permission_keys or []) if _oa_allows(oa, k)]


def _private_fields(fields: dict) -> dict:
    """The "_then_deal" / "_abandoned" carriers a pending intent holds
    alongside the person's own fields — kept when the flow is re-asked."""
    return {k: v for k, v in (fields or {}).items() if str(k).startswith("_") and v is not None}


async def _apply_customer_action(
    client: DataClient, *, chosen_row: dict, action: str, fields: dict,
    ctx: ResolvedContext, license_id: str, language: str,
) -> ChatReply:
    """The actual update/promote work, factored out of _handle_customer_intent
    so a resumed disambiguation (9.7 follow-up: multiple customers shared a
    name, the user picked one from a numbered list) can reach it directly
    with the already-resolved row, instead of repeating the name lookup a
    second time against a customer that's already been chosen."""
    row = chosen_row
    if action == "promote":
        try:
            updated = await client.promote_customer(
                license_id, row["id"], actor_id=ctx.chann_uid,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(
                    name=_display_name(row)
                ))
            raise
        await _remember_customer(client, ctx, updated)
        return ChatReply(
            text=_t(CUSTOMER_PROMOTED, language).format(name=_display_name(updated)),
            entity_type="customer", entity_id=updated["id"],
        )
    editable = {
        k: v for k, v in fields.items()
        if k in ("first_name", "last_name", "phone", "email", "address", "notes")
        and v not in (None, "")
    }
    if not editable:
        return ChatReply(text=_t(CUSTOMER_NEEDS_SOMETHING, language))
    from .phone import phone_problem

    problem = phone_problem(editable.get("phone"))
    if problem:
        return _phone_reply(problem, str(editable.get("phone")), language)
    try:
        updated = await client.update_customer(
            license_id, row["id"], editable, actor_id=ctx.chann_uid,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(
                name=_display_name(row)
            ))
        raise
    await _remember_customer(client, ctx, updated)
    return ChatReply(
        text=_t(CUSTOMER_UPDATED, language).format(name=f" {_display_name(updated)} "),
        entity_type="customer", entity_id=updated["id"],
    )


async def _remember_customer(client: DataClient, ctx: ResolvedContext, row: dict) -> None:
    """Records "the customer we were just talking about", so a follow-up
    like "สร้างดีล" with no name at all can fall back to them instead of
    refusing. See cache.k_last_customer_ref for why this can't just reuse
    pending_intent.

    Per SHOP: without a license there is one slot per person per OA, and a
    customer opened in one shop was still "the customer we were just
    talking about" after switching to another (10 ก.ย. 2569). No license,
    nothing remembered — a conversation with no shop chosen has no record
    to be on.
    """
    lic = str(getattr(ctx, "license_id", "") or "")
    if not lic:
        return
    try:
        await client.set_last_customer_ref(
            ctx.chann_uid, ctx.oa, license_id=lic, customer_id=row["id"],
            name=_display_name(row), ttl_seconds=LAST_CUSTOMER_REF_TTL_S,
        )
    except Exception:
        # Best-effort, same as _remember_entity: failing to remember must
        # never break the view that triggered it.
        log.exception("failed to remember last customer ref")


async def _last_customer_ref(client: DataClient, ctx: ResolvedContext) -> dict | None:
    """The customer in context FOR THIS SHOP, or None."""
    lic = str(getattr(ctx, "license_id", "") or "")
    if not lic:
        return None
    return await client.get_last_customer_ref(ctx.chann_uid, ctx.oa, license_id=lic)


async def _last_entity_ref(client: DataClient, ctx: ResolvedContext) -> dict | None:
    """The record in context FOR THIS SHOP, or None."""
    lic = str(getattr(ctx, "license_id", "") or "")
    if not lic:
        return None
    return await client.get_last_entity_ref(ctx.chann_uid, ctx.oa, license_id=lic)


# An hour, not ten minutes. Ten was long enough for a demo and too short
# for work: someone confirms a customer, takes a phone call, and comes
# back to open a deal — and was told they had no permission, because the
# context had expired and the message fell through to the AI.
LAST_ENTITY_REF_TTL_S = 3600


async def _remember_entity(
    client: DataClient, ctx: ResolvedContext, *, entity_type: str, entity_id, code: str,
    extra: dict | None = None,
) -> None:
    """Records "the record we were just looking at" — generalises
    _remember_customer to deals and quotes, for notes and reminders. See
    cache.k_last_entity_ref for why this is a separate key from
    last_customer_ref rather than reusing it."""
    lic = str(getattr(ctx, "license_id", "") or "")
    if not lic:
        return
    try:
        await client.set_last_entity_ref(
            ctx.chann_uid, ctx.oa, license_id=lic, entity_type=entity_type,
            entity_id=str(entity_id), code=code,
            ttl_seconds=LAST_ENTITY_REF_TTL_S, extra=extra,
        )
    except Exception:
        # Best-effort: failing to cache "what we were just looking at" must
        # never break the detail view that triggered it.
        log.exception("failed to remember last entity ref")


LAST_CUSTOMER_REF_TTL_S = 3600

DEAL_CREATED_FROM_CONTEXT = {
    "th": "สร้างดีล {deal_id} สำหรับ {name} (ลูกค้าที่เพิ่งคุยถึง) เรียบร้อยแล้ว",
    "en": "Created deal {deal_id} for {name} (the customer just mentioned).",
}


async def _handle_deal_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext,
    license_id, permission_keys: list[str], language: str, message: str | None = None,
    abandoned: dict | None = None,
) -> ChatReply:
    action = intent.get("action")
    fields = dict(intent.get("fields") or {})
    abandoned = fields.pop("_abandoned", None) or abandoned
    license_id = str(license_id)
    if action == "create":
        target_name = (fields.get("target_name") or "").strip()
        # User review (4 Sep 2026): amount and closing date are read from
        # the message itself, with the model's values checked against it.
        deal_fields, ambiguous = _deal_fields_from_message(message or "", fields)
        if ambiguous:
            return await _ask_deal_ambiguity(
                client, ctx=ctx, message=message or "", target_name=target_name or None,
                fields=deal_fields, ambiguous=ambiguous, language=language,
            )
        if not target_name:
            last_ref = await _last_customer_ref(client, ctx)
            if last_ref is None:
                return ChatReply(text=_t(DEAL_NEEDS_TARGET_NAME, language), intent=intent)
            contact = {"id": last_ref["customer_id"], "first_name": last_ref["name"]}
            # Confirmed, not assumed: the wrong person on a deal was the
            # review's complaint, and it came from exactly this fallback.
            return await _confirm_deal_for_context(client, ctx=ctx, contact=contact, fields=deal_fields, language=language)
        contact, err = await _find_one_customer_by_name(
            client, license_id, target_name, language,
            ctx=ctx, resume_entity="deal", resume_action="create",
            resume_fields={**fields, **{k: str(v) for k, v in deal_fields.items()}},
        )
        if err is not None:
            if _draft_matches_name(abandoned, target_name) and err.text == _t(CUSTOMER_NOT_FOUND, language).format(name=target_name):
                # The name is the customer whose creation was just
                # abandoned — offered, not "not found" (owner test, 8 Sep 2026).
                return await _offer_draft_customer_deal(
                    client, ctx=ctx, draft=abandoned, deal_fields=deal_fields, language=language,
                )
            return err
        return await _apply_deal_create(
            client, contact=contact, fields=deal_fields, ctx=ctx,
            license_id=license_id, language=language,
        )

    if action in READ_ACTIONS:
        # The typed "ข้อมูลดีล D-2026-0001" and "รายการดีล" have always
        # worked; the model's reading of the same request did not.
        code = str(fields.get("deal_code") or fields.get("code") or fields.get("deal_id") or "").strip().upper()
        if not code:
            found = DEAL_ID_RE.search(message or "")
            code = found.group(0).upper() if found else ""
        if code:
            return await _handle_deal_detail(
                client, license_id=license_id, code=code,
                permission_keys=permission_keys, language=language, ctx=ctx,
            )
        if message and _asks_latest_deal(message):
            # "ขอข้อมูลดีลล่าสุด" — the deal in play, never a deal called
            # "ล่าสุด"; the same handler the trigger reaches.
            return await _handle_latest_deal(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        who = _strip_polite_tail(_strip_honorific(str(fields.get("target_name") or "").strip()))
        return await _handle_deal_list(
            client, ctx=ctx, license_id=license_id, permission_keys=permission_keys,
            language=language, for_customer=who or None,
        )

    return _no_handler_reply(intent, language, ctx.oa)


async def _apply_deal_create(
    client: DataClient, *, contact: dict, fields: dict, ctx: ResolvedContext,
    license_id: str, language: str, used_context: bool = False,
) -> ChatReply:
    """The actual deal-creation work, factored out of _handle_deal_intent
    so a resumed disambiguation (multiple customers shared a name, the
    user picked one from a numbered list) can reach it directly with the
    already-resolved contact, instead of repeating the name lookup."""
    fields = _normalise_deal_fields(fields)
    payload: dict = {"contact_id": contact["id"], "notes": fields.get("notes")}
    if fields.get("amount") is not None:
        payload["amount"] = str(fields["amount"])
        payload["currency"] = fields.get("currency") or "THB"
    if fields.get("expected_close_date"):
        payload["expected_close_date"] = fields["expected_close_date"].isoformat()
    try:
        owner = await _member_id_of(client, license_id, ctx)
        if owner:
            payload = {**payload, "owner_member_id": owner}
        row = await client.create_deal(license_id, payload, actor_id=ctx.chann_uid)
    except DataTierError as exc:
        # A customer holds one open deal at a time. Saying which one, with
        # a button to open it, is the useful answer — a bare "conflict"
        # leaves them to go and find it.
        duplicate = exc.structured or {}
        if duplicate.get("error") == "duplicate":
            code = duplicate.get("existing_code", "")
            return ChatReply(
                text=_t(DEAL_ALREADY_OPEN, language).format(code=code),
                quick_replies=[("ดูดีล", f"ข้อมูลดีล {code}")] if code else [],
            )
        if _is_not_found(exc):
            return ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(
                name=_display_name(contact)
            ))
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(
                name=_display_name(contact)
            ))
        raise
    # Remember it. The very next thing someone does after creating a deal
    # is quote it, and without this "สร้างใบเสนอราคา" had no idea which
    # deal was meant — it fell through to the AI, which had no idea
    # either, and the person got a list of permissions.
    await _remember_entity(
        client, ctx, entity_type="deal", entity_id=row["id"], code=row["deal_id"],
    )

    template = DEAL_CREATED_FROM_CONTEXT if used_context else DEAL_CREATED
    return ChatReply(
        text=_t(template, language).format(
            deal_id=row["deal_id"], name=_display_name(contact),
        ) + _deal_details_line(fields, language),
        entity_type="deal", entity_id=row["id"],
    )


def _normalise_deal_fields(fields: dict | None) -> dict:
    """Fields may arrive as Decimals/dates (fresh) or as strings (resumed
    from a pending intent). One shape out."""
    from decimal import Decimal, InvalidOperation

    from .deal_fields import parse_amount
    from .thai_datetime import parse_thai_date

    out = dict(fields or {})
    amount = out.get("amount")
    if amount not in (None, "") and not isinstance(amount, Decimal):
        try:
            out["amount"] = Decimal(str(amount))
        except InvalidOperation:
            parsed, _, _ = parse_amount(str(amount))
            out["amount"] = parsed
    if out.get("amount") is None:
        out.pop("amount", None)
    close = out.get("expected_close_date")
    if isinstance(close, str) and close:
        out["expected_close_date"] = parse_thai_date(close, local_today())
    if not out.get("expected_close_date"):
        out.pop("expected_close_date", None)
    return out


PRODUCT_SAVED = {
    "th": "บันทึกสินค้า {name} (รหัส {code}) เรียบร้อยแล้ว",
    "en": "Saved product {name} (code {code}).",
}
PRODUCT_NEEDS_ID_AND_NAME = {
    "th": "กรุณาระบุรหัสสินค้าและชื่อสินค้า",
    "en": "Please provide both a product code and a product name.",
}
PRODUCT_INVALID_VALUE = {
    "th": "ข้อมูลราคาหรือรายละเอียดสินค้าไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง (เช่น ราคาต้องเป็นตัวเลขล้วน)",
    "en": "The price or another product value doesn't look right — please check and try again "
          "(e.g. the price must be numbers only).",
}


async def _handle_product_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext,
    license_id, language: str, permission_keys: list[str] | None = None,
) -> ChatReply:
    """Phase 7 master data, made reachable from chat. ProductRepository.
    upsert (already idempotent on product_id since 7.5) means create and
    update are the same call — there is no meaningful difference between
    "add a product" and "add a product that happens to already exist" from
    the chat side."""
    action = intent.get("action")
    fields = intent.get("fields") or {}
    license_id = str(license_id)

    if action in READ_ACTIONS:
        # "มีสินค้าอะไรบ้างที่เป็น พัดลม", understood by the model as viewing
        # products, used to be told the chat could not do it (owner test,
        # 8 Sep 2026). The catalogue list, filtered by whatever was named.
        query = next(
            (str(fields.get(k)).strip() for k in ("product_name", "target_name", "query", "name", "category")
             if fields.get(k)), None,
        )
        return await _handle_product_list(
            client, license_id=license_id, permission_keys=list(permission_keys or []) or ["product.manage"],
            language=language, query=query,
        )
    if action not in ("create", "update"):
        return _no_handler_reply(intent, language, ctx.oa)

    product_id = (fields.get("product_id") or "").strip()
    product_name = (fields.get("product_name") or "").strip()
    if not product_id or not product_name:
        return ChatReply(text=_t(PRODUCT_NEEDS_ID_AND_NAME, language), intent=intent)

    payload = {
        "product_id": product_id,
        "product_name": product_name,
        "sku": fields.get("sku"),
        "category": fields.get("category"),
        "unit_price": fields.get("unit_price"),
        "description": fields.get("description"),
    }
    try:
        row = await client.upsert_product(license_id, product_id, payload, actor_id=ctx.chann_uid)
    except Exception as exc:  # noqa: BLE001
        if _is_conflict(exc):
            return ChatReply(text=_t(PRODUCT_INVALID_VALUE, language), intent=intent)
        raise
    return ChatReply(
        text=_t(PRODUCT_SAVED, language).format(name=row["product_name"], code=row["product_id"]),
        entity_type="product", entity_id=row["id"], intent=intent,
    )


QUOTE_NEEDS_DEAL_CODE = {
    "th": "กรุณาระบุรหัสดีลที่จะสร้างใบเสนอราคา เช่น D-2026-0001",
    "en": "Please provide the deal code to create a quote from, e.g. D-2026-0001",
}
QUOTE_DEAL_NOT_FOUND = {
    "th": "ไม่พบดีลรหัส {deal_id} ในบริษัทนี้",
    "en": "No deal {deal_id} was found in this company.",
}
QUOTE_CREATED = {
    "th": "สร้างใบเสนอราคา {quote_id} จากดีล {deal_id} เรียบร้อยแล้ว",
    "en": "Created quote {quote_id} from deal {deal_id}.",
}


async def _handle_quote_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext,
    license_id, language: str, permission_keys: list[str] | None = None,
    message: str = "",
) -> ChatReply:
    """10.1's quote-from-deal creation only — the DOCX-authoring/AI-mapping/
    SmartBrowz-render pipeline (10.4-10.6) isn't wired to chat at all yet;
    see data/chann_data/repositories/phase10.py's module docstring for why.
    A quote created here exists in "draft" status with no rendered document
    (Quote.generated_document_id nullable, by 10.3's design) until that
    pipeline exists."""
    action = intent.get("action")
    fields = intent.get("fields") or {}
    license_id = str(license_id)
    held = list(permission_keys or []) or ["quote.read"]

    if action in READ_ACTIONS:
        # "ขอดูใบเสนอราคา Q-2026-0001" / "ขอดูใบเสนอราคาล่าสุด": the same
        # detail and list handlers the typed triggers reach.
        code = str(fields.get("quote_code") or fields.get("code") or fields.get("quote_id") or "").strip().upper()
        if not code:
            found = QUOTE_CODE_RE.search(message or "")
            code = found.group(1).upper() if found else ""
        if not code:
            # "the latest one" is the quote this conversation is already on;
            # with nothing in context the list is the honest answer, not a
            # guess at which quote was meant.
            try:
                ref = await _last_entity_ref(client, ctx)
            except Exception:  # noqa: BLE001
                ref = None
            if ref and ref.get("entity_type") == "quote" and ref.get("code"):
                code = str(ref["code"]).upper()
        if code:
            return await _handle_quote_detail(
                client, ctx=ctx, license_id=license_id, code=code,
                permission_keys=held, language=language,
            )
        return await _handle_quote_list(
            client, license_id=license_id, permission_keys=held, language=language,
        )

    if action != "create":
        return _no_handler_reply(intent, language, ctx.oa)

    # One creation path. The model's job is to recognise the request; what
    # happens next — the deal in context when no code was said, the
    # duplicate, the empty deal, the download button — belongs to the
    # handler the buttons already reach. This road used to be a second,
    # weaker copy: it demanded a deal_code with no context fallback, and
    # re-raised a data error that was not a not-found.
    return await _handle_quote_create_direct(
        client, ctx=ctx, license_id=license_id, message=message,
        permission_keys=held, language=language,
        deal_code=str(fields.get("deal_code") or fields.get("code") or "").strip().upper() or None,
    )

# How long an unanswered question stays open. Long enough that a user can
# finish another chat and come back; short enough that tomorrow's unrelated
# "0812345678" is never silently attached to yesterday's half-built record.
PENDING_INTENT_TTL_S = 600


def _is_continuation(pending: dict | None, intent: dict) -> bool:
    """Is this message the answer to the question the last turn asked?

    Conservative on purpose. Wrongly treating a NEW request as a continuation
    would file the user's words into an unrelated record, which is far worse
    than wrongly treating a continuation as new — that just re-asks.
    """
    if not pending:
        return False
    entity = intent.get("entity")
    if entity and entity != pending.get("entity"):
        return False            # a different subject entirely
    if (intent.get("action") or "") == "suggest":
        return False            # explicitly asking something else
    if intent.get("action") and intent["action"] != pending.get("action"):
        return False
    fields = intent.get("fields") or {}
    if not fields:
        return False
    wanted = set(pending.get("missing") or [])
    # Either it supplies something that was actually asked for, or it supplies
    # values without naming any entity at all — the shape of a bare answer.
    if entity == pending.get("entity") == "customer" and pending.get("action") == "create":
        # An address while we ask for a phone is still this customer's
        # information. A different explicit name starts a new customer.
        previous = pending.get("fields") or {}
        if any(fields.get(k) and previous.get(k) and fields[k] != previous[k]
               for k in ("first_name", "last_name")):
            return False
        if set(fields) & {"phone", "email", "address", "notes"}:
            return True
    if entity == pending.get("entity") == "followup" and pending.get("action") == "create":
        # A reminder's day and its hour are one answer. "บ่าย 2" against a
        # pending "which day?" is this appointment, not a new one — read as
        # a switch it answered "เปลี่ยนจากตั้งนัดเป็นตั้งนัดแล้วครับ" and
        # dropped the name already collected.
        if set(fields) & {"due_date", "due_time", "notes"}:
            return True
    return bool(wanted & set(fields)) or not entity


def _merge_pending(pending: dict, intent: dict) -> dict:
    """Fold the new answer into the action already under way."""
    fields = {**(pending.get("fields") or {}), **(intent.get("fields") or {})}
    still_missing = [
        f for f in (pending.get("missing") or [])
        if fields.get(f) in (None, "")
    ]
    return {
        "action": pending.get("action") or intent.get("action"),
        "entity": pending.get("entity") or intent.get("entity"),
        "fields": fields,
        "missing": still_missing,
    }


# ---------------------------------------------------------------- Storefront
#
# 9.4 — Lazada-style cross-tenant product search. Deliberately independent of
# ctx.resolution: a customer already linked to one shop can still browse and
# become a Lead at another, and someone with NO link yet can browse before
# ever choosing one. Handled entirely before the tenant-resolution gate that
# governs everything else in this function.
#
# Spec 9.4/15's own chat wording is "พิมพ์ \"ค้นหา [keyword]\"" — matched
# directly, not sent through the AI parser: free-text product search is a
# keyword lookup, not something that benefits from a model call, and a
# trigger word keeps this from firing on ordinary conversation.
STOREFRONT_SEARCH_TRIGGERS = ("ค้นหาสินค้า", "ค้นหา", "หาสินค้า", "หาซื้อ", "อยากได้", "อยากซื้อ", "มองหา", "search", "looking for", "find")
# "หาแอร์ 12000": "หา" alone is too short to be a trigger, so it counts
# only in front of a thing one buys.
_STOREFRONT_FIND_RE = re.compile(
    r"^หา\s*((?:" + "|".join(sorted(
        {"แอร์", "ตู้เย็น", "เครื่องซักผ้า", "ทีวี", "พัดลม", "เครื่องทำน้ำอุ่น", "ไมโครเวฟ", "ปั๊มน้ำ", "เครื่องกรองน้ำ", "สินค้า", "ของ", "รุ่น", "อะไหล่"},
        key=len, reverse=True,
    )) + r").*)$",
)
# Spec page 1, tile 2: on the customer OA "สินค้าทั้งหมด" is the storefront
# (every shop's products), not the shop's own product list a staff member
# gets from the same words on the sales OA.
STOREFRONT_BROWSE_EXTRA = ("ดูสินค้าทั้งหมด", "all products", "browse products")
STOREFRONT_BROWSE_LIMIT = 10
STOREFRONT_EMPTY = {
    "th": "ยังไม่มีสินค้าในระบบครับ ลองใหม่ภายหลัง หรือพิมพ์ \"ค้นหา\" ตามด้วยชื่อสินค้า",
    "en": "No products are listed yet. Try later, or type \"search\" and a product name.",
}
STOREFRONT_RESULTS_LIMIT = 5
STOREFRONT_PENDING_TTL_S = 300

STOREFRONT_NO_QUERY = {
    "th": 'พิมพ์ "ค้นหา" ตามด้วยชื่อสินค้าที่ต้องการ เช่น "ค้นหา พัดลม"',
    "en": 'Type "search" followed by what you\'re looking for, e.g. "search fan"',
}
STOREFRONT_NO_RESULTS = {
    "th": "ไม่พบสินค้าที่ตรงกับ \"{query}\"",
    "en": 'No products matched "{query}"',
}
STOREFRONT_RESULTS_HEADER = {
    "th": "พบสินค้าดังนี้ พิมพ์หมายเลขเพื่อสนใจสินค้านั้น:",
    "en": "Found these products — type the number to express interest:",
}
STOREFRONT_INVALID_SELECTION = {
    "th": "กรุณาพิมพ์หมายเลข 1-{n} จากรายการที่แนะนำ",
    "en": "Please type a number from 1 to {n} from the list shown",
}
STOREFRONT_INTEREST_RECORDED = {
    "th": "บันทึกความสนใจใน \"{product}\" จากร้าน {shop} เรียบร้อยแล้ว "
          "ทางร้านจะติดต่อกลับ",
    "en": 'Recorded your interest in "{product}" from {shop} — they will '
          "be in touch.",
}
STOREFRONT_CONFIRM_TTL_S = 120
# Reported live: a bare word like "พัดลม" is genuinely ambiguous for a
# customer — do they want to search for one to buy, ask about a repair
# ticket they already filed for one, or something else entirely? Jumping
# straight to a product list assumes the first meaning with no chance to
# say otherwise. This asks first; only an explicit "ค้นหา [term]" (already
# an unambiguous request) skips straight to results.
STOREFRONT_CONFIRM_PROMPT = {
    "th": "พบสินค้าที่เกี่ยวข้องกับ \"{query}\" ต้องการดูรายการสินค้าไหม? "
          'พิมพ์ "ใช่" หรือ "ค้นหา {query}" เพื่อดูรายการ',
    "en": 'Found products related to "{query}" — want to see the list? '
          'Type "yes" or "search {query}" to see them.',
}
STOREFRONT_CONFIRM_WORDS = ("ใช่", "โอเค", "เอา", "ต้องการ", "yes", "y", "ok")


def _parse_storefront_query(message: str) -> str | None:
    text = (message or "").strip()
    lowered = text.lower()
    for trigger in STOREFRONT_SEARCH_TRIGGERS:
        if lowered.startswith(trigger.lower()):
            return text[len(trigger):].strip(" \t:：-—")
    found = _STOREFRONT_FIND_RE.match(text)
    if found:
        return found.group(1).strip()
    return None


def _format_storefront_results(results: list[dict], language: str) -> str:
    lines = [_t(STOREFRONT_RESULTS_HEADER, language)]
    for i, r in enumerate(results, start=1):
        price = f" — {r['unit_price']}" if r.get("unit_price") is not None else ""
        lines.append(f"{i}. {r['product_name']} ({r['company_name']}){price}")
    return "\n".join(lines)


def _is_storefront_confirmation(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(text == w or text.startswith(w) for w in STOREFRONT_CONFIRM_WORDS)


async def _storefront_browse_reply(
    client: DataClient, *, ctx: ResolvedContext, language: str,
) -> ChatReply:
    """The whole storefront as a numbered list, a pick pending — reached
    from the customer OA pre-pass and from the customer branch of
    handle_chat_message (a quick-reply button lands there directly)."""
    results = await client.storefront_browse(limit=STOREFRONT_BROWSE_LIMIT)
    if not results:
        return ChatReply(text=_t(STOREFRONT_EMPTY, language))
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa,
        action="select", entity="storefront", fields={"options": results}, missing=[],
        ttl_seconds=STOREFRONT_PENDING_TTL_S,
    )
    return ChatReply(text=_format_storefront_results(results, language))


async def maybe_handle_storefront(
    client: DataClient, *, message: str, ctx: ResolvedContext, language: str,
) -> ChatReply | None:
    """Returns a reply if this message was storefront browsing (a search, a
    confirmation of one just offered, or a selection from a list already
    shown), else None so the caller proceeds with its normal tenant-scoped
    handling."""
    pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)

    if _matches_phrase(message, PRODUCT_LIST_PHRASES + STOREFRONT_BROWSE_EXTRA):
        if pending is not None and pending.get("entity") in ("storefront", "storefront_confirm"):
            await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return await _storefront_browse_reply(client, ctx=ctx, language=language)

    if pending is not None and pending.get("entity") == "storefront_confirm":
        cached = pending.get("fields") or {}
        cached_results = cached.get("results") or []
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        # Either an explicit yes, or the customer just re-typed "ค้นหา ..."
        # themselves — both mean the same thing here.
        if _is_storefront_confirmation(message) or _parse_storefront_query(message):
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa,
                action="select", entity="storefront",
                fields={"options": cached_results}, missing=[],
                ttl_seconds=STOREFRONT_PENDING_TTL_S,
            )
            return ChatReply(text=_format_storefront_results(cached_results, language))
        # Anything else means they meant something other than a product
        # search ("พัดลมที่แจ้งซ่อมไว้เป็นยังไงบ้าง" and similar) — drop it
        # and let the message be handled normally instead of insisting.
        return None

    if pending is not None and pending.get("entity") == "storefront":
        options = pending.get("fields", {}).get("options") or []
        text = (message or "").strip()
        if not text.isdigit() and (
            _is_customer_command(text) or _looks_like_fault(text) or _looks_like_service_request(text)
            or _looks_like_a_question(text) or _is_only_a_greeting(text) or len(text) > 30
        ):
            # Not a pick: the person moved on ("แจ้งซ่อม", "วิธีใช้", a
            # fault, a question). The list is dropped and the message is
            # whatever it is — it used to answer "กรุณาพิมพ์หมายเลข" to
            # everything for five minutes (review, 6 Sep 2026). A short
            # "เอาอันแรก" is still a pick that needs its number.
            await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
            return None
        if not text.isdigit() or not (1 <= int(text) <= len(options)):
            return ChatReply(
                text=_t(STOREFRONT_INVALID_SELECTION, language).format(n=len(options))
            )
        chosen = options[int(text) - 1]
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        row = await storefront_service.record_interest(
            client, chann_uid=ctx.chann_uid, license_id=chosen["license_id"],
            product_name=chosen["product_name"], company_name=chosen.get("company_name"),
            display_name=ctx.display_name, language=language,
        )
        return ChatReply(
            text=_t(STOREFRONT_INTEREST_RECORDED, language).format(
                product=chosen["product_name"], shop=chosen["company_name"],
            ),
            entity_type="customer", entity_id=row["id"],
        )

    query = _parse_storefront_query(message)
    if query is None:
        # A bare word ("พัดลม", no "ค้นหา" prefix) is genuinely ambiguous —
        # confirm before committing to "this was a product search" rather
        # than assuming it and listing results outright. A company code
        # (its own distinct alphabet) or anything too short to be a real
        # search term is never intercepted, so shop-code/shop-name lookup
        # in the registration flow is completely unaffected.
        text = (message or "").strip()
        if len(text) < 2 or COMPANY_CODE_RE.match(text.upper()):
            return None
        if _is_customer_command(text) or _is_menu_tile(text) or _looks_like_fault(text) or _looks_like_phone(text):
            # A tile, a command, a fault: not a product to search for.
            return None
        results = await client.storefront_search(text, limit=STOREFRONT_RESULTS_LIMIT)
        if not results:
            return None
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa,
            action="confirm", entity="storefront_confirm",
            fields={"query": text, "results": results}, missing=[],
            ttl_seconds=STOREFRONT_CONFIRM_TTL_S,
        )
        return ChatReply(text=_t(STOREFRONT_CONFIRM_PROMPT, language).format(query=text))
    if not query:
        return ChatReply(text=_t(STOREFRONT_NO_QUERY, language))

    results = await client.storefront_search(query, limit=STOREFRONT_RESULTS_LIMIT)
    if not results:
        return ChatReply(text=_t(STOREFRONT_NO_RESULTS, language).format(query=query))

    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa,
        action="select", entity="storefront", fields={"options": results}, missing=[],
        ttl_seconds=STOREFRONT_PENDING_TTL_S,
    )
    return ChatReply(text=_format_storefront_results(results, language))



# ------------------------------------------------ technician situations (B12)
#
# What a technician says from the road or the doorstep, none of which had a
# handler (review, 6 Sep 2026: 18 sentences → "ยังไม่แน่ใจ"): on the way,
# running late, nobody home, parts needed, cannot finish today, move the
# visit. Each is acknowledged, written on the job as a note, and the
# dispatcher hears about the ones that change the plan.
_SITUATION_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cannot_finish", ("ทำไม่จบ", "ไม่จบ", "ทำไม่เสร็จ", "ไม่เสร็จวันนี้", "ทำไม่ทัน", "ไม่ทันวันนี้", "ต้องมาต่อ", "มาต่ออีกวัน", "มาต่อพรุ่งนี้",
                       "ต้องกลับมา", "กลับมาใหม่", "มาใหม่อีกรอบ", "งานใหญ่กว่าที่คิด", "ต้องใช้เวลาอีก", "ยังไม่เสร็จ", "can't finish", "cannot finish",
                       "come back", "another day", "not done today", "need another visit")),
    ("need_parts", ("สั่งอะไหล่", "ต้องสั่ง", "รออะไหล่", "อะไหล่ไม่มี", "ไม่มีอะไหล่", "อะไหล่หมด", "ต้องเบิก", "เบิกอะไหล่", "อะไหล่ไม่พอ",
                    "ต้องหาอะไหล่", "need parts", "order parts", "waiting for parts", "no parts", "out of stock")),
    ("not_home", ("ลูกค้าไม่อยู่", "ไม่อยู่บ้าน", "ไม่มีคนอยู่", "ไม่มีใครอยู่", "ไม่มีคนเปิด", "โทรไม่ติด", "ติดต่อลูกค้าไม่ได้", "บ้านปิด", "ไม่เปิดประตู",
                  "ลูกค้าไม่รับสาย", "ไม่รับสาย", "ลูกค้าไม่มา", "รอลูกค้า", "nobody home", "not home", "no answer", "no one home", "customer not in")),
    ("late", ("ถึงช้า", "ไปช้า", "จะสาย", "สายหน่อย", "ช้าหน่อย", "รถติด", "เลท", "ช้ากว่ากำหนด", "ไม่ทันนัด", "ถึงช้ากว่านัด", "ไปถึงช้า", "late",
              "running late", "delayed", "traffic", "จะช้า", "ช้านิดนึง", "ช้านิดหน่อย")),
    ("on_my_way", ("กำลังไป", "กำลังเดินทาง", "ออกเดินทาง", "เดินทางไป", "กำลังจะไป", "ออกจากร้านแล้ว", "ใกล้ถึง", "อีกสักครู่ถึง", "on my way", "otw",
                   "omw", "heading there", "heading over", "leaving now", "อีก 10 นาที", "อีก 15 นาที", "อีก 20 นาที", "อีก 30 นาที", "อีกสิบนาที",
                   "อีกครึ่งชั่วโมง")),
    ("reschedule", ("ขอเลื่อน", "เลื่อนนัด", "เลื่อนไป", "ขอเปลี่ยนวัน", "เปลี่ยนวันนัด", "ย้ายนัด", "เลื่อนเป็น", "เลื่อนงาน", "reschedule", "move the visit",
                    "postpone")),
)
_SITUATION_LABEL = {
    "on_my_way": {"th": "กำลังเดินทางไป", "en": "on the way"},
    "late": {"th": "จะถึงช้ากว่านัด", "en": "running late"},
    "not_home": {"th": "ลูกค้าไม่อยู่บ้าน", "en": "customer not home"},
    "need_parts": {"th": "ต้องสั่งอะไหล่", "en": "parts needed"},
    "cannot_finish": {"th": "วันนี้ทำไม่จบ ต้องมาต่อ", "en": "cannot finish today"},
    "reschedule": {"th": "ขอเลื่อนนัด", "en": "reschedule requested"},
}
SITUATION_NOTED = {
    "th": "รับทราบครับ บันทึกไว้ในงาน {code} แล้ว: \"{note}\"",
    "en": "Noted on {code}: \"{note}\".",
}
SITUATION_TOLD_SHOP = {"th": "\nแจ้งทีมจ่ายงานให้แล้ว", "en": "\nThe dispatcher has been told."}
SITUATION_ASK_DATE = {
    "th": "\nเลื่อนไปวันไหนครับ พิมพ์ เช่น \"เลื่อนนัด {code} พรุ่งนี้ 10 โมง\"",
    "en": "\nWhen should it move to? e.g. \"reschedule {code} tomorrow 10am\"",
}
SITUATION_MOVED = {
    "th": "เลื่อนนัด {code} เป็น {when} แล้วครับ แจ้งทีมจ่ายงานและลูกค้าให้แล้ว",
    "en": "Moved {code} to {when}. The dispatcher and the customer have been told.",
}
SITUATION_NO_JOB = {
    "th": "รับทราบครับ แต่ยังไม่แน่ใจว่างานไหน พิมพ์เลขงานด้วย เช่น \"{example} T-2026-0001\"",
    "en": "Noted — but which job? Include the number, e.g. \"{example} T-2026-0001\".",
}
_SITUATION_ON_MY_WAY_CUSTOMER = {
    "th": "ช่างกำลังเดินทางไปหาคุณ งาน {code}",
    "en": "The technician is on the way — job {code}",
}
_SITUATION_LATE_CUSTOMER = {
    "th": "ช่างแจ้งว่าจะถึงช้ากว่านัดเล็กน้อย งาน {code} ขออภัยในความไม่สะดวก",
    "en": "The technician will arrive a little later than planned — job {code}. Sorry for the delay.",
}


def _technician_situation(message: str) -> str | None:
    """Which situation a technician's sentence describes, or None."""
    canon = _canonical(message).replace(" ", "")
    if not canon or len(canon) > 120:
        return None
    for kind, words in _SITUATION_WORDS:
        if any(w.replace(" ", "") in canon for w in words):
            return kind
    return None


async def _notify_dispatchers(client: DataClient, license_id: str, text: str, text_en: str) -> int:
    """Best-effort push to everyone who dispatches. Returns how many heard."""
    told = 0
    try:
        members = await client.list_members(license_id)
        for m in await _dispatchers(client, license_id, members):
            uid = str(m.get("chann_uid") or "")
            if not uid:
                continue
            await send_notification(
                client, license_id=license_id, target_chann_uid=uid,
                target_line_user_id=await client.line_target_of(uid), type="ticket_changed",
                message=text, message_en=text_en, oa="sales",
            )
            told += 1
    except Exception:  # noqa: BLE001
        log.exception("could not tell the dispatchers about a technician's situation")
    return told


async def _handle_technician_situation(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, kind: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    from .thai_datetime import (
        format_thai_date, format_thai_time, looks_like_a_time_attempt,
        parse_thai_date, parse_thai_time,
    )

    if "ticket.update" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    label = _t(_SITUATION_LABEL[kind], language)
    try:
        member, ticket, _inferred = await _ticket_for_action(
            client, license_id, ctx, message, prefer_status=("in_progress", "assigned"),
        )
    except Exception:
        log.exception("could not find the job for a technician's situation")
        member, ticket = None, None
    if member is None or ticket is None:
        return ChatReply(
            text=_t(SITUATION_NO_JOB, language).format(example=(message or "").strip()[:30]),
            quick_replies=[("งานของฉัน", "งานของฉัน")],
        )
    code = str(ticket.get("ticket_number") or "")
    ticket_id = str(ticket.get("id") or "")
    note = (message or "").strip()[:300]

    # Written on the job, so the office and the next technician see it.
    try:
        await client.create_note(
            license_id, {"entity_type": "ticket", "entity_id": ticket_id, "body": f"[{label}] {note}"},
            actor_id=ctx.chann_uid,
        )
    except Exception:  # noqa: BLE001
        log.exception("could not note a technician's situation on %s", code)

    # A new date in the sentence ("ลูกค้าไม่อยู่ ขอเลื่อนไปพรุ่งนี้บ่าย") moves
    # the visit; the customer and the dispatcher both hear.
    today = local_today()
    # "วันนี้ทำไม่จบ ต้องมาต่อพรุ่งนี้": the day being spoken about is not
    # the new date; only a day AFTER today, or an explicit move, is one.
    dated = re.sub(r"วันนี้(?=ทำ|ไม่|ยัง|เสร็จ|จบ|ไป)|today", " ", message or "")
    new_date = parse_thai_date(dated, today) if kind in ("reschedule", "not_home", "cannot_finish", "need_parts") else None
    explicit_move = kind == "reschedule" or "เลื่อน" in _canonical(message) or "มาต่อ" in _canonical(message) or "กลับมา" in _canonical(message)
    if new_date is not None and (new_date > today or (explicit_move and new_date >= today)):
        new_time = parse_thai_time(message)
        if new_time is None:
            if looks_like_a_time_attempt(message):
                return ChatReply(text=_t(TIME_NOT_UNDERSTOOD, language))
            new_time = time(9, 0)
        try:
            await client.update_ticket(
                license_id, ticket_id,
                {"scheduled_date": new_date.isoformat(), "scheduled_time": new_time.isoformat()},
                actor_id=ctx.chann_uid,
            )
        except Exception:
            log.exception("could not move %s", code)
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        when = f"{format_thai_date(new_date)} {format_thai_time(new_time)}"
        await _notify_ticket_change(
            client, license_id, ticket_id, f"ช่างเลื่อนนัด {code} เป็น {when} ({label})", language,
            text_en=f"The technician moved job {code} to {when} ({label})",
            customer_text=f"ช่างขอเลื่อนนัดงาน {code} เป็น {when} ครับ",
            customer_text_en=f"The technician moved your job {code} to {when}",
        )
        return ChatReply(
            text=_t(SITUATION_MOVED, language).format(code=code, when=when),
            entity_type="ticket", entity_id=ticket_id,
            quick_replies=[("งานของฉัน", "งานของฉัน")],
        )

    text = _t(SITUATION_NOTED, language).format(code=code, note=label)
    quick: list[tuple[str, str]] = []
    if kind in ("not_home", "cannot_finish", "need_parts", "reschedule"):
        told = await _notify_dispatchers(
            client, license_id,
            f"ช่าง {ctx.display_name or ctx.chann_uid} แจ้งงาน {code}: {label} — \"{note[:120]}\"",
            f"Technician {ctx.display_name or ctx.chann_uid} on job {code}: {label} — \"{note[:120]}\"",
        )
        if told:
            text += _t(SITUATION_TOLD_SHOP, language)
    if kind in ("on_my_way", "late"):
        table = _SITUATION_ON_MY_WAY_CUSTOMER if kind == "on_my_way" else _SITUATION_LATE_CUSTOMER
        await _notify_customer(client, ticket, table["th"].format(code=code), table["en"].format(code=code))
    if kind == "reschedule":
        text += _t(SITUATION_ASK_DATE, language).format(code=code)
    if kind in ("on_my_way", "late"):
        quick.append(("เช็คอิน", f"เช็คอิน {code}"))
    if kind != "reschedule":
        quick.append(("เลื่อนนัด", f"เลื่อนนัด {code} "))
    if str(ticket.get("status") or "") == "in_progress":
        quick.append(("ปิดงาน", f"ปิดงาน {code}"))
    quick.append(("ดูข้อมูลงาน", f"ข้อมูลงาน {code}"))
    return ChatReply(text=text, entity_type="ticket", entity_id=ticket_id, quick_replies=quick[:4])


# ------------------------------------------- interrupting a create flow (owner, 7 Sep 2026)
#
# The bot was adding a lead and had asked for the phone; the owner typed
# "สร้างดีล…" and a deal was created for the customer mentioned before, the
# lead left half-done. A different COMMAND while a create flow waits for an
# answer is confirmed first, naming both — a list or a menu tile is still
# answered in place with the flow kept (round A), and "ยกเลิก" still drops
# the flow. The command text is stored so one tap runs exactly what was
# typed; the other button restores the original question.
_CREATE_FLOW_ENTITIES = frozenset({"customer", "deal", "followup", "ticket", "product", "warranty", "quote", "note"})
FLOW_LABELS = {
    "customer": {"th": "เพิ่มลูกค้า", "en": "adding a customer"},
    "deal": {"th": "สร้างดีล", "en": "creating a deal"},
    "followup": {"th": "ตั้งนัด", "en": "setting an appointment"},
    "ticket": {"th": "เปิดใบงาน", "en": "opening a ticket"},
    "product": {"th": "เพิ่มสินค้า", "en": "adding a product"},
    "warranty": {"th": "ลงทะเบียนเครื่อง", "en": "registering a unit"},
    "quote": {"th": "สร้างใบเสนอราคา", "en": "creating a quote"},
    "note": {"th": "บันทึกโน้ต", "en": "saving a note"},
}
FLOW_SWITCH_CONFIRM = {
    "th": "กำลัง{flow}{name}อยู่ (ยังขาด{missing}) — จะยกเลิกแล้ว{new}แทนไหมครับ",
    "en": "Still {flow}{name} (missing: {missing}) — cancel it and {new} instead?",
}
# LINE caps a button label at 20 characters: the English ones are short forms.
FLOW_SWITCH_GO_LABEL = {"th": "{new}เลย", "en": "Do the new one"}
FLOW_SWITCH_KEEP_LABEL = {"th": "{flow}ต่อ", "en": "Keep the {short}"}
FLOW_SHORT_EN = {"customer": "customer", "deal": "deal", "followup": "appointment", "ticket": "ticket", "product": "product",
                 "warranty": "unit", "quote": "quote", "note": "note"}
FLOW_SWITCH_KEEP_TEXT = "ทำรายการเดิมต่อ"
FLOW_SWITCH_RESUMED = {"th": "ทำรายการเดิมต่อครับ ", "en": "Back to it. "}
_FLOW_SWITCH_GO_WORDS = frozenset({"ทำเลย", "เปลี่ยนเลย", "ยกเลิกแล้วทำใหม่", "ใช่", "ใช่เลย", "yes", "ok", "โอเค", "เอาอันใหม่", "go ahead", "switch"})
_FLOW_SWITCH_KEEP_WORDS = frozenset({"ทำรายการเดิมต่อ", "ทำต่อ", "ต่อ", "ไม่", "ไม่เปลี่ยน", "อันเดิม", "ทำอันเดิมต่อ", "continue", "keep going", "no", "keep"})
_NEW_COMMAND_LABELS: tuple[tuple[tuple[str, ...], dict], ...] = (
    (("สร้างดีล", "เปิดดีล", "create deal", "open deal", "new deal"), {"th": "สร้างดีล", "en": "create the deal"}),
    (("เพิ่มลูกค้า", "สร้างลูกค้า", "ลูกค้าใหม่", "add customer", "new customer"), {"th": "เพิ่มลูกค้า", "en": "add the customer"}),
    (("เตือน", "นัด", "ตั้งนัด", "remind", "อย่าลืม"), {"th": "ตั้งนัด", "en": "set the appointment"}),
    (("ใบเสนอราคา", "quote"), {"th": "สร้างใบเสนอราคา", "en": "create the quote"}),
    (("เปิดใบงาน", "แจ้งซ่อม", "เปิดงาน", "มอบหมาย", "assign"), {"th": "ทำรายการงานซ่อม", "en": "handle the ticket"}),
    (("เช็คอิน", "check in"), {"th": "เช็คอิน", "en": "check in"}),
    (("ปิดงาน", "check out", "เช็คเอาท์"), {"th": "ปิดงาน", "en": "check out"}),
    (("บันทึกว่า", "จดว่า", "โน้ต", "note"), {"th": "บันทึกโน้ต", "en": "save the note"}),
    (("สินค้า", "product"), {"th": "จัดการสินค้า", "en": "handle the product"}),
    (("ลงทะเบียน", "register"), {"th": "ลงทะเบียนเครื่อง", "en": "register the unit"}),
    (("ลบ", "เก็บถาวร", "delete", "archive"), {"th": "ลบรายการ", "en": "delete the record"}),
)


def _new_command_label(message: str, language: str) -> str:
    canon = _canonical(message)
    for words, label in _NEW_COMMAND_LABELS:
        if any(w in canon for w in words):
            return _t(label, language)
    return "ทำคำสั่งใหม่" if language != "en" else "run the new command"


def _is_create_command(message: str, oa: str) -> bool:
    """A verb that starts something — never a question, never an answer."""
    canon = _canonical(message)
    if not canon or _looks_like_a_question(message):
        return False
    heads = ("สร้าง", "เพิ่ม", "เปิด", "ลงทะเบียน", "ตั้ง", "เตือน", "นัด", "อย่าลืม", "บันทึกว่า", "จดว่า", "โน้ต", "มอบหมาย",
             "จ่ายงาน", "ลบ", "เก็บถาวร", "ออกเอกสาร", "ออกใบเสนอราคา", "ทำใบเสนอราคา", "create", "add", "new ", "open", "register",
             "remind", "assign", "delete", "archive", "note")
    return any(canon.startswith(h) for h in heads) and len(_normalise(message)) >= 4


def _is_new_command(message: str, oa: str) -> bool:
    """A different closed command, as opposed to the answer to the pending
    question, a list, a tile, help or cancel."""
    if _is_read_request(message, oa) or _is_menu_tile(message, oa) or _is_help_request(message, oa):
        return False
    if _normalise(message) in _SLOT_FILL_ABORT_WORDS or _is_small_talk(message):
        return False
    if _is_create_command(message, oa):
        return True
    if oa in ("sales", "technician"):
        if _command_like(message, CHECKIN_TRIGGERS + CHECKOUT_TRIGGERS) or _action_command(
            message, TICKET_CLAIM_TRIGGERS + TICKET_REJECT_TRIGGERS + TICKET_ASSIGN_TRIGGERS,
        ):
            return True
    if oa == "sales":
        lowered = _canonical(message)
        return (
            _parse_after_trigger(message, DEAL_CREATE_TRIGGERS) is not None or any(t in lowered for t in DEAL_CREATE_BARE_TRIGGERS)
            or _is_reminder_command(message) or _is_reminder_cancel_command(message) or _is_reminder_move_command(message)
            or any(t in lowered for t in QUOTE_CREATE_TRIGGERS) or any(lowered.startswith(t) for t in NOTE_TRIGGERS)
            or _is_technician_invite_request(message) or _is_sales_invite_request(message)
            or _is_ambiguous_invite_request(message) or _lead_delete_target(message) is not None
        )
    return False


def _flow_name(fields: dict) -> str:
    for key in ("target_name", "customer_name", "name", "first_name", "product_name", "deal_name"):
        if fields.get(key):
            value = str(fields[key]).strip()
            if key == "first_name" and fields.get("last_name"):
                value += f" {fields['last_name']}"
            return value
    return ""


async def _confirm_flow_switch(
    client: DataClient, *, ctx: ResolvedContext, pending: dict, message: str, language: str,
) -> ChatReply:
    entity = str(pending.get("entity") or "")
    fields = dict(pending.get("fields") or {})
    missing = list(pending.get("missing") or [])
    flow = _t(FLOW_LABELS.get(entity, {"th": "ทำรายการ", "en": "working on something"}), language)
    name = _flow_name(fields)
    new = _new_command_label(message, language)
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="switch", entity="flow_switch",
            fields={"original": {"action": pending.get("action") or "", "entity": entity, "fields": fields, "missing": missing},
                    "command": (message or "").strip()[:300]},
            missing=["choice"], ttl_seconds=PENDING_INTENT_TTL_S,
        )
    except Exception:
        log.exception("could not hold a flow-switch confirmation")
    labels = ", ".join(MISSING_FIELD_LABELS.get(m, {}).get(language) or str(m) for m in missing) or ("ข้อมูล" if language != "en" else "details")
    go_label = _t(FLOW_SWITCH_GO_LABEL, language).format(new=new)[:20]
    keep_label = _t(FLOW_SWITCH_KEEP_LABEL, language).format(flow=flow, short=FLOW_SHORT_EN.get(entity, "flow"))[:20]
    buttons = [(go_label, (message or "").strip()[:300]), (keep_label, FLOW_SWITCH_KEEP_TEXT)]
    return ChatReply(
        text=_t(FLOW_SWITCH_CONFIRM, language).format(
            flow=flow, name=f" {name}" if name else "", missing=labels, new=new,
        ),
        quick_replies=buttons,
    )


async def _restore_flow(client: DataClient, ctx: ResolvedContext, original: dict) -> None:
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action=str(original.get("action") or ""), entity=original.get("entity"),
            fields=original.get("fields") or {}, missing=list(original.get("missing") or []),
            ttl_seconds=PENDING_INTENT_TTL_S,
        )
    except Exception:
        log.exception("could not restore an interrupted flow")


# ------------------------------------------------ bare record codes (B10)
CUSTOMER_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(C-\d{4}-\d{4})(?![0-9])", re.IGNORECASE)
QUOTE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(Q-\d{4}-\d{4})(?![0-9])", re.IGNORECASE)
QUOTE_DETAIL_TEXT = {
    "th": "ใบเสนอราคา {code} · {status}\nดีล: {deal}\nรายการ: {lines} รายการ{doc}",
    "en": "Quote {code} · {status}\nDeal: {deal}\nLines: {lines}{doc}",
}
QUOTE_HAS_DOCUMENT = {"th": "\nมีเอกสารแล้ว", "en": "\nDocument issued"}
REPORT_DETAIL_TEXT = {
    "th": "รายงาน {code} · {status}\nงาน: {ticket}\nพบ: {found}\nแก้: {done}{parts}",
    "en": "Report {code} · {status}\nJob: {ticket}\nFound: {found}\nDone: {done}{parts}",
}
REPORT_PARTS_LINE = {"th": "\nอะไหล่: {parts}", "en": "\nParts: {parts}"}


def _bare_record_code(message: str) -> tuple[str, str] | None:
    """("customer" | "deal" | "quote" | "service_report", CODE) when the
    message is nothing but the code (particles allowed), else None."""
    for form in _bare_forms(message):
        m = re.fullmatch(r"(?:ลูกค้า|ดีล|ใบเสนอราคา|รายงาน|quote|deal|customer|report|ข้อมูล)?((?:sr|[cdq])-\d{4}-\d{4})", form)
        if m:
            code = m.group(1).upper()
            kind = {"C": "customer", "D": "deal", "Q": "quote", "S": "service_report"}[code[0]]
            return kind, code
    return None


async def _handle_quote_detail(
    client: DataClient, *, ctx: ResolvedContext, license_id, code: str, permission_keys: list[str], language: str,
) -> ChatReply:
    if "quote.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    try:
        quotes = await client.list_quotes(license_id)
    except Exception:
        log.exception("quote detail failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    quote = next((q for q in quotes if str(q.get("quote_id") or "").upper() == code.upper()), None)
    if quote is None:
        return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun("quote", language), code=code))
    try:
        lines = await client.list_quote_products(license_id, str(quote.get("id")))
    except Exception:
        lines = []
    deal_code = ""
    try:
        deals = await client.list_deals(license_id)
        deal = next((d for d in deals if str(d.get("id")) == str(quote.get("deal_id"))), None)
        deal_code = str((deal or {}).get("deal_id") or "")
    except Exception:
        pass
    await _remember_entity(client, ctx, entity_type="quote", entity_id=str(quote.get("id") or ""), code=code)
    buttons = [("ออกเอกสาร", f"ออกเอกสาร {code}")]
    if deal_code:
        buttons.append(("ดูดีล", f"ข้อมูลดีล {deal_code}"))
    return ChatReply(
        text=_t(QUOTE_DETAIL_TEXT, language).format(
            code=code, status=_label(QUOTE_STATUS_LABELS, quote.get("status"), language),
            deal=deal_code or "-", lines=len(lines),
            doc=_t(QUOTE_HAS_DOCUMENT, language) if quote.get("generated_document_id") else "",
        ),
        entity_type="quote", entity_id=str(quote.get("id") or ""),
        quick_replies=buttons,
    )


async def _handle_report_detail(
    client: DataClient, *, ctx: ResolvedContext, license_id, code: str, permission_keys: list[str], language: str,
) -> ChatReply:
    if "service_report.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    try:
        rows = await client.list_service_reports(license_id)
    except Exception:
        log.exception("report detail failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    report = next((r for r in rows if str(r.get("report_id") or "").upper() == code.upper()), None)
    if report is None:
        return ChatReply(text=_t(NOT_FOUND_BY_CODE, language).format(what=_entity_noun("service_report", language), code=code))
    data = report.get("report_data") or {}
    ticket_code = ""
    try:
        ticket = await client.get_ticket(license_id, str(report.get("ticket_id") or ""))
        ticket_code = str((ticket or {}).get("ticket_number") or "")
    except Exception:
        pass
    blank = _t(_NOT_SET, language)
    parts = str(data.get("parts_changed") or "")
    quick = [("ออกรายงาน", f"ออกรายงาน {code}")] if str(report.get("status") or "") == "approved" else []
    if ctx.oa == "sales" and str(report.get("status") or "") in ("submitted", "pending"):
        quick = [("อนุมัติ", f"อนุมัติ {code}"), ("ไม่อนุมัติ", f"ไม่อนุมัติ {code}")]
    return ChatReply(
        text=_t(REPORT_DETAIL_TEXT, language).format(
            code=code, status=_label(REPORT_STATUS_LABELS, report.get("status"), language),
            ticket=ticket_code or "-", found=str(data.get("found_issue") or blank)[:120],
            done=str(data.get("work_done") or blank)[:120],
            parts=_t(REPORT_PARTS_LINE, language).format(parts=parts[:80]) if parts else "",
        ),
        entity_type="service_report", entity_id=str(report.get("id") or ""),
        quick_replies=quick,
    )


# ------------------------------------------------ two requests in one line (B13)
# "เช็คอินแล้วนะ แล้วก็ขอดูงานพรุ่งนี้ด้วย": the first is done, and the second
# is either done too (when it only reads) or named back with a button, so
# nothing typed is silently dropped (review, 6 Sep 2026).
_MULTI_CONNECTOR_RE = re.compile(
    r"\s*(?:แล้วก็|และก็|แล้วขอ|และขอ|แล้วช่วย|และช่วย|จากนั้น|\s+และ\s+|\s+and\s+(?:also\s+|then\s+)?|\s+then\s+|\s+also\s+|"
    r"\s+แล้ว(?=เปิด|สร้าง|ขอ|ดู|เช็ค|ปิด|แจ้ง|เลื่อน|ยกเลิก|รับ|บันทึก|มอบหมาย|ตั้ง|เตือน|นัด|ลง))\s*",
)
def _localise_reply(reply: ChatReply, language: str) -> ChatReply:
    """The last pass over every reply: quick-reply labels in the reader's
    language, and no doubled spaces or periods (review, 6 Sep 2026, B14)."""
    if reply is None:
        return reply
    text = reply.text or ""
    text = re.sub(r"(?<=[^\n]) {2,}(?=[^\n])", " ", text)
    text = re.sub(r"(?<=[ก-๙A-Za-z0-9\"”)])\.\.(?!\.)", ".", text)
    text = re.sub(r"น\.\.", "น.", text)
    text = re.sub(r"(?<=\S) \.(?=\s|$)", ".", text)
    reply.text = text
    if language == "en" and reply.quick_replies:
        reply.quick_replies = [
            (QUICK_REPLY_LABELS_EN.get(label, label), send) for label, send in reply.quick_replies
        ]
    return reply


# The labels the bot puts on its own buttons, in English. The text a button
# SENDS stays Thai — that is what the triggers read.
QUICK_REPLY_LABELS_EN: dict[str, str] = {
    "แจ้งซ่อม": "Report a fault", "งานของฉัน": "My jobs", "รายการดีล": "Deals", "ยกเลิกการปิดงาน": "Cancel check-out",
    "ดูสถานะงาน": "Job status", "งานที่เปิดรับ": "Open jobs", "ออกเอกสาร": "Issue document", "สินค้าทั้งหมด": "All products",
    "สร้างดีล": "New deal", "รายชื่อช่าง": "Technicians", "ยกเลิก": "Cancel", "ไม่มีหมายเลขเครื่อง": "No serial",
    "ปิดงาน": "Check out", "สร้างใบเสนอราคา": "New quote", "วิธีใช้": "Help", "ลงทะเบียนสินค้า": "Register product",
    "รายการเตือน": "Reminders", "ยืนยันลบ": "Confirm delete", "ไม่ใช่ ระบุชื่อ": "No, name them", "ประกันของฉัน": "My warranties",
    "แทนที่ทั้งหมด": "Replace all", "ทีมช่าง": "Teams", "ติดต่อร้าน": "Contact shop", "ดูข้อมูลงาน": "Job details",
    "ใช่ สร้างเลย": "Yes, create it", "งานวันนี้": "Today", "คุยกับร้าน": "Talk to the shop", "เก็บของเดิม": "Keep existing",
    "อัปเดตข้อมูลเดิม": "Update existing", "ออกเอกสารใหม่": "Reissue", "ออกรายงานใหม่": "Reissue report",
    "รายชื่อลูกค้า": "Customers", "รายการรออนุมัติ": "Pending approvals", "รายการประกัน": "Warranties",
    "รายการใบเสนอราคา": "Quotes", "รายการงาน": "Tickets", "ยืนยันลบข้อมูล": "Confirm erase", "ยืนยันการอนุมัติ": "Confirm flow",
    "ยืนยันกฎ": "Confirm rule", "เพิ่มสินค้า": "Add product", "เพิ่มลูกค้าใหม่": "Add customer", "พรุ่งนี้": "Tomorrow",
    "นัดหมายวันนี้": "Today's appointments", "ดูทุกงาน": "All jobs", "ดูดีล": "View deal", "ดูข้อมูลบริษัท": "Company profile",
    "ดูข้อมูลดีล": "Deal details", "ดูข้อมูล": "Details", "ใช้รายชื่อเดิม": "Use existing", "เช็คประกัน": "Check warranty",
    "จบการสนทนา": "End chat", "ขอรหัสเชิญช่าง": "Invite code", "ข้อมูลร้าน": "Shop info", "ข้อมูลบริษัท": "Company profile",
    "ข้อมูลดีล": "Deal details", "แก้ที่อยู่ของฉัน": "Change my address", "เช็คอิน": "Check in", "เลื่อนนัด": "Reschedule",
    "ยืนยันยกเลิก": "Confirm cancel", "ไม่ยกเลิก": "Keep it", "ไม่ปฏิเสธ": "Keep the job", "อนุมัติ": "Approve",
    "ไม่อนุมัติ": "Reject", "ออกรายงาน": "Report PDF", "รายงานของฉัน": "My reports", "ตั้งเตือนใหม่": "New reminder",
    "ดีลเดือนนี้": "This month's deals", "ดีลเลยกำหนด": "Overdue deals", "ข้อมูลลูกค้า": "Customer details",
}


SECOND_INTENT_ACK = {
    "th": "ส่วน \"{part}\" — แตะปุ่มด้านล่างหรือพิมพ์แยกอีกข้อความได้เลยครับ",
    "en": "As for \"{part}\" — tap the button below or send it as its own message.",
}


def _is_read_request(text: str, oa: str) -> bool:
    """A request that only shows something — safe to answer alongside
    another one."""
    tables: tuple[tuple[str, ...], ...] = (
        TICKET_MINE_PHRASES, TICKET_OPEN_PHRASES, TICKET_TEAM_PHRASES, TICKET_LIST_PHRASES, TODAY_WORK_PHRASES,
        REPORT_LIST_PHRASES, CUSTOMER_PROFILE_PHRASES, CAPABILITY_PHRASES,
    )
    if oa == "sales":
        tables += (
            CUSTOMER_LIST_PHRASES, DEAL_LIST_PHRASES, DEAL_OPEN_PHRASES, QUOTE_LIST_PHRASES, PRODUCT_LIST_PHRASES,
            REMINDER_LIST_TRIGGERS, UPCOMING_WORK_PHRASES, TECHNICIAN_LIST_PHRASES, SHOP_INFO_PHRASES, APPROVAL_LIST_PHRASES,
            WARRANTY_BOOK_PHRASES, TEAM_LIST_PHRASES, COMPANY_VIEW_PHRASES, SALES_SUMMARY_PHRASES,
        )
    if oa == "customer":
        tables += (CUSTOMER_STATUS_PHRASES, CUSTOMER_WARRANTY_MINE_PHRASES, CUSTOMER_ORDERS_PHRASES, CUSTOMER_CONTACT_PHRASES)
    if any(_matches_phrase(text, t) for t in tables):
        return True
    bare = BARE_JOB_WORDS | (BARE_CUSTOMER_WORDS | BARE_DEAL_WORDS | BARE_PRODUCT_WORDS if oa == "sales" else frozenset())
    return _is_bare_word(text, bare) or _is_help_request(text, oa)


def _is_closed_request(text: str, oa: str) -> bool:
    """Something a handler (not the model) answers on its own."""
    if _is_read_request(text, oa) or _is_menu_tile(text, oa):
        return True
    if oa in ("sales", "technician"):
        if _command_like(text, CHECKIN_TRIGGERS + CHECKOUT_TRIGGERS + CHECKOUT_LOOSE_TRIGGERS):
            return True
        if _action_command(text, TICKET_CLAIM_TRIGGERS + TICKET_REJECT_TRIGGERS + TICKET_ASSIGN_TRIGGERS) or _bare_decision(text):
            return True
    if oa == "sales":
        lowered = _canonical(text)
        if _parse_after_trigger(text, DEAL_CREATE_TRIGGERS) is not None or any(t in lowered for t in DEAL_CREATE_BARE_TRIGGERS):
            return True
        if _is_reminder_command(text) or _is_reminder_cancel_command(text) or any(t in lowered for t in QUOTE_CREATE_TRIGGERS):
            return True
    if oa == "customer":
        return _looks_like_fault(text) or _is_reschedule_request(text) or _is_register_request(text) or _wants_a_human(text)
    return False


def _split_multi_intent(message: str, oa: str) -> tuple[str, str] | None:
    text = (message or "").strip()
    if not text or "\n" in text or len(text) > 160:
        return None
    lowered = text.lower()
    # A note, a report request or a company command carries "และ" as prose.
    if any(lowered.startswith(t) for t in NOTE_TRIGGERS) or any(t in lowered for t in ("บันทึกว่า", "จดว่า", "โน้ต")):
        return None
    if oa == "sales" and _is_ai_report_request(text):
        return None
    m = _MULTI_CONNECTOR_RE.search(text)
    if not m or m.start() < 2:
        return None
    first, second = text[: m.start()].strip(" ,"), text[m.end():].strip(" ,")
    if len(_normalise(first)) < 2 or len(_normalise(second)) < 2:
        return None
    if not _is_closed_request(second, oa):
        return None
    if oa == "customer" and not (_looks_like_fault(first) or _is_closed_request(first, oa) or _looks_like_service_request(first)):
        return None
    return first, second


async def handle_chat_message(
    client: DataClient,
    *,
    message: str,
    ctx: ResolvedContext,
    language: str = "th",
    ai_client=None,
) -> ChatReply:
    """Spec 6.4's slot-filling pattern, in the order the spec states.

    This wrapper splits a two-request line (B13) and localises the reply
    (B14); _route_chat_message is the router itself."""
    message = _normalise_message(message)
    # Dates and times are rendered by thai_datetime from the display
    # preferences; the reply language wins over whatever the webhook set
    # (review, 6 Sep 2026, B14: "Scheduled: 8 ก.ย. 2569 10:00 น." in English).
    from .thai_datetime import display_prefs, set_display_prefs

    if str(display_prefs().get("language") or "th") != language:
        set_display_prefs({**display_prefs(), "language": language})
    parts = None
    if ctx.resolution is TenantResolution.SINGLE:
        parts = _split_multi_intent(message, ctx.oa)
        if parts is not None:
            try:
                pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
            except Exception:
                pending = None
            if pending is not None and (
                pending.get("missing") or pending.get("entity") in ("service_report", "customer_ticket", "ticket_reject", "flow_switch")
            ):
                parts = None
    if parts is None:
        return _localise_reply(await _route_chat_message(client, message=message, ctx=ctx, language=language, ai_client=ai_client), language)
    first, second = parts
    reply = await _route_chat_message(client, message=first, ctx=ctx, language=language, ai_client=ai_client)
    if _is_read_request(second, ctx.oa):
        more = await _route_chat_message(client, message=second, ctx=ctx, language=language, ai_client=ai_client)
        reply.text = (reply.text or "") + "\n\n" + (more.text or "")
        seen = {q[1] for q in reply.quick_replies}
        reply.quick_replies = (reply.quick_replies + [q for q in more.quick_replies if q[1] not in seen])[:4]
        if more.list_card and not reply.list_card:
            reply.list_card = more.list_card
    else:
        reply.text = (reply.text or "") + "\n\n" + _t(SECOND_INTENT_ACK, language).format(part=second[:60])
        reply.quick_replies = ([(second[:20], second[:300])] + list(reply.quick_replies))[:4]
    return _localise_reply(reply, language)


async def _route_chat_message(
    client: DataClient,
    *,
    message: str,
    ctx: ResolvedContext,
    language: str = "th",
    ai_client=None,
    abandoned: dict | None = None,
) -> ChatReply:
    """The router: one message, one handler.

    `abandoned` is the create flow a confirmed switch just dropped, carried
    so the half-made record can still be offered (see _offer_draft_customer_deal)."""
    if ctx.resolution is TenantResolution.MULTIPLE:
        # Several companies and no stored choice: the message is either
        # the choice itself, or it gets the chooser (buttons, rule 3:
        # never guess, never go quiet). The owner's account hit the old
        # text "บัญชีนี้ดูแลหลายร้าน … พิมพ์ชื่อร้าน" and typing the name
        # brought the same line back, because nothing read the answer.
        chosen = _membership_named(message, ctx.memberships)
        if chosen is not None:
            return await _switch_tenant(client, ctx=ctx, membership=chosen, language=language)
        chooser = _tenant_chooser(ctx, language)
        if (
            ctx.oa == "customer" and not _is_customer_command(message) and not _looks_like_a_question(message)
            and (_looks_like_fault(message) or _looks_like_service_request(message))
        ):
            # A two-shop customer's fault is kept while they pick the shop,
            # and filed there (review, 6 Sep 2026: it was dropped and had
            # to be typed again).
            await _hold_customer_message(client, ctx, message)
            chooser.text = _t(HELD_UNTIL_SHOP_CHOSEN, language) + "\n\n" + chooser.text
        return chooser
    if ctx.resolution is not TenantResolution.SINGLE:
        # No tenant means no permission set and no place to write to.
        return ChatReply(text=greet(ctx, language))
    if ctx.alternatives:
        # One company active, others available: "เปลี่ยนร้าน" opens the
        # chooser, and naming another one switches straight away.
        if _matches_phrase(message, SWITCH_TENANT_PHRASES):
            return _tenant_chooser(ctx, language, include_current=True)
        other = _membership_named(message, ctx.alternatives, explicit_only=True)
        if other is not None:
            return await _switch_tenant(client, ctx=ctx, membership=other, language=language)
    elif _matches_phrase(message, SWITCH_TENANT_PHRASES):
        # One shop only: say so instead of sending the words to the model.
        return ChatReply(
            text=_t(SINGLE_SHOP, language).format(company=ctx.memberships[0].get("company_name") or "-"),
        )

    license_id = ctx.license_id
    member = ctx.memberships[0]

    # Phase 16.5 — PDPA rights come before help and before any intent: a
    # person asking for their data, or to be forgotten, is not asking for
    # a permission list. Every OA, every role.
    if _matches_phrase(message, PDPA_EXPORT_PHRASES):
        try:
            out = await pdpa_service.export_my_data(
                client, chann_uid=ctx.chann_uid, via="chat", language=language,
            )
        except (DataTierError, Exception):  # noqa: BLE001
            log.exception("pdpa export failed for %s", ctx.chann_uid)
            return ChatReply(text=_t(pdpa_service.FAILED, language))
        return ChatReply(text=out["text"])
    if _matches_phrase(message, PDPA_ERASE_PHRASES):
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="erase", entity="pdpa_erase", fields={}, missing=[], ttl_seconds=600,
        )
        return ChatReply(
            text=_t(pdpa_service.ERASE_CONFIRM, language),
            quick_replies=[("ยืนยันลบข้อมูล", "ยืนยันลบข้อมูล")],
        )
    if _matches_phrase(message, PDPA_ERASE_CONFIRM_PHRASES):
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
        if not pending or pending.get("entity") != "pdpa_erase":
            return ChatReply(text=_t(pdpa_service.ERASE_NOTHING, language))
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        try:
            out = await pdpa_service.erase_me(
                client, chann_uid=ctx.chann_uid, via="chat", language=language,
            )
        except (DataTierError, Exception):  # noqa: BLE001
            log.exception("pdpa erasure failed for %s", ctx.chann_uid)
            return ChatReply(text=_t(pdpa_service.FAILED, language))
        return ChatReply(text=out["text"])

    # Phase 18 — a suspended tenant is read-only: nothing new through chat.
    # A person's own PDPA rights (above) still work; those are against the
    # platform, not the shop.
    if str(member.get("license_status") or "active") == "suspended":
        return ChatReply(
            text=_t(TENANT_SUSPENDED, language).format(company=member.get("company_name") or ""),
        )

    if ctx.oa == "customer":
        # A customer is linked through customer_license_links and holds no
        # license_members row BY DESIGN (Phase 6.5: linking must never
        # grant tenant permissions). The authorization lookup below is a
        # members query and 404s for every customer — so every linked
        # customer was told "ยังไม่พบบริษัทที่ผูกไว้" and could not report a
        # fault at all (3 Sep). Their branch checks no permission keys.
        permission_keys: list[str] = []
        context: dict = {}
    else:
        context = await client.authorization_context(
            str(license_id), ctx.chann_uid, channel=member_channel(ctx.oa),
        )
        if context is None:
            return ChatReply(text=_t(REPLY_NOT_REGISTERED, language))
        permission_keys = list(context.get("permission_keys") or [])

    # A create flow waiting for its answer (owner, 7 Sep 2026): a different
    # command is confirmed before it replaces the flow; the flow's own
    # answer, a list, a tile and "ยกเลิก" pass through untouched.
    if ctx.oa in ("sales", "technician"):
        try:
            early_pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
        except Exception:
            early_pending = None
        if early_pending is not None and early_pending.get("entity") == "flow_switch":
            held = early_pending.get("fields") or {}
            original = held.get("original") or {}
            command = str(held.get("command") or "")
            norm = _normalise(message)
            if (message or "").strip() == command.strip() or norm in _FLOW_SWITCH_GO_WORDS:
                await _drop_pending_quietly(client, ctx)
                return await _route_chat_message(
                    client, message=command, ctx=ctx, language=language, ai_client=ai_client,
                    abandoned=_abandoned_flow(original),
                )
            await _restore_flow(client, ctx, original)
            if norm in _FLOW_SWITCH_KEEP_WORDS or (message or "").strip() == FLOW_SWITCH_KEEP_TEXT:
                return ChatReply(
                    text=_t(FLOW_SWITCH_RESUMED, language) + ask_for_missing(list(original.get("missing") or []), language),
                )
            # Anything else answers the original question.
            early_pending = {**original}
        if early_pending is not None and early_pending.get("entity") == "bulk_customer_phone":
            resolved = await _resolve_bulk_customer_phone(
                client, ctx=ctx, license_id=license_id, message=message, pending=early_pending,
                permission_keys=permission_keys, language=language,
            )
            if resolved is not None:
                return resolved
        if early_pending is not None and early_pending.get("entity") == "staff_ticket_device":
            # A tap on one of the customer's machines finishes the job the
            # staff member started; anything else is a new request.
            resolved = await _resolve_staff_ticket_device(
                client, ctx=ctx, license_id=license_id, message=message, pending=early_pending,
                permission_keys=permission_keys, language=language,
            )
            if resolved is not None:
                return resolved
            early_pending = None
        if early_pending is not None and early_pending.get("entity") == "line_item_add":
            # "1500" / "ใช่" / "เป็นสินค้ารายการใหม่" after "add it as a new
            # line?" — anything else is a new request and the question is dropped.
            resolved = await _resolve_line_item_add(
                client, ctx=ctx, license_id=license_id, message=message, pending=early_pending,
                permission_keys=permission_keys, language=language,
            )
            if resolved is not None:
                return resolved
            early_pending = None
        if ctx.oa == "sales" and early_pending is not None and early_pending.get("entity") in (
            "template_design", "template_design_type", "template_refine",
        ):
            # "ใช้เลย" / "แก้เพิ่ม" / "ทิ้ง", the document type, or the edit
            # to make. Anything else drops the draft and falls through as a
            # new request — the draft stays on the templates page either way.
            resolved = await _resolve_template_design(
                client, ctx=ctx, license_id=license_id, message=message, pending=early_pending,
                permission_keys=permission_keys, language=language, ai_client=ai_client,
            )
            if resolved is not None:
                return resolved
            early_pending = None
        if (
            early_pending is not None and early_pending.get("entity") in _CREATE_FLOW_ENTITIES
            and early_pending.get("missing") and _is_new_command(message, ctx.oa)
        ):
            return await _confirm_flow_switch(client, ctx=ctx, pending=early_pending, message=message, language=language)

    # Closed, trigger-matched flow (see registration.py's create-company /
    # invite-code paths for the same pattern) — checked before the AI
    # parser, and before the pending-intent load below, since it is
    # unrelated to any in-progress slot-filling.
    if ctx.oa == "sales" and (
        _is_technician_invite_request(message) or _is_sales_invite_request(message)
        or _is_ambiguous_invite_request(message)
    ):
        # Measured on the real handler, 10 ก.ย. 2569: "ช่างใหม่จะเข้าร้าน
        # ยังไง" — a question about HOW — issued a real, single-use invite
        # code, and so did "ไม่ต้องเชิญช่างแล้ว" and "ลูกค้าถามว่าเพิ่มช่าง
        # ยังไง". An invite code is not a harmless read: it is a credential
        # that lets a stranger join the shop. One guard in front of all
        # three shapes, before any of them decides which role to issue.
        held = _intent_guard_reply(message, action="invite_create", language=language)
        if held is not None:
            return held
    if ctx.oa == "sales" and _is_technician_invite_request(message):
        return await _handle_invite_request(
            client, ctx=ctx, permission_keys=permission_keys, language=language,
            role="technician",
        )
    if ctx.oa == "sales" and _is_sales_invite_request(message):
        # "member" is the salesperson template; the reply says the role can
        # be changed afterwards rather than asking for it now — one question
        # to issue a code, not two.
        return await _handle_invite_request(
            client, ctx=ctx, permission_keys=permission_keys, language=language,
            role="member",
        )
    if ctx.oa == "sales" and _is_ambiguous_invite_request(message):
        return _ask_which_invite(language)

    # "ออกแบบใบเสนอราคา" — the AI drafting a document template (owner,
    # 9 Sep 2026). Before the quote and report phrases below, which share
    # the words "ใบเสนอราคา" and "รายงาน": this asks for a FORM, and a
    # sentence that says so must not be read as issuing a document.
    if ctx.oa == "sales" and _is_template_design_request(message):
        return await _handle_template_design(
            client, ctx=ctx, license_id=license_id, message=message,
            permission_keys=permission_keys, language=language, ai_client=ai_client,
        )

    # Assignment policy (Phase 11.6). Sales OA only, and before the AI
    # path: the policy TEXT goes to a model deliberately, but the command
    # that carries it must not, or "ตั้งกฎมอบหมาย" could be parsed as
    # something else entirely.
    if ctx.oa == "sales":
        if _matches_phrase(message, ASSIGN_CONFIRM):
            return await _handle_assignment_confirm(
                client, ctx=ctx, license_id=license_id,
                pending=await client.get_pending_intent(ctx.chann_uid, ctx.oa),
                permission_keys=permission_keys, language=language,
            )
        if _matches_phrase(message, ASSIGN_POLICY_SHOW):
            return await _handle_assignment_show(
                client, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        policy_trigger = next(
            (t for t in ASSIGN_POLICY_TRIGGERS if t in message.lower()), None,
        )
        if policy_trigger:
            return await _handle_assignment_policy(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=policy_trigger, permission_keys=permission_keys,
                language=language, ai_client=ai_client,
            )

        # Approvals (Phase 14-B). Longest phrase first, always: "อนุมัติ" is
        # a substring of "ไม่อนุมัติ", "รายการรออนุมัติ" and "ตั้งการอนุมัติ",
        # and the bare trigger would swallow every one of them.
        approval_policy_trigger = next(
            (t for t in APPROVAL_POLICY_TRIGGERS if t in message.lower()), None,
        )
        if approval_policy_trigger:
            return await _handle_approval_policy(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=approval_policy_trigger, permission_keys=permission_keys,
                language=language, ai_client=ai_client,
            )
        if _matches_phrase(message, APPROVAL_POLICY_CONFIRM):
            return await _handle_approval_policy_confirm(
                client, ctx=ctx, license_id=license_id,
                pending=await client.get_pending_intent(ctx.chann_uid, ctx.oa),
                permission_keys=permission_keys, language=language,
            )
        if _matches_phrase(message, APPROVAL_POLICY_SHOW):
            return await _handle_approval_policy_show(
                client, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        if _matches_phrase(message, APPROVAL_LIST_PHRASES) or _asks_for_approval_list(message):
            return await _handle_approval_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        # Approving issues the customer's PDF and fires the satisfaction
        # survey, so this pair is guarded in front of BOTH branches — and
        # the ordering below cannot be trusted to separate them: the reject
        # trigger "ไม่อนุมัติ" is not a substring of "ไม่ต้องอนุมัติ", so a
        # refusal fell through to the approve test and approved the report.
        if any(
            t in message.lower()
            for t in (APPROVAL_REJECT_TRIGGERS + APPROVAL_APPROVE_TRIGGERS)
        ):
            held_approval = _intent_guard_reply(
                message, action="approval_act", language=language,
                triggers=APPROVAL_REJECT_TRIGGERS + APPROVAL_APPROVE_TRIGGERS,
            )
            if held_approval is not None:
                return held_approval
        reject_trigger = next(
            (t for t in APPROVAL_REJECT_TRIGGERS if t in message.lower()), None,
        )
        if reject_trigger and _action_command(message, APPROVAL_REJECT_TRIGGERS, SERVICE_REPORT_CODE_RE):
            return await _handle_approval_act(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                approve=False, trigger=reject_trigger,
            )
        approve_trigger = next(
            (t for t in APPROVAL_APPROVE_TRIGGERS if t in message.lower()), None,
        )
        if approve_trigger and _action_command(message, APPROVAL_APPROVE_TRIGGERS, SERVICE_REPORT_CODE_RE):
            return await _handle_approval_act(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                approve=True, trigger=approve_trigger,
            )

    # Tickets are checked AFTER assignment policy on purpose: "ตั้งกฎมอบหมาย"
    # contains "มอบหมาย", so matching the ticket trigger first swallowed
    # every attempt to configure a rule and refused it for lacking
    # ticket.update. Same substring trap as ไม่สำเร็จ/สำเร็จ in Phase 9 and
    # ออกเอกสารใหม่/ออกเอกสาร in Phase 10 — the longer, more specific
    # phrase has to be tested first.
    # Tickets (Phase 12). Available on the technician OA too — a technician
    # taking a job is the whole point, and routing that through the sales
    # OA only would make the feature unreachable for the people who use it.
    if ctx.oa in ("sales", "technician"):
        # "เช็คประกัน SN12345" on site. The owner's call: a technician
        # answering the customer in front of them beats sending them to
        # phone the shop for an answer the system already holds.
        if any(t in message.lower() for t in SERIAL_LOOKUP_TRIGGERS) and _oa_allows(
            ctx.oa, "warranty.read"
        ):
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )
        if TICKET_CODE_RE.fullmatch((message or "").strip()) or (
            TICKET_CODE_RE.search(message or "") and _normalise(TICKET_CODE_RE.sub("", message)) in ("", "งาน", "ดู", "ดูงาน", "เช็ค", "ข้อมูล", "job", "ticket")
        ):
            # The code on its own: the job, in full.
            return await _handle_ticket_detail(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        # A bare C-/D-/Q-/SR- code — with or without "ครับ" — is that record
        # (review, 6 Sep 2026, B10: only T- worked, the rest went to the model).
        bare_code = _bare_record_code(message)
        if bare_code is not None and (ctx.oa == "sales" or bare_code[0] == "service_report"):
            kind, code = bare_code
            if kind == "customer":
                return await _handle_customer_detail(
                    client, license_id=license_id, code=code, permission_keys=permission_keys, language=language, ctx=ctx,
                )
            if kind == "deal":
                return await _handle_deal_detail(
                    client, license_id=license_id, code=code, permission_keys=permission_keys, language=language, ctx=ctx,
                )
            if kind == "quote":
                return await _handle_quote_detail(
                    client, ctx=ctx, license_id=license_id, code=code, permission_keys=permission_keys, language=language,
                )
            return await _handle_report_detail(
                client, ctx=ctx, license_id=license_id, code=code, permission_keys=permission_keys, language=language,
            )
        if _looks_like_a_question(message) and _asks_about_warranty(message) and _oa_allows(ctx.oa, "warranty.read"):
            # "SN12345678 ประกันหมดยัง" / "เครื่องนี้ยังมีประกันไหม"
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )
        if any(t in message.lower() for t in TICKET_DETAIL_TRIGGERS) and (
            TICKET_CODE_RE.search(message or "")
            or _matches_phrase(message, TICKET_DETAIL_TRIGGERS)
        ):
            return await _handle_ticket_detail(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if TICKET_CODE_RE.search(message or "") and _looks_like_a_question(message) and not _asks_about_warranty(message) and not (
            ctx.oa == "sales" and _asks_for_approval_list(message)
        ):
            # "งาน T-2026-0001 ถึงไหนแล้ว", "ใครรับงาน T-2026-0001", "T-2026-0001
            # ที่อยู่ไหน": a question about a named job is its detail
            # (review, 6 Sep 2026, B11).
            return await _handle_ticket_detail(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if _names_a_serial(message) and (_looks_like_a_question(message) or len(_normalise(message)) <= 40) and _oa_allows(
            ctx.oa, "warranty.read"
        ) and not _is_register_request(message) and not any(t in message.lower() for t in SERIAL_REGISTER_TRIGGERS):
            # "เครื่อง SN12345678 ของใคร", "เครื่องนี้ประกันหมดยัง SN12345678".
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )
        if ("ใบเสนอราคา" in (message or "") or "quote" in (message or "").lower()) and (
            any(t in (message or "").lower() for t in ("pdf", "ไฟล์", "เอกสาร", "ส่งให้ลูกค้า", "send"))
            or _normalise(message).startswith(("ส่งใบเสนอราคา", "ส่ง quote", "ส่งquote", "sendquote"))
        ) and not any(t in (message or "").lower() for t in QUOTE_CREATE_TRIGGERS):
            # "ขอ pdf ใบเสนอราคา" is the quote's document, not a report's
            # (review, 6 Sep 2026).
            quote_code = QUOTE_CODE_RE.search(message or "")
            return await _handle_quote_issue(
                client, license_id=license_id, code=quote_code.group(1).upper() if quote_code else "",
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid, allow_reissue="ใหม่" in (message or "") or "again" in (message or "").lower(),
            )
        if any(t in (message or "").lower() for t in REPORT_PDF_TRIGGERS):
            # With no code in the sentence this handler picks the person's
            # only approved report and issues it, so "ออกรายงานยังไง" — a
            # bare how-do-I — sent a customer their PDF (10 ก.ย. 2569).
            held_report = _intent_guard_reply(
                message, action="document_issue", language=language,
                triggers=REPORT_PDF_TRIGGERS + REPORT_PDF_REISSUE,
            )
            if held_report is not None:
                return held_report
            return await _handle_report_pdf(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if _matches_phrase(message, REPORT_LIST_PHRASES):
            return await _handle_report_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        # User review (4 Sep 2026): delete a lead (soft delete, confirmed) and
        # the inactive-lead cleanup setting — closed commands, no model call.
        # User review (4 Sep 2026): several customers in one message go in
        # one at a time, before the single-customer prompt sees the text.
        bulk_entries = _bulk_customer_entries(message, allow_untriggered=ctx.oa == "sales")
        if bulk_entries:
            # "ไม่ต้องเพิ่มลูกค้า สมชาย …; สมหญิง …" created both rows, and
            # "เพิ่มลูกค้าหลายคนยังไง เช่น สมชาย … " — someone asking how the
            # paste format works, with an example — created the example
            # (10 ก.ย. 2569). The vocabulary for this was written when the
            # guard was built and the call site was never added.
            held_bulk = _intent_guard_reply(
                message, action="customer_bulk", language=language,
            )
            if held_bulk is not None:
                return held_bulk
            return await _handle_bulk_customer_add(
                client, ctx=ctx, license_id=license_id, entries=bulk_entries,
                permission_keys=permission_keys, language=language,
            )
        cleanup_reply = await _maybe_lead_cleanup_setting(
            client, ctx=ctx, license_id=license_id, message=message,
            permission_keys=permission_keys, language=language,
        )
        if cleanup_reply is not None:
            return cleanup_reply
        lead_target = _lead_delete_target(message)
        if lead_target is not None:
            return await _handle_lead_archive_request(
                client, ctx=ctx, license_id=license_id, name=lead_target or None,
                permission_keys=permission_keys, language=language,
            )
        # Phase 17 ตาราง/กราฟ. A free-form question asked as a chart stays
        # with the report engine and gets the picture as one more output of
        # the same answer; the four fixed sales pictures are deterministic,
        # so "ขอกราฟยอดขาย" never costs a model call.
        chart_request = _chart_request(message) if ctx.oa == "sales" else None
        if chart_request is not None and _is_ai_report_request(message):
            return await _handle_ai_report(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language, ai_client=ai_client,
                with_chart=True,
            )
        if chart_request is not None:
            return await _handle_sales_chart(
                client, ctx=ctx, license_id=license_id, request=chart_request,
                permission_keys=permission_keys, language=language,
            )
        if ctx.oa == "sales" and _is_ai_report_request(message):
            return await _handle_ai_report(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language, ai_client=ai_client,
            )
        if _matches_phrase(message, TICKET_MINE_PHRASES) or _is_bare_word(message, BARE_JOB_WORDS):
            return await _handle_ticket_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language, mine=True,
            )
        if _matches_phrase(message, TICKET_OPEN_PHRASES):
            return await _handle_ticket_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language, open_only=True,
            )
        if _matches_phrase(message, TICKET_TEAM_PHRASES) or _normalise(message).startswith(("งานทีม", "งานของทีม", "teamjobs")):
            # "งานทีมแอร์" names the team; the list is filtered by membership
            # anyway (review, 6 Sep 2026).
            return await _handle_ticket_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language, team_only=True,
            )
        if _matches_phrase(message, TICKET_LIST_PHRASES):
            return await _handle_ticket_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        # A guided report in progress takes priority over every trigger.
        # The answer to "พบปัญหาอะไรครับ" is "คอมเพรสเซอร์รั่ว" — which
        # contains no command word at all, so without this it fell through
        # to the AI parser and the report was silently abandoned.
        try:
            in_progress = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
        except Exception:
            in_progress = None
        if in_progress and in_progress.get("entity") == "service_report" and not (
            # A tile or a command is answered as itself; the draft stays
            # (review, 6 Sep 2026: "วิธีใช้" became found_issue). Digits and
            # placeholders are handled inside the draft — re-asked, never
            # filed.
            _is_menu_tile(message, ctx.oa)
            or _command_like(message, CHECKIN_TRIGGERS)
            or _action_command(message, TICKET_CLAIM_TRIGGERS + TICKET_REJECT_TRIGGERS)
            or _bare_decision(message) is not None
            or _matches_phrase(message, TODAY_WORK_PHRASES + LANGUAGE_TOGGLE_PHRASES + CAPABILITY_PHRASES)
        ):
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if in_progress and in_progress.get("entity") == "ticket_reject" and (
            _is_menu_tile(message, ctx.oa) or TICKET_CODE_RE.search(message or "")
            or _command_like(message, CHECKIN_TRIGGERS + CHECKOUT_TRIGGERS + CHECKOUT_LOOSE_TRIGGERS)
            or _action_command(message, TICKET_CLAIM_TRIGGERS + TICKET_REJECT_TRIGGERS + TICKET_ASSIGN_TRIGGERS)
        ):
            # A tile or another command while the decline waits for its
            # reason: the command wins, the decline is forgotten.
            await _drop_pending_quietly(client, ctx)
            in_progress = None
        if in_progress and in_progress.get("entity") == "ticket_reject":
            return await _resolve_ticket_reject_confirm(
                client, ctx=ctx, license_id=license_id, message=message, pending=in_progress,
                permission_keys=permission_keys, language=language,
            )

        # What a technician says from the road or the doorstep (B12):
        # acknowledged, noted on the job, dispatcher told. Before check-in:
        # "มาถึงช้าหน่อย" is late, not arrived.
        if ctx.oa == "technician":
            situation = _technician_situation(message)
            if situation is not None and not _command_like(message, CHECKOUT_TRIGGERS) and not _is_menu_tile(message, ctx.oa):
                # This branch files a note on the job AND pushes it to the
                # dispatch team, so a misread is seen by other people.
                # "ยังไม่ต้องสั่งอะไหล่" was filed as "ต้องสั่งอะไหล่" — the
                # opposite of what was said — and "ขอเลื่อนนัดยังไง", asking
                # HOW to reschedule, was filed as a request to reschedule
                # (10 ก.ย. 2569).
                held_situation = _intent_guard_reply(
                    message, action="job_situation", language=language,
                    triggers=tuple(w for _kind, words in _SITUATION_WORDS for w in words),
                )
                if held_situation is not None:
                    return held_situation
                return await _handle_technician_situation(
                    client, ctx=ctx, license_id=license_id, message=message, kind=situation,
                    permission_keys=permission_keys, language=language,
                )
        # Check-in/out before claim: "ปิดงาน" and "รับงาน" are different
        # actions on the same ticket, and the shorter claim trigger must
        # not swallow a close. Command-like only: a sentence that merely
        # contains "ถึงแล้ว" is not a check-in.
        if _command_like(message, CHECKOUT_TRIGGERS) or (
            _command_like(message, CHECKOUT_LOOSE_TRIGGERS)
            and (ctx.oa == "technician" or TICKET_CODE_RE.search(message or ""))
        ):
            guarded = _intent_guard_reply(message, action="check_out", language=language)
            if guarded is not None:
                return guarded
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if _command_like(message, CHECKIN_TRIGGERS):
            # "ไม่ถึงหน้างาน" and "เช็คอินต้องทำอย่างไร" both look like a
            # check-in to the trigger table and neither is one (review v3,
            # B03). _command_like already refuses the plainest of these;
            # this is the level above its phrase list.
            guarded = _intent_guard_reply(message, action="check_in", language=language)
            if guarded is not None:
                return guarded
            return await _handle_check_in(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        # "รับ" / "ไม่รับ" on their own — the answer to an offer — with or
        # without the code and a reason ("รับ T-2026-0002", "ไปไม่ได้ครับ
        # ป่วย", "บ่ว่าง"; review, 6 Sep 2026, B9).
        decision = _bare_decision(message) if ctx.oa == "technician" or TICKET_CODE_RE.search(message or "") else None
        if decision == "decline":
            return await _handle_ticket_reject(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if decision == "accept":
            return await _handle_ticket_claim(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if ctx.oa == "technician" and _asks_about_current_job(message):
            # "ลูกค้าเบอร์อะไร", "งานนี้ที่อยู่ไหน": the job they are on.
            return await _handle_ticket_detail(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        # Claim, reject and assign all write, and none of them consulted a
        # guard: _disclaims_a_job_action is reached from _command_like
        # (check-in/check-out) but not from _action_command, which is what
        # these three use. Each gets its own action name so the refusal can
        # say which job word it read.
        for _job_action, _job_triggers in (
            ("job_reject", TICKET_REJECT_TRIGGERS),
            ("job_claim", TICKET_CLAIM_TRIGGERS),
            ("job_assign", TICKET_ASSIGN_TRIGGERS),
        ):
            if _action_command(message, _job_triggers):
                held_job = _intent_guard_reply(
                    message, action=_job_action, language=language,
                )
                if held_job is not None:
                    return held_job
                break

        if _action_command(message, TICKET_REJECT_TRIGGERS):
            return await _handle_ticket_reject(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if _action_command(message, TICKET_CLAIM_TRIGGERS):
            return await _handle_ticket_claim(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        assign_trigger = next(
            (t for t in TICKET_ASSIGN_TRIGGERS if t in message.lower()), None,
        )
        if assign_trigger and _action_command(message, TICKET_ASSIGN_TRIGGERS):
            return await _handle_ticket_assign(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=assign_trigger, permission_keys=permission_keys,
                language=language,
            )
        if ctx.oa == "sales" and _implicit_assignment(message):
            # "ให้สมศักดิ์ไป T-2026-0001", "T-2026-0001 ให้สมศักดิ์",
            # "สมศักดิ์ไปงาน T-2026-0001 นะ" (review, 6 Sep 2026, B8).
            return await _handle_ticket_assign(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger="", permission_keys=permission_keys, language=language,
            )

    # Customer OA. A customer is not a tenant member and holds no
    # permission keys at all, so every branch below would refuse them —
    # which is why this comes first and why it does not check permissions.
    # Their boundary is the shop they are linked to, which license_id
    # already is.
    wanted = _language_switch_requested(message)
    if wanted is None and _matches_phrase(message, LANGUAGE_TOGGLE_PHRASES):
        wanted = "en" if language == "th" else "th"
    if wanted:
        return await _switch_language(client, ctx=ctx, language=wanted)
    pref_reply = await _maybe_set_display_pref(client, ctx=ctx, message=message, language=language)
    if pref_reply is not None:
        return pref_reply
    if _matches_phrase(message, DASHBOARD_OPEN_PHRASES):
        return _dashboard_open_reply(ctx.oa, message, language)
    if ctx.oa == "sales":
        setting_reply = await _maybe_auto_accept_setting(
            client, ctx=ctx, license_id=license_id, message=message,
            permission_keys=permission_keys, language=language,
        )
        if setting_reply is not None:
            return setting_reply
        policy_reply = await _maybe_chat_policy_setting(
            client, ctx=ctx, license_id=license_id, message=message,
            permission_keys=permission_keys, language=language,
        )
        if policy_reply is not None:
            return policy_reply

    if ctx.oa == "customer":
        if _is_help_request(message, "customer") or _is_help_step_request(message):
            return await _help_reply(
                client, ctx=ctx, permission_keys=permission_keys, language=language, message=message,
            )
        if _is_small_talk(message):
            return ChatReply(
                text=_t(SMALL_TALK_REPLY, language),
                quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("งานของฉัน", "งานของฉัน")],
            )
        if _matches_phrase(message, CUSTOMER_PROFILE_PHRASES + CUSTOMER_SHOP_PHRASES):
            return await _handle_customer_profile_view(
                client, ctx=ctx, license_id=license_id, language=language,
            )
        # A survey answer (Phase 14-B): "2", or a scale label. Only when a
        # survey is actually waiting — otherwise a bare digit is whatever
        # it was before, and the catch-all below must not see it first.
        survey_reply = await _maybe_answer_survey(
            client, ctx=ctx, license_id=license_id, message=message, language=language,
        )
        if survey_reply is not None:
            return survey_reply
        if _menu_digit(message) is not None:
            step_reply = await _help_step_from_pending(
                client, ctx=ctx, message=message, language=language, permission_keys=permission_keys,
            )
            if step_reply is not None:
                return step_reply
        # The rich-menu tiles that are not a fault report. Each is an
        # exact phrase, tested before the catch-all that would otherwise
        # turn the tile's label into a repair job.
        if _matches_phrase(message, CUSTOMER_CONTACT_PHRASES + ("ข้อมูลบริษัท", "ข้อมูลร้าน", "shop info")) or (
            _asks_shop_contact(message) and not _looks_like_fault(message) and not _is_reschedule_request(message)
        ):
            return await _handle_customer_contact(
                client, license_id=license_id, language=language, ctx=ctx,
            )
        if _matches_phrase(message, CUSTOMER_WARRANTY_MINE_PHRASES) or _matches_phrase(message, tuple(CUSTOMER_WARRANTY_MINE_WORDS)):
            return await _handle_warranty_mine(
                client, ctx=ctx, license_id=license_id, language=language,
            )
        if _matches_phrase(message, CUSTOMER_ORDERS_PHRASES):
            return await _handle_orders_mine(
                client, ctx=ctx, license_id=license_id, language=language,
            )
        if _matches_phrase(message, PRODUCT_LIST_PHRASES + STOREFRONT_BROWSE_EXTRA):
            # The storefront, not the shop's list — same as the OA pre-pass;
            # a quick-reply button reaches this branch directly.
            return await _storefront_browse_reply(client, ctx=ctx, language=language)
        chat_first = _chat_start_text(message)
        if chat_first is not None:
            # Guarded only in the BARE form. _chat_start_text already
            # requires the phrase to open the message, so anything after it
            # is the customer's first line to the shop — "คุยกับร้าน ราคา
            # แอร์ 12000 BTU เท่าไหร่" is a question FOR the shop, not a
            # question about whether to open a conversation, and holding it
            # would be the other mistake this codebase has made twice.
            held_chat = _intent_guard_reply(
                message, action="chat_open", language=language,
                triggers=CUSTOMER_CHAT_PHRASES,
            ) if not chat_first else None
            if held_chat is not None:
                return held_chat
            return await _handle_customer_chat_start(
                client, ctx=ctx, license_id=license_id, first_message=chat_first, language=language,
            )
        if _matches_phrase(message, CUSTOMER_CHAT_END_PHRASES):
            return await _handle_customer_chat_end(
                client, ctx=ctx, license_id=license_id, language=language,
            )
        if _wants_a_human(message) or (_is_complaint(message) and not _is_cancel_hint(message)):
            # "ขอคุยกับคนจริงๆ", "แอดมินอยู่ไหม", "บริการแย่มาก": a person at
            # the shop, now — with the complaint as the first line so
            # nobody has to type it twice (review, 6 Sep 2026, B7).
            #
            # Guarded on the asking-for-a-person half only. Opening a
            # conversation pushes a notification to every agent, and
            # "ไม่ต้องคุยกับร้าน" and "คุยกับร้านยังไง" each summoned the
            # whole sales team (10 ก.ย. 2569). A COMPLAINT is never held:
            # an unhappy customer reaches a person whatever shape the
            # sentence takes, which is the point of this branch.
            if not _is_complaint(message):
                held_chat = _intent_guard_reply(
                    message, action="chat_open", language=language,
                    triggers=CUSTOMER_CHAT_PHRASES,
                )
                if held_chat is not None:
                    return held_chat
            return await _handle_customer_chat_start(
                client, ctx=ctx, license_id=license_id,
                first_message=(message or "").strip() if _is_complaint(message) else "", language=language,
            )
        # Editing your own profile is a customer's other legitimate reason
        # to type here, and it is allowed regardless of permissions
        # (Phase 8 — self-edit is always permitted). Catching everything as
        # a fault report turned "แก้เบอร์เป็น 08..." into a repair job,
        # which a test caught immediately.
        if any(t in message.lower() for t in SERIAL_REGISTER_TRIGGERS) or _is_register_request(message):
            return await _handle_warranty_register(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
                permission_keys=permission_keys,
            )
        if any(t in message.lower() for t in SERIAL_LOOKUP_TRIGGERS):
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )
        forwarded = await _maybe_forward_to_shop(
            client, ctx=ctx, license_id=license_id, message=message, language=language,
        )
        if forwarded is not None:
            return forwarded
        if not _looks_like_profile_edit(message):
            # Phase 15: while a conversation with the shop is running, free
            # text is a line in it, not a repair job. Commands above still
            # work, and the storefront pre-pass still searches (15.4).
            try:
                live = await live_chat.live_session(
                    client, license_id=str(license_id), chann_uid=ctx.chann_uid,
                )
            except Exception:
                log.exception("live chat lookup failed")
                live = None
            if live is not None and not _is_customer_command(message) and not await _customer_report_waiting(client, ctx, message):
                return await _handle_customer_chat_line(
                    client, ctx=ctx, license_id=license_id, session=live, message=message,
                    language=language,
                )
            return await _handle_customer_report(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language, permission_keys=permission_keys, ai_client=ai_client,
            )

    # User review (4 Sep 2026): "ทำอะไรกับ Lead ได้บ้าง" / "ฉันมีสิทธิ์ทำอะไร"
    # get the detailed, permission-derived answer; the plain "ทำอะไรได้บ้าง"
    # still gets the guide below.
    if ctx.oa in ("sales", "technician"):
        capability_reply = await _maybe_capability_question(
            client, ctx=ctx, message=message, permission_keys=permission_keys, language=language,
        )
        if capability_reply is not None:
            return capability_reply

    if ctx.oa in ("sales", "technician") and _matches_phrase(message, CUSTOMER_PROFILE_PHRASES):
        return await _handle_staff_profile_view(client, ctx=ctx, license_id=license_id, language=language)

    # Editing your own details is always permitted (Phase 8, self-edit), so
    # this sits above every entity handler: on the technician OA there is no
    # customer to create, and on the sales OA "ชื่อ …" alone names nobody else.
    if ctx.oa in ("sales", "technician"):
        own = _profile_field_edit(message)
        if own is not None and ctx.oa == "sales" and _looks_like_name_and_phone(message):
            # "ชื่อ สมชาย ใจดี เบอร์ 0812345678" is somebody being added,
            # not a salesperson editing their own record: nobody sets
            # their own name and their own number in one unlabelled
            # breath, and this one parsed the phone INTO the surname
            # ("ใจดี เบอร์ 0812345678") before answering that the profile
            # cannot be edited (10 ก.ย. 2569). The reading goes to the
            # model; the self-edit form without a phone is untouched, and
            # so is the technician OA.
            own = None
        if own is None and early_pending is not None and early_pending.get("entity") == "profile_edit":
            own = _bare_profile_value(message, list(early_pending.get("missing") or []))
            if own is not None:
                await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        if own is not None:
            # "เบอร์ 0812345678 ใช่ไหม" — someone checking what we have on
            # file — stored the phone as "0812345678 ใช่ไหม", question
            # particle and all (10 ก.ย. 2569). A sentence that asks is not
            # a sentence that sets. The pending-answer path above is left
            # alone: a bare value answering a question we just asked is an
            # answer, not a question of its own.
            held_own = _intent_guard_reply(
                message, action="profile_update", language=language,
                triggers=_PROFILE_EDIT_HINTS,
            ) if _profile_field_edit(message) is not None else None
            if held_own is not None:
                return held_own
            return await _handle_profile_intent(
                client, intent={"action": "update", "entity": "profile", "fields": own},
                ctx=ctx, language=language,
            )

    # A bare number right after the help menu picks a topic.
    if _menu_digit(message) is not None:
        step_reply = await _help_step_from_pending(
            client, ctx=ctx, message=message, language=language, permission_keys=permission_keys,
        )
        if step_reply is not None:
            return step_reply

    # Help, before anything else and on every OA. Someone who types "ใช้ยังไง"
    # is telling you they are stuck; routing that through intent parsing to
    # maybe get a permission list back is not an answer. Owner (6 Sep 2026):
    # in layers — a short menu of topics first, one topic at a time after.
    if (
        _is_help_request(message, ctx.oa) or _is_help_step_request(message)
        or _matches_phrase(message, CAPABILITY_PHRASES)
    ):
        return await _help_reply(
            client, ctx=ctx, permission_keys=permission_keys, language=language, message=message,
        )

    # Notes and reminders (6.3/6.7). Before the AI path for the same reason
    # as the other closed-vocabulary commands, and additionally because a
    # misparsed reminder date is silently wrong rather than visibly wrong.
    # A bare greeting on a staff OA. Spending an AI call to work out that
    # "สวัสดี" is a greeting is strange, and when the AI is down the
    # greeting got an apology — the first thing a new user saw.
    if ctx.oa in ("sales", "technician") and _is_only_a_greeting(message):
        return ChatReply(text=_t(STAFF_GREETING, language))

    # The day's work, on either staff OA: a technician's "งานวันนี้" is
    # their jobs, a salesperson's is their follow-ups and open deals.
    if ctx.oa == "technician" and _matches_phrase(message, TODAY_WORK_PHRASES):
        return await _handle_ticket_list(
            client, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language, mine=True,
        )

    if ctx.oa == "sales":
        if _matches_phrase(message, TODAY_WORK_PHRASES):
            return await _handle_work_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, days=1,
            )
        if _matches_phrase(message, UPCOMING_WORK_PHRASES):
            return await _handle_work_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, days=7,
            )
        # Editing and deleting come before listing and creating: every one
        # of their triggers contains "บันทึก", which both of those match.
        #
        # And all three write, so all three are guarded here, once, in front
        # of the branch that picks between them. Measured on the real
        # handlers (10 ก.ย. 2569): "ไม่ต้องบันทึกว่า C-2026-0001 ลูกค้าขอ
        # ส่วนลด" saved the note, "อย่าเพิ่งลบบันทึกนั้น" deleted the
        # customer's latest note — resolving the target from context, with
        # no code in the sentence at all — and "แก้บันทึกยังไง", a question
        # about HOW, overwrote that note with the body "ยังไง". Listing is
        # left outside the guard: reading is not a mutation.
        # Mirrors the dispatch order below exactly — delete, then edit, then
        # list, then create — because anything else leaves a door open.
        # Excluding every message that matched a LIST trigger was that
        # door: "บันทึกของ" is a list trigger, so "แก้บันทึกของ C-2026-0001
        # ยังไง" skipped the guard and overwrote the note with the body
        # "ของ  ยังไง", and "ไม่ต้องลบบันทึกของ C-2026-0001" deleted it
        # (found by the adversarial sweep, 10 ก.ย. 2569).
        _note_lowered = message.lower()
        _writes_a_note = any(
            t in _note_lowered for t in (NOTE_DELETE_TRIGGERS + NOTE_EDIT_TRIGGERS)
        ) or (
            any(t in _note_lowered for t in NOTE_TRIGGERS)
            and not any(t in _note_lowered for t in NOTE_LIST_TRIGGERS)
        )
        if _writes_a_note:
            held_note = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="note_write", language=language,
            )
            if held_note is not None:
                return held_note
        if any(t in message.lower() for t in NOTE_DELETE_TRIGGERS):
            return await _handle_note_edit(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid, delete=True,
            )
        if any(t in message.lower() for t in NOTE_EDIT_TRIGGERS):
            return await _handle_note_edit(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid,
            )
        if any(t in message.lower() for t in NOTE_LIST_TRIGGERS):
            return await _handle_note_list(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        note_trigger = next((t for t in NOTE_TRIGGERS if t in message.lower()), None)
        if note_trigger:
            return await _handle_note_create(
                client, ctx=ctx, license_id=license_id, message=message, trigger=note_trigger,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid,
            )
        day_span = _reminder_list_day(message)
        if day_span is not None:
            return await _handle_work_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, days=day_span,
            )
        if (
            _matches_phrase(message, REMINDER_LIST_TRIGGERS)
            or any(t in message.lower() for t in REMINDER_LIST_TRIGGERS)
        ) and not _mentions_a_datetime(message):
            # The datetime guard tells "นัดหมาย" (show me the diary) from
            # "นัดหมายพรุ่งนี้บ่าย 2" (make one): same word, opposite
            # requests, and the contains-match here used to swallow the
            # second into the first.
            return await _handle_reminder_list(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        # Moving comes before both: "เลื่อนนัด"/"เปลี่ยนเวลา" contain a
        # create trigger, and a bare "เปลี่ยนเวลาเป็น 13.00" matches nothing
        # else at all.
        if _is_reminder_move_command(message):
            # "ยังไม่เปลี่ยนเวลานัดเป็น 16:00" moved it (review v3, B01).
            guarded = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="appointment_move", language=language,
            )
            if guarded is not None:
                return guarded
            return await _handle_reminder_move(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid,
            )
        # Cancelling comes before creating: "ยกเลิกเตือน C-2026-0011" names
        # a record and contains the create verb, so the create matcher would
        # otherwise claim it and answer "ไม่เข้าใจวันที่".
        if _is_reminder_cancel_command(message):
            # "ไม่ต้องยกเลิกนัด C-2026-0001", "อย่าลบนัด C-2026-0001" and
            # "ลบนัดไปแล้วหรือยัง" all cancelled it (review v3, B01).
            guarded = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="appointment_cancel", language=language,
            )
            if guarded is not None:
                return guarded
            return await _handle_reminder_cancel(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid,
            )
        # "นัด" inside a sentence is not a reminder command. "มีลูกค้าใหม่
        # สมชาย ... นัดดูวันศุกร์" is a customer with an appointment in the
        # notes, and matching the substring swallowed the whole message
        # before the AI could create the customer — the seventh substring
        # collision here, and the worst, because it broke the main flow.
        #
        # A reminder command STARTS with its verb or names a record code.
        if _is_reminder_command(message):
            # Same rule as the quote branch: the vocabulary may decline,
            # only a sentence the handler can finish may act. Narrowing
            # the dispatch alone would have cost "ไม่ต้องตั้งนัด" its
            # "ยังไม่ได้ตั้งนัด" answer.
            guarded = _intent_guard_reply(message, action="appointment_create", language=language)
            if guarded is not None:
                return guarded
        if _is_typed_reminder_command(message):
            return await _handle_reminder_create(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid,
            )

    # Phase 10 list/detail reads (Master Spec 9.2). Checked before the AI
    # path and before pending-intent: these are complete requests in
    # themselves, never a slot-filling answer, and a person asking to see
    # their customer list should get it even mid-conversation.
    if ctx.oa == "sales":
        if _matches_phrase(message, CUSTOMER_LIST_PHRASES) or _is_bare_word(message, BARE_CUSTOMER_WORDS):
            return await _handle_customer_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language,
            )
        # "เพิ่มสินค้า" is genuinely ambiguous: it means "put a line item
        # on a deal" AND "add a product to the catalogue", which need
        # different permissions and do different things. Caught by a test
        # the moment this trigger was added.
        #
        # Resolved by whether a deal is in play — named in the message, or
        # the one just being discussed. Someone who has just opened a deal
        # and says "เพิ่มสินค้า พัดลม ราคา 500" means that deal; someone
        # with no deal in context is building their catalogue.
        # "ขอข้อมูลดีลล่าสุด" / "ดีลนี้" / "สินค้าในดีล": the deal in play, never
        # a deal whose code is "ล่าสุด" (owner test, 8 Sep 2026).
        if _asks_latest_deal(message):
            return await _handle_latest_deal(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if _asks_latest_customer(message):
            return await _handle_latest_customer(
                client, ctx=ctx, license_id=license_id, permission_keys=permission_keys, language=language,
            )
        # Lines on a deal or quote, the way people say it: "เพิ่มพัดลมอีก 3
        # ตัว", "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ", "ลบสินค้าพัดลมออก",
        # "ลดพัดลม 1 ตัว". None from the handler means the sentence was not
        # about a line after all, and the readings below still get it.
        line_cmd = _parse_line_item_command(message)
        if line_cmd is not None:
            # "ไม่ต้องเพิ่มพัดลมอีก 3 ตัว" and "เพิ่มพัดลมอีก 3 ตัวได้เท่าไหร่"
            # parse as perfectly good line commands; neither asks for one
            # (review v3, quantity-014/015/016).
            guarded = _intent_guard_reply(message, action="line_item", language=language)
            if guarded is not None:
                return guarded
            handled = await _handle_line_item_command(
                client, ctx=ctx, license_id=license_id, cmd=line_cmd, message=message,
                permission_keys=permission_keys, language=language,
            )
            if handled is not None:
                return handled
        product_query = _product_search_term(message)
        if product_query is not None:
            return await _handle_product_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, query=product_query,
            )
        # The three trigger-table line branches below — remove, edit and
        # product-add — all write, and none of them was guarded; only the
        # _parse_line_item_command path above was. Measured on the real
        # handlers (10 ก.ย. 2569): "ไม่ต้องลบสินค้าพัดลม" removed the line,
        # "ไม่ต้องแก้ราคาพัดลมเหลือ 1400" changed the price, and "เมื่อวาน
        # เพิ่มสินค้า ทีวี 40 นิ้ว ราคา 4000 ไปแล้ว" — someone narrating
        # what they did yesterday — added a NEW line whose product name
        # literally contained "ไปแล้ว". One check in front of all three.
        if any(
            t in message.lower()
            for t in (LINE_REMOVE_TRIGGERS + LINE_EDIT_TRIGGERS + DEAL_PRODUCT_ADD_TRIGGERS)
        ):
            held_line = _intent_guard_reply(message, action="line_item", language=language)
            if held_line is not None:
                return held_line

        remove_trigger = next(
            (t for t in LINE_REMOVE_TRIGGERS if t in message.lower()), None,
        )
        if remove_trigger:
            return await _handle_line_edit(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=remove_trigger, permission_keys=permission_keys,
                language=language, remove=True,
            )
        if any(t in message.lower() for t in QUOTE_VOID_TRIGGERS):
            # "ไม่ต้องยกเลิกใบเสนอราคา Q-2026-0001" rejected the quotation
            # until this guard was here (10 ก.ย. 2569).
            held = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="quote_status", language=language,
            )
            if held is not None:
                return held
            return await _handle_quote_status(
                client, ctx=ctx, license_id=license_id, message=message, target="rejected",
                permission_keys=permission_keys, language=language,
            )
        if any(t in message.lower() for t in QUOTE_ACCEPT_TRIGGERS):
            # And the one that got it backwards: "ลูกค้ายังไม่ตอบรับใบเสนอ
            # ราคา Q-2026-0001" — the customer has NOT accepted — set the
            # quotation to accepted.
            held = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="quote_status", language=language,
            )
            if held is not None:
                return held
            return await _handle_quote_status(
                client, ctx=ctx, license_id=license_id, message=message, target="accepted",
                permission_keys=permission_keys, language=language,
            )
        if (
            any(t in message.lower() for t in QUOTE_DISCOUNT_TRIGGERS)
            or ("ลดราคา" in message.lower() and "%" in message)
            or _is_whole_quote_discount(message)
        ):
            held = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="quote_terms", language=language,
            )
            if held is not None:
                return held
            return await _handle_quote_discount(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        edit_trigger = next(
            (t for t in LINE_EDIT_TRIGGERS if t in message.lower()), None,
        )
        if edit_trigger:
            return await _handle_line_edit(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=edit_trigger, permission_keys=permission_keys,
                language=language,
            )

        bare = await _handle_bare_create_prompt(message, permission_keys, language)
        if bare is not None:
            return bare

        product_trigger = next(
            (t for t in DEAL_PRODUCT_ADD_TRIGGERS if t in message.lower()), None,
        )
        if product_trigger and not re.search(
            r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", message or "", re.IGNORECASE
        ):
            last_ref = await _last_entity_ref(client, ctx)
            if not (last_ref and last_ref.get("entity_type") == "deal"):
                product_trigger = None
        if product_trigger:
            return await _handle_deal_product_add(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=product_trigger, permission_keys=permission_keys,
                language=language,
            )

        if any(t in message.lower() for t in QUOTE_CREATE_TRIGGERS):
            # The words may still SAY NO. They may no longer say yes.
            #
            # This branch used to dispatch on the words appearing anywhere,
            # which is why "ยังไม่สร้างใบเสนอราคา D-2026-0001",
            # "…ไปหรือยัง" and "แค่ถามวิธี…" all issued a real Q-2026-0001
            # (review v3, B02). Narrowing the dispatch to _is_typed_quote_create
            # fixed that — and took the refusal with it: "ไว้ก่อนนะ เดี๋ยวมา
            # ทำใบเสนอราคา" went from "รับทราบครับ ยังไม่ได้สร้าง…" to
            # "ยังไม่แน่ใจว่าต้องการอะไร" (measured 10 ก.ย. 2569).
            #
            # So the guard keeps the whole vocabulary and the dispatch keeps
            # the narrow one. A word can only ever decline here; acting takes
            # a sentence that reads as an order.
            guarded = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="quote_create", language=language,
            )
            if guarded is not None:
                return guarded
            if _is_typed_quote_create(message):
                return await _handle_quote_create_direct(
                    client, ctx=ctx, license_id=license_id, message=message,
                    permission_keys=permission_keys, language=language,
                )

        # "ไม่ต้องสร้างดีลให้ สมชาย ใจดี" created the deal (10 ก.ย. 2569).
        # In front of BOTH deal-create shapes below — the named one and the
        # bare one that reads the name from context — because a refusal
        # that only covers one of them is not a refusal.
        if _parse_after_trigger(message, DEAL_CREATE_TRIGGERS) is not None or any(
            t in message.lower() for t in DEAL_CREATE_BARE_TRIGGERS
        ):
            held = await _guarded_in_context(
                client, ctx=ctx, license_id=license_id, message=message,
                action="deal_create", language=language,
            )
            if held is not None:
                return held

        create_for = _parse_after_trigger(message, DEAL_CREATE_TRIGGERS)
        if create_for is not None and not _deal_name_only(_strip_polite_tail(create_for)):
            # "เปิดดีลให้เลย" / "สร้างดีลให้หน่อย": no name — the customer just
            # mentioned, through the bare path below.
            create_for = None
        if create_for:
            return await _handle_deal_create_direct(
                client, ctx=ctx, license_id=license_id, name=_strip_polite_tail(_deal_name_only(create_for) or ""),
                permission_keys=permission_keys, language=language,
                rest=_after_deal_conjunction(message), message=message, abandoned=abandoned,
            )
        # "สร้างดีล" on its own, right after looking at a customer. Naming
        # them again immediately after being shown their record is the kind
        # of repetition that makes a chat product feel like a form.
        #
        # Taken ONLY when the context is actually there. Refusing here when
        # it is not would be a regression: the AI path can still pull a
        # name out of a longer sentence, and short-circuiting it took that
        # away — caught by an existing test.
        if any(t in message.lower() for t in DEAL_CREATE_BARE_TRIGGERS) and not re.search(
            r"(?<![A-Za-z0-9])(D-\d{4}-\d{4})(?![0-9])", message or "", re.IGNORECASE
        ):
            if await _last_customer_ref(client, ctx):
                return await _handle_deal_create_direct(
                    client, ctx=ctx, license_id=license_id, name=_deal_name_from_message(message),
                    permission_keys=permission_keys, language=language,
                    rest=_after_deal_conjunction(message) or _trailing_product(message), message=message,
                    abandoned=abandoned,
                )
            # Deliberately NO refusal here. Falling through lets the AI
            # pull a customer name out of a longer sentence, which it can
            # and this path cannot — short-circuiting removed that once
            # already and two tests caught it both times.
            #
            # The permission list someone saw in production came from the
            # context having EXPIRED after ten minutes, not from this
            # branch; the TTL above is the actual fix.

        # Checked before the bare list phrases: "ดูดีลของจุใจ" contains
        # "ดูดีล", and matching the shorter form first would list every deal
        # in the tenant instead of that customer's.
        if DEAL_ID_RE.search(message or "") and _looks_like_a_question(message) and _parse_deal_stage_command(message) is None:
            # "ดีล D-2026-0001 เป็นไงบ้าง": the deal, in full (review, 6 Sep 2026).
            return await _handle_deal_detail(
                client, license_id=license_id, code=DEAL_ID_RE.search(message or "").group(0).upper(),
                permission_keys=permission_keys, language=language, ctx=ctx,
            )
        for_customer = _parse_after_trigger(message, DEAL_FOR_CUSTOMER_TRIGGERS) or _deal_owner_asked(message)
        if not for_customer and _CONTEXT_CUSTOMER_DEALS_RE.match(_canonical(message)):
            last_customer = await _last_customer_ref(client, ctx)
            if last_customer and last_customer.get("name"):
                for_customer = str(last_customer["name"])
        if for_customer:
            return await _handle_deal_list(
                client, ctx=ctx, license_id=license_id, permission_keys=permission_keys,
                language=language, for_customer=for_customer,
            )

        if _matches_phrase(message, DEAL_OPEN_PHRASES):
            return await _handle_deal_list(
                client, ctx=ctx, license_id=license_id, permission_keys=permission_keys,
                language=language, open_only=True,
            )
        if any(t in message.lower() for t in DEAL_CLOSE_DATE_TRIGGERS) and not _looks_like_deal_creation(message):
            return await _handle_deal_close_date(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )

        if _matches_phrase(message, SALES_SUMMARY_PHRASES) or any(
            p in message.lower() for p in SALES_SUMMARY_PHRASES
        ):
            return await _handle_sales_summary(
                client, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )

        value_match = _DEAL_VALUE_RE.search(message or "")
        if value_match:
            return await _handle_deal_query(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, kind="over_value",
                threshold=_thai_amount(value_match.group(1), value_match.group(2)),
            )
        for query_kind, phrases in DEAL_QUERY_PHRASES.items():
            if any(p in message.lower() for p in phrases):
                return await _handle_deal_query(
                    client, license_id=license_id, permission_keys=permission_keys,
                    language=language, kind=query_kind,
                )

        if _matches_phrase(message, DEAL_LIST_PHRASES) or _is_bare_word(message, BARE_DEAL_WORDS):
            return await _handle_deal_list(
                client, ctx=ctx, license_id=license_id, permission_keys=permission_keys,
                language=language,
            )
        if _matches_phrase(message, PRODUCT_LIST_PHRASES) or _is_bare_word(message, BARE_PRODUCT_WORDS) or (
            # "ราคาแอร์เท่าไหร่" on the staff OA: the catalogue with prices.
            _asks_price(message) and _looks_like_a_question(message)
            and not re.search(r"(?<![A-Za-z0-9])(?:SR|[CDQT])-\d{4}-\d{4}", message or "", re.I)
            and not any(w in _canonical(message) for w in ("ดีล", "ใบเสนอ", "ส่วนลด", "quote", "deal", "ค่าแรง", "ค่าบริการ"))
            and not re.search(r"\d{3,}", message or "") and len(_normalise(message)) <= 30
        ):
            return await _handle_product_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language,
            )
        if _matches_phrase(message, QUOTE_LIST_PHRASES):
            return await _handle_quote_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language,
            )

        search_term = _parse_after_trigger(message, CUSTOMER_SEARCH_TRIGGERS)
        if search_term is None and not _looks_like_name_and_phone(message):
            # "ลูกค้าชื่อสมชาย", "เบอร์สมชาย", "สมชาย เบอร์อะไร", "ค้นหา สมชาย"
            #
            # But a term carrying a full name AND a phone number is not a
            # lookup — nobody searches by handing over both. That shape
            # answered "ไม่พบลูกค้าที่ตรงกับ สมชาย ใจดี เบอร์ 0812345678"
            # to somebody adding a customer (10 ก.ย. 2569). The explicit
            # "ค้นหาลูกค้า …" arm above still wins.
            search_term = _customer_lookup_term(message)
        if search_term is not None:
            search_term = _strip_polite_tail(re.sub(r"^(?:ที่ชื่อ|ชื่อว่า|ชื่อ|ที่)\s*", "", search_term).strip())
            if not search_term:
                return ChatReply(text=_t(SEARCH_NEEDS_TERM, language))
            return await _handle_customer_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, search_term=search_term,
            )

        customer_code = _parse_after_trigger(message, CUSTOMER_DETAIL_TRIGGERS)
        if customer_code is not None:
            customer_code = _strip_polite_tail(customer_code)
            return await _handle_customer_detail(
                client, license_id=license_id, code=customer_code,
                permission_keys=permission_keys, language=language, ctx=ctx,
            )

        # Issuing a document builds and sends a PDF to the customer.
        # "ไม่ต้องออกเอกสาร Q-2026-0001", "เช่น พิมพ์ว่า ออกเอกสาร …" and
        # "ออกรายงานยังไง" (which picked the person's only approved report
        # and issued it) all produced one (10 ก.ย. 2569).
        #
        # In front of the RE-ISSUE branch as well, not after it: that
        # branch is deliberately first, because "ออกเอกสารใหม่" contains
        # "ออกเอกสาร" — so a guard placed below it never saw
        # "ไม่ต้องออกเอกสารใหม่ Q-2026-0001", which re-issued the document.
        _DOC_TRIGGERS = (
            QUOTE_ISSUE_TRIGGERS + QUOTE_REISSUE_PHRASES
            + REPORT_PDF_TRIGGERS + REPORT_PDF_REISSUE
        )
        if any(t in message.lower() for t in _DOC_TRIGGERS):
            held_doc = _intent_guard_reply(
                message, action="document_issue", language=language,
                triggers=_DOC_TRIGGERS,
            )
            if held_doc is not None:
                return held_doc
        # Re-issue checked first: "ออกเอกสารใหม่" contains "ออกเอกสาร", the
        # same substring trap as ไม่สำเร็จ/สำเร็จ in Phase 9.
        reissue_code = _parse_after_trigger(message, QUOTE_REISSUE_PHRASES)
        if reissue_code is not None:
            return await _handle_quote_issue(
                client, license_id=license_id, code=reissue_code,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid, allow_reissue=True,
            )
        issue_code = _parse_after_trigger(message, QUOTE_ISSUE_TRIGGERS)
        if issue_code is not None:
            return await _handle_quote_issue(
                client, license_id=license_id, code=issue_code,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid, allow_reissue=False,
            )

        deal_code = _parse_after_trigger(message, DEAL_DETAIL_TRIGGERS)
        if deal_code is not None:
            return await _handle_deal_detail(
                client, license_id=license_id, code=deal_code,
                permission_keys=permission_keys, language=language, ctx=ctx,
            )
        # "ลูกค้าสนใจอยากได้พัดลม 1 ตัว" right after adding the customer: a
        # deal with that line, offered (owner test, 8 Sep 2026).
        interest = _sales_interest_item(message)
        if interest is not None:
            offered = await _handle_sales_interest(
                client, ctx=ctx, license_id=license_id, item=interest, message=message,
                permission_keys=permission_keys, language=language,
            )
            if offered is not None:
                return offered

    # Company identity (Phase 10) — same closed-pattern reasoning, and one
    # step stronger: these values are printed on a legal document the
    # customer receives, so they must never pass through a model that could
    # "correct" a tax ID. Sales OA only, since this is a company-management
    # action with no meaning on the Customer or Technician channels.
    if ctx.oa == "sales":
        team_reply = await _maybe_handle_teams(
            client, ctx=ctx, license_id=license_id, message=message,
            permission_keys=permission_keys, language=language,
        )
        if team_reply is not None:
            return team_reply
    if ctx.oa == "sales" and (any(t in (message or "").lower() for t in SERIAL_REGISTER_TRIGGERS) or (
        _is_register_request(message) and _names_a_serial(message)
    )):
        # "ไม่ต้องลงทะเบียน SN12345678" registered it (10 ก.ย. 2569).
        held_reg = _intent_guard_reply(message, action="warranty_register", language=language)
        if held_reg is not None:
            return held_reg
        return await _handle_warranty_register(
            client, ctx=ctx, license_id=license_id, message=message, language=language,
            permission_keys=permission_keys,
        )
    if ctx.oa == "sales" and _matches_phrase(message, SHOP_INFO_PHRASES):
        return await _handle_shop_info(client, ctx=ctx, license_id=license_id, language=language)
    if ctx.oa == "sales" and _matches_phrase(message, WARRANTY_BOOK_PHRASES):
        return await _handle_warranty_book(
            client, license_id=license_id, permission_keys=permission_keys, language=language,
        )
    if ctx.oa == "sales" and _matches_phrase(message, TECHNICIAN_LIST_PHRASES):
        return await _handle_technician_list(
            client, license_id=license_id, permission_keys=permission_keys, language=language,
        )

    if ctx.oa == "sales" and (_is_company_profile_view(message) or _matches_phrase(message, SETTINGS_PHRASES)):
        return await _handle_company_profile_view(
            client, license_id=license_id, permission_keys=permission_keys,
            language=language,
        )

    company_updates = (
        _parse_company_profile_commands(message) if ctx.oa == "sales" else []
    )
    if company_updates:
        # These values are printed on documents the customer receives, so a
        # tax ID set from "ตั้งที่อยู่บริษัทยังไง" is worse than most: the
        # how-to form wrote the question itself into the record.
        held_company = _intent_guard_reply(
            message, action="company_update", language=language,
            # The handler's own words, so the guard and the parser agree on
            # what the action is called: ACTION_WORDS carries the generic
            # phrasings, but the commands people actually type are
            # "ตั้งเลขผู้เสียภาษี …", "ตั้งที่อยู่บริษัท …", one per field.
            triggers=tuple(t for triggers, _f in COMPANY_FIELD_TRIGGERS for t in triggers),
        )
        if held_company is not None:
            return held_company
        return await _handle_company_profile_command(
            client, license_id=license_id, updates=company_updates,
            permission_keys=permission_keys, language=language, actor_id=ctx.chann_uid,
        )

    # Same reasoning: deal stage transitions (9.6) are a closed, deterministic
    # pattern (a deal code plus a small set of stage keywords) — matched
    # directly rather than sent through the AI parser, and checked before
    # pending-intent since it is unrelated to any in-progress slot-filling.
    deal_stage_cmd = _parse_deal_stage_command(message) if ctx.oa == "sales" else None
    if deal_stage_cmd is None and ctx.oa == "sales":
        # "ปิดสำเร็จ" with no code, right after working on a deal. The
        # parser needs a code; the context has one. Only when the stage
        # word is the whole message — "ปิดสำเร็จ" alone — so a sentence
        # that merely contains it is not hijacked.
        bare_stage = _bare_stage_word(message)
        if bare_stage:
            last_ref = await _last_entity_ref(client, ctx)
            if last_ref and last_ref.get("entity_type") == "deal" and last_ref.get("code"):
                deal_stage_cmd = (str(last_ref["code"]).upper(), bare_stage)
            elif last_ref and last_ref.get("entity_type") == "quote":
                # The last thing discussed was the quote; "ปิดสำเร็จ" after
                # sending one means the deal behind it. Closing the deal
                # is what happens right after the quote is accepted.
                try:
                    quotes = await client.list_quotes(str(license_id))
                    quote = next(
                        (q for q in quotes if str(q.get("id")) == str(last_ref.get("entity_id"))),
                        None,
                    )
                    if quote and quote.get("deal_id"):
                        deals = await client.list_deals(str(license_id))
                        deal = next(
                            (d for d in deals if str(d.get("id")) == str(quote["deal_id"])), None,
                        )
                        if deal and deal.get("deal_id"):
                            deal_stage_cmd = (str(deal["deal_id"]).upper(), bare_stage)
                except Exception:
                    log.exception("could not resolve the deal behind the last quote")
    if deal_stage_cmd is not None:
        deal_code, target_stage = deal_stage_cmd
        # "ยังไม่ต้องปิดดีล D-2026-0001 สำเร็จ" closed the deal as won, and
        # "ลูกค้าบอกว่าปิดดีล D-2026-0001 สำเร็จแล้วเหรอ" — a question about
        # what someone else said — closed it too (10 ก.ย. 2569). The stage
        # keywords are the triggers here; ACTION_WORDS["deal_stage"] carries
        # them, since this branch matches on a parser rather than a table.
        held = _intent_guard_reply(
            message, action="deal_stage", language=language, code=deal_code,
        )
        if held is not None:
            return held
        if "deal.update" not in set(permission_keys):
            catalog = await client.permission_catalog()
            return ChatReply(text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language, oa=ctx.oa,
                requested_action="update", requested_entity="deal",
            ))
        return await _handle_deal_stage_command(
            client, license_id=license_id, deal_code=deal_code, target_stage=target_stage,
            permission_keys=permission_keys, language=language, actor_id=ctx.chann_uid,
            message=message,
            )

    # What the previous turn was still waiting for, if anything. Loaded before
    # parsing so the model can be told about it — a bare "0812345678" is not
    # parseable in isolation, only as the answer to a question that was asked.
    pending_intent = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    if pending_intent is not None and pending_intent.get("entity") in _DETERMINISTIC_FLOWS:
        # A report draft or a customer's open report is not the model's to
        # complete or to clear; the message is read on its own.
        pending_intent = None

    # User review (4 Sep 2026): the closed follow-ups this engine asks for —
    # a duplicate customer's fate, a merge conflict, a delete confirmation,
    # "is the deal for the customer just mentioned?" — are answered with a
    # word or a number and never sent through the model.
    if pending_intent is not None and pending_intent.get("entity") in (
        "customer_duplicate", "customer_merge_confirm", "customer_archive_confirm", "deal_context_confirm",
        "deal_item_confirm", "draft_customer_deal_confirm",
    ):
        kind = pending_intent.get("entity")
        if kind == "deal_item_confirm":
            return await _resolve_deal_item_confirm(
                client, ctx=ctx, license_id=license_id, message=message, pending=pending_intent,
                permission_keys=permission_keys, language=language,
            )
        if kind == "draft_customer_deal_confirm":
            return await _resolve_draft_customer_deal_confirm(
                client, ctx=ctx, license_id=license_id, message=message, pending=pending_intent,
                permission_keys=permission_keys, language=language,
            )
        if kind == "customer_duplicate":
            return await _resolve_customer_duplicate(
                client, ctx=ctx, license_id=str(license_id), message=message, pending=pending_intent,
                permission_keys=permission_keys, language=language,
            )
        if kind == "customer_merge_confirm":
            return await _resolve_customer_merge_confirm(
                client, ctx=ctx, license_id=str(license_id), message=message, pending=pending_intent,
                permission_keys=permission_keys, language=language,
            )
        if kind == "customer_archive_confirm":
            return await _resolve_archive_confirm(
                client, ctx=ctx, license_id=license_id, message=message, pending=pending_intent,
                permission_keys=permission_keys, language=language,
            )
        return await _resolve_deal_context_confirm(
            client, ctx=ctx, license_id=license_id, message=message, pending=pending_intent,
            permission_keys=permission_keys, language=language,
        )

    # A bare number answering "which one did you mean?" is a closed,
    # deterministic pattern — same reasoning as deal-stage-command and
    # technician-invite above: matched directly, never sent through the AI
    # parser, since a lone digit carries no meaning parse_intent could
    # recover on its own anyway.
    if (
        pending_intent is not None
        and pending_intent.get("entity") == "customer_disambiguation"
        and (message or "").strip().isdigit()
    ):
        return await _resolve_customer_disambiguation(
            client, ctx=ctx, license_id=license_id, message=message,
            pending=pending_intent, permission_keys=permission_keys, language=language,
        )

    if pending_intent is not None and pending_intent.get("entity") == "help_menu":
        # A topic was offered and something else was typed: the menu is
        # done, the message is whatever it is.
        await _drop_pending_quietly(client, ctx)
        pending_intent = None
    if pending_intent is not None and pending_intent.get("missing"):
        # Not only the six words the abort list happens to hold: "หยุดก่อน",
        # "พักไว้ก่อน", "เดี๋ยวค่อยทำ" and "ยังไม่เพิ่มลูกค้านะ" are the same
        # request and went to the model, which answered "not sure"
        # (review v3, pending-cancel 008-012).
        #
        # But only the reasons that MEAN abandonment. Accepting every HOLD
        # meant a question mid-flow — "ต้องกรอกอะไรบ้าง", "กรอกยังไง",
        # "เพิ่มไปหรือยัง" — destroyed the half-filled record and answered
        # "ยกเลิกแล้วครับ", a cancellation the person never asked for.
        _abort_word = _normalise(message) in _SLOT_FILL_ABORT_WORDS
        _verdict = intent_to_act(
            message, action="pending_flow", canonical=_canonical(message),
        )
        if _abort_word or _verdict.reason in _ABANDONING_REASONS:
            # "ยกเลิก" while a question is open: closed, deterministic, no
            # model (review, 6 Sep 2026).
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(SLOT_FILL_CANCELLED, language))
        if _verdict.reason in ("question", "howto", "status"):
            # Answer it, keep the flow, and say where we are. The pending
            # is deliberately left untouched.
            return _slot_fill_still_open(pending_intent, language)
    if pending_intent is None and _is_small_talk(message):
        return ChatReply(text=_t(SMALL_TALK_REPLY, language))

    try:
        intent = await parse_intent(
            message=message,
            chann_uid=ctx.chann_uid,
            role=context.get("role", member.get("role", "")),
            license_id=str(license_id),
            permission_keys=_keys_this_oa_can_use(permission_keys, ctx.oa),
            language=language,
            client=ai_client,
            pending=pending_intent,
            # The OA decides which capabilities exist at all, so it decides
            # what the model is shown. A technician's prompt drops from
            # 13,591 to 6,004 characters and a customer's to 6,783 — and,
            # more to the point, neither is offered an action the
            # permission gate would refuse a moment later
            # (owner, 10 ก.ย. 2569, requirement 2).
            oa=ctx.oa,
        )
    except AINotConfigured as exc:
        # A deploy problem, not an outage — log loudly, but the user still
        # gets the same plain apology rather than a configuration detail.
        log.error("AI not configured: %s", exc)
        return ChatReply(text=unavailable_reply(language))
    except AIUnavailable as exc:
        log.warning("AI unavailable: %s", exc)
        return ChatReply(text=unavailable_reply(language))

    switched_from = None
    if _is_continuation(pending_intent, intent):
        intent = _merge_pending(pending_intent, intent)
    elif (
        pending_intent is not None and pending_intent.get("missing")
        and pending_intent.get("entity") in _CREATE_FLOW_ENTITIES and intent.get("action") != "suggest"
    ):
        # A different request while a create flow waited for its answer:
        # the flow is dropped, and the reply says so — a silent switch reads
        # as the assistant losing the thread (owner test, 8 Sep 2026).
        switched_from = pending_intent
    carried = _abandoned_flow(switched_from) or abandoned
    notice = _switch_notice(switched_from, message, language, intent) if switched_from else ""

    # ... but never start collecting for something this LINE cannot do at
    # all. Asking a technician for a customer's phone number and then
    # refusing the answer as a sales-only command is worse than saying so at
    # the first message (owner, 10 Sep 2026).
    gate_needed = required_permission(intent.get("action") or "", intent.get("entity") or "")
    if gate_needed is not None and not _oa_allows(ctx.oa, gate_needed):
        if pending_intent is not None:
            await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(
            text=_t(SUGGEST_WRONG_OA, language) + "\n\n" + _t(GUIDE_POINTER, language),
            intent=intent, quick_reply_url=_guide_button(ctx.oa, language),
        )

    # Missing fields come first: never refuse a request we did not understand.
    missing = _prune_missing(intent.get("missing") or [], intent, message)
    if missing:
        # Remember what is still outstanding so the next message — which may
        # be nothing but the answer itself — can be understood as part of it.
        held_fields = dict(intent.get("fields") or {})
        if carried is not None:
            held_fields["_abandoned"] = carried
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa,
            action=intent.get("action", ""),
            entity=intent.get("entity"),
            fields=held_fields,
            missing=missing,
            ttl_seconds=PENDING_INTENT_TTL_S,
        )
        return ChatReply(text=notice + ask_for_missing(missing, language), intent=intent)

    # Nothing outstanding any more: whatever was open is either now complete
    # or has been abandoned for a new request. Either way it must not linger.
    if pending_intent is not None:
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    if (intent.get("fields") or {}).get("_abandoned") is not None:
        # Carried through the pending intent; handed on beside the intent,
        # never inside it — the reply's intent is the person's request.
        fields = dict(intent.get("fields") or {})
        carried = carried or fields.pop("_abandoned")
        fields.pop("_abandoned", None)
        intent = {**intent, "fields": fields}

    reply = await _execute_intent(
        client, intent=intent, ctx=ctx, license_id=license_id, message=message,
        permission_keys=permission_keys, language=language, abandoned=carried,
    )
    if notice and (reply.text or "").strip():
        reply.text = notice + reply.text
    return reply


async def _execute_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, abandoned: dict | None = None,
) -> ChatReply:
    """The model's reading, gated and dispatched — the tail of the router,
    separate so a switch notice can be put in front of whatever it says."""
    # One canonical verb from here down. The model answers with whichever
    # synonym the sentence used — "เพิ่มลูกค้าสมชาย" comes back as
    # action="add", "แก้ชื่อลูกค้า" as "edit", "ดูรายการรออนุมัติ" as
    # "view" — and ACTION_ALIASES existed to absorb that. But it was applied
    # at the permission gate ONLY, so the aliases passed the gate and then
    # met handlers that compare the verb directly: `add` + a complete name
    # and phone reached _handle_customer_intent, matched no branch, and fell
    # out of the bottom as "ในแชทยังทำรายการนี้ไม่ได้" — the model had read
    # the sentence correctly and one string comparison threw it away, with
    # nothing written and nothing logged (owner's review of the message
    # path, 10 ก.ย. 2569). Two more tables are keyed on canonical verbs and
    # were being missed the same way: _AI_GUARDED, so "ไม่ต้องนัด" read as
    # action="add" skipped the negation guard entirely, and READ_ACTIONS.
    # The comment on READ_ACTIONS above calls this exact bug out; it is
    # fixed here at the one place every model answer passes through, rather
    # than by another hand-kept list that can drift again.
    raw_action = str(intent.get("action") or "").strip().lower()
    if raw_action:
        intent = {**intent, "action": ACTION_ALIASES.get(raw_action, raw_action)}
    if intent.get("action") == "suggest" and ctx.oa == "customer":
        # A customer holds no permission keys; "you have no permissions,
        # ask your admin" is the wrong sentence for them.
        return _customer_fallback(message, language)
    if intent.get("action") == "suggest":
        net = await _appointment_net(
            client, ctx=ctx, license_id=license_id, message=message,
            permission_keys=permission_keys, language=language,
        )
        if net is not None:
            return net
        catalog = await client.permission_catalog()
        return ChatReply(
            text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language, oa=ctx.oa,
            ),
            intent=intent,
            quick_reply_url=_guide_button(ctx.oa, language),
        )

    # Profile edits (Phase 8) bypass the generic gate entirely: self-edit is
    # always allowed regardless of tenant permission keys, and that "always"
    # is exactly what ACTION_PERMISSIONS cannot express — it maps
    # (action, entity) to a single permission key, with no notion of "unless
    # it's your own record". Handled here, before the gate ever runs.
    if intent.get("entity") == "profile":
        return await _handle_profile_intent(client, intent=intent, ctx=ctx, language=language)

    # The real permission gate. Checked here rather than trusted from the
    # model: asked for "รายงานทางการเงิน", the model happily returned
    # action=view entity=financial_report — an entity that does not exist and
    # that no permission key covers. Echoing "coming soon" at that would both
    # mislead the user and, once Phase 9 adds execution, skip the check
    # entirely for anything the model mislabels.
    req_action = intent.get("action", "")
    req_entity = intent.get("entity")
    needed = required_permission(req_action, req_entity)
    if (
        needed is None
        or needed not in set(permission_keys)
        or not _oa_allows(ctx.oa, needed)
    ):
        # Same net as the suggest branch, but ONLY for the model inventing
        # an entity nobody has (needed is None): a dated sentence the model
        # mislabelled must not outrank the date, the context, and the
        # permission we can all see. A real permission denial, though,
        # stays a denial — quietly turning "เลื่อนปิดดีล..." from someone
        # without deal.update into a reminder would hide the refusal.
        if needed is None:
            net = await _appointment_net(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
            if net is not None:
                return net
        if ctx.oa == "customer":
            return _customer_fallback(message, language)
        catalog = await client.permission_catalog()
        return ChatReply(
            text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language, oa=ctx.oa,
                requested_action=req_action, requested_entity=req_entity,
            ),
            intent=intent,
            quick_reply_url=_guide_button(ctx.oa, language),
        )

    # The model read an action into the sentence; the sentence still has to
    # have asked for it. One call, here, rather than in each branch below:
    # the review supplied action=check_in for "ทำไมต้องเช็คอิน" and
    # action=create/quote for "ยังไม่สร้างใบเสนอราคา D-2026-0001", and both
    # were performed (review v3, B02/B03).
    #
    # Guarded by DEFAULT. The lookup used to return None for a pair nobody
    # had listed, and None meant "go ahead" — so 30 of the 48 mutating
    # (entity, action) pairs the model can dispatch had no shape check at
    # all, `("customer","create")` among them. Measured 10 ก.ย. 2569:
    # "ไม่ต้องเพิ่มลูกค้า สมชาย ใจดี 0812345678" and "…ยังไงครับ" each wrote
    # a real customer row. Now a specific vocabulary is used when one
    # exists and a generic one otherwise, so adding a handler cannot
    # silently add an unguarded road (owner, requirement 5).
    guarded_as = _AI_GUARDED.get((str(req_entity or ""), req_action))
    if guarded_as is None and req_action in _MUTATING_ACTIONS:
        guarded_as = "record_delete" if req_action in ("delete", "cancel") else "record_write"
    if guarded_as is not None:
        # proposed=True: the model claimed this action, possibly from a
        # word the guard's table has never seen. A refusal it cannot bind
        # to the action is then a reason to ask, not a licence to write.
        guarded = _intent_guard_reply(
            message, action=guarded_as, language=language, proposed=True,
        )
        if guarded is not None:
            return guarded

    # Domain execution. Phase 9 adds real customer/deal CRUD; everything
    # else still falls through to the stub below until its own phase lands.
    if intent.get("entity") == "customer":
        return await _handle_customer_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
            permission_keys=permission_keys,
        )
    if intent.get("entity") == "deal":
        return await _handle_deal_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language, message=message, abandoned=abandoned,
        )
    if intent.get("entity") == "product":
        return await _handle_product_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
            permission_keys=permission_keys,
        )
    if intent.get("entity") == "quote":
        return await _handle_quote_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
            permission_keys=permission_keys, message=message,
        )
    if intent.get("entity") == "report":
        return await _handle_sales_summary(
            client, license_id=license_id,
            permission_keys=permission_keys, language=language,
        )
    # The model's FIELDS are checked too, not only its verb. Two ways it
    # got a write it should not have (measured 10 ก.ย. 2569):
    #
    #   "ปิดดีล D-2026-0001 สำเร็จ" on the technician OA came back as
    #   entity=ticket with code="D-2026-0001" — a DEAL code on a job — and
    #   the handler filed a note against a ticket instead. The technician
    #   prompt has no deal vocabulary (by design), so the model reached for
    #   the nearest entity it was allowed to name.
    #
    #   "ไม่ได้ไปนะครับวันนี้ ลูกค้าเลื่อนเอง" came back as entity=ticket
    #   with status="เลื่อนนัด" — not a status this system has — and the
    #   handler read it as a reschedule, moved the appointment and told the
    #   CUSTOMER we had moved it.
    #
    # A value the model invented is not a value. Both are refused here,
    # before dispatch, and the reply says which record type the code is.
    mismatch = _entity_code_mismatch(intent)
    if mismatch is not None:
        return mismatch
    _drop_invented_values(intent)
    if intent.get("entity") in ("ticket", "service_report", "followup", "warranty", "approval"):
        # "approval" was handled inside _handle_ai_understood_intent and
        # never dispatched TO it: "มีอะไรรอผมตรวจบ้าง" and "อนุมัติ
        # SR-2026-0001" passed the gate and fell to the stub below, with
        # working handlers three lines away.
        return await _handle_ai_understood_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language, message=message,
        )
    if intent.get("entity") == "line_item":
        return await _handle_line_item_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language, message=message,
        )
    if intent.get("entity") == "note":
        note_action = intent.get("action")
        if note_action in READ_ACTIONS:
            # "ดูบันทึกของลูกค้ารายนี้" — note.read has been in
            # ACTION_PERMISSIONS since Phase 6 and reached nothing.
            fields = intent.get("fields") or {}
            return await _handle_note_list(
                client, ctx=ctx, license_id=license_id,
                message=" ".join(
                    str(p) for p in (
                        "ดูบันทึก", fields.get("entity_code") or fields.get("code") or "",
                        fields.get("target_name") or "", message,
                    ) if str(p or "").strip()
                ),
                permission_keys=permission_keys, language=language,
            )
        if note_action in ("update", "delete"):
            fields = intent.get("fields") or {}
            return await _handle_note_edit(
                client, ctx=ctx, license_id=license_id,
                # _joined is local to the other router; a note command is
                # three short parts, so building the text here is clearer
                # than reaching for it.
                message=" ".join(
                    str(part) for part in (
                        "ลบบันทึก" if note_action == "delete" else "แก้บันทึก",
                        fields.get("entity_code") or fields.get("code") or "",
                        fields.get("body") or "",
                        message,
                    ) if part
                ),
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid, delete=(note_action == "delete"),
            )
        return await _handle_note_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
        )
    if intent.get("entity") in ("team", "sales_group"):
        return await _handle_team_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language, message=message,
        )
    if intent.get("entity") == "member" and intent.get("action") in READ_ACTIONS:
        # "มีช่างคนไหนบ้าง" — the roster the typed "รายชื่อช่าง" shows.
        return await _handle_technician_list(
            client, license_id=license_id, permission_keys=permission_keys, language=language,
        )
    if intent.get("entity") == "setting":
        return await _handle_setting_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language,
        )

    # Domain execution arrives with the entities themselves (Phase 7+). Until
    # then the parse is echoed back rather than pretending work was done —
    # claiming "created" with nothing written would be a lie the user acts on.
    # The reply names the dashboard page that DOES do it, so an honest "not
    # here" is still somewhere to go.
    return _no_handler_reply(intent, language, ctx.oa)


async def _handle_team_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str, message: str = "",
) -> ChatReply:
    """A team request the model read, routed to the handler the typed words
    reach. _maybe_handle_teams parses a sentence, so the model's reading is
    rebuilt into one — and when it declines (returns None) the honest reply
    with the Teams page is what comes back, never silence."""
    action = str(intent.get("action") or "")
    fields = intent.get("fields") or {}
    name = str(fields.get("team_name") or fields.get("name") or "").strip()
    members = fields.get("members")
    if isinstance(members, (list, tuple)):
        members = ", ".join(str(m) for m in members if str(m or "").strip())
    members = str(members or fields.get("target_name") or "").strip()

    if action in READ_ACTIONS:
        rebuilt = "รายชื่อทีมช่าง"
    elif action == "create" and name:
        # The create trigger takes everything after it as the team's name,
        # so the members must NOT be appended here — "สร้างทีมช่าง ทีมแอร์
        # มี สมศักดิ์" would name the team "ทีมแอร์ มี สมศักดิ์".
        rebuilt = f"สร้างทีมช่าง {name}"
    elif action == "delete" and name:
        rebuilt = f"ลบทีมช่าง {name}"
    elif action == "update" and name and members:
        rebuilt = f"เพิ่ม {members} เข้าทีม {name}"
    else:
        rebuilt = message or ""

    handled = await _maybe_handle_teams(
        client, ctx=ctx, license_id=license_id, message=rebuilt,
        permission_keys=permission_keys, language=language,
    )
    return handled if handled is not None else _no_handler_reply(intent, language, ctx.oa)


async def _handle_setting_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """The shop's own details. Reading is the company card every member can
    see; writing goes through the same validate-all-then-write path the
    typed commands use, so one bad tax id still refuses the whole message."""
    action = str(intent.get("action") or "")
    fields = intent.get("fields") or {}
    if action in READ_ACTIONS:
        return await _handle_company_profile_view(
            client, license_id=license_id, permission_keys=permission_keys, language=language,
        )
    if action == "update":
        # The prompt asks the model for "phone"; the profile column is
        # company_phone. Translating here rather than widening
        # _company_field_to_payload keeps one name per column.
        aliases = {"phone": "company_phone", "email": "company_email",
                   "address": "company_address", "name": "legal_name"}
        updates = []
        for key, value in fields.items():
            field = aliases.get(str(key), str(key))
            if field in COMPANY_PROFILE_LABELS and value not in (None, ""):
                updates.append((field, str(value).strip()))
        if not updates:
            return _no_handler_reply(intent, language, ctx.oa)
        return await _handle_company_profile_command(
            client, license_id=license_id, updates=updates,
            permission_keys=permission_keys, language=language, actor_id=ctx.chann_uid,
        )
    return _no_handler_reply(intent, language, ctx.oa)


_ACTION_VERBS = {
    "create": {"th": "สร้าง", "en": "create"},
    "read": {"th": "ดู", "en": "view"},
    "update": {"th": "แก้ไข", "en": "update"},
    "delete": {"th": "ลบ", "en": "delete"},
    "archive": {"th": "เก็บถาวร", "en": "archive"},
    "assign": {"th": "มอบหมาย", "en": "assign"},
    "approve": {"th": "อนุมัติ", "en": "approve"},
    "reject": {"th": "ไม่อนุมัติ", "en": "reject"},
    "manage": {"th": "จัดการ", "en": "manage"},
}

# Where in the dashboard each entity is managed, and what that page is
# called. A reply that says "not in chat yet — try the dashboard" and stops
# is a dead end: the person has to go and find the screen themselves, and
# the ones who cannot are exactly the ones who asked in chat. Naming the
# page — and handing over the button when a LIFF id is configured — is the
# difference between an answer and a shrug (owner, 8 Sep 2026).
#
# Kept separate from CAPABILITY_GROUP_PAGE on purpose: that map is keyed by
# capability GROUP for "what can I do with X", this one by the entity the
# model emits, and the two vocabularies are not the same list.
ENTITY_DASHBOARD_PAGE: dict[str, tuple[str, dict[str, str]]] = {
    "customer": ("customers", {"th": "รายชื่อลูกค้า", "en": "Customers"}),
    "note": ("customers", {"th": "รายชื่อลูกค้า", "en": "Customers"}),
    "followup": ("customers", {"th": "รายชื่อลูกค้า", "en": "Customers"}),
    "deal": ("deals", {"th": "ดีล", "en": "Deals"}),
    "line_item": ("deals", {"th": "ดีล", "en": "Deals"}),
    "quote": ("quotes", {"th": "ใบเสนอราคา", "en": "Quotes"}),
    "product": ("products", {"th": "สินค้า", "en": "Products"}),
    "ticket": ("tickets", {"th": "งานซ่อม", "en": "Jobs"}),
    "service_report": ("reports", {"th": "รายงานบริการ", "en": "Service reports"}),
    "approval": ("approvals", {"th": "รออนุมัติ", "en": "Approvals"}),
    "warranty": ("warranties", {"th": "การรับประกัน", "en": "Warranties"}),
    "team": ("teams", {"th": "ทีม", "en": "Teams"}),
    "sales_group": ("teams", {"th": "ทีม", "en": "Teams"}),
    "member": ("members", {"th": "สมาชิกและสิทธิ์", "en": "Members and permissions"}),
    "role": ("roles", {"th": "บทบาทและสิทธิ์", "en": "Roles and permissions"}),
    "setting": ("company", {"th": "ข้อมูลบริษัท", "en": "Company details"}),
    "audit_log": ("company", {"th": "ข้อมูลบริษัท", "en": "Company details"}),
    "report": ("index", {"th": "แดชบอร์ด", "en": "Dashboard"}),
}
NO_HANDLER_ON_PAGE = {
    "th": "แต่ในแชทยังทำรายการนี้ไม่ได้ ทำได้ที่หน้า \"{page}\" ในแดชบอร์ดครับ",
    "en": "That is not available in chat yet — the \"{page}\" page in the dashboard does it.",
}
NO_HANDLER_NO_PAGE = {
    "th": "แต่ในแชทยังทำรายการนี้ไม่ได้ ลองใช้แดชบอร์ด หรือแจ้งผู้ดูแลบริษัท",
    "en": "That is not available in chat yet — try the dashboard, or ask the shop admin.",
}
OPEN_ENTITY_PAGE = {"th": "เปิดหน้า{page}", "en": "Open {page}"}


def _entity_page_button(entity, language: str, oa: str = "sales") -> tuple[str, str] | None:
    """The (label, url) that opens the dashboard page for this entity, or
    None when the entity has no page or the shop has no LIFF id."""
    known = ENTITY_DASHBOARD_PAGE.get(str(entity or "").strip().lower())
    if not known:
        return None
    section, label = known
    url = dashboard_link(section, oa)
    return (_t(OPEN_ENTITY_PAGE, language).format(page=_t(label, language)), url) if url else None


def _no_handler_reply(intent: dict, language: str, oa: str = "sales") -> ChatReply:
    """The honest answer, with somewhere to go.

    Nothing is faked here — the request really has no handler — but the
    reply now names the screen that does have one, and carries the button
    to it when the shop is set up for deep links.
    """
    return ChatReply(
        text=_pending_execution_reply(intent, language),
        entity_type=intent.get("entity"),
        intent=intent,
        quick_reply_url=_entity_page_button(intent.get("entity"), language, oa)
        or _guide_button(oa, language),
    )


_SLOT_FILL_ABORT_WORDS = frozenset(_normalise(w) for w in (
    "ยกเลิก", "cancel", "ไม่เอาแล้ว", "เลิก", "ยกเลิกก่อน", "ไม่ทำแล้ว", "never mind",
))
SLOT_FILL_CANCELLED = {
    "th": "ยกเลิกแล้วครับ ไม่ได้บันทึกอะไร",
    "en": "Cancelled — nothing was saved.",
}
# The reasons that actually mean "drop what we were doing". The guard has
# seven, and the slot-fill abort used to accept ALL of them: asking
# "ต้องกรอกอะไรบ้าง" in the middle of adding a customer threw the
# half-filled record away and announced a cancellation nobody asked for
# (owner, 10 ก.ย. 2569 — "การถามแทรกต้องไม่ล้างงานค้าง"). A question is
# not an abandonment; it is a person trying to answer you.
_ABANDONING_REASONS = frozenset({"abandoned", "negated", "later"})
# What to say when the interruption is a question. Answering it and then
# picking the flow back up is the whole point — the reply names what is
# already held, so "ต้องกรอกอะไรบ้าง" is answered rather than deflected.
SLOT_FILL_STILL_OPEN = {
    "th": "ตอนนี้กรอกไว้แล้ว: {have}\nยังขาด: {missing}\nพิมพ์ต่อได้เลยครับ (หรือ \"ยกเลิก\" ถ้าไม่ทำแล้ว)",
    "en": "So far: {have}\nStill needed: {missing}\nJust carry on — or send \"cancel\" to stop.",
}
SLOT_FILL_NOTHING_YET = {"th": "ยังไม่มีข้อมูล", "en": "nothing yet"}
_DETERMINISTIC_FLOWS = frozenset({
    "service_report", "customer_ticket", "pending_customer_message", "ticket_reject", "customer_contact",
    # "which of this customer's machines is the job about" — answered by a
    # tap carrying a serial, resumed by hand above.
    "staff_ticket_device",
    # A draft template waiting for "ใช้เลย" / "แก้เพิ่ม" / "ทิ้ง" is answered
    # by a word, resumed by hand above, and holds the draft HTML — none of
    # which is the model's to complete or to clear.
    "template_design", "template_design_type", "template_refine",
})


def _pending_execution_reply(intent: dict, language: str) -> str:
    """The intent was understood but nothing handles that shape yet.

    Said in words a person uses — "การเก็บถาวรลูกค้า", not "archive
    customer" — because the raw action/entity pair was the single most
    common machine token to reach a screen.
    """
    raw = str(intent.get("action") or "")
    action = ACTION_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    entity = str(intent.get("entity") or "")
    verb = _t(_ACTION_VERBS.get(action, {"th": raw, "en": raw}), language)
    noun = _entity_noun(entity, language) if entity else ""
    known = ENTITY_DASHBOARD_PAGE.get(entity.strip().lower())
    tail = (
        _t(NO_HANDLER_ON_PAGE, language).format(page=_t(known[1], language))
        if known else _t(NO_HANDLER_NO_PAGE, language)
    )
    if language == "en":
        return f"Understood — you want to {verb} {noun}".rstrip() + ". " + tail
    return (
        f"เข้าใจแล้วครับ ต้องการ{verb}{noun} " if noun else "เข้าใจแล้วครับ "
    ) + tail


async def handle_reply(
    client: DataClient,
    *,
    message_id: str,
    reply_text: str,
    ctx: ResolvedContext,
    language: str = "th",
    ai_client=None,
) -> ChatReply:
    """Spec 6.5 — a reply acts on the entity the original message was about."""
    if ctx.resolution is not TenantResolution.SINGLE:
        return ChatReply(text=greet(ctx, language))

    mapping = await client.get_message_entity(str(ctx.license_id), message_id)
    if mapping is None:
        return ChatReply(text=_t(REPLY_NO_SOURCE_MESSAGE, language))

    # Seed the conversation context BEFORE dispatching, not after.
    #
    # This used to call handle_chat_message first and overwrite the reply's
    # entity fields afterwards — by which point the handler had already run
    # and had no idea which record it was meant to act on. So replying
    # "ออกเอกสาร" to a quote-created message still demanded the quote code,
    # even though the message being replied to names it.
    #
    # Writing it into the same last_entity_ref that _resolve_target_or_context
    # already reads means every command that resolves a target gets this for
    # free, rather than each one needing its own reply-aware branch.
    code = await _code_for_entity(
        client, str(ctx.license_id), mapping["entity_type"], str(mapping["entity_id"]),
    )
    if code:
        await _remember_entity(
            client, ctx, entity_type=mapping["entity_type"],
            entity_id=mapping["entity_id"], code=code,
        )

    reply = await handle_chat_message(
        client, message=reply_text, ctx=ctx, language=language, ai_client=ai_client
    )
    # Still asserted on the way out: the entity is decided by what was
    # replied to, not by whatever the model inferred from the reply text.
    reply.entity_type = mapping["entity_type"]
    reply.entity_id = str(mapping["entity_id"])
    return reply


def _is_ai_report_request(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text or len(text) < 4:
        return False
    if _matches_phrase(message, REPORT_LIST_PHRASES) or any(t in text for t in REPORT_PDF_TRIGGERS):
        return False
    # The fixed sales summary ("ยอดขาย", "สรุปยอด", "สรุปยอดขาย" …) stays deterministic.
    if _matches_phrase(message, SALES_SUMMARY_PHRASES) or (
        len(text) <= 14 and any(p in text for p in SALES_SUMMARY_PHRASES)
        and not text.startswith(("รายงาน", "report"))
    ):
        return False
    return any(text.startswith(t) for t in AI_REPORT_TRIGGERS)


async def _handle_ai_report(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, ai_client=None, with_chart: bool = False,
) -> ChatReply:
    from . import reports_ai

    if "view_reports" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    company = ""
    try:
        company = str((ctx.memberships[0] if ctx.memberships else {}).get("company_name") or "")
    except Exception:  # noqa: BLE001
        company = ""
    try:
        out = await reports_ai.handle_report_request(
            client, license_id=str(license_id), message=message, language=language,
            actor_id=ctx.chann_uid, ai_client=ai_client, company_name=company,
            with_chart=with_chart,
        )
    except reports_ai.ReportSpecInvalid as exc:
        return ChatReply(text=_t(reports_ai.INVALID, language).format(reason=str(exc)))
    except (AINotConfigured, AIUnavailable):
        return ChatReply(text=_t(AI_REPORT_UNAVAILABLE, language))
    except DataTierError:
        log.exception("ai report failed for %s", ctx.chann_uid)
        return ChatReply(text=_t(AI_REPORT_UNAVAILABLE, language))
    if out.get("clarify"):
        return ChatReply(text=out["clarify"])
    text = out["text"]
    files_line = reports_ai.files_line(out.get("files") or {}, language)
    if files_line:
        text = f"{text}\n\n{files_line}"
    images: list[str] = []
    buttons: list[tuple[str, str]] = []
    if with_chart:
        # The same result, in the shape they asked for. Three outcomes, and
        # each one is said: the picture; "this is one number, there is
        # nothing to plot"; "the picture could not be made".
        if out.get("chart"):
            images = [out["chart"]]
        elif not out.get("plottable"):
            text += _t(reports_ai.CHART_NEEDS_GROUPS, language)
        else:
            text += _t(reports_ai.CHART_UNAVAILABLE, language)
    else:
        buttons.append((_t(CHART_AS_CHART_BUTTON, language), f"{message.strip()[:250]} เป็นกราฟ"
                        if language != "en" else f"{message.strip()[:250]} as a chart"))
    return ChatReply(
        text=text, images=images, quick_replies=buttons,
        intent={"action": "report", "entity": out["spec"]["entity"]},
    )


def _chart_request(message: str) -> dict | None:
    """Which sales picture this message is asking for, or None when it is
    not asking for one. Kept next to the report handlers on purpose: a
    chart is a report's output format, not a new feature with its own
    vocabulary."""
    text = (message or "").strip().lower()
    if not text or not any(word in text for word in CHART_WORDS):
        return None
    if any(word in text for word in CHART_OWNER_WORDS):
        return {"kind": "owner", "options": {}}
    if any(word in text for word in CHART_PRODUCT_WORDS):
        top = _CHART_TOP_RE.search(text)
        n = next((g for g in (top.groups() if top else ()) if g), None)
        return {"kind": "products", "options": {"top": int(n)} if n else {}}
    if any(word in text for word in CHART_MONTHLY_WORDS):
        months = _CHART_MONTHS_RE.search(text)
        return {"kind": "monthly", "options": {"months": int(months.group(1))} if months else {}}
    # "กราฟดีลแต่ละสถานะ" and a bare "ขอกราฟยอดขาย" are the same picture:
    # the pipeline, which is what "สรุปการขาย" already answers in words.
    return {"kind": "pipeline", "options": {}}


# The pipeline chart is the sales summary with a different skin, so it asks
# for the same permission that summary asks for; the three that slice the
# shop's numbers a new way are reports and ask for view_reports, the key
# the AI report engine already uses.
CHART_PERMISSION = {"pipeline": "deal.read", "monthly": "view_reports",
                    "products": "view_reports", "owner": "view_reports"}


async def _handle_sales_chart(
    client: DataClient, *, ctx: ResolvedContext, license_id, request: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """A picture of the shop's numbers, plus the sentence that says what it
    shows. The text is always complete on its own — a LINE notification
    preview never renders an image, and storage may not be configured."""
    from . import charts, reports_ai, sales_charts

    kind = str(request.get("kind") or "pipeline")
    if CHART_PERMISSION.get(kind, "view_reports") not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    company = ""
    try:
        company = str((ctx.memberships[0] if ctx.memberships else {}).get("company_name") or "")
    except Exception:  # noqa: BLE001
        company = ""
    try:
        answer = await sales_charts.build(
            kind, client, license_id=str(license_id), language=language,
            company_name=company, **(request.get("options") or {}),
        )
    except Exception:  # noqa: BLE001
        log.exception("sales chart failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    text = answer.summary
    images: list[str] = []
    png = charts.render_or_none(answer.chart)
    url = await reports_ai.publish_chart(png, license_id=str(license_id)) if png else None
    if url:
        images = [url]
    else:
        text += _t(CHART_TEXT_ONLY, language)
    buttons = [
        (_t(label, language), _t(says, language))
        for other, label, says in CHART_OTHER_BUTTONS if other != kind
    ][:3]
    return ChatReply(
        text=text, images=images, quick_replies=buttons,
        intent={"action": "report", "entity": "deals"},
    )



# ============================================================ user review, 4 Sep 2026

def _matches_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = (text or "").strip().lower()
    return any(lowered == p.lower() for p in phrases)


def _customer_code(row: dict) -> str:
    return str(row.get("customer_id") or row.get("code") or "")


async def _handle_customer_duplicate(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, language: str,
    duplicate: dict, new_fields: dict,
) -> ChatReply:
    """Issue 2: the Data tier said a record with this phone/email already
    exists. Say which one, and offer the three sensible moves — never
    "please enter it again", which was the loop reviewers hit."""
    existing_id = str(duplicate.get("existing_id") or "")
    code = str(duplicate.get("existing_code") or "")
    field = str(duplicate.get("field") or "phone")
    existing = None
    try:
        existing = await client.get_customer(license_id, existing_id) if existing_id else None
    except Exception:  # noqa: BLE001
        log.exception("could not read the duplicate customer %s", existing_id)
    if existing is None:
        try:
            existing = next(
                (r for r in await client.list_customers(license_id) if str(r.get("id")) == existing_id), None,
            )
        except Exception:  # noqa: BLE001
            existing = None
    existing = existing or {"id": existing_id, "customer_id": code}
    name = _display_name(existing) or code
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="resolve", entity="customer_duplicate",
        fields={"existing": existing, "new_fields": new_fields, "field": field},
        missing=[], ttl_seconds=DUPLICATE_TTL_S,
    )
    value = new_fields.get(field) or existing.get(field) or ""
    return ChatReply(
        text=_t(DUPLICATE_FOUND, language).format(
            name=name, code=code or _customer_code(existing),
            field=_t(DUPLICATE_FIELD_LABEL.get(field, DUPLICATE_FIELD_LABEL["phone"]), language), value=value,
        ),
        entity_type="customer", entity_id=existing_id or None,
        quick_replies=[
            ("ใช้รายชื่อเดิม", "ใช้รายชื่อเดิม"), ("อัปเดตข้อมูลเดิม", "อัปเดตข้อมูลเดิม"), ("ยกเลิก", "ยกเลิก"),
        ],
    )


def _merge_plan(existing: dict, new_fields: dict) -> tuple[dict, dict]:
    """(fill, conflicts): fill = new values for fields the record lacks;
    conflicts = {field: (old, new)} where both exist and differ."""
    fill: dict = {}
    conflicts: dict = {}
    for key in ("first_name", "last_name", "phone", "email", "address", "notes"):
        new = new_fields.get(key)
        if new in (None, ""):
            continue
        old = existing.get(key)
        if old in (None, ""):
            fill[key] = new
        elif str(old).strip().lower() != str(new).strip().lower():
            conflicts[key] = (old, new)
    return fill, conflicts


def _field_lines(items: dict, language: str) -> str:
    lines = []
    for key, pair in items.items():
        label = _t(CUSTOMER_FIELD_LABEL.get(key, {"th": key, "en": key}), language)
        lines.append("• " + label + ": " + str(pair[0]) + " → " + str(pair[1]))
    return "\n".join(lines)


async def _resolve_customer_duplicate(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, message: str,
    pending: dict, language: str, permission_keys: list[str] | None = None,
) -> ChatReply:
    # The confirmation may arrive after a role change or a shop switch —
    # the same reasoning _resolve_archive_confirm states beside it, which
    # was never applied here. Both arms of this resolver write a customer
    # row, and it checked neither the key nor the channel (10 ก.ย. 2569).
    if "customer.update" not in set(permission_keys) or not _oa_allows(ctx.oa, "customer.update"):
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    fields = pending.get("fields") or {}
    existing = fields.get("existing") or {}
    new_fields = fields.get("new_fields") or {}
    name = _display_name(existing) or _customer_code(existing)
    code = _customer_code(existing)
    if _matches_any(message, DUPLICATE_CANCEL_PHRASES):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(text=_t(DUPLICATE_CANCELLED, language))
    if _matches_any(message, DUPLICATE_USE_PHRASES):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        await _remember_customer(client, ctx, existing)
        return ChatReply(
            text=_t(DUPLICATE_USED, language).format(name=name, code=code),
            entity_type="customer", entity_id=existing.get("id"),
            quick_replies=[("สร้างดีล", f"สร้างดีลให้ {name}"), ("ดูข้อมูล", f"ข้อมูลลูกค้า {name}")],
        )
    if _matches_any(message, DUPLICATE_MERGE_PHRASES):
        fill, conflicts = _merge_plan(existing, new_fields)
        updated = existing
        if fill:
            updated = await client.update_customer(license_id, existing["id"], fill, actor_id=ctx.chann_uid)
        await _remember_customer(client, ctx, updated)
        if conflicts:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="resolve", entity="customer_merge_confirm",
                fields={"existing": updated, "conflicts": {k: list(v) for k, v in conflicts.items()}},
                missing=[], ttl_seconds=DUPLICATE_TTL_S,
            )
            return ChatReply(
                text=_t(DUPLICATE_CONFLICTS, language).format(name=name, code=code, lines=_field_lines(conflicts, language)),
                entity_type="customer", entity_id=existing.get("id"),
                quick_replies=[("แทนที่ทั้งหมด", "แทนที่ทั้งหมด"), ("เก็บของเดิม", "เก็บของเดิม")],
            )
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        if not fill:
            return ChatReply(text=_t(DUPLICATE_NOTHING_TO_MERGE, language).format(name=name, code=code),
                             entity_type="customer", entity_id=existing.get("id"))
        labels = ", ".join(_t(CUSTOMER_FIELD_LABEL.get(k, {"th": k, "en": k}), language) for k in fill)
        return ChatReply(
            text=_t(DUPLICATE_MERGED, language).format(name=name, code=code, fields=labels),
            entity_type="customer", entity_id=existing.get("id"),
        )
    return ChatReply(
        text=_t(DUPLICATE_CHOICE_INVALID, language),
        quick_replies=[("ใช้รายชื่อเดิม", "ใช้รายชื่อเดิม"), ("อัปเดตข้อมูลเดิม", "อัปเดตข้อมูลเดิม"), ("ยกเลิก", "ยกเลิก")],
    )


async def _resolve_customer_merge_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, message: str,
    pending: dict, language: str, permission_keys: list[str] | None = None,
) -> ChatReply:
    # The confirmation may arrive after a role change or a shop switch —
    # the same reasoning _resolve_archive_confirm states beside it, which
    # was never applied here. Both arms of this resolver write a customer
    # row, and it checked neither the key nor the channel (10 ก.ย. 2569).
    if "customer.update" not in set(permission_keys) or not _oa_allows(ctx.oa, "customer.update"):
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    fields = pending.get("fields") or {}
    existing = fields.get("existing") or {}
    conflicts = fields.get("conflicts") or {}
    name = _display_name(existing) or _customer_code(existing)
    if _matches_any(message, MERGE_KEEP_PHRASES) or _matches_any(message, DUPLICATE_CANCEL_PHRASES):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(text=_t(MERGE_KEPT, language).format(name=name))
    if _matches_any(message, MERGE_REPLACE_PHRASES):
        replace = {k: v[1] for k, v in conflicts.items()}
        updated = await client.update_customer(license_id, existing["id"], replace, actor_id=ctx.chann_uid)
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        await _remember_customer(client, ctx, updated)
        labels = ", ".join(_t(CUSTOMER_FIELD_LABEL.get(k, {"th": k, "en": k}), language) for k in replace)
        return ChatReply(text=_t(MERGE_REPLACED, language).format(name=name, fields=labels),
                         entity_type="customer", entity_id=existing.get("id"))
    return ChatReply(
        text=_t(DUPLICATE_CONFLICTS, language).format(
            name=name, code=_customer_code(existing), lines=_field_lines({k: tuple(v) for k, v in conflicts.items()}, language),
        ),
        quick_replies=[("แทนที่ทั้งหมด", "แทนที่ทั้งหมด"), ("เก็บของเดิม", "เก็บของเดิม")],
    )


# ---------------------------------------------------------------- Issue 3

def _lead_delete_target(message: str) -> str | None:
    """The name after "ลบ Lead …", or "" when the message points at the
    customer in context ("ลบ Lead นี้"), or None when it is not a delete."""
    lowered = (message or "").strip().lower()
    trigger = next((t for t in LEAD_DELETE_TRIGGERS if lowered.startswith(t)), None)
    if trigger is None:
        return None
    text = (message or "").strip()[len(trigger):].strip()
    changed = True
    while changed and text:
        changed = False
        # longest first, and start over after every strip so "รายนี้ออกจาก
        # lead สมชาย" loses "รายนี้" then "ออกจาก lead" — never a bare "ออก"
        for word in sorted(LEAD_DELETE_NAME_STRIP, key=len, reverse=True):
            if text.lower().startswith(word):
                text = text[len(word):].strip()
                changed = True
                break
            if text.lower().endswith(word):
                text = text[: -len(word)].strip()
                changed = True
                break
    return _strip_polite_tail(text.strip(" :,"))


async def _handle_lead_archive_request(
    client: DataClient, *, ctx: ResolvedContext, license_id, name: str | None,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """Issue 3: "ลบ Lead สมชาย" / "ลบ Lead นี้". Archive is the platform's
    soft delete; it needs customer.archive and an explicit confirmation."""
    if "customer.archive" not in set(permission_keys) or not _oa_allows(ctx.oa, "customer.archive"):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language), quick_reply_url=_guide_button(ctx.oa, language))
    license_id = str(license_id)
    if name:
        row, problem = await _find_one_customer_by_name(
            client, license_id, name, language,
            ctx=ctx, resume_entity="customer", resume_action="archive", resume_fields={},
        )
        if problem is not None:
            return problem
    else:
        last_ref = await _last_customer_ref(client, ctx)
        if last_ref is None:
            return ChatReply(text=_t(ARCHIVE_NEEDS_NAME, language))
        row = next(
            (r for r in await client.list_customers(license_id) if str(r.get("id")) == str(last_ref["customer_id"])),
            {"id": last_ref["customer_id"], "first_name": last_ref["name"]},
        )
    return await _ask_archive_confirmation(client, ctx=ctx, row=row, language=language)


async def _ask_archive_confirmation(client: DataClient, *, ctx: ResolvedContext, row: dict, language: str) -> ChatReply:
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="resolve", entity="customer_archive_confirm",
        fields={"customer": row}, missing=[], ttl_seconds=DUPLICATE_TTL_S,
    )
    stage = str(row.get("stage") or "lead")
    return ChatReply(
        text=_t(ARCHIVE_CONFIRM, language).format(
            name=_display_name(row), code=_customer_code(row), stage=_t(STAGE_LABEL.get(stage, {"th": stage, "en": stage}), language),
        ),
        entity_type="customer", entity_id=row.get("id"),
        quick_replies=[("ยืนยันลบ", "ยืนยันลบ"), ("ยกเลิก", "ยกเลิก")],
    )


async def _resolve_archive_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    row = (pending.get("fields") or {}).get("customer") or {}
    name = _display_name(row)
    if _matches_any(message, DUPLICATE_CANCEL_PHRASES):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(text=_t(ARCHIVE_CANCELLED, language).format(name=name))
    if not _matches_any(message, ARCHIVE_CONFIRM_PHRASES):
        return ChatReply(text=_t(ARCHIVE_CHOICE_INVALID, language),
                         quick_replies=[("ยืนยันลบ", "ยืนยันลบ"), ("ยกเลิก", "ยกเลิก")],
                     )
    # Re-checked at execution: the confirmation may arrive after a role change.
    if "customer.archive" not in set(permission_keys):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    try:
        await client.archive_customer(str(license_id), row["id"], actor_id=ctx.chann_uid)
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(name=name))
        log.exception("could not archive customer %s", row.get("id"))
        raise
    return ChatReply(text=_t(ARCHIVE_DONE, language).format(name=name, code=_customer_code(row)),
                     entity_type="customer", entity_id=row.get("id"))


async def _maybe_lead_cleanup_setting(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    from . import lead_cleanup

    text = (message or "").strip()
    lowered = text.lower()
    set_match = next((p for p in LEAD_CLEANUP_SET_PHRASES if lowered.startswith(p)), None)
    off = _matches_any(text, LEAD_CLEANUP_OFF_PHRASES)
    view = _matches_any(text, LEAD_CLEANUP_VIEW_PHRASES)
    if not (set_match or off or view):
        return None
    # Holding the key is not the same as being allowed to use it here. On
    # the technician OA, where _oa_allows says setting.manage does not
    # exist, "ตั้งค่าลบ lead อัตโนมัติ 90 วัน" wrote the policy — and that
    # policy archives customers in bulk, from a channel with no customer
    # permission at all (10 ก.ย. 2569). Same shape as _appointment_net.
    if not _oa_allows(ctx.oa, "setting.manage"):
        return None
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if set_match or off:
        # The number is pulled with a bare re.search, which happily ignores
        # a trailing question — so "ตั้งค่าลบ lead อัตโนมัติ 90 วัน ได้ยังไง
        # ครับ", asking HOW, switched the whole shop to auto-archiving
        # leads after 90 days (10 ก.ย. 2569). Viewing is left unguarded:
        # reading the setting changes nothing.
        held_setting = _intent_guard_reply(
            message, action="shop_setting", language=language,
            triggers=tuple(LEAD_CLEANUP_SET_PHRASES) + tuple(LEAD_CLEANUP_OFF_PHRASES),
        )
        if held_setting is not None:
            return held_setting
    if set_match:
        digits = re.search(r"(\d{1,4})", text[len(set_match):])
        days = int(digits.group(1)) if digits else 0
        if not 1 <= days <= lead_cleanup.MAX_DAYS:
            return ChatReply(text=_t(LEAD_CLEANUP_BAD_NUMBER, language))
        await client.put_license_setting(str(license_id), lead_cleanup.SETTING_KEY, days, actor_id=ctx.chann_uid)
    elif off:
        await client.put_license_setting(str(license_id), lead_cleanup.SETTING_KEY, 0, actor_id=ctx.chann_uid)
    days = await lead_cleanup.lead_auto_archive_days(client, str(license_id))
    state = _t(LEAD_CLEANUP_ON, language).format(days=days) if days else _t(LEAD_CLEANUP_OFF, language)
    return ChatReply(text=_t(LEAD_CLEANUP_STATE, language).format(state=state))


# ---------------------------------------------------------------- Issue 4

def _deal_details_line(fields: dict, language: str) -> str:
    from .deal_fields import format_amount
    from .thai_datetime import format_thai_date

    amount = fields.get("amount")
    close = fields.get("expected_close_date")
    if amount is not None and close:
        return _t(DEAL_DETAILS_LINE, language).format(
            amount=format_amount(amount, fields.get("currency") or "THB"), close=format_thai_date(close),
        )
    if amount is not None:
        return _t(DEAL_AMOUNT_ONLY_LINE, language).format(amount=format_amount(amount, fields.get("currency") or "THB"))
    if close:
        return _t(DEAL_DATE_ONLY_LINE, language).format(close=format_thai_date(close))
    return ""


def _deal_fields_from_message(message: str, ai_fields: dict | None) -> tuple[dict, list[str]]:
    """Amount / currency / expected close date, read from the message with
    the model's suggestions checked against it. Returns (fields, ambiguous)."""
    from .deal_fields import extract_deal_fields

    out = extract_deal_fields(message or "", ai_fields or {}, local_today())
    fields: dict = {}
    if out["amount"] is not None:
        fields["amount"] = out["amount"]
        fields["currency"] = out["currency"]
    if out["expected_close_date"]:
        fields["expected_close_date"] = out["expected_close_date"]
    if ai_fields and ai_fields.get("notes"):
        fields["notes"] = ai_fields["notes"]
    return fields, list(out["ambiguous"])


async def _ask_deal_ambiguity(
    client: DataClient, *, ctx: ResolvedContext, message: str, target_name: str | None,
    fields: dict, ambiguous: list[str], language: str,
) -> ChatReply:
    """Only the unclear field is asked; everything else read so far is kept
    in the pending intent, so the answer completes the same deal."""
    from .deal_fields import parse_amount

    held = {k: (str(v) if not isinstance(v, str) else v) for k, v in fields.items()}
    if target_name:
        held["target_name"] = target_name
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="create", entity="deal", fields=held,
        missing=ambiguous, ttl_seconds=PENDING_INTENT_TTL_S,
    )
    if "amount" in ambiguous:
        _, _, candidates = parse_amount(message)
        values = ", ".join(f"{c:,.0f}" for c in candidates)
        return ChatReply(text=_t(DEAL_AMOUNT_AMBIGUOUS, language).format(values=values))
    return ChatReply(text=_t(DEAL_DATE_AMBIGUOUS, language))


async def _resolve_deal_context_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    fields = pending.get("fields") or {}
    contact = fields.get("contact") or {}
    deal_fields = dict(fields.get("deal_fields") or {})
    if _matches_any(message, DEAL_CONTEXT_NO) or _matches_any(message, DUPLICATE_CANCEL_PHRASES):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(text=_t(DEAL_CONTEXT_CANCELLED, language))
    if not _matches_any(message, DEAL_CONTEXT_YES):
        return ChatReply(text=_t(DEAL_CONTEXT_CHOICE_INVALID, language),
                         quick_replies=[("ใช่ สร้างเลย", "ใช่"), ("ไม่ใช่ ระบุชื่อ", "ไม่ใช่")],
                     )
    await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    if "deal.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    return await _apply_deal_create(
        client, contact=contact, fields=deal_fields, ctx=ctx,
        license_id=str(license_id), language=language, used_context=True,
    )


async def _confirm_deal_for_context(
    client: DataClient, *, ctx: ResolvedContext, contact: dict, fields: dict, language: str,
) -> ChatReply:
    serialisable = {k: (str(v) if not isinstance(v, (str, int, float)) else v) for k, v in fields.items()}
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="resolve", entity="deal_context_confirm",
        fields={"contact": contact, "deal_fields": serialisable}, missing=[], ttl_seconds=DEAL_CONTEXT_TTL_S,
    )
    return ChatReply(
        text=_t(DEAL_CONTEXT_CONFIRM, language).format(name=_display_name(contact), details=_deal_details_line(fields, language)),
        quick_replies=[("ใช่ สร้างเลย", "ใช่"), ("ไม่ใช่ ระบุชื่อ", "ไม่ใช่")],
    )


# ---------------------------------------------------------------- what the customer wants (owner test, 8 Sep 2026)
#
# "ลูกค้าสนใจอยากได้พัดลม 1 ตัว", said right after adding the customer, is a
# sale in the making. It used to go to the model and come back "ยังไม่แน่ใจ".
# Now it is an offer: open a deal for the customer just added, with that
# line on it — confirmed, because the wrong customer on a deal is worse
# than one tap.
DEAL_INTEREST_CONFIRM = {
    "th": "ต้องการสร้างดีลสำหรับ {name} และเพิ่ม{item}ใช่ไหมครับ?",
    "en": "Create a deal for {name} and add {item}?",
}
DEAL_INTEREST_CANCELLED = {
    "th": "ยังไม่ได้สร้างดีลครับ ถ้าต้องการ พิมพ์ \"สร้างดีลให้ {name}\"",
    "en": 'No deal created. Type "create deal for {name}" when you want one.',
}
DEAL_INTEREST_CHOICE_INVALID = {
    "th": "ตอบ \"ใช่\" เพื่อสร้างดีลและเพิ่มสินค้า หรือ \"ไม่ใช่\"",
    "en": 'Reply "yes" to create the deal with that item, or "no".',
}
DEAL_INTEREST_YES_LABEL = {"th": "ใช่ สร้างเลย", "en": "Yes, create it"}
DEAL_INTEREST_NO_LABEL = {"th": "ไม่ใช่", "en": "No"}

_INTEREST_RE = re.compile(
    r"^(?:ลูกค้า(?:คนนี้|รายนี้|ท่านนี้)?|เขา|เค้า|คนนี้|customer|they|he|she)?\s*"
    r"(?:สนใจ(?:อยากได้|อยากซื้อ|จะซื้อ|ซื้อ|จะเอา)?|อยากได้|อยากซื้อ|ต้องการ(?:ซื้อ|จะซื้อ)?|จะซื้อ|จะเอา|เอา|ขอ(?:ซื้อ)?|สั่ง(?:ซื้อ)?|"
    r"wants?(?: to buy)?|would like|is interested in|interested in|asked for|ordered?)\s*(.+)$",
    re.I,
)
_INTEREST_STOP_WORDS = (
    "ดีล", "ดู", "ข้อมูล", "รายการ", "รายชื่อ", "เบอร์", "โทร", "รหัส", "ใบเสนอราคา", "pdf", "ไฟล์", "เอกสาร", "นัด", "เตือน",
    "วันที่", "พรุ่งนี้", "วันนี้", "บ่าย", "เช้า", "โมง", "ลูกค้าใหม่", "ชื่อ", "ที่อยู่", "ซ่อม", "แจ้ง", "เปลี่ยน", "แก้", "ลบ", "ยกเลิก",
    "เรื่อง", "บ้าน", "คุย", "ปรึกษา", "สอบถาม", "ถาม", "ทราบ", "รู้",
    "ประกัน", "ช่าง", "ทีม", "สิทธิ์", "ภาษา", "เมนู", "วิธี", "quote", "deal", "phone", "address", "repair", "reminder",
    "appointment", "list", "report", "help", "menu",
)


def _sales_interest_item(message: str) -> str | None:
    """"พัดลม 1 ตัว" out of "ลูกค้าสนใจอยากได้พัดลม 1 ตัว", or None when the
    sentence is not a customer wanting a product."""
    text = " ".join((message or "").split())
    if not text or len(text) > 80 or _looks_like_a_question(text):
        return None
    m = _INTEREST_RE.match(text)
    if not m:
        return None
    rest = _strip_polite_tail(m.group(1).strip(" :,"))
    low = rest.lower()
    if not rest or any(w in low for w in _INTEREST_STOP_WORDS) or re.search(r"[CDQT]-\d{4}-\d{4}|\d{7,}", rest, re.I):
        return None
    # "ขอ …" / "เอา …" alone are how people ask for anything; only a counted
    # thing ("ขอพัดลม 2 ตัว") is a customer wanting a product.
    verb = text[: m.start(1)].strip().lower()
    counted = bool(_QTY_RE.search(rest))
    if any(verb.endswith(v) for v in ("ขอ", "ขอซื้อ", "เอา", "จะเอา")) and not counted:
        return None
    return rest


def _interest_is_counted(item: str) -> bool:
    return bool(_QTY_RE.search(item or ""))


async def _handle_sales_interest(
    client: DataClient, *, ctx: ResolvedContext, license_id, item: str, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    """Offer a deal for the customer just added, with the item on it.

    None when there is no customer in context — the model still gets the
    sentence, as before.
    """
    last_ref = await _last_customer_ref(client, ctx)
    if not last_ref:
        return None
    if not _interest_is_counted(item):
        # "ลูกค้าสนใจเรื่องการซื้อบ้าน" is a remark, not an order. Uncounted,
        # the thing wanted has to be a product the shop sells.
        try:
            products = await client.list_products(str(license_id))
        except Exception:
            products = []
        needle = _strip_item_particles(item).lower()
        if not needle or not any(
            needle in str(p.get("product_name") or "").lower() or str(p.get("product_name") or "").lower() in needle
            for p in products
        ):
            return None
    if "deal.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        pending = None
    if pending is not None and pending.get("missing") and pending.get("entity") in _CREATE_FLOW_ENTITIES:
        # A flow is waiting for its answer: confirm before replacing it,
        # the same as any other command typed mid-flow.
        return await _confirm_flow_switch(client, ctx=ctx, pending=pending, message=message, language=language)
    contact = {"id": last_ref["customer_id"], "first_name": last_ref["name"]}
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="resolve", entity="deal_item_confirm",
        fields={"contact": contact, "item": item}, missing=[], ttl_seconds=DEAL_CONTEXT_TTL_S,
    )
    return ChatReply(
        text=_t(DEAL_INTEREST_CONFIRM, language).format(name=_display_name(contact), item=item),
        quick_replies=[(_t(DEAL_INTEREST_YES_LABEL, language), "ใช่"), (_t(DEAL_INTEREST_NO_LABEL, language), "ไม่ใช่")],
    )


async def _resolve_deal_item_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    fields = pending.get("fields") or {}
    contact = fields.get("contact") or {}
    item = str(fields.get("item") or "")
    if _matches_any(message, DEAL_CONTEXT_NO) or _matches_any(message, DUPLICATE_CANCEL_PHRASES):
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        return ChatReply(text=_t(DEAL_INTEREST_CANCELLED, language).format(name=_display_name(contact)))
    if not _matches_any(message, DEAL_CONTEXT_YES):
        return ChatReply(
            text=_t(DEAL_INTEREST_CHOICE_INVALID, language),
            quick_replies=[(_t(DEAL_INTEREST_YES_LABEL, language), "ใช่"), (_t(DEAL_INTEREST_NO_LABEL, language), "ไม่ใช่")],
        )
    await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    if "deal.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    reply = await _apply_deal_create(
        client, contact=contact, fields={}, ctx=ctx, license_id=license_id, language=language,
    )
    deal_code = None
    if reply.entity_id:
        try:
            deals = await client.list_deals(license_id)
            deal_code = next((str(d.get("deal_id")) for d in deals if str(d.get("id")) == str(reply.entity_id)), None)
        except Exception:
            deal_code = None
    elif reply.quick_replies:
        # "already has D-… open": the item goes on that one instead.
        existing = DEAL_ID_RE.search(reply.quick_replies[0][1] or "")
        deal_code = existing.group(0).upper() if existing else None
    if not deal_code or not item:
        return reply
    follow_on = await _handle_deal_product_add(
        client, ctx=ctx, license_id=license_id, message=f"เพิ่มสินค้า {item} เข้าดีล {deal_code}",
        trigger="เพิ่มสินค้า", permission_keys=permission_keys, language=language,
    )
    return ChatReply(
        text=f"{reply.text}\n{follow_on.text}",
        entity_type=follow_on.entity_type or reply.entity_type,
        entity_id=follow_on.entity_id or reply.entity_id,
        quick_replies=follow_on.quick_replies,
    )


# ---------------------------------------------------------------- "the latest deal"
#
# "ขอข้อมูลดีลล่าสุด" was read as a deal whose code is "ล่าสุด". The deal
# just created or edited is what "ล่าสุด" / "นี้" / "เมื่อกี้" mean; with no
# deal in the conversation, the list of recent deals is the honest answer.
_LATEST_DEAL_RE = re.compile(
    r"^(?:ขอ)?(?:ดู|ข้อมูล|รายละเอียด|เปิด|เช็ค|โชว์|แสดง|show|view|open|see)?(?:ข้อมูล|รายละเอียด)?(?:ดีล|deal)"
    r"(?:ล่าสุด|ที่เพิ่งสร้าง|ที่เพิ่งเปิด|ที่เพิ่งทำ|เมื่อกี้|เมื่อสักครู่|นี้|อันนี้|ตัวนี้|ปัจจุบัน|ที่กำลังทำ|ที่เพิ่งคุย)"
    r"(?:มีอะไรบ้าง|มีสินค้าอะไรบ้าง|หน่อย|เป็นไงบ้าง|เป็นยังไงบ้าง)?$"
)
_LATEST_DEAL_EN_RE = re.compile(
    r"^(?:show|view|open|see|whats|what'?s|details?of|info(?:on|of)?)?(?:me)?(?:the)?(?:latest|last|this|current|newest|mostrecent|recent)deal(?:details?|info)?$"
    r"|^(?:the)?deal(?:i|we)?(?:just)?(?:created|made|opened)$"
)
_DEAL_ITEMS_RE = re.compile(
    r"^(?:ขอ)?(?:ดู)?(?:รายการ)?(?:สินค้า|รายการ|ของ)(?:ที่อยู่|ที่มี)?(?:ใน|ของ|บน)ดีล(?:นี้|ล่าสุด|เมื่อกี้)?(?:มีอะไรบ้าง|ทั้งหมด)?$"
    r"|^ดีล(?:นี้|ล่าสุด|เมื่อกี้)?มี(?:สินค้า|รายการ|ของ)?อะไรบ้าง$"
    r"|^(?:the)?(?:items|lines|products)(?:on|in)(?:the|this)?deal$|^dealitems$|^whatsonthedeal$|^whatisonthedeal$"
)
_LATEST_CUSTOMER_RE = re.compile(
    r"^(?:ขอ)?(?:ดู|ข้อมูล|รายละเอียด|เปิด|เช็ค|show|view)?(?:ข้อมูล)?(?:ลูกค้า|customer)"
    r"(?:ล่าสุด|ที่เพิ่งเพิ่ม|ที่เพิ่งสร้าง|เมื่อกี้|คนล่าสุด|รายล่าสุด|ที่เพิ่งคุย)$"
    r"|^(?:show|view)?(?:me)?(?:the)?(?:latest|last|newest|mostrecent)customer$"
)
DEAL_NO_CONTEXT_LIST = {
    "th": "ยังไม่มีดีลที่เพิ่งคุยถึงในแชทนี้ นี่คือรายการดีลล่าสุดครับ",
    "en": "No deal in this conversation yet — here are the latest deals.",
}
CUSTOMER_NO_CONTEXT_LIST = {
    "th": "ยังไม่มีลูกค้าที่เพิ่งคุยถึงในแชทนี้ นี่คือรายชื่อลูกค้าครับ",
    "en": "No customer in this conversation yet — here is the customer list.",
}


def _asks_latest_deal(message: str) -> bool:
    norm = _normalise(DEAL_ID_RE.sub(" ", message or ""))
    return bool(norm) and bool(_LATEST_DEAL_RE.match(norm) or _LATEST_DEAL_EN_RE.match(norm) or _DEAL_ITEMS_RE.match(norm))


def _asks_latest_customer(message: str) -> bool:
    norm = _normalise(message)
    return bool(norm) and bool(_LATEST_CUSTOMER_RE.match(norm))


async def _handle_latest_deal(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, permission_keys: list[str], language: str,
) -> ChatReply:
    code_match = DEAL_ID_RE.search(message or "")
    if code_match:
        return await _handle_deal_detail(
            client, license_id=license_id, code=code_match.group(0).upper(), permission_keys=permission_keys,
            language=language, ctx=ctx,
        )
    try:
        last_ref = await _last_entity_ref(client, ctx)
    except Exception:
        last_ref = None
    if last_ref and last_ref.get("entity_type") == "deal" and last_ref.get("code"):
        return await _handle_deal_detail(
            client, license_id=license_id, code=str(last_ref["code"]), permission_keys=permission_keys,
            language=language, ctx=ctx,
        )
    listed = await _handle_deal_list(
        client, ctx=ctx, license_id=license_id, permission_keys=permission_keys, language=language,
    )
    if set(permission_keys) & DEAL_VIEW_KEYS:
        listed.text = _t(DEAL_NO_CONTEXT_LIST, language) + "\n" + (listed.text or "")
    return listed


async def _handle_latest_customer(
    client: DataClient, *, ctx: ResolvedContext, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    last_ref = await _last_customer_ref(client, ctx)
    if last_ref and last_ref.get("name"):
        return await _handle_customer_detail(
            client, license_id=license_id, code=str(last_ref["name"]), permission_keys=permission_keys,
            language=language, ctx=ctx,
        )
    listed = await _handle_customer_list(client, license_id=license_id, permission_keys=permission_keys, language=language)
    if "customer.read" in set(permission_keys):
        listed.text = _t(CUSTOMER_NO_CONTEXT_LIST, language) + "\n" + (listed.text or "")
    return listed


# ---------------------------------------------------------------- an abandoned flow, said out loud
#
# "ลูกค้าใหม่ สามเสน เขตคิงคิ" → "กรุณาระบุเบอร์โทร" → "สร้างดีล" → "กรุณาระบุ
# ชื่อลูกค้า": the customer flow was dropped without a word, and when the
# person then typed the same name for the deal they were told no such
# customer exists. Two fixes: a switch that happens without a confirmation
# says so, and the half-made customer is remembered and offered.
FLOW_SWITCHED = {"th": "เปลี่ยนจาก{flow}เป็น{new}แล้วครับ ", "en": "Switched from {flow} to {new}. "}
DRAFT_CUSTOMER_DEAL_OFFER = {
    "th": "ยังสร้างลูกค้า {name} ไม่เสร็จ (ขาด{missing}) ต้องการสร้างลูกค้าโดยไม่มี{missing}และเปิดดีลให้เลยไหมครับ?",
    "en": "Customer {name} is still unfinished (no {missing}). Create them without it and open the deal now?",
}
DRAFT_CUSTOMER_FINISH_FIRST = {
    "th": "งั้นพิมพ์{missing}ของ {name} มาก่อนครับ จะสร้างลูกค้าให้เสร็จแล้วเปิดดีลให้ต่อเลย",
    "en": "Then send {name}'s {missing} first — the customer is finished, and the deal opened right after.",
}
DRAFT_CUSTOMER_CHOICE_INVALID = {
    "th": "ตอบ \"ใช่\" เพื่อสร้างลูกค้าและเปิดดีลเลย หรือ \"ไม่ใช่\" เพื่อใส่{missing}ก่อน",
    "en": 'Reply "yes" to create the customer and the deal now, or "no" to add the {missing} first.',
}
DRAFT_CUSTOMER_YES_LABEL = {"th": "ใช่ สร้างเลย", "en": "Yes, create both"}
DRAFT_CUSTOMER_NO_LABEL = {"th": "ใส่เบอร์ก่อน", "en": "Add the phone first"}


def _abandoned_flow(pending: dict | None) -> dict | None:
    """What a dropped create flow held, small enough to carry inside the
    next pending intent."""
    if not pending or pending.get("entity") not in _CREATE_FLOW_ENTITIES:
        return None
    fields = {k: v for k, v in (pending.get("fields") or {}).items() if not str(k).startswith("_")}
    return {"entity": pending.get("entity"), "action": pending.get("action") or "create", "fields": fields,
            "missing": list(pending.get("missing") or [])}


def _switch_notice(pending: dict, message: str, language: str, intent: dict | None = None) -> str:
    flow = _t(FLOW_LABELS.get(str(pending.get("entity") or ""), {"th": "รายการเดิม", "en": "the previous request"}), language)
    new = ""
    if intent and str(intent.get("action") or "") == "create" and str(intent.get("entity") or "") in FLOW_LABELS:
        new = _t(FLOW_LABELS[str(intent["entity"])], language)
    return _t(FLOW_SWITCHED, language).format(flow=flow, new=new or _new_command_label(message, language))


def _draft_matches_name(draft: dict | None, name: str) -> bool:
    if not draft or draft.get("entity") != "customer" or not name:
        return False
    full = _flow_name(draft.get("fields") or {}).lower()
    needle = name.strip().lower()
    return bool(full) and (needle in full or full in needle)


def _missing_label(missing: list[str], language: str) -> str:
    return ", ".join(MISSING_FIELD_LABELS.get(m, {}).get(language) or str(m) for m in missing) or (
        "ข้อมูล" if language != "en" else "details"
    )


async def _offer_draft_customer_deal(
    client: DataClient, *, ctx: ResolvedContext, draft: dict, deal_fields: dict, language: str,
) -> ChatReply:
    # Everything the half-made customer still needs — last_name included.
    # It used to be filtered out here, so the offer said "ขาดเบอร์โทร"
    # while the record was also missing a surname, and the create handler
    # then asked for the surname after the phone arrived. One list, one
    # question (10 ก.ย. 2569).
    missing = list(draft.get("missing") or [])
    held = {k: (str(v) if not isinstance(v, (str, int, float)) else v) for k, v in (deal_fields or {}).items()}
    await client.set_pending_intent(
        ctx.chann_uid, ctx.oa, action="resolve", entity="draft_customer_deal_confirm",
        fields={"draft": draft.get("fields") or {}, "missing": missing, "deal_fields": held},
        missing=[], ttl_seconds=DEAL_CONTEXT_TTL_S,
    )
    label = _missing_label(missing, language)
    return ChatReply(
        text=_t(DRAFT_CUSTOMER_DEAL_OFFER, language).format(name=_flow_name(draft.get("fields") or {}), missing=label),
        quick_replies=[(_t(DRAFT_CUSTOMER_YES_LABEL, language), "ใช่"), (_t(DRAFT_CUSTOMER_NO_LABEL, language)[:20], "ไม่ใช่")],
    )


async def _resolve_draft_customer_deal_confirm(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    fields = pending.get("fields") or {}
    draft = dict(fields.get("draft") or {})
    missing = list(fields.get("missing") or [])
    deal_fields = dict(fields.get("deal_fields") or {})
    name = _flow_name(draft)
    label = _missing_label(missing, language)
    if _matches_any(message, DEAL_CONTEXT_NO) or _matches_any(message, DUPLICATE_CANCEL_PHRASES):
        # Back to the customer, with the deal to follow once it is complete.
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="create", entity="customer",
            fields={**draft, "_then_deal": deal_fields}, missing=missing, ttl_seconds=PENDING_INTENT_TTL_S,
        )
        return ChatReply(text=_t(DRAFT_CUSTOMER_FINISH_FIRST, language).format(name=name, missing=label))
    if not _matches_any(message, DEAL_CONTEXT_YES):
        return ChatReply(
            text=_t(DRAFT_CUSTOMER_CHOICE_INVALID, language).format(missing=label),
            quick_replies=[(_t(DRAFT_CUSTOMER_YES_LABEL, language), "ใช่"), (_t(DRAFT_CUSTOMER_NO_LABEL, language)[:20], "ไม่ใช่")],
        )
    await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
    keys = set(permission_keys)
    if "customer.create" not in keys or "deal.create" not in keys:
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    editable = {k: v for k, v in draft.items() if k in ("first_name", "last_name", "phone", "email", "address", "notes") and v not in (None, "")}
    try:
        owner = await _member_id_of(client, license_id, ctx)
        if owner:
            editable = {**editable, "owner_member_id": owner}
        row = await client.create_customer(license_id, editable, actor_id=ctx.chann_uid)
    except Exception as exc:  # noqa: BLE001
        structured = getattr(exc, "structured", None) or {}
        if structured.get("error") == "duplicate":
            return await _handle_customer_duplicate(
                client, ctx=ctx, license_id=license_id, language=language, duplicate=structured, new_fields=editable,
            )
        log.exception("creating the drafted customer failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
    await _remember_customer(client, ctx, row)
    created = _t(CUSTOMER_CREATED, language).format(name=f" {_display_name(row)} ")
    deal_reply = await _apply_deal_create(
        client, contact=row, fields=deal_fields, ctx=ctx, license_id=license_id, language=language,
    )
    return ChatReply(
        text=f"{created}\n{deal_reply.text}", entity_type=deal_reply.entity_type or "customer",
        entity_id=deal_reply.entity_id or row.get("id"), quick_replies=deal_reply.quick_replies,
    )


def _looks_like_deal_creation(message: str) -> bool:
    """A sentence that names a person or opens a deal is a creation, not a
    close-date/value command on an existing deal — unless it carries a
    D- code, which is unambiguous. User review (4 Sep 2026): "ดีลนี้ของ
    อาทิตย์ มูลค่า … คาดว่าจะปิด …" was swallowed by the close-date command."""
    text = message or ""
    if re.search(r"\bD-\d{4}-\d{4}\b", text, re.IGNORECASE):
        return False
    lowered = text.lower()
    if any(t in lowered for t in DEAL_CREATE_TRIGGERS) or any(t in lowered for t in DEAL_CREATE_BARE_TRIGGERS):
        return True
    if "เปิดดีล" in lowered:
        return True
    return _deal_name_from_message(text) is not None and ("มูลค่า" in lowered or "ราคา" in lowered or "ดีล" in lowered)


# ---------------------------------------------------------------- Issue 1

def _capability_group_asked(message: str) -> str | None:
    """Which area a "what can I do with …" question is about, if any."""
    lowered = (message or "").strip().lower()
    if not any(m in lowered for m in CAPABILITY_ASK_MARKERS):
        return None
    hits: list[tuple[int, str]] = []
    for group, aliases in CAPABILITY_GROUP_ALIASES.items():
        for alias in aliases:
            position = lowered.find(alias)
            if position != -1:
                hits.append((position, group))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def _is_general_capability_question(message: str) -> bool:
    lowered = (message or "").strip().lower()
    return (
        ("ทำอะไร" in lowered or "ทําอะไร" in lowered or "what can" in lowered or "what does" in lowered)
        and ("ได้บ้าง" in lowered or "ได้ไหม" in lowered or "do" in lowered)
    ) or lowered in ("ระบบทำอะไรได้บ้าง", "คุณสามารถทำอะไรได้บ้าง", "ทำอะไรได้บ้าง")


def _group_keys(group: str, catalog: list[dict]) -> list[dict]:
    return [e for e in catalog if (e.get("group") or (str(e.get("key") or "").split(".", 1)[0])) == group
            and not str(e.get("key") or "").startswith("platform.admin.")]


def _label_of(entry: dict, language: str) -> str:
    return (entry.get("label") or {}).get(language) or (entry.get("label") or {}).get("th") or str(entry.get("key"))


def capability_detail(group: str, permission_keys, catalog: list[dict], language: str, oa: str = "sales") -> ChatReply:
    """Issue 1: one area in depth — the commands this person can actually
    run there (HELP_SECTIONS filtered by permission), the permissions held
    and not held (from the catalogue, never hardcoded), and the dashboard
    page when one exists."""
    held = set(permission_keys)
    group_label = _group_label(group, language)
    commands = []
    seen = set()
    for _title, entries in HELP_SECTIONS:
        for key, cmd, what in entries:
            key_group = key.split(".", 1)[0]
            if key_group != group or key not in held or cmd in seen:
                continue
            seen.add(cmd)
            commands.append(f"• {cmd}\n   → {what}")
    entries = _group_keys(group, catalog)
    held_labels = [_label_of(e, language) for e in entries if e.get("key") in held]
    missing_labels = [_label_of(e, language) for e in entries if e.get("key") not in held]
    url = dashboard_link(CAPABILITY_GROUP_PAGE.get(group, ""), oa) if group in CAPABILITY_GROUP_PAGE else None
    if not held_labels and not commands:
        return ChatReply(
            text=_t(CAPABILITY_DETAIL_NONE, language).format(group=group_label) + "\n" + _t(CAPABILITY_FOLLOW_UP, language),
            quick_reply_url=_guide_button(oa, language),
        )
    # One screen (review, 6 Sep 2026, B16): four commands at most, two lines
    # each, then the permissions — the rest is one line naming the guide.
    lines = [_t(CAPABILITY_DETAIL_HEADER, language).format(group=group_label)]
    lines.extend(commands[:4])
    if len(commands) > 4:
        lines.append(_t(CAPABILITY_MORE_COMMANDS, language).format(n=len(commands) - 4))
    if held_labels:
        lines.append(_t(CAPABILITY_HELD, language).format(labels=", ".join(held_labels)))
    if missing_labels:
        lines.append(_t(CAPABILITY_NOT_HELD, language).format(labels=", ".join(missing_labels)))
    if len(commands) <= 4:
        lines.append(_t(CAPABILITY_FOLLOW_UP, language))
    return ChatReply(
        text="\n".join(lines),
        quick_reply_url=(_t(CAPABILITY_OPEN_PAGE, language).format(group=group_label), url) if url else _guide_button(oa, language),
    )


def capability_overview_line(permission_keys, catalog: list[dict], language: str) -> str:
    """One line naming the areas this person holds anything in, for the end
    of the guide — so "what can the system do" is answered with the guide
    AND an invitation to drill into one area."""
    held = set(permission_keys)
    groups: list[str] = []
    for entry in catalog:
        key = str(entry.get("key") or "")
        if key not in held or key.startswith("platform.admin."):
            continue
        group = entry.get("group") or key.split(".", 1)[0]
        if group not in groups:
            groups.append(group)
    if not groups:
        return ""
    return _t(CAPABILITY_OVERVIEW, language).format(groups=" · ".join(_group_label(g, language) for g in groups))


def permission_summary(permission_keys, catalog: list[dict], language: str) -> str:
    """Issue 1: "ฉันมีสิทธิ์ทำอะไร" — grouped, from the catalogue, only what
    is held. Short labels per area, not a flat 49-item list."""
    held = set(permission_keys)
    groups: dict[str, list[str]] = {}
    for entry in catalog:
        key = str(entry.get("key") or "")
        if key not in held or key.startswith("platform.admin."):
            continue
        group = entry.get("group") or key.split(".", 1)[0]
        groups.setdefault(group, []).append(_label_of(entry, language))
    if not groups:
        return _t(PERMISSION_SUMMARY_NONE, language)
    lines = [_t(PERMISSION_SUMMARY_HEADER, language)]
    shown = list(groups.items())[:8]
    for group, labels in shown:
        lines.append(f"• {_group_label(group, language)}: {', '.join(labels)}")
    rest = list(groups)[8:]
    if rest:
        lines.append(("…และอีก {n} หมวด: " if language != "en" else "…and {n} more: ").format(n=len(rest))
                     + " · ".join(_group_label(g, language) for g in rest))
    lines.append(_t(CAPABILITY_FOLLOW_UP, language))
    return "\n".join(lines)


async def _maybe_capability_question(
    client: DataClient, *, ctx: ResolvedContext, message: str, permission_keys: list[str], language: str,
) -> ChatReply | None:
    """Issue 1: detailed capability questions, answered from permissions
    and the registered commands. The plain "ทำอะไรได้บ้าง" keeps going to
    the guide (help hook) — with the areas line appended there."""
    keys = _filter_by_oa(permission_keys, ctx.oa)
    if _matches_phrase(message, PERMISSION_ASK_PHRASES) or _matches_phrase(message, CAPABILITY_PHRASES):
        try:
            catalog = await client.permission_catalog()
        except Exception:  # noqa: BLE001
            log.exception("could not read the permission catalogue")
            catalog = []
        return ChatReply(text=permission_summary(keys, catalog, language), quick_reply_url=_guide_button(ctx.oa, language))
    group = _capability_group_asked(message)
    if group is None:
        return None
    try:
        catalog = await client.permission_catalog()
    except Exception:  # noqa: BLE001
        log.exception("could not read the permission catalogue")
        catalog = []
    return capability_detail(group, keys, catalog, language, ctx.oa)



# ============================================================ user review, batch 2 (4 Sep 2026)

def _phone_reply(problem: str, value: str, language: str) -> ChatReply:
    return ChatReply(text=_t(PHONE_INVALID[problem], language).format(value=value))


_BULK_PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{7,}\d")
_BULK_EMAIL_RE = re.compile(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+")


_BULK_SEPARATOR_RE = re.compile(r"\s*,\s*|\s+และ\s+|\s+กับ\s+|\s+&\s+|\s+and\s+|\s*[;；]\s*|\s*\|\s*")


_NAME_LINE_VERBS = (
    "ตั้ง", "แก้", "เปลี่ยน", "ลบ", "เพิ่ม", "สร้าง", "บันทึก", "จด", "โน้ต", "เตือน", "นัด", "มอบหมาย", "อัปเดต", "ปิด", "เปิด",
    "ยกเลิก", "เช็ค", "ดู", "ขอ", "ค้นหา", "หา", "รับ", "ส่ง", "ออก", "ลง", "โทร", "update", "set", "change", "add", "create", "note",
    "remind", "assign", "delete", "call", "check",
)


def _looks_like_name_and_phone(part: str) -> bool:
    if not _BULK_PHONE_RE.search(part) or re.search(r"(?<![A-Za-z0-9])(?:SR|[CDQT])-\d{4}-\d{4}", part, re.I):
        return False
    rest = _BULK_EMAIL_RE.sub(" ", _BULK_PHONE_RE.sub(" ", part))
    words = [w for w in re.split(r"[\s,]+", rest.replace("เบอร์", " ").replace("โทร", " ")) if w]
    if not 1 <= len(words) <= 4 or any(re.search(r"\d", w) for w in words):
        return False
    if any(words[0].lower().startswith(v) for v in _NAME_LINE_VERBS):
        return False
    return not _is_closed_request(part, "sales")


# "1." "2)" "-" "•" "๓." in front of a pasted line. People number a list
# before they paste it; without this the number joined the first name and
# the whole line stopped looking like "name … phone" (owner, 9 Sep 2026).
_BULK_LIST_MARKER_RE = re.compile(r"^\s*(?:[-–—•*]+|[(\[]?[0-9๐-๙]{1,3}[.)\]])\s*(?=[^\s0-9๐-๙])")


def _strip_list_marker(line: str) -> str:
    return _BULK_LIST_MARKER_RE.sub("", line.strip(), count=1).strip()


def _bulk_customer_entries(message: str, *, allow_untriggered: bool = False) -> list[dict] | None:
    """Two or more customers in one message, or None. Each entry is the
    words of a line: an email token, a phone-looking token, the rest is the
    name (first word = first name, remainder = last name).

    Owner, 7 Sep 2026: also lines pasted WITHOUT the trigger word when
    every line reads "name … phone" (allow_untriggered), and one line
    with people separated by commas / "และ" / ";" — or simply by their
    phone numbers."""
    text = (message or "").strip()
    lowered = text.lower()
    trigger = next((t for t in BULK_CUSTOMER_TRIGGERS if lowered.startswith(t)), None)
    if trigger is None and not allow_untriggered:
        return None
    body = text[len(trigger):].strip(" :：\n") if trigger else text
    parts = [_strip_list_marker(p) for p in re.split(r"[\n;；]+", body) if p.strip()]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        phones = list(_BULK_PHONE_RE.finditer(body))
        if len(phones) >= 2:
            pieces = [p.strip() for p in _BULK_SEPARATOR_RE.split(body) if p and p.strip()]
            if len(pieces) < 2:
                # "สมชาย 0812345678 สมหญิง 0898765432": each phone ends a person.
                pieces, last = [], 0
                for m in phones:
                    pieces.append(body[last:m.end()].strip(" ,"))
                    last = m.end()
                if body[last:].strip(" ,"):
                    pieces.append(body[last:].strip(" ,"))
            parts = [p for p in pieces if p]
    if len(parts) < 2:
        return None
    if trigger is None:
        # Nothing announced a list: every line must read "name … phone" —
        # a phone, one to four name words, nothing that starts a command.
        for part in parts:
            if not _looks_like_name_and_phone(part):
                return None
    entries = []
    for part in parts:
        tokens = part.replace(",", " ").split()
        email = next((tok for tok in tokens if _BULK_EMAIL_RE.fullmatch(tok)), None)
        phone_match = _BULK_PHONE_RE.search(part)
        phone_tokens = set(phone_match.group(0).split()) if phone_match else set()
        name_tokens = [tok for tok in tokens if tok != email and tok not in phone_tokens and not re.fullmatch(r"[\d\-+().]+", tok)]
        entries.append({
            "first_name": name_tokens[0] if name_tokens else None,
            "last_name": " ".join(name_tokens[1:]) or None,
            "phone": phone_match.group(0).strip() if phone_match else None,
            "email": email, "raw": part,
        })
    return entries


async def _handle_bulk_customer_add(
    client: DataClient, *, ctx: ResolvedContext, license_id, entries: list[dict],
    permission_keys: list[str], language: str,
) -> ChatReply:
    """User review (4 Sep 2026): several leads in one message. Each row is
    its own outcome — created, skipped as a duplicate (with the existing
    code), or refused with the reason — never a half-done batch."""
    from .phone import phone_problem

    # The permission key AND the channel. Holding the key is not enough:
    # OA_ALLOWED_PERMISSION_KEYS says a technician's LINE offers no
    # customer capability at all, and the owner of a shop holds every key
    # on every channel — so an owner pasting a list into the TECHNICIAN OA
    # created real customer rows there (measured 10 ก.ย. 2569). Every other
    # write on the AI road passes _oa_allows; this one and the phone
    # resolver below were the two that did not.
    if "customer.create" not in set(permission_keys) or not _oa_allows(ctx.oa, "customer.create"):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    license_id = str(license_id)
    saved: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    asked: list[dict] = []
    last_row = None
    owner_id = await _member_id_of(client, license_id, ctx)
    for line_no, entry in enumerate(entries, start=1):
        label = _bulk_label(entry, language)
        if not entry.get("first_name"):
            failed.append(f"{entry['raw'][:30]} ({'ไม่มีชื่อ' if language != 'en' else 'no name'})")
            continue
        if not entry.get("phone"):
            # Owner, 7 Sep 2026: a row without a phone is asked for, one
            # at a time, after the complete rows are saved — not failed.
            asked.append({**entry, "line_no": line_no})
            continue
        outcome, row = await _bulk_create_one(client, license_id, ctx, entry, owner_id, language)
        if outcome == "saved":
            saved.append(f"{label} ({row.get('customer_id', '')})")
            last_row = row
        elif outcome == "skipped":
            skipped.append(f"{label} → {row.get('existing_code', '')}")
        else:
            failed.append(str(row.get("reason") or label))
    if last_row is not None:
        await _remember_customer(client, ctx, last_row)
    if asked:
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="create", entity="bulk_customer_phone",
                fields={"rows": asked, "saved": saved, "skipped": skipped, "failed": failed, "owner_id": owner_id},
                missing=["phone"], ttl_seconds=PENDING_INTENT_TTL_S,
            )
        except Exception:
            log.exception("could not hold the bulk rows that need a phone")
        text = _bulk_summary(saved, skipped, failed, language, asked=len(asked))
        return ChatReply(
            text=text + "\n" + _bulk_ask_phone(asked[0], language),
            quick_replies=[("ข้าม", "ข้าม"), ("ยกเลิกที่เหลือ", "ยกเลิก")],
        )
    return ChatReply(
        text=_bulk_summary(saved, skipped, failed, language),
        quick_replies=[("รายชื่อลูกค้า", "รายชื่อลูกค้า")],
    )


def _bulk_label(entry: dict, language: str) -> str:
    return " ".join(p for p in (entry.get("first_name"), entry.get("last_name")) if p) or str(entry.get("raw") or "")[:30]


async def _bulk_create_one(client: DataClient, license_id: str, ctx: ResolvedContext, entry: dict, owner_id, language: str) -> tuple[str, dict]:
    """("saved", row) | ("skipped", {"existing_code"}) | ("failed", {"reason"})."""
    from .phone import phone_problem

    label = _bulk_label(entry, language)
    if phone_problem(entry.get("phone")):
        return "failed", {"reason": f"{label} ({_t(BULK_BAD_PHONE, language)})"}
    payload = {k: v for k, v in entry.items() if k in ("first_name", "last_name", "phone", "email") and v}
    if owner_id:
        payload = {**payload, "owner_member_id": owner_id}
    try:
        row = await client.create_customer(license_id, payload, actor_id=ctx.chann_uid)
    except Exception as exc:  # noqa: BLE001
        structured = getattr(exc, "structured", None) or {}
        if structured.get("error") == "duplicate":
            return "skipped", {"existing_code": structured.get("existing_code", "")}
        log.warning("bulk customer add failed for %s: %s", label, exc)
        return "failed", {"reason": label}
    return "saved", row


def _bulk_summary(saved: list[str], skipped: list[str], failed: list[str], language: str, asked: int = 0) -> str:
    text = _t(BULK_CUSTOMER_SUMMARY, language).format(
        saved=len(saved),
        skipped_line=_t(BULK_SKIPPED_LINE, language).format(n=len(skipped), items=", ".join(skipped)) if skipped else "",
        failed_line=_t(BULK_FAILED_LINE, language).format(n=len(failed), items=", ".join(failed)) if failed else "",
    )
    if saved:
        text += "\n" + ", ".join(saved)
    if asked:
        text += _t(BULK_ASKED_LINE, language).format(n=asked)
    return text


def _bulk_ask_phone(entry: dict, language: str) -> str:
    return _t(BULK_ASK_PHONE, language).format(line=entry.get("line_no", "?"), name=_bulk_label(entry, language))


BULK_ASKED_LINE = {"th": "\nยังไม่มีเบอร์ {n} ราย — ถามทีละราย:", "en": "\n{n} without a phone — asking one at a time:"}
BULK_ASK_PHONE = {
    "th": "บรรทัด {line} ({name}) ยังไม่มีเบอร์ — พิมพ์เบอร์ หรือ \"ข้าม\"",
    "en": "Line {line} ({name}) has no phone — type it, or \"skip\"",
}
BULK_SKIPPED_BY_YOU = {"th": "\nข้ามตามที่บอก {n} ราย: {items}", "en": "\nSkipped at your word ({n}): {items}"}
BULK_ROWS_CANCELLED = {"th": "ยกเลิกรายที่เหลือแล้วครับ", "en": "The remaining rows were dropped."}
_BULK_SKIP_WORDS = frozenset({"ข้าม", "skip", "ไม่มี", "ไม่มีเบอร์", "-", "ไม่รู้", "ไม่ทราบ", "none", "no phone"})


async def _resolve_bulk_customer_phone(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply | None:
    """The answer to "บรรทัด 2 (สมหญิง) ยังไม่มีเบอร์": a phone, "ข้าม", or
    "ยกเลิก". Anything else is not an answer — the rows are dropped and
    None lets the message be what it is."""
    if not _oa_allows(ctx.oa, "customer.create"):
        # Same gate as _handle_bulk_customer_add: the pending row set was
        # stored on one channel and must not be finished on another.
        return None
    fields = dict(pending.get("fields") or {})
    rows = list(fields.get("rows") or [])
    saved = list(fields.get("saved") or [])
    skipped = list(fields.get("skipped") or [])
    failed = list(fields.get("failed") or [])
    dropped = list(fields.get("dropped") or [])
    text = (message or "").strip()
    norm = _normalise(text)
    if not rows:
        await _drop_pending_quietly(client, ctx)
        return None
    if norm in _SLOT_FILL_ABORT_WORDS:
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(BULK_ROWS_CANCELLED, language) + "\n" + _bulk_summary(saved, skipped, failed, language))
    current, rest = rows[0], rows[1:]
    if norm in _BULK_SKIP_WORDS or text in _BULK_SKIP_WORDS:
        dropped.append(_bulk_label(current, language))
    elif _looks_like_phone(text) or re.fullmatch(r"\+?[\d\-\s().]{8,16}", text):
        if "customer.create" not in set(permission_keys):
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        outcome, row = await _bulk_create_one(
            client, str(license_id), ctx, {**current, "phone": re.sub(r"[\s-]", "", text)}, fields.get("owner_id"), language,
        )
        label = _bulk_label(current, language)
        if outcome == "saved":
            saved.append(f"{label} ({row.get('customer_id', '')})")
            await _remember_customer(client, ctx, row)
        elif outcome == "skipped":
            skipped.append(f"{label} → {row.get('existing_code', '')}")
        else:
            # A bad number: the same row is asked again.
            return ChatReply(
                text=str(row.get("reason") or "") + "\n" + _bulk_ask_phone(current, language),
                quick_replies=[("ข้าม", "ข้าม"), ("ยกเลิกที่เหลือ", "ยกเลิก")],
            )
    else:
        # Not a phone and not "skip": the person moved on.
        await _drop_pending_quietly(client, ctx)
        return None
    if rest:
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="create", entity="bulk_customer_phone",
                fields={**fields, "rows": rest, "saved": saved, "skipped": skipped, "failed": failed, "dropped": dropped},
                missing=["phone"], ttl_seconds=PENDING_INTENT_TTL_S,
            )
        except Exception:
            log.exception("could not hold the remaining bulk rows")
        return ChatReply(text=_bulk_ask_phone(rest[0], language), quick_replies=[("ข้าม", "ข้าม"), ("ยกเลิกที่เหลือ", "ยกเลิก")])
    await _drop_pending_quietly(client, ctx)
    summary = _bulk_summary(saved, skipped, failed, language)
    if dropped:
        summary += _t(BULK_SKIPPED_BY_YOU, language).format(n=len(dropped), items=", ".join(dropped))
    return ChatReply(
        text=summary,
        quick_replies=[("รายชื่อลูกค้า", "รายชื่อลูกค้า")],
    )


# ------------------------------------------------- AI-designed templates
#
# Owner, 9 Sep 2026: "ตอนนี้รองรับให้ผู้ใช้พิมพ์ในแชทเพื่อให้ AI ช่วยออกแบบให้
# ในแชทแล้วใช่ไหม สำหรับ Sale OA กับคนที่มีสิทธิ์". It did not — a shop could
# upload a .docx or paste HTML on the dashboard, and that was all.
#
# The Sales OA only, and `setting.manage` only, because that is the key every
# other template route already requires: a person who cannot upload a
# template must not be able to conjure one by asking nicely. The permission
# is checked when the flow starts AND again at publish, since a published
# template immediately becomes what every document of that type looks like
# and the two moments can be minutes apart.
#
# The drafting itself is `services/documents/design.py`; this is only the
# conversation around it.

TEMPLATE_DESIGN_TRIGGERS = (
    "ออกแบบใบเสนอราคา", "ทำแม่แบบใบเสนอราคา", "ทำเทมเพลตใบเสนอราคา",
    "ออกแบบฟอร์มใบเสนอราคา", "ออกแบบใบรายงานการซ่อม", "ออกแบบฟอร์มรายงานการซ่อม",
    "ทำแม่แบบใบรายงานการซ่อม", "ทำเทมเพลตรายงานการซ่อม", "ออกแบบแบบฟอร์มเอกสาร",
    "ออกแบบเอกสาร", "ออกแบบแบบฟอร์ม", "ออกแบบฟอร์ม", "ออกแบบเทมเพลต",
    "ทำแม่แบบเอกสาร", "ทำเทมเพลตเอกสาร", "ทำแม่แบบ", "ทำเทมเพลต", "ออกแบบ",
    "design a quotation template", "design a quote template",
    "design a service report template", "design a document template",
    "make a quotation template", "create a quotation template",
    "design a template", "make a template", "template design",
)

# The words that decide which of the two documents is meant. Only two,
# because they are the only two the engine has a snapshot builder for —
# a template for anything else would pass every check and then have
# nothing to fill it at issue time.
_TEMPLATE_QUOTE_WORDS = (
    "ใบเสนอราคา", "ใบเสนอ", "เสนอราคา", "quotation", "quote",
)
_TEMPLATE_REPORT_WORDS = (
    "รายงานการซ่อม", "รายงานซ่อม", "ใบรายงาน", "ใบแจ้งผลการบริการ",
    "รายงานบริการ", "ใบงานซ่อม", "service report", "repair report",
)

TEMPLATE_TYPE_WORDS = {"quote": _TEMPLATE_QUOTE_WORDS, "service_report": _TEMPLATE_REPORT_WORDS}

# The draft lives long enough for someone to open the preview, look at it
# properly, and come back. The ten minutes the slot-filling flows use is
# not long enough to read a document.
TEMPLATE_DESIGN_TTL_S = 1800

_TEMPLATE_YES_WORDS = frozenset({
    "ใช่เลย", "ใช้เลย", "ใช้อันนี้", "เอาอันนี้", "ตกลง", "เผยแพร่", "ใช้แบบนี้",
    "publish", "use it", "use this", "yes",
})
_TEMPLATE_REFINE_WORDS = frozenset({
    "แก้เพิ่ม", "แก้อีก", "ขอแก้", "แก้ไข", "ปรับเพิ่ม", "แก้หน่อย",
    "refine", "change it", "edit",
})
_TEMPLATE_DROP_WORDS = frozenset({
    "ทิ้ง", "ทิ้งเลย", "ไม่เอา", "ไม่ใช้", "ยกเลิก", "ลบทิ้ง",
    "discard", "drop it", "no",
})
# --- reading an answer that is about the draft ---------------------------
#
# Review v3, B08: the three sets above were matched for EQUALITY, so
# "ใช้เลยครับ" (the same word with a polite particle), "ยืนยันใช้แบบนี้"
# (the same word with the confirmation spelled out) and "ขอดูก่อน" (asking
# to look before deciding) all fell through to "this is a new subject",
# cleared the pending state and published nothing. The saved draft was
# still on the templates page, but the conversation could not continue it.
#
# So the decision below is made on CONTAINMENT for the phrases long enough
# to carry their own meaning, and equality is kept for the short English
# tokens where containment would fire on any sentence that happens to hold
# them ("no" inside "notebook"). Refusal is checked before agreement, so
# "ไม่ใช้แบบนี้" is a refusal and not a confirmation of "ใช้แบบนี้".
#
# Scope: this reads a reply to a question this flow just asked, with a
# draft on the table. It is not, and must not become, a general
# mutation-intent decision for chat.py — that belongs to the router.
_TEMPLATE_DROP_CONTAINS = (
    "ไม่เอา", "ไม่ใช้", "ยกเลิก", "ลบทิ้ง", "ทิ้งเลย", "ทิ้ง",
    "discard", "drop it",
)
_TEMPLATE_YES_CONTAINS = (
    "ใช่เลย", "ใช้เลย", "ใช้อันนี้", "เอาอันนี้", "ใช้แบบนี้", "เอาแบบนี้",
    "ใช้ตัวนี้", "เอาตัวนี้", "ยืนยันใช้", "ตกลงใช้", "เผยแพร่",
    "publish", "use it", "use this",
)
# "Let me look first" — a decision deferred, not a decision made. The
# phrases are deliberately specific: a bare "ขอดู" is how people ask for
# every list in the system, and would steal messages that are not about
# this draft at all.
_TEMPLATE_LOOK_CONTAINS = (
    "ขอดูก่อน", "ดูก่อน", "ขอดูตัวอย่าง", "ดูตัวอย่าง", "ขอคิดดู", "คิดดูก่อน",
    "ขอเวลาคิด", "ยังไม่ตัดสินใจ", "let me look", "let me see", "preview",
)
_TEMPLATE_REFINE_CONTAINS = (
    "แก้เพิ่ม", "แก้อีก", "ขอแก้", "แก้ไข", "ปรับเพิ่ม", "แก้หน่อย", "refine",
    "change it",
)
# Plainly about the draft, without saying what to do with it. Asked back
# rather than thrown away.
#
# Every one of these POINTS at the draft rather than naming the subject:
# "แบบฟอร์มนี้", not "แบบฟอร์ม". The general noun is deliberately absent —
# "แบบฟอร์มมีกี่แบบ" is a question about the shop's templates, and reading
# it as an answer to "what shall I do with this draft?" would trap the
# person in the question they were trying to leave.
_TEMPLATE_SUBJECT_CONTAINS = (
    "ร่างนี้", "ร่างเมื่อกี้", "แบบฟอร์มนี้", "เทมเพลตนี้",
    "แบบนี้", "อันนี้", "ตัวนี้", "this draft", "this one",
)
# ...and only in a short reply. A deictic fragment IS the whole message
# when someone is answering a question; a sentence that merely contains
# "แบบนี้" on its way somewhere else is somewhere else.
_TEMPLATE_SUBJECT_MAX_CHARS = 24
_TEMPLATE_SUBJECT_EXACT = frozenset({
    "โอเค", "โอเคครับ", "โอเคค่ะ", "ได้", "ได้ครับ", "ได้ค่ะ", "ok", "okay",
    "อืม", "เอ่อ", "แล้วไง", "ยังไง", "ไง",
})


def _template_draft_decision(message: str) -> str:
    """What a reply with a draft on the table is asking for.

    One of: `drop`, `look`, `publish`, `refine`, `edit`, `unclear`, or
    `other` — and only `other` gives the message back to the router as a
    new subject.
    """
    text = (message or "").strip().lower()
    norm = _normalise(message)
    if not text:
        return "other"

    def says(phrases) -> bool:
        return any(phrase in text or phrase in norm for phrase in phrases)

    # Equality for the sets as they were, so nothing that worked before
    # stops working, then containment for the phrases.
    if norm in _TEMPLATE_DROP_WORDS or text in _TEMPLATE_DROP_WORDS:
        return "drop"
    if says(_TEMPLATE_DROP_CONTAINS):
        return "drop"
    if says(_TEMPLATE_LOOK_CONTAINS):
        return "look"
    if norm in _TEMPLATE_YES_WORDS or text in _TEMPLATE_YES_WORDS:
        return "publish"
    if says(_TEMPLATE_YES_CONTAINS):
        return "publish"
    if norm in _TEMPLATE_REFINE_WORDS or text in _TEMPLATE_REFINE_WORDS:
        return "refine"
    if says(_TEMPLATE_REFINE_CONTAINS):
        return "refine"
    if any(text.startswith(lead) for lead in _TEMPLATE_EDIT_LEAD):
        return "edit"
    if norm in _TEMPLATE_SUBJECT_EXACT or text in _TEMPLATE_SUBJECT_EXACT:
        return "unclear"
    if len(text) <= _TEMPLATE_SUBJECT_MAX_CHARS and says(_TEMPLATE_SUBJECT_CONTAINS):
        return "unclear"
    return "other"


# An edit said straight out, without pressing "แก้เพิ่ม" first. Recognised so
# the draft is not thrown away by someone who simply answered the question
# they were actually being asked.
_TEMPLATE_EDIT_LEAD = (
    "เพิ่ม", "ตัด", "ลบ", "เอาออก", "แก้", "เปลี่ยน", "ย้าย", "ใส่", "ทำให้",
    "ขอให้", "อยากให้", "ปรับ", "ขยาย", "ลด", "จัด",
    "add ", "remove ", "change ", "make ", "move ", "put ", "delete ",
)

TEMPLATE_ASK_TYPE = {
    "th": (
        "ได้เลยครับ จะให้ออกแบบเอกสารแบบไหนดี\n\n"
        "• ใบเสนอราคา\n"
        "• ใบรายงานการซ่อม\n\n"
        "ตอบชื่อเอกสารมาได้เลย และบอกด้วยก็ได้ว่าอยากได้แบบไหน "
        "เช่น \"ใบเสนอราคา เรียบ ๆ มีหัวร้านตัวใหญ่\""
    ),
    "en": (
        "Happy to. Which document should I design?\n\n"
        "• a quotation\n"
        "• a service report\n\n"
        "Say which one, and how you want it to look if you have a preference."
    ),
}
TEMPLATE_TYPE_LABEL = {
    "quote": {"th": "ใบเสนอราคา", "en": "quotation"},
    "service_report": {"th": "ใบรายงานการซ่อม", "en": "service report"},
}
TEMPLATE_DRAFT_READY = {
    "th": (
        "ร่าง{label}ให้แล้วครับ — ยังเป็นฉบับร่าง ยังไม่ได้เอาไปใช้จริง\n\n"
        "ชื่อแบบฟอร์ม: {name}\n"
        "{summary}\n"
        "{warning}"
        "ดูตัวอย่างที่กรอกข้อมูลจริงไว้แล้วได้จากปุ่มด้านล่าง\n"
        "ไฟล์ Word สำหรับแก้เองต่อ: {docx}\n\n"
        "พอใจแล้วกด \"ใช้เลย\" · อยากแก้กด \"แก้เพิ่ม\" · ไม่เอากด \"ทิ้ง\""
    ),
    "en": (
        "Here is a draft {label} — still a draft, not in use yet.\n\n"
        "Template name: {name}\n"
        "{summary}\n"
        "{warning}"
        "Tap below to see it filled with real sample data.\n"
        "Word file to edit yourself: {docx}\n\n"
        "\"ใช้เลย\" to publish · \"แก้เพิ่ม\" to change it · \"ทิ้ง\" to drop it"
    ),
}
TEMPLATE_DRAFT_SUMMARY = {
    "th": "ใส่ช่องข้อมูลให้ {n} ช่อง เช่น {examples}",
    "en": "{n} data fields, e.g. {examples}",
}
TEMPLATE_NO_PREVIEW_LINK = {
    "th": "(ยังไม่ได้ตั้งค่าลิงก์ของร้านนี้ ดูตัวอย่างได้ที่ หน้าจอ > แบบฟอร์มเอกสาร)",
    "en": "(no public link configured — open dashboard > document templates)",
}
TEMPLATE_UNKNOWN_FIELDS = {
    "th": (
        "⚠️ AI ใส่ช่องที่ระบบไม่มีข้อมูลให้: {names}\n"
        "ช่องพวกนี้จะพิมพ์ออกมาเป็นช่องว่างบนเอกสารจริง "
        "ถ้าไม่ต้องการ กด \"แก้เพิ่ม\" แล้วบอกให้ตัดออกได้ครับ\n"
    ),
    "en": (
        "⚠️ The AI used fields the system cannot fill: {names}\n"
        "They will print as blanks. Tap \"แก้เพิ่ม\" to have them removed.\n"
    ),
}
TEMPLATE_MISSING_ESSENTIALS = {
    "th": (
        "⚠️ แบบนี้ยังไม่มี: {names}\n"
        "เอกสารที่ขาดช่องพวกนี้อาจใช้อ้างอิงไม่ได้ กด \"แก้เพิ่ม\" เพื่อให้เติมให้ได้ครับ\n"
    ),
    "en": "⚠️ This design is missing: {names}\nTap \"แก้เพิ่ม\" to have them added.\n",
}
TEMPLATE_REJECTED = {
    "th": (
        "ร่างที่ AI ส่งมามีสิ่งที่ระบบไม่อนุญาตให้เก็บไว้ในแบบฟอร์ม "
        "จึงไม่ได้บันทึกอะไรไว้เลยครับ\n\n"
        "สิ่งที่พบ:\n{reasons}\n\n"
        "แบบฟอร์มของร้านเก็บได้เฉพาะข้อความ ตาราง และการจัดหน้า "
        "เพราะแบบฟอร์มถูกนำไปสร้างเอกสารจริงบนเครื่องของระบบ\n"
        "ลองสั่งใหม่อีกครั้งได้ครับ เช่น \"ออกแบบ{label} เรียบ ๆ มีหัวร้านตัวใหญ่\""
    ),
    "en": (
        "The AI's draft contained things a template is not allowed to hold, "
        "so nothing was saved.\n\nFound:\n{reasons}\n\n"
        "A template may contain text, tables and layout only.\nTry asking again."
    ),
}
TEMPLATE_REFINE_ASK = {
    "th": (
        "ได้ครับ อยากแก้ตรงไหนบอกมาได้เลย "
        "เช่น \"เพิ่มช่องเลขที่ผู้เสียภาษี\" หรือ \"ตัดโลโก้ออก\" หรือ \"ทำหัวเรื่องให้ใหญ่ขึ้น\""
    ),
    "en": (
        "Sure — what should change? e.g. \"add the tax id field\", "
        "\"remove the logo\", \"make the heading bigger\"."
    ),
}
TEMPLATE_PUBLISHED = {
    "th": (
        "เผยแพร่แล้วครับ ✅\n\n"
        "ตั้งแต่นี้ไป {label} ทุกใบที่ออกจากระบบจะใช้แบบฟอร์ม \"{name}\" นี้\n"
        "อยากกลับไปใช้แบบเดิมหรือแก้เพิ่ม เปิด หน้าจอ > แบบฟอร์มเอกสาร ได้ทุกเมื่อ"
    ),
    "en": (
        "Published ✅\n\nEvery {label} the system issues from now on uses the "
        "template \"{name}\".\nChange it again from dashboard > document templates."
    ),
}
TEMPLATE_DISCARDED = {
    "th": (
        "ทิ้งร่างแล้วครับ ไม่ได้เอาไปใช้ — {label} ยังใช้แบบฟอร์มเดิมเหมือนเดิม\n"
        "อยากลองใหม่ พิมพ์ \"ออกแบบ{label}\" ได้ทุกเมื่อ"
    ),
    "en": (
        "Draft dropped — your {label} still uses the template it used before.\n"
        "Type \"ออกแบบ{label}\" to try again any time."
    ),
}
TEMPLATE_SAVE_FAILED = {
    "th": "บันทึกร่างแบบฟอร์มไม่สำเร็จครับ ยังไม่มีอะไรถูกเปลี่ยน ลองใหม่อีกครั้งได้เลย",
    "en": "The draft could not be saved. Nothing was changed — please try again.",
}
TEMPLATE_PUBLISH_FAILED = {
    "th": "เผยแพร่ไม่สำเร็จครับ ร่างยังอยู่ ลองกด \"ใช้เลย\" อีกครั้งได้",
    "en": "Publishing failed. The draft is still there — tap \"ใช้เลย\" again.",
}
TEMPLATE_LOOK_FIRST = {
    "th": (
        "ได้เลยครับ ดูให้เต็มที่ ร่าง{label} ยังอยู่ ยังไม่ได้เอาไปใช้จริง\n"
        "{docx}\n\n"
        "ดูเสร็จแล้ว พอใจกด \"ใช้เลย\" · อยากแก้กด \"แก้เพิ่ม\" · ไม่เอากด \"ทิ้ง\""
    ),
    "en": (
        "Of course — take your time. The draft {label} is still here and "
        "still not in use.\n{docx}\n\n"
        "When you have looked: \"ใช้เลย\" to publish · \"แก้เพิ่ม\" to change "
        "it · \"ทิ้ง\" to drop it"
    ),
}
TEMPLATE_WHICH_ONE = {
    "th": (
        "ร่าง{label} ยังรออยู่ครับ ยังไม่ได้เอาไปใช้จริง "
        "จะให้ทำอะไรกับร่างนี้ดี\n\n"
        "• \"ใช้เลย\" — เอาไปใช้กับ{label}ทุกใบตั้งแต่นี้ไป\n"
        "• \"แก้เพิ่ม\" — บอกได้ว่าอยากแก้ตรงไหน\n"
        "• \"ทิ้ง\" — ไม่เอาร่างนี้"
    ),
    "en": (
        "The draft {label} is still waiting and still not in use. What "
        "would you like to do with it?\n\n"
        "• \"ใช้เลย\" — use it for every {label} from now on\n"
        "• \"แก้เพิ่ม\" — tell me what to change\n"
        "• \"ทิ้ง\" — drop it"
    ),
}
TEMPLATE_DRAFT_GONE = {
    "th": "ร่างแบบฟอร์มหมดอายุแล้วครับ พิมพ์ \"ออกแบบใบเสนอราคา\" เพื่อเริ่มใหม่ได้เลย",
    "en": "That draft has expired. Type \"ออกแบบใบเสนอราคา\" to start again.",
}

_TEMPLATE_DECIDE_BUTTONS = [("ใช้เลย", "ใช้เลย"), ("แก้เพิ่ม", "แก้เพิ่ม"), ("ทิ้ง", "ทิ้ง")]


def _is_template_design_request(message: str) -> bool:
    """Does this sentence ask for a template to be designed?

    Substring rather than whole-message equality: people wrap the request
    in a sentence ("ช่วยออกแบบใบเสนอราคาให้หน่อยได้ไหม"), and none of these
    phrases appears anywhere else in this module's triggers.
    """
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(trigger.lower() in text for trigger in TEMPLATE_DESIGN_TRIGGERS)


def _template_type_from(message: str) -> str | None:
    """Which document the words name, or None when they do not say.

    Both named is the same as neither: the person is asked, rather than
    the first match winning silently.
    """
    text = (message or "").strip().lower()
    hits = [
        kind for kind, words in TEMPLATE_TYPE_WORDS.items()
        if any(word.lower() in text for word in words)
    ]
    return hits[0] if len(hits) == 1 else None


def _template_description(message: str) -> str:
    """What the person said, with the command words left in.

    Deliberately not stripped down to "the rest of the sentence": the
    trigger itself carries the request ("ออกแบบใบเสนอราคา" is the whole
    instruction most of the time), and cutting it leaves the model an
    empty brief.
    """
    return (message or "").strip()


def _template_name_for(document_type: str) -> str:
    return {
        "quote": "แบบฟอร์มใบเสนอราคา (ออกแบบด้วย AI)",
        "service_report": "แบบฟอร์มใบรายงานการซ่อม (ออกแบบด้วย AI)",
    }[document_type]


def _template_principal(ctx: ResolvedContext, license_id, permission_keys: list[str]):
    """This person, as the template routes expect to be handed them.

    The routes are called rather than reimplemented — they own finding-or-
    creating the template row, storing the compiled HTML, versioning, and
    the publish state machine, and none of that should exist twice. They
    re-check `setting.manage` themselves against this object, which is the
    point: the check at publish time is theirs, not a memory of ours.

    `license_status` is "active" because the router already refused a
    suspended tenant several stages above; a message never reaches here
    otherwise.
    """
    from ..services.authorization import TenantPrincipal

    return TenantPrincipal(
        license_id=str(license_id),
        chann_uid=ctx.chann_uid,
        role="sales",
        is_owner=False,
        permission_keys=frozenset(permission_keys),
        audience="sales",
        license_status="active",
    )


async def _template_asset_link(store, *, path: str, content: bytes, content_type: str,
                               filename: str | None = None) -> str | None:
    """Store one file and return a link a phone can open, or None.

    None rather than a broken URL, for `dashboard_link`'s reason: a link
    that opens an error page is worse than no link at all. Both callers
    print an alternative when they get None.
    """
    from .assets import asset_link

    try:
        await store.put(key=path, content=content, content_type=content_type)
    except Exception:
        log.exception("could not store a designed-template asset at %s", path)
        return None
    return asset_link(path, content_type=content_type, filename=filename)


async def _template_draft_reply(
    client: DataClient, *, ctx: ResolvedContext, license_id, document_type: str,
    description: str, previous_html: str | None, template_id: str | None,
    permission_keys: list[str], language: str, ai_client=None,
) -> ChatReply:
    """Draft, check, store as a DRAFT version, and show it.

    The order matters and is the whole safety story: the model's markup is
    sanitised and its placeholders checked BEFORE anything is written, so
    a draft that fails leaves no template row, no version and no stored
    object behind — there is nothing to clean up because nothing was made.
    """
    from ..routers_phase2 import (
        DOCX_CONTENT_TYPE, TemplateUploadIn, preview_document_template,
        upload_document_template,
    )
    from .documents.design import (
        DOCUMENT_TYPE_LABELS, TemplateRejected, draft_template, missing_essentials,
    )
    from .documents.html_docx import html_to_docx
    from .storage.base import get_document_store

    label = _t(TEMPLATE_TYPE_LABEL[document_type], language)

    try:
        profile = await client.get_company_profile(str(license_id)) or {}
    except Exception:
        # A shop that has not filled in its profile still gets a template;
        # the header is placeholders either way.
        log.exception("could not read the company profile for a template design")
        profile = {}

    try:
        html, invented = await draft_template(
            document_type=document_type, description=description,
            company=profile, previous_html=previous_html, ai_client=ai_client,
        )
    except (AINotConfigured, AIUnavailable) as exc:
        # Point 5: the standard unavailable reply, and nothing half-made.
        # Nothing has been written at this point, so "nothing left behind"
        # is a property of the order above rather than a cleanup step.
        log.warning("template design unavailable: %s", exc)
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=unavailable_reply(language))
    except TemplateRejected as exc:
        log.warning("template draft rejected: %s", exc.reasons)
        await _drop_pending_quietly(client, ctx)
        return ChatReply(
            text=_t(TEMPLATE_REJECTED, language).format(
                reasons="\n".join(f"• {r}" for r in exc.reasons), label=label,
            ),
        )

    name = _template_name_for(document_type)
    principal = _template_principal(ctx, license_id, permission_keys)
    try:
        saved = await upload_document_template(
            str(license_id),
            TemplateUploadIn(
                template_name=name, html=html, document_type=document_type,
            ),
            principal,
            client,
        )
    except Exception:
        log.exception("storing a designed template failed")
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(TEMPLATE_SAVE_FAILED, language))

    template_id = str(saved["template_id"])
    version_id = str(saved["version_id"])

    # The preview route fills the stored template with a real sample
    # snapshot and moves the version draft -> previewed. Calling it means
    # what the person looks at is what the engine produces, not a second
    # rendering path that could disagree with it.
    preview_html = ""
    try:
        previewed = await preview_document_template(
            str(license_id), template_id, version_id, principal, client,
        )
        preview_html = str(previewed.get("html") or "")
    except Exception:
        log.exception("previewing a designed template failed")

    store = get_document_store()
    preview_url = None
    if preview_html:
        preview_url = await _template_asset_link(
            store,
            path=f"{license_id}/templates/{template_id}/{version_id}-preview.html",
            content=preview_html.encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )
    docx_url = await _template_asset_link(
        store,
        path=f"{license_id}/templates/{template_id}/{version_id}-design.docx",
        content=html_to_docx(html),
        content_type=DOCX_CONTENT_TYPE,
        filename=f"{document_type}-template.docx",
    )

    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="design", entity="template_design",
            fields={
                "template_id": template_id, "version_id": version_id,
                "document_type": document_type, "html": html,
                "description": description, "name": name,
                # Kept so a reply that is plainly about the draft — "ขอดูก่อน"
                # — can hand the same two links back without redrafting
                # anything (review v3, B08).
                "preview_url": preview_url or "", "docx_url": docx_url or "",
            },
            missing=[], ttl_seconds=TEMPLATE_DESIGN_TTL_S,
        )
    except Exception:
        log.exception("could not hold a designed template for confirmation")

    from .documents.fill import placeholders_in

    used = sorted(placeholders_in(html))
    examples = ", ".join(f"{{{{{p}}}}}" for p in used[:3])
    warning = ""
    if invented:
        warning += _t(TEMPLATE_UNKNOWN_FIELDS, language).format(
            names=", ".join(f"{{{{{p}}}}}" for p in invented),
        )
    lacking = missing_essentials(html, document_type)
    if lacking:
        warning += _t(TEMPLATE_MISSING_ESSENTIALS, language).format(
            names=", ".join(
                label_th for label_th in (
                    dict(_template_vocabulary(document_type)).get(name_, name_)
                    for name_ in lacking
                )
            ),
        )

    text = _t(TEMPLATE_DRAFT_READY, language).format(
        label=label, name=name,
        summary=_t(TEMPLATE_DRAFT_SUMMARY, language).format(n=len(used), examples=examples),
        warning=warning,
        docx=docx_url or _t(TEMPLATE_NO_PREVIEW_LINK, language),
    )
    return ChatReply(
        text=text,
        quick_replies=_TEMPLATE_DECIDE_BUTTONS,
        quick_reply_url=(
            (("ดูตัวอย่างเอกสาร" if language != "en" else "See the document"), preview_url)
            if preview_url else _dashboard_button("templates", language)
        ),
    )


def _template_vocabulary(document_type: str):
    from .documents.design import vocabulary_for

    return vocabulary_for(document_type)


async def _handle_template_design(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, ai_client=None,
) -> ChatReply:
    """"ออกแบบใบเสนอราคา" — the first turn.

    The permission gate is first and is the ordinary refusal: someone
    without `setting.manage` gets exactly what they get for every other
    thing they cannot do, and the flow never starts, so no draft is made
    and no model call is spent.
    """
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    document_type = _template_type_from(message)
    if document_type is None:
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="design", entity="template_design_type",
                fields={"description": _template_description(message)},
                missing=["document_type"], ttl_seconds=TEMPLATE_DESIGN_TTL_S,
            )
        except Exception:
            log.exception("could not hold a template design question")
        return ChatReply(
            text=_t(TEMPLATE_ASK_TYPE, language),
            # The button sends the whole request, not just the answer: the
            # question expires after half an hour, and a tap that lands
            # after that should still start the right design rather than
            # be read as a bare noun the router has no use for.
            quick_replies=[
                ("ใบเสนอราคา", "ออกแบบใบเสนอราคา"),
                ("ใบรายงานการซ่อม", "ออกแบบใบรายงานการซ่อม"),
            ],
        )

    return await _template_draft_reply(
        client, ctx=ctx, license_id=license_id, document_type=document_type,
        description=_template_description(message), previous_html=None,
        template_id=None, permission_keys=permission_keys, language=language,
        ai_client=ai_client,
    )


async def _resolve_template_design(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str, pending: dict,
    permission_keys: list[str], language: str, ai_client=None,
) -> ChatReply | None:
    """The answer to a template question — which type, or what to do with
    a draft. `None` means "not an answer to this": the draft is dropped
    and the router carries on with the message as a fresh request.
    """
    entity = pending.get("entity")
    fields = pending.get("fields") or {}
    norm = _normalise(message)
    stripped = (message or "").strip().lower()

    # Which of the two documents. Any other sentence is a new request.
    if entity == "template_design_type":
        document_type = _template_type_from(message)
        if norm in _TEMPLATE_DROP_WORDS or stripped in _TEMPLATE_DROP_WORDS:
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(SLOT_FILL_CANCELLED, language))
        if document_type is None:
            await _drop_pending_quietly(client, ctx)
            return None
        if "setting.manage" not in set(permission_keys):
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        await _drop_pending_quietly(client, ctx)
        described = str(fields.get("description") or "")
        return await _template_draft_reply(
            client, ctx=ctx, license_id=license_id, document_type=document_type,
            description=f"{described}\n{message}".strip(), previous_html=None,
            template_id=None, permission_keys=permission_keys, language=language,
            ai_client=ai_client,
        )

    document_type = str(fields.get("document_type") or "quote")
    label = _t(TEMPLATE_TYPE_LABEL.get(document_type, TEMPLATE_TYPE_LABEL["quote"]), language)

    # "แก้เพิ่ม" was pressed last turn; this message is the instruction.
    if entity == "template_refine":
        if norm in _TEMPLATE_DROP_WORDS or stripped in _TEMPLATE_DROP_WORDS:
            return await _discard_template_draft(client, ctx=ctx, label=label, language=language)
        if "setting.manage" not in set(permission_keys):
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        return await _template_draft_reply(
            client, ctx=ctx, license_id=license_id, document_type=document_type,
            description=(message or "").strip(),
            previous_html=str(fields.get("html") or "") or None,
            template_id=str(fields.get("template_id") or "") or None,
            permission_keys=permission_keys, language=language, ai_client=ai_client,
        )

    # A draft is on the table: publish it, change it, look at it again,
    # say what was meant, or move on (review v3, B08 — see
    # `_template_draft_decision` for why this is no longer three exact
    # vocabularies).
    decision = _template_draft_decision(message)

    if decision == "drop":
        return await _discard_template_draft(client, ctx=ctx, label=label, language=language)

    if decision == "publish":
        # Publishing changes every document the shop issues from here on,
        # and the decision is made by CONTAINMENT over "เผยแพร่"/"ใช้เลย" —
        # so "ยังไม่ต้องเผยแพร่", "อย่าเพิ่งเผยแพร่", "เผยแพร่ยังไง" and
        # "ลูกค้าบอกว่าจะใช้แบบนี้" all published it (10 ก.ย. 2569). The
        # drop list only checks refusals spelled ไม่เอา/ไม่ใช้/ยกเลิก/ทิ้ง,
        # which "ไม่ต้องใช้" is not.
        held_publish = _intent_guard_reply(
            message, action="template_publish", language=language,
            triggers=tuple(_TEMPLATE_YES_CONTAINS) + tuple(_TEMPLATE_YES_WORDS),
        )
        if held_publish is not None:
            return held_publish
        return await _publish_template_draft(
            client, ctx=ctx, license_id=license_id, pending=pending,
            permission_keys=permission_keys, language=language,
        )

    if decision == "look":
        # "ขอดูก่อน" — a decision deferred, not made. The draft stays, the
        # clock is restarted so reading it does not cost the person their
        # place, and the same links come back.
        await _hold_template_draft(client, ctx=ctx, pending=pending)
        docx_url = str(fields.get("docx_url") or "")
        return ChatReply(
            text=_t(TEMPLATE_LOOK_FIRST, language).format(
                label=label,
                docx=(
                    f"ไฟล์ Word: {docx_url}" if docx_url and language != "en"
                    else (f"Word file: {docx_url}" if docx_url else "")
                ),
            ).strip(),
            quick_replies=_TEMPLATE_DECIDE_BUTTONS,
            quick_reply_url=_template_preview_button(fields, language),
        )

    if decision == "refine":
        try:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa, action="design", entity="template_refine",
                fields=fields, missing=["instruction"], ttl_seconds=TEMPLATE_DESIGN_TTL_S,
            )
        except Exception:
            log.exception("could not hold a template refine")
        return ChatReply(text=_t(TEMPLATE_REFINE_ASK, language))

    # An edit said outright ("เพิ่มช่องเลขที่ผู้เสียภาษี") without pressing the
    # button first. Treated as the edit it plainly is — throwing the draft
    # away here would punish someone for answering the question directly.
    if decision == "edit":
        if "setting.manage" not in set(permission_keys):
            await _drop_pending_quietly(client, ctx)
            return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
        return await _template_draft_reply(
            client, ctx=ctx, license_id=license_id, document_type=document_type,
            description=(message or "").strip(),
            previous_html=str(fields.get("html") or "") or None,
            template_id=str(fields.get("template_id") or "") or None,
            permission_keys=permission_keys, language=language, ai_client=ai_client,
        )

    if decision == "unclear":
        # Plainly about the draft, without saying what to do with it.
        # Asked back rather than acted on: publishing changes every
        # document the shop issues from now on, and "โอเค" is not consent
        # to that. The draft is kept so the answer can continue it.
        await _hold_template_draft(client, ctx=ctx, pending=pending)
        return ChatReply(
            text=_t(TEMPLATE_WHICH_ONE, language).format(label=label),
            quick_replies=_TEMPLATE_DECIDE_BUTTONS,
            quick_reply_url=_template_preview_button(fields, language),
        )

    # Anything else is a different request. The draft is left unpublished
    # (it is a DRAFT version on the templates page, not litter) and the
    # router goes on.
    await _drop_pending_quietly(client, ctx)
    return None


def _template_preview_button(fields: dict, language: str):
    """The "see the document" button, when the draft has a preview link."""
    preview_url = str(fields.get("preview_url") or "")
    if not preview_url:
        return _dashboard_button("templates", language)
    return (
        ("ดูตัวอย่างเอกสาร" if language != "en" else "See the document"),
        preview_url,
    )


async def _hold_template_draft(
    client: DataClient, *, ctx: ResolvedContext, pending: dict,
) -> None:
    """Keep the draft on the table, with its clock restarted.

    Re-setting rather than leaving it alone is deliberate: a person who
    goes away to read the preview and comes back must not find the draft
    expired because reading it took longer than what was left of the TTL.
    """
    try:
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa, action="design", entity="template_design",
            fields=pending.get("fields") or {}, missing=[],
            ttl_seconds=TEMPLATE_DESIGN_TTL_S,
        )
    except Exception:
        log.exception("could not keep a designed template on the table")


async def _discard_template_draft(
    client: DataClient, *, ctx: ResolvedContext, label: str, language: str,
) -> ChatReply:
    """"ทิ้ง" — forget the draft, publish nothing, change nothing.

    The version itself is left as a DRAFT rather than deleted: an unpublished
    version is invisible to document issuing, the templates page shows it,
    and 10.5's rule is that a published version is never mutated — deleting
    drafts would be a second, different lifecycle for no gain.
    """
    await _drop_pending_quietly(client, ctx)
    return ChatReply(text=_t(TEMPLATE_DISCARDED, language).format(label=label))


async def _publish_template_draft(
    client: DataClient, *, ctx: ResolvedContext, license_id, pending: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """"ใช้เลย" — the explicit act, and the only way a template goes live.

    `setting.manage` is checked here again rather than trusted from the
    turn that made the draft: publishing is what changes every document
    the shop issues, the two turns can be half an hour apart, and a role
    can be taken away in between. The publish route checks it a third
    time against the same principal.
    """
    from ..routers_phase2 import publish_document_template

    fields = pending.get("fields") or {}
    document_type = str(fields.get("document_type") or "quote")
    label = _t(TEMPLATE_TYPE_LABEL.get(document_type, TEMPLATE_TYPE_LABEL["quote"]), language)
    template_id = str(fields.get("template_id") or "")
    version_id = str(fields.get("version_id") or "")

    if "setting.manage" not in set(permission_keys):
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not template_id or not version_id:
        await _drop_pending_quietly(client, ctx)
        return ChatReply(text=_t(TEMPLATE_DRAFT_GONE, language))

    try:
        await publish_document_template(
            str(license_id), template_id, version_id,
            _template_principal(ctx, license_id, permission_keys), client,
        )
    except Exception:
        log.exception("publishing a designed template failed")
        return ChatReply(text=_t(TEMPLATE_PUBLISH_FAILED, language))

    await _drop_pending_quietly(client, ctx)
    return ChatReply(
        text=_t(TEMPLATE_PUBLISHED, language).format(
            label=label, name=str(fields.get("name") or ""),
        ),
        quick_reply_url=_dashboard_button("templates", language),
    )
