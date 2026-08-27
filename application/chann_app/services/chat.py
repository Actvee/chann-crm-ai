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
