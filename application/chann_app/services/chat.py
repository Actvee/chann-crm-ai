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

    try:
        intent = await parse_intent(
            message=message,
            chann_uid=ctx.chann_uid,
            role=context.get("role", member.get("role", "")),
            license_id=str(license_id),
            permission_keys=permission_keys,
            language=language,
            client=ai_client,
        )
    except AINotConfigured as exc:
        # A deploy problem, not an outage — log loudly, but the user still
        # gets the same plain apology rather than a configuration detail.
        log.error("AI not configured: %s", exc)
        return ChatReply(text=unavailable_reply(language))
    except AIUnavailable as exc:
        log.warning("AI unavailable: %s", exc)
        return ChatReply(text=unavailable_reply(language))

    # Missing fields come first: never refuse a request we did not understand.
    missing = intent.get("missing") or []
    if missing:
        return ChatReply(text=ask_for_missing(missing, language), intent=intent)

    if intent.get("action") == "suggest":
        catalog = await client.permission_catalog()
        return ChatReply(
            text=suggest_what_you_can_do(permission_keys, catalog, language),
            intent=intent,
        )

    # The real permission gate. Checked here rather than trusted from the
    # model: asked for "รายงานทางการเงิน", the model happily returned
    # action=view entity=financial_report — an entity that does not exist and
    # that no permission key covers. Echoing "coming soon" at that would both
    # mislead the user and, once Phase 9 adds execution, skip the check
    # entirely for anything the model mislabels.
    req_action = intent.get("action", "")
    req_entity = intent.get("entity")
    needed = required_permission(req_action, req_entity)
    if needed is None or needed not in set(permission_keys):
        catalog = await client.permission_catalog()
        return ChatReply(
            text=suggest_what_you_can_do(
                permission_keys, catalog, language,
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
