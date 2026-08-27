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

from ..data_client import DataClient
from .ai.client import AIUnavailable, AINotConfigured
from .ai.intent import parse_intent, unavailable_reply
from .identity import ResolvedContext, TenantResolution

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
    "add": "create",
    "new": "create",
    "edit": "update",
    "modify": "update",
    "remove": "delete",
}

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
    ("read", "note"): "note.read",
    ("create", "note"): "note.create",
    ("update", "note"): "note.update",
    ("read", "followup"): "followup.read",
    ("create", "followup"): "followup.create",
    ("update", "followup"): "followup.update",
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
    "invite technician",
    "technician invite code",
)

TECHNICIAN_INVITE_REPLY = {
    "th": "รหัสเชิญช่าง: {code}\nให้ช่างพิมพ์รหัสนี้ผ่านช่องทาง Technician เพื่อเข้าร่วมบริษัทนี้ (ใช้ได้ครั้งเดียว หมดอายุใน 7 วัน)",
    "en": "Technician invite code: {code}\nHave the technician type this code on the Technician OA to join this company (one-time use, expires in 7 days).",
}

TECHNICIAN_INVITE_DENIED = {
    "th": "การออกรหัสเชิญช่างต้องมีสิทธิ์จัดการสมาชิก",
    "en": "Issuing a technician invite code requires member-management permission",
}


def _is_technician_invite_request(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(trigger.lower() in text for trigger in TECHNICIAN_INVITE_TRIGGERS)


async def _handle_technician_invite_request(
    client: DataClient, *, ctx: ResolvedContext, permission_keys: list[str],
    language: str,
) -> ChatReply:
    if "member.manage" not in set(permission_keys):
        return ChatReply(text=_t(TECHNICIAN_INVITE_DENIED, language))
    invite = await client.create_invite(
        str(ctx.license_id),
        {"role": "technician", "max_uses": 1, "expires_in_days": 7},
        actor_id=ctx.chann_uid,
    )
    return ChatReply(
        text=_t(TECHNICIAN_INVITE_REPLY, language).format(code=invite["invite_code"]),
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
    (("ปิดไม่สำเร็จ", "ปิดดีลไม่สำเร็จ", "ไม่สำเร็จ", "lost", "lose"), "lost"),
    (("ปิดสำเร็จ", "ปิดดีลสำเร็จ", "สำเร็จ", "won", "win"), "won"),
    (("เสนอราคาแล้ว", "เสนอราคา", "proposed", "propose"), "proposed"),
)
# Checked separately from the table above: Thai naturally splits this one
# across the deal code ("เปิดดีล D-2026-0001 ใหม่"), so it needs both words
# present rather than one contiguous phrase. Safe as an AND-check because a
# deal code must already be present in the message for this function to be
# called at all — "เปิด"+"ใหม่" alone, without a deal code anywhere, means
# nothing here.
_REOPEN_WORDS = ("เปิด", "ใหม่")
_REOPEN_ENGLISH = ("reopen",)


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
    return None


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
) -> ChatReply:
    license_id = str(license_id)
    deals = await client.list_deals(license_id)
    match = next((d for d in deals if d["deal_id"].upper() == deal_code), None)
    if match is None:
        return ChatReply(text=_t(DEAL_STAGE_NOT_FOUND, language).format(deal_id=deal_code))

    allow_reopen = "deal.reopen" in set(permission_keys)
    if match["stage"] in ("won", "lost") and target_stage == "new" and not allow_reopen:
        return ChatReply(text=_t(DEAL_REOPEN_DENIED, language))

    try:
        row = await client.transition_deal_stage(
            license_id, match["id"], target_stage,
            allow_reopen=allow_reopen, actor_id=actor_id,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_conflict(exc):
            return ChatReply(text=_t(DEAL_STAGE_ILLEGAL, language).format(deal_id=deal_code))
        if _is_not_found(exc):
            return ChatReply(text=_t(DEAL_STAGE_NOT_FOUND, language).format(deal_id=deal_code))
        raise
    return ChatReply(
        text=_t(DEAL_STAGE_UPDATED, language).format(deal_id=row["deal_id"], stage=row["stage"]),
        entity_type="deal", entity_id=row["id"],
    )


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
    "th": "คุณเป็นสมาชิกหลายบริษัท: {names} — กรุณาเลือก",
    "en": "You belong to several companies: {names} — please choose one",
}

ASK_MISSING = {
    "th": "กรุณาระบุ{fields}",
    "en": "Please provide {fields}",
}

SUGGEST_HEADER = {
    "th": "คุณสามารถทำสิ่งเหล่านี้ได้:",
    "en": "Here is what you can do:",
}

# Two different reasons land here, and users need to hear the right one:
# not knowing a feature exists reads very differently from being denied it.
SUGGEST_NO_PERMISSION_LEAD = {
    "th": "คุณยังไม่มีสิทธิ์ทำสิ่งนี้ แต่คุณสามารถทำสิ่งเหล่านี้ได้:",
    "en": "You do not have permission for that, but here is what you can do:",
}
SUGGEST_UNKNOWN_FEATURE_LEAD = {
    "th": "ระบบยังไม่มีฟังก์ชันนี้ ตอนนี้คุณสามารถทำสิ่งเหล่านี้ได้:",
    "en": "That is not a feature yet — here is what you can do right now:",
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
    "th": "ขออภัย ไม่เข้าใจคำสั่ง ลองพิมพ์ว่า \"ทำอะไรได้บ้าง\"",
    "en": 'Sorry, I did not understand. Try typing "what can I do"',
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
    entity_type: str | None = None
    entity_id: str | None = None
    intent: dict | None = field(default=None, repr=False)


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
        if language == "en":
            return f"Hello {name} — connected to {company}"
        return f"สวัสดีคุณ{name} — เชื่อมต่อกับ {company} แล้ว"

    if ctx.resolution is TenantResolution.MULTIPLE:
        names = ", ".join(m.get("company_name", "") for m in ctx.memberships)
        return _t(REPLY_CHOOSE_TENANT, language).format(names=names)

    # Not registered: LINE display name is all we have.
    name = ctx.display_name or ctx.chann_uid
    if language == "en":
        return f"Hello {name} — {_t(REPLY_NOT_REGISTERED, 'en')}"
    return f"สวัสดีคุณ{name} — {_t(REPLY_NOT_REGISTERED, 'th')}"


def ask_for_missing(missing: list[str], language: str = "th") -> str:
    """Spec 6.4 — ask only for what is actually absent."""
    labels = ", ".join(str(m) for m in missing)
    return _t(ASK_MISSING, language).format(fields=labels)


def suggest_what_you_can_do(
    permission_keys,
    catalog: list[dict],
    language: str = "th",
    *,
    requested_action: str | None = None,
    requested_entity: str | None = None,
) -> str:
    """Spec 6.6/6.9 — list ONLY what this member actually holds.

    Built from the member's own permission set intersected with the catalogue,
    never from their role name: two tenants can both have a role called
    "sales" with entirely different permissions, so suggesting by role would
    offer people things they cannot do.

    requested_action/requested_entity are optional context from the intent
    that led here. They change two things: which lead-in sentence is used
    (not knowing a feature exists is a different message from being denied
    it), and which group is shown first — a flat 49-item alphabetical list is
    not an answer to "can I see the financial report", and a group the person
    never asked about does not belong ahead of the one they did.
    """
    held = set(permission_keys)
    if not held:
        return _t(SUGGEST_NOTHING, language)

    # Group first, in catalogue order, so the fallback (no request context)
    # still reads as organised rather than alphabetical-by-key.
    groups: dict[str, list[str]] = {}
    for entry in catalog:
        key = entry.get("key")
        if key not in held:
            continue
        if str(key).startswith("platform.admin."):
            continue
        label = (entry.get("label") or {}).get(language) or (
            entry.get("label") or {}
        ).get("th")
        group = entry.get("group") or "general"
        groups.setdefault(group, []).append(label or str(key))

    if not groups:
        return _t(SUGGEST_NOTHING, language)

    # Was the request understood as a real feature, and does this person hold
    # it? required_permission returning None means the system does not have
    # that capability at all — a different situation from holding the wrong
    # permission, and the two must not be worded the same way.
    needed = required_permission(requested_action or "", requested_entity)
    feature_is_known = needed is not None

    priority_group = None
    if needed:
        priority_group = needed.split(".", 1)[0] if "." in needed else "general"
    elif requested_entity:
        # Even for an unmapped entity, if its name happens to match a real
        # group (the model said "product" for something we do track), lead
        # with that rather than an arbitrary catalogue-order group.
        candidate = str(requested_entity).strip().lower()
        if candidate in groups:
            priority_group = candidate

    # An unmapped entity with nothing to key off of (the model invented a
    # word like "financial_report" that matches no real group) has no honest
    # way to pick which groups are relevant. Showing two arbitrary groups —
    # e.g. "approvals" and "billing" for a question about reports — repeats
    # the exact confusion this rewrite exists to fix. Keep it short instead
    # and point at the full list on request rather than guessing.
    if requested_entity and not feature_is_known and priority_group is None:
        return (
            _t(SUGGEST_UNKNOWN_FEATURE_LEAD, language)
            + ("\n" if language == "en" else "\n")
            + ('Type "what can I do" to see the full list.' if language == "en"
               else 'พิมพ์ "ทำอะไรได้บ้าง" เพื่อดูรายการทั้งหมด')
        )

    ordered_groups = list(groups.keys())
    if priority_group in groups:
        ordered_groups.remove(priority_group)
        ordered_groups.insert(0, priority_group)

    lines: list[str] = []
    other_groups_shown = 0
    for group in ordered_groups:
        is_priority = group == priority_group
        if not is_priority:
            if other_groups_shown >= SUGGEST_OTHER_GROUPS:
                continue
            other_groups_shown += 1
        items = groups[group]
        cap = SUGGEST_GROUP_LIMIT if is_priority else SUGGEST_LIMIT
        shown_items = items[:cap]
        lines.append(f"{_group_label(group, language)}:")
        lines.extend(f"  • {label}" for label in shown_items)
        if len(items) > len(shown_items):
            more = len(items) - len(shown_items)
            lines.append(f"  … +{more}" if language == "en" else f"  … และอีก {more} รายการ")

    remaining_groups = len(ordered_groups) - (1 if priority_group in groups else 0) - other_groups_shown
    if remaining_groups > 0:
        lines.append(
            f"… +{remaining_groups} more categories" if language == "en"
            else f"… และอีก {remaining_groups} หมวดหมู่"
        )

    if requested_entity and not feature_is_known:
        lead = _t(SUGGEST_UNKNOWN_FEATURE_LEAD, language)
    elif requested_entity:
        lead = _t(SUGGEST_NO_PERMISSION_LEAD, language)
    else:
        lead = _t(SUGGEST_HEADER, language)

    return lead + "\n" + "\n".join(lines)


# Phase 8 — the fields a profile edit is allowed to touch through chat.
# Mirrors data/chann_data/repositories/profile.py's EDITABLE_FIELDS; kept as
# a separate constant rather than imported, since the Application tier has
# no dependency on the Data tier's Python package (only its HTTP API).
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
CUSTOMER_AMBIGUOUS = {
    "th": "พบลูกค้าหลายคนที่ชื่อ {name}: {options} — กรุณาระบุเบอร์โทรด้วย",
    "en": "Several customers named {name} found: {options} — please include a phone number.",
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


def _display_name(row: dict) -> str:
    name = " ".join(p for p in (row.get("first_name"), row.get("last_name")) if p).strip()
    return name or row.get("phone") or row.get("email") or "(ไม่มีชื่อ)"


async def _find_one_customer_by_name(
    client: DataClient, license_id: str, name: str, language: str,
) -> tuple[dict | None, ChatReply | None]:
    """Name-based lookup, because a chat message names a customer by name,
    never by the internal id nobody but the system ever sees.

    Returns (row, None) on exactly one match, or (None, ChatReply) with a
    not-found/ambiguous reply the caller should return as-is otherwise —
    the same "ask to be more specific" shape registration.py's shop search
    already uses for the identical kind of ambiguity.
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
        options = ", ".join(
            f"{_display_name(m)} ({m.get('phone') or '-'})" for m in matches[:5]
        )
        return None, ChatReply(
            text=_t(CUSTOMER_AMBIGUOUS, language).format(name=name, options=options)
        )
    return matches[0], None


async def _handle_customer_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext,
    license_id, language: str,
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
        still_missing = [f for f in ("last_name", "phone") if not editable.get(f)]
        if still_missing:
            await client.set_pending_intent(
                ctx.chann_uid, ctx.oa,
                action="create", entity="customer", fields=editable,
                missing=still_missing, ttl_seconds=PENDING_INTENT_TTL_S,
            )
            return ChatReply(text=ask_for_missing(still_missing, language), intent=intent)
        try:
            row = await client.create_customer(license_id, editable, actor_id=ctx.chann_uid)
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return ChatReply(text=_t(CUSTOMER_NEEDS_SOMETHING, language), intent=intent)
            raise
        await _remember_customer(client, ctx, row)
        return ChatReply(
            text=_t(CUSTOMER_CREATED, language).format(name=f" {_display_name(row)} "),
            entity_type="customer", entity_id=row["id"], intent=intent,
        )

    if action in ("update", "promote"):
        target_name = fields.get("target_name")
        row, err = await _find_one_customer_by_name(client, license_id, target_name, language)
        if err is not None:
            return err
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
                entity_type="customer", entity_id=updated["id"], intent=intent,
            )
        editable = {
            k: v for k, v in fields.items()
            if k in ("first_name", "last_name", "phone", "email", "address", "notes")
            and v not in (None, "")
        }
        if not editable:
            return ChatReply(text=_t(CUSTOMER_NEEDS_SOMETHING, language), intent=intent)
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
            entity_type="customer", entity_id=updated["id"], intent=intent,
        )

    return ChatReply(text=_pending_execution_reply(intent, language), intent=intent)


async def _remember_customer(client: DataClient, ctx: ResolvedContext, row: dict) -> None:
    """Records "the customer we were just talking about", so a follow-up
    like "สร้างดีล" with no name at all can fall back to them instead of
    refusing. See cache.k_last_customer_ref for why this can't just reuse
    pending_intent."""
    await client.set_last_customer_ref(
        ctx.chann_uid, ctx.oa, customer_id=row["id"], name=_display_name(row),
        ttl_seconds=LAST_CUSTOMER_REF_TTL_S,
    )


LAST_CUSTOMER_REF_TTL_S = 600

DEAL_CREATED_FROM_CONTEXT = {
    "th": "สร้างดีล {deal_id} สำหรับ {name} (ลูกค้าที่เพิ่งคุยถึง) เรียบร้อยแล้ว",
    "en": "Created deal {deal_id} for {name} (the customer just mentioned).",
}


async def _handle_deal_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext,
    license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    action = intent.get("action")
    fields = intent.get("fields") or {}
    license_id = str(license_id)

    if action == "create":
        target_name = fields.get("target_name")
        used_context = False
        if not (target_name or "").strip():
            # No name at all — fall back to whoever was just discussed,
            # rather than refusing outright. A wrong guess here would be
            # worse than asking, so this only fires when a real reference
            # exists (see cache.k_last_customer_ref) and says so explicitly
            # in the reply rather than silently substituting.
            last_ref = await client.get_last_customer_ref(ctx.chann_uid, ctx.oa)
            if last_ref is None:
                return ChatReply(text=_t(DEAL_NEEDS_TARGET_NAME, language), intent=intent)
            contact = {"id": last_ref["customer_id"], "first_name": last_ref["name"]}
            used_context = True
        else:
            contact, err = await _find_one_customer_by_name(
                client, license_id, target_name, language,
            )
            if err is not None:
                return err
        try:
            row = await client.create_deal(
                license_id,
                {"contact_id": contact["id"], "notes": fields.get("notes")},
                actor_id=ctx.chann_uid,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return ChatReply(text=_t(CUSTOMER_NOT_FOUND, language).format(
                    name=_display_name(contact)
                ))
            raise
        template = DEAL_CREATED_FROM_CONTEXT if used_context else DEAL_CREATED
        return ChatReply(
            text=_t(template, language).format(
                deal_id=row["deal_id"], name=_display_name(contact),
            ),
            entity_type="deal", entity_id=row["id"], intent=intent,
        )

    return ChatReply(text=_pending_execution_reply(intent, language), intent=intent)


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
    license_id, language: str,
) -> ChatReply:
    """Phase 7 master data, made reachable from chat. ProductRepository.
    upsert (already idempotent on product_id since 7.5) means create and
    update are the same call — there is no meaningful difference between
    "add a product" and "add a product that happens to already exist" from
    the chat side."""
    action = intent.get("action")
    fields = intent.get("fields") or {}
    license_id = str(license_id)

    if action not in ("create", "update"):
        return ChatReply(text=_pending_execution_reply(intent, language), intent=intent)

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
    fields = intent.get("fields") or {}
    if not fields:
        return False
    wanted = set(pending.get("missing") or [])
    # Either it supplies something that was actually asked for, or it supplies
    # values without naming any entity at all — the shape of a bare answer.
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
STOREFRONT_SEARCH_TRIGGERS = ("ค้นหา", "search")
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


def _parse_storefront_query(message: str) -> str | None:
    text = (message or "").strip()
    lowered = text.lower()
    for trigger in STOREFRONT_SEARCH_TRIGGERS:
        if lowered.startswith(trigger.lower()):
            return text[len(trigger):].strip(" \t:：-—")
    return None


def _format_storefront_results(results: list[dict], language: str) -> str:
    lines = [_t(STOREFRONT_RESULTS_HEADER, language)]
    for i, r in enumerate(results, start=1):
        price = f" — {r['unit_price']}" if r.get("unit_price") is not None else ""
        lines.append(f"{i}. {r['product_name']} ({r['company_name']}){price}")
    return "\n".join(lines)


async def maybe_handle_storefront(
    client: DataClient, *, message: str, ctx: ResolvedContext, language: str,
) -> ChatReply | None:
    """Returns a reply if this message was storefront browsing (a search or
    a selection from one already in progress), else None so the caller
    proceeds with its normal tenant-scoped handling."""
    pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    if pending is not None and pending.get("entity") == "storefront":
        options = pending.get("fields", {}).get("options") or []
        text = (message or "").strip()
        if not text.isdigit() or not (1 <= int(text) <= len(options)):
            return ChatReply(
                text=_t(STOREFRONT_INVALID_SELECTION, language).format(n=len(options))
            )
        chosen = options[int(text) - 1]
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
        row = await client.storefront_record_interest(
            chann_uid=ctx.chann_uid, license_id=chosen["license_id"],
            product_name=chosen["product_name"],
        )
        return ChatReply(
            text=_t(STOREFRONT_INTEREST_RECORDED, language).format(
                product=chosen["product_name"], shop=chosen["company_name"],
            ),
            entity_type="customer", entity_id=row["id"],
        )

    query = _parse_storefront_query(message)
    if query is None:
        return None
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


async def handle_chat_message(
    client: DataClient,
    *,
    message: str,
    ctx: ResolvedContext,
    language: str = "th",
    ai_client=None,
) -> ChatReply:
    """Spec 6.4's slot-filling pattern, in the order the spec states."""
    if ctx.resolution is not TenantResolution.SINGLE:
        # No single tenant means no permission set and no place to write to.
        return ChatReply(text=greet(ctx, language))

    license_id = ctx.license_id
    member = ctx.memberships[0]

    context = await client.authorization_context(str(license_id), ctx.chann_uid)
    if context is None:
        return ChatReply(text=_t(REPLY_NOT_REGISTERED, language))
    permission_keys = list(context.get("permission_keys") or [])

    # Closed, trigger-matched flow (see registration.py's create-company /
    # invite-code paths for the same pattern) — checked before the AI
    # parser, and before the pending-intent load below, since it is
    # unrelated to any in-progress slot-filling.
    if ctx.oa == "sales" and _is_technician_invite_request(message):
        return await _handle_technician_invite_request(
            client, ctx=ctx, permission_keys=permission_keys, language=language,
        )

    # Same reasoning: deal stage transitions (9.6) are a closed, deterministic
    # pattern (a deal code plus a small set of stage keywords) — matched
    # directly rather than sent through the AI parser, and checked before
    # pending-intent since it is unrelated to any in-progress slot-filling.
    deal_stage_cmd = _parse_deal_stage_command(message) if ctx.oa == "sales" else None
    if deal_stage_cmd is not None:
        deal_code, target_stage = deal_stage_cmd
        if "deal.update" not in set(permission_keys):
            catalog = await client.permission_catalog()
            return ChatReply(text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language,
                requested_action="update", requested_entity="deal",
            ))
        return await _handle_deal_stage_command(
            client, license_id=license_id, deal_code=deal_code, target_stage=target_stage,
            permission_keys=permission_keys, language=language, actor_id=ctx.chann_uid,
        )

    # What the previous turn was still waiting for, if anything. Loaded before
    # parsing so the model can be told about it — a bare "0812345678" is not
    # parseable in isolation, only as the answer to a question that was asked.
    pending_intent = await client.get_pending_intent(ctx.chann_uid, ctx.oa)

    try:
        intent = await parse_intent(
            message=message,
            chann_uid=ctx.chann_uid,
            role=context.get("role", member.get("role", "")),
            license_id=str(license_id),
            permission_keys=permission_keys,
            language=language,
            client=ai_client,
            pending=pending_intent,
        )
    except AINotConfigured as exc:
        # A deploy problem, not an outage — log loudly, but the user still
        # gets the same plain apology rather than a configuration detail.
        log.error("AI not configured: %s", exc)
        return ChatReply(text=unavailable_reply(language))
    except AIUnavailable as exc:
        log.warning("AI unavailable: %s", exc)
        return ChatReply(text=unavailable_reply(language))

    if _is_continuation(pending_intent, intent):
        intent = _merge_pending(pending_intent, intent)

    # Missing fields come first: never refuse a request we did not understand.
    missing = intent.get("missing") or []
    if missing:
        # Remember what is still outstanding so the next message — which may
        # be nothing but the answer itself — can be understood as part of it.
        await client.set_pending_intent(
            ctx.chann_uid, ctx.oa,
            action=intent.get("action", ""),
            entity=intent.get("entity"),
            fields=intent.get("fields") or {},
            missing=missing,
            ttl_seconds=PENDING_INTENT_TTL_S,
        )
        return ChatReply(text=ask_for_missing(missing, language), intent=intent)

    # Nothing outstanding any more: whatever was open is either now complete
    # or has been abandoned for a new request. Either way it must not linger.
    if pending_intent is not None:
        await client.clear_pending_intent(ctx.chann_uid, ctx.oa)

    if intent.get("action") == "suggest":
        catalog = await client.permission_catalog()
        return ChatReply(
            text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language,
            ),
            intent=intent,
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
        catalog = await client.permission_catalog()
        return ChatReply(
            text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language,
                requested_action=req_action, requested_entity=req_entity,
            ),
            intent=intent,
        )

    # Domain execution. Phase 9 adds real customer/deal CRUD; everything
    # else still falls through to the stub below until its own phase lands.
    if intent.get("entity") == "customer":
        return await _handle_customer_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
        )
    if intent.get("entity") == "deal":
        return await _handle_deal_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language,
        )
    if intent.get("entity") == "product":
        return await _handle_product_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
        )

    # Domain execution arrives with the entities themselves (Phase 7+). Until
    # then the parse is echoed back rather than pretending work was done —
    # claiming "created" with nothing written would be a lie the user acts on.
    return ChatReply(
        text=_pending_execution_reply(intent, language),
        entity_type=intent.get("entity"),
        intent=intent,
    )


def _pending_execution_reply(intent: dict, language: str) -> str:
    action = intent.get("action") or "?"
    entity = intent.get("entity") or "?"
    if language == "en":
        return (
            f"Understood: {action} {entity}. "
            "This action is not available yet — it arrives with that module."
        )
    return (
        f"เข้าใจแล้ว: {action} {entity} "
        "— ฟังก์ชันนี้ยังไม่เปิดใช้งาน จะพร้อมเมื่อโมดูลนั้นเสร็จ"
    )


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

    reply = await handle_chat_message(
        client, message=reply_text, ctx=ctx, language=language, ai_client=ai_client
    )
    # The entity is decided by what was replied to, not by whatever the model
    # inferred from the reply text — "แก้ชื่อเป็นสมหญิง" names no entity at all.
    reply.entity_type = mapping["entity_type"]
    reply.entity_id = str(mapping["entity_id"])
    return reply
