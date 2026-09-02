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
from .ai.intent import parse_intent, unavailable_reply
from .identity import ResolvedContext, TenantResolution
from .registration import COMPANY_CODE_RE

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
    # A line ON a deal or quote, not the catalogue product. Editing one
    # changes a single record, so it needs the permission for that record
    # rather than product.manage — and without these entries the request
    # was reported as "not a feature yet" when it plainly is one.
    # The verbs the AI uses for field work. Without these the gate above
    # reads "no permission covers that" and answers "not a feature" — for
    # things the technician OA does all day.
    ("claim", "ticket"): "ticket.update",
    ("check_in", "service_report"): "service_report.create",
    ("check_out", "service_report"): "service_report.create",
    ("update", "line_item"): "deal.update",
    ("delete", "line_item"): "deal.update",
    ("create", "line_item"): "deal.update",
    ("read", "note"): "note.read",
    ("create", "note"): "note.create",
    ("update", "note"): "note.update",
    ("read", "followup"): "followup.read",
    ("create", "followup"): "followup.create",
    ("update", "followup"): "followup.update",
    # Cancelling is an update to the row's status, not its own permission —
    # same reasoning as ("promote", "customer") above.
    ("cancel", "followup"): "followup.update",
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

COMPANY_VIEW_PHRASES = ("ข้อมูลบริษัท", "ดูข้อมูลบริษัท", "company profile", "company info")

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
    compact = (message or "").strip().lower().replace(" ", "")
    if not compact:
        return False
    # Exact-ish match only: a longer sentence that merely contains the words
    # is more likely to be an edit command, which the parser above handles.
    return any(compact == p.replace(" ", "") for p in COMPANY_VIEW_PHRASES)


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
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(COMPANY_DENIED, language))
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

NOTE_TRIGGERS = ("บันทึกว่า", "จดว่า", "โน้ตว่า", "note")
NOTE_LIST_TRIGGERS = ("ดูบันทึก", "บันทึกของ", "ประวัติ")
REMINDER_TRIGGERS = ("เตือน", "นัด", "remind")
# Dispatched BEFORE reminder creation: every one of these contains "เตือน",
# so the create matcher would otherwise claim "ยกเลิกเตือน C-2026-0011" and
# answer "ไม่เข้าใจวันที่" — the same longer-first rule every Thai trigger
# collision in this project has ended up needing.
REMINDER_CANCEL_TRIGGERS = ("ยกเลิกเตือน", "ยกเลิกการเตือน", "ลบเตือน", "cancel reminder")
TODAY_WORK_PHRASES = ("งานวันนี้", "ที่ต้องทำวันนี้", "today")
UPCOMING_WORK_PHRASES = ("งานสัปดาห์นี้", "งานที่ค้าง", "ที่ต้องติดตาม", "upcoming")

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

CODE_PREFIX_TO_ENTITY = {"C": "customer", "D": "deal", "Q": "quote"}


def _find_entity_code(message: str) -> tuple[str, str] | None:
    """(entity_type, code) for the first record code in the message."""
    match = ENTITY_CODE_RE.search(message or "")
    if not match:
        return None
    code = match.group(1).upper()
    return CODE_PREFIX_TO_ENTITY[code[0]], code


async def _resolve_entity(client: DataClient, license_id: str, entity_type: str, code: str):
    """The row a code refers to, or None. Tenant-scoped by every underlying
    list call, so a code from another tenant simply does not resolve."""
    if entity_type == "customer":
        rows = await client.list_customers(license_id)
        return next((r for r in rows if str(r.get("customer_id", "")).upper() == code), None)
    if entity_type == "deal":
        rows = await client.list_deals(license_id)
        return next((r for r in rows if str(r.get("deal_id", "")).upper() == code), None)
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

    last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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
            text=_t(NOT_FOUND_BY_CODE, language).format(what=exc.entity_type, code=exc.code)
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
            text=_t(NOT_FOUND_BY_CODE, language).format(what=exc.entity_type, code=exc.code)
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


async def _handle_note_list(
    client: DataClient, *, license_id, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    if "note.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    found = _find_entity_code(message)
    if found is None:
        return ChatReply(text=_t(NOTE_NEEDS_TARGET, language))
    entity_type, code = found

    license_id = str(license_id)
    try:
        row = await _resolve_entity(client, license_id, entity_type, code)
        if row is None:
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what=entity_type, code=code)
            )
        notes = await client.list_notes(license_id, entity_type, str(row["id"]))
    except Exception:
        log.exception("note list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not notes:
        return ChatReply(text=_t(NOTE_EMPTY, language).format(code=code))

    lines = [f"บันทึกของ {code}:"]
    for note in notes[:LIST_LIMIT]:
        stamp = str(note.get("created_at") or "")[:10]
        lines.append(f"· {stamp} {note.get('body') or ''}")
    return ChatReply(text="\n".join(lines))


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
    "th": "ยังไม่มีนัดหมายที่ค้างอยู่",
    "en": "Nothing scheduled.",
}
REMINDER_LIST_HEAD = {
    "th": "นัดหมายที่ค้างอยู่ {count} รายการ",
    "en": "{count} upcoming",
}


async def _handle_reminder_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """What is coming up, soonest first."""
    if "followup.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    try:
        rows = await client.list_follow_ups(str(license_id), status="pending")
    except Exception:
        log.exception("could not list follow-ups")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    # Mine first, then everyone's — a salesperson opening this wants their
    # own day, not the shop's whole diary.
    mine = [r for r in rows if str(r.get("owner_chann_uid") or "") == ctx.chann_uid]
    shown = (mine or rows)
    shown = sorted(shown, key=lambda r: str(r.get("due_date") or "9999-12-31"))

    if not shown:
        return ChatReply(text=_t(REMINDER_LIST_EMPTY, language))

    # With the digest silent about overdue work (owner decision — see
    # reminders.py), this list is the ONE place a slipped or misfiled row
    # can be seen, and ยกเลิกเตือน needs the code printed here to act on.
    # The rows carry only entity_id, so name each the way the digest does;
    # raw ISO dates and code-less lines made the list unusable for exactly
    # the cleanup it exists to serve.
    from .thai_datetime import format_thai_date as _fmt_date

    today = datetime.now(BANGKOK_TZ).date()
    lines = []
    for row in shown[:LIST_LIMIT]:
        raw = str(row.get("due_date") or "")
        try:
            due_on = date.fromisoformat(raw)
            when = _fmt_date(due_on) if language == "th" else raw
            if language == "th" and due_on < today:
                when += " (เลยกำหนด)"
        except ValueError:
            when = raw
        if row.get("due_time"):
            when = f"{when} {str(row['due_time'])[:5]}"
        who = await _describe_entity_by_id(
            client, str(license_id), str(row.get("entity_type") or ""),
            str(row.get("entity_id") or ""),
        )
        what = str(row.get("notes") or "").strip()
        lines.append(f"· {when} · {who}{f' — {what}' if what else ''}")

    return ChatReply(
        text=_t(REMINDER_LIST_HEAD, language).format(count=len(shown))
        + "\n" + "\n".join(lines)
    )


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
    has_code = re.search(r"\b[CDQT]-\d{4}-\d{4}\b", message or "", re.I) is not None
    return has_code and any(t in lowered for t in REMINDER_TRIGGERS)


def _is_reminder_cancel_command(message: str) -> bool:
    """A cancel instruction, told apart from setting a reminder.

    Checked before _is_reminder_command on purpose: "ยกเลิกเตือน
    C-2026-0011" starts with a cancel verb but also contains "เตือน" and a
    record code, which is exactly what the create matcher claims.
    """
    lowered = (message or "").strip().lower()
    if not lowered:
        return False
    if any(lowered.startswith(t) for t in REMINDER_CANCEL_TRIGGERS):
        return True
    has_code = re.search(r"\b[CDQT]-\d{4}-\d{4}\b", message or "", re.I) is not None
    return has_code and any(t in lowered for t in REMINDER_CANCEL_TRIGGERS)


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
            text=_t(NOT_FOUND_BY_CODE, language).format(what=exc.entity_type, code=exc.code)
        )
    if target is None:
        return ChatReply(text=_t(REMINDER_CANCEL_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

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


async def _handle_reminder_create(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    permission_keys: list[str], language: str, actor_id: str,
) -> ChatReply:
    from .thai_datetime import format_thai_date, format_thai_time, parse_thai_date, parse_thai_time

    if "followup.create" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        target = await _resolve_target_or_context(client, ctx, license_id, message)
    except _TargetNotFound as exc:
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(what=exc.entity_type, code=exc.code)
        )
    if target is None:
        return ChatReply(text=_t(REMINDER_NEEDS_TARGET, language))
    entity_type, entity_id, code = target

    # Parse against the tenant's own day, not UTC: at 23:00 in Bangkok, UTC
    # is still yesterday, and "พรุ่งนี้" would land on today.
    today = datetime.now(BANGKOK_TZ).date()
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

    if "followup.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        due = await client.due_follow_ups(str(license_id), days=days)
    except Exception:
        log.exception("due follow-ups failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not due:
        return ChatReply(text=_t(WORK_EMPTY, language), quick_replies=[("รายการดีล", "รายการดีล")])

    # Same owner policy as the morning digest (see reminders.py): "งานวันนี้"
    # names only work that has not passed. due_follow_ups includes overdue
    # rows on purpose so they are never lost — they stay visible in
    # รายการเตือน, flagged เลยกำหนด, which is where cancelling lives too.
    today_local = datetime.now(BANGKOK_TZ).date()

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
    return ChatReply(text="\n".join(lines))


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
    language: str,
) -> ChatReply:
    """A customer registering the thing they bought."""
    match = SERIAL_RE.search(message or "")
    if not match:
        return ChatReply(text=_t(WARRANTY_NEEDS_SERIAL, language))
    serial = match.group(1).upper()

    license_id = str(license_id)
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

    try:
        row = await client.register_warranty(
            license_id,
            {
                "serial_number": serial,
                "customer_chann_uid": ctx.chann_uid,
                "product_id": str(product["id"]) if product else None,
                "product_name": (product or {}).get("product_name"),
            },
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        # Already registered is a normal thing to hit — someone checking
        # rather than registering — so it reads as information, not error.
        return ChatReply(text=str(exc.detail))
    except Exception:
        log.exception("warranty registration failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(WARRANTY_REGISTERED, language).format(
            number=row.get("warranty_number"),
            product=row.get("product_name") or serial,
            end=row.get("warranty_end"),
        ),
        entity_type="warranty", entity_id=str(row.get("id") or ""),
        quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม")],
    )


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
                    number=row.get("warranty_number"),
                    product=row.get("product_name") or serial,
                    status=row.get("status"),
                    end=row.get("warranty_end"),
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
    "สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "hello", "hi", "สอบถาม", "ขอสอบถาม",
)

CUSTOMER_JOB_STATUS = {
    "th": "งาน {code} ของคุณ: {status}\nนัด: {when}\nช่าง: {tech}",
    "en": "Your job {code}: {status}. Scheduled: {when}. Technician: {tech}.",
}
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
    "ไหม", "มั้ย", "กี่โมง", "เมื่อไหร่", "เมื่อไร", "เท่าไหร่", "เท่าไร", "ยังไง",
    "อย่างไร", "ทำไม", "ที่ไหน", "ใคร", "หรือเปล่า", "หรือยัง", "รึเปล่า", "รึยัง", "?",
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
        and SERIAL_RE.fullmatch(token) is not None
    )


def _looks_like_a_question(text: str) -> bool:
    """Is this asking something rather than reporting something?

    Thai questions end in a particle or contain a question word; a fault
    report is a statement. Not perfect — "แอร์เสียไหม" is a question that
    is also about a fault — but a question about a fault is still not a
    request to open a second job.
    """
    lowered = (text or "").strip().lower()
    return any(marker in lowered for marker in _QUESTION_MARKERS)


def _is_only_a_greeting(text: str) -> bool:
    """True when the message is a greeting and nothing else.

    Length matters as much as the words. "สวัสดีครับ" is someone saying
    hello; "สวัสดีครับ แอร์เสียครับ" is someone being polite before
    reporting a fault, and treating the second as a greeting would discard
    the reason they wrote.
    """
    stripped = (text or "").strip().lower().rstrip("!?. ")
    if not stripped:
        return True
    for greeting in GREETING_PHRASES:
        if stripped.startswith(greeting):
            # Whatever follows the greeting, minus the usual polite
            # particles, is the real message — if anything is left, it is
            # not just a greeting.
            rest = stripped[len(greeting):].strip()
            for particle in ("ครับ", "ค่ะ", "คะ", "จ้า", "ครับผม", "there"):
                rest = rest.replace(particle, "")
            if len(rest.strip()) < 3:
                return True
    return False


CUSTOMER_REPORT_HINTS = (
    "เสีย", "ไม่ทำงาน", "พัง", "ซ่อม", "แจ้งซ่อม", "มีปัญหา", "ใช้ไม่ได้",
    "ไม่เย็น", "น้ำรั่ว", "รั่ว", "เสียงดัง", "broken", "not working", "repair",
)

REPORT_TAKEN = {
    "th": "รับแจ้งแล้วครับ เลขงาน {code}\n\"{issue}\"\n\nขอที่อยู่ที่จะให้ช่างไปด้วยครับ",
    "en": "Logged as {code}.\n\"{issue}\"\n\nWhat address should the technician go to?",
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


# Phrases that mean "change my own details" rather than "something is
# broken". Kept narrow: anything not clearly a profile edit is treated as
# a fault report, because that is what a customer messaging a repair shop
# is overwhelmingly doing.
_PROFILE_EDIT_HINTS = (
    "แก้เบอร์", "เปลี่ยนเบอร์", "แก้ชื่อ", "เปลี่ยนชื่อ", "แก้อีเมล",
    "เปลี่ยนอีเมล", "แก้ที่อยู่ของฉัน", "ข้อมูลส่วนตัว", "โปรไฟล์",
    "my profile", "change my",
)


def _looks_like_profile_edit(message: str) -> bool:
    lowered = (message or "").lower()
    return any(hint in lowered for hint in _PROFILE_EDIT_HINTS)


async def _handle_customer_report(
    client: DataClient, *, ctx: ResolvedContext, license_id, message: str,
    language: str,
) -> ChatReply:
    """A customer reporting a fault, or answering the follow-up questions.

    No permission check: a customer is not a tenant member and holds no
    permission keys at all. The tenant boundary is the shop they are linked
    to, which is what license_id already is here.
    """
    license_id = str(license_id)
    text = (message or "").strip()

    # An in-flight report waiting for its address or its appointment. Held
    # in the same Redis slot as every other multi-turn exchange.
    try:
        pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)
    except Exception:
        pending = None

    if pending and pending.get("entity") == "customer_ticket":
        ticket_id = (pending.get("fields") or {}).get("ticket_id")
        awaiting = pending.get("missing") or []

        if (
            ticket_id and "address" in awaiting
            and not _is_bare_serial(text)
            # A question is not an address. "ช่างจะมากี่โมง" was saved as
            # where the customer lives, and the address was never asked
            # for again.
            and not _looks_like_a_question(text)
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
                format_thai_date, format_thai_time, parse_thai_date, parse_thai_time,
            )

            today = datetime.now(BANGKOK_TZ).date()
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
                return ChatReply(text=_t(REMINDER_NEEDS_DATE, language))
            due_time = parse_thai_time(text)
            fields: dict = {"scheduled_date": due_date.isoformat()}
            if due_time is not None:
                fields["scheduled_time"] = due_time.isoformat()
            try:
                await client.update_ticket(
                    license_id, str(ticket_id), fields, actor_id=ctx.chann_uid,
                )
                await client.clear_pending_intent(ctx.chann_uid, ctx.oa)
            except Exception:
                log.exception("could not save a customer's appointment")
                return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

            # The shop finds out now, not when someone happens to look.
            await _notify_new_ticket(client, license_id, str(ticket_id), language)
            return ChatReply(
                text=_t(REPORT_SCHEDULED, language).format(
                    date=format_thai_date(due_date),
                    time=f" {format_thai_time(due_time)}" if due_time else "",
                )
            )

    # Changing or cancelling an appointment. A customer whose plans change
    # and who cannot say so will simply not be there when the technician
    # arrives — which costs the shop a visit and the customer their day.
    if _matches_phrase(message, CUSTOMER_CANCEL_PHRASES) or any(
        w in text.lower() for w in CUSTOMER_CANCEL_TRIGGERS
    ):
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=True,
        )
    if any(w in text.lower() for w in CUSTOMER_RESCHEDULE_TRIGGERS):
        return await _handle_customer_amend(
            client, ctx=ctx, license_id=license_id, message=text,
            language=language, cancel=False,
        )

    # "งานของฉัน" from a customer means their own reports, not a
    # technician's queue.
    if _matches_phrase(message, TICKET_MINE_PHRASES + TICKET_LIST_PHRASES):
        try:
            tickets = await client.list_tickets(license_id)
        except Exception:
            log.exception("customer ticket list failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        mine = [t for t in tickets if t.get("customer_chann_uid") == ctx.chann_uid]
        if not mine:
            return ChatReply(text=_t(REPORT_NONE, language))
        lines = [
            _t(REPORT_STATUS_LINE, language).format(
                code=t.get("ticket_number"),
                status=_label(TICKET_STATUS_LABELS, t.get("status"), language),
                when=f" · {t.get('scheduled_date')}" if t.get("scheduled_date") else "",
            )
            for t in mine[:LIST_LIMIT]
        ]
        return ChatReply(text="\n".join(lines))

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
    if _looks_like_a_question(text):
        try:
            tickets = await client.list_tickets(license_id)
        except Exception:
            tickets = []
        mine = [
            t for t in tickets
            if t.get("customer_chann_uid") == ctx.chann_uid
            and str(t.get("status") or "") not in ("completed", "cancelled")
        ]
        if mine:
            t = mine[0]
            when = " ".join(
                str(x) for x in (t.get("scheduled_date"), t.get("scheduled_time")) if x
            )
            return ChatReply(
                text=_t(CUSTOMER_JOB_STATUS, language).format(
                    code=t.get("ticket_number"),
                    status=_label(TICKET_STATUS_LABELS, t.get("status"), language),
                    when=when or "ยังไม่ได้นัด",
                    tech=t.get("assigned_to_name") or "ยังไม่ได้มอบหมาย",
                ),
                quick_replies=[("ดูสถานะงาน", "งานของฉัน")],
            )
        return ChatReply(
            text=_t(CUSTOMER_QUESTION_FORWARDED, language),
            quick_replies=[("แจ้งซ่อม", "แจ้งซ่อม"), ("วิธีใช้", "วิธีใช้")],
        )

    try:
        profile = await client.get_profile(ctx.chann_uid)
    except Exception:
        profile = None

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

    return ChatReply(
        text=_t(REPORT_TAKEN, language).format(
            code=ticket.get("ticket_number"), issue=text[:80],
        ),
        entity_type="service_ticket", entity_id=str(ticket.get("id") or ""),
    )


CUSTOMER_CANCEL_PHRASES = ("ยกเลิก", "ไม่เอาแล้ว", "cancel")
CUSTOMER_CANCEL_TRIGGERS = ("ยกเลิกงาน", "ยกเลิกนัด", "cancel job")
CUSTOMER_RESCHEDULE_TRIGGERS = ("เลื่อนนัด", "เปลี่ยนวัน", "เลื่อน", "reschedule")

AMEND_NO_OPEN_JOB = {
    "th": "ไม่มีงานที่นัดไว้อยู่ครับ",
    "en": "You have no scheduled job right now.",
}
AMEND_PICK_ONE = {
    "th": "มีงานอยู่หลายรายการ ระบุเลขงานด้วยครับ เช่น \"เลื่อนนัด T-2026-0001 วันศุกร์\"",
    "en": "You have several jobs — include the number.",
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


async def _handle_customer_amend(
    client: DataClient, *, ctx: ResolvedContext, license_id: str, message: str,
    language: str, cancel: bool,
) -> ChatReply:
    """A customer moving or cancelling their own appointment."""
    from .thai_datetime import (
        format_thai_date, format_thai_time, parse_thai_date, parse_thai_time,
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
        return ChatReply(text=_t(AMEND_PICK_ONE, language))

    code = str(ticket.get("ticket_number") or "")
    ticket_id = str(ticket.get("id") or "")

    if cancel:
        try:
            await client.set_ticket_status(
                license_id, ticket_id, "cancelled", actor_id=ctx.chann_uid,
            )
        except Exception:
            log.exception("customer cancellation failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
        # The shop finds out now. A cancellation nobody is told about is a
        # technician driving to an empty house.
        await _notify_ticket_change(
            client, license_id, ticket_id,
            f"ลูกค้ายกเลิกงาน {code}", language,
        )
        return ChatReply(text=_t(AMEND_CANCELLED, language).format(code=code))

    today = datetime.now(BANGKOK_TZ).date()
    due_date = parse_thai_date(message, today)
    if due_date is None:
        return ChatReply(text=_t(REMINDER_NEEDS_DATE, language))
    due_time = parse_thai_time(message)
    fields: dict = {"scheduled_date": due_date.isoformat()}
    if due_time is not None:
        fields["scheduled_time"] = due_time.isoformat()
    try:
        await client.update_ticket(license_id, ticket_id, fields, actor_id=ctx.chann_uid)
    except Exception:
        log.exception("customer reschedule failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    when = format_thai_date(due_date) + (
        f" {format_thai_time(due_time)}" if due_time else ""
    )
    await _notify_ticket_change(
        client, license_id, ticket_id, f"ลูกค้าเลื่อนนัด {code} เป็น {when}", language,
    )
    return ChatReply(
        text=_t(AMEND_RESCHEDULED, language).format(
            code=code, date=format_thai_date(due_date),
            time=f" {format_thai_time(due_time)}" if due_time else "",
        )
    )


async def _notify_ticket_change(
    client: DataClient, license_id: str, ticket_id: str, text: str, language: str,
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

    targets = {
        str(m.get("chann_uid"))
        for m in members
        if str(m.get("role") or "").lower() in ("owner", "admin")
        and str(m.get("status") or "active") == "active"
    }
    assignee_ref = str((ticket or {}).get("assigned_to_ref") or "")
    for member in members:
        if str(member.get("id")) == assignee_ref and member.get("chann_uid"):
            targets.add(str(member["chann_uid"]))

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
                entity_type="service_ticket",
                entity_id=ticket_id,
                oa="sales",
            )
        except Exception:
            log.exception("could not notify %s about a ticket change", chann_uid)


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

    # Owners and admins: the people whose job it is to dispatch. Notifying
    # every member would put a customer's address in front of technicians
    # who have not been given the job.
    targets = [
        m for m in members
        if str(m.get("role") or "").lower() in ("owner", "admin")
        and str(m.get("status") or "active") == "active"
    ]
    text = (
        f"แจ้งซ่อมใหม่ {ticket.get('ticket_number')}\n"
        f"{ticket.get('customer_name') or '—'}\n"
        f"{ticket.get('issue_description') or ''}"
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
                entity_type="service_ticket",
                entity_id=ticket_id,
                oa="sales",
            )
        except Exception:
            log.exception("could not notify %s about a new ticket", chann_uid)


# ------------------------------------------------ Phase 13 field service

CHECKIN_TRIGGERS = (
    "เช็คอิน", "เช็กอิน", "ถึงหน้างาน", "ถึงแล้ว", "มาถึงแล้ว", "เริ่มงาน",
    "check in", "checkin",
)
CHECKOUT_TRIGGERS = ("เช็คเอาท์", "เช็กเอาต์", "ปิดงาน", "check out", "checkout")

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
                report[field] = value
    return report


async def _resolve_ticket_for_member(
    client: DataClient, license_id: str, ctx: ResolvedContext, code: str,
) -> tuple[dict | None, dict | None]:
    """(member, ticket) for a code this person may act on, or (member, None)."""
    member = await client.get_member(license_id, ctx.chann_uid)
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
    member = await client.get_member(license_id, ctx.chann_uid)
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

    mine = [
        t for t in tickets
        if str(t.get("assigned_to_ref") or "") == str(member["id"])
        and str(t.get("status") or "") in (prefer_status or ("assigned", "in_progress"))
    ]
    if len(mine) == 1:
        return member, mine[0], True
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
            client, license_id, ctx, message, prefer_status=("assigned",),
        )
        if member is None or ticket is None:
            return ChatReply(text=_t(TICKET_PICK_ONE, language))
        code = str(ticket.get("ticket_number") or "")
        # No GPS from a text message. The LIFF page sends coordinates; chat
        # records the arrival without pretending to a location it does not
        # have, which is better than storing one that is wrong.
        result = await client.check_in_ticket(
            license_id, str(ticket["id"]), member_id=str(member["id"]),
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        return ChatReply(text=_t(CHECKIN_FAILED, language).format(detail=exc.detail))
    except Exception:
        log.exception("check-in failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(CHECKIN_DONE, language).format(
            code=code,
            customer=" ".join(
                p for p in (result.get("customer_name"), result.get("customer_phone")) if p
            ) or "—",
            address=result.get("service_address") or "—",
        ),
        entity_type="service_ticket", entity_id=str(result.get("id") or ""),
        quick_replies=[("ปิดงาน", f"ปิดงาน {code}\nพบ: \nแก้: ")],
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
        if awaiting:
            answer = (message or "").strip()
            if awaiting[0] == "parts_changed" and answer.lower() in _NONE_ANSWERS:
                answer = ""
            fields[awaiting[0]] = answer
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
            member = await client.get_member(license_id, ctx.chann_uid)
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
            return ChatReply(text=_t(CHECKIN_FAILED, language).format(detail=exc.detail))
        except Exception:
            log.exception("check-out failed")
            return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))
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
        blocked = exc.structured or {}
        if blocked.get("error") == "checkout_blocked":
            # The gate's own list, passed through. The technician is
            # standing in a customer's house reading this.
            return ChatReply(
                text=_t(CHECKOUT_NEEDS_REPORT, language).format(
                    missing=", ".join(blocked.get("missing") or []), code=code,
                )
            )
        return ChatReply(text=_t(CHECKIN_FAILED, language).format(detail=exc.detail))
    except Exception:
        log.exception("check-out failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(CHECKOUT_DONE, language).format(
            code=code, report=result.get("report_id") or "",
        ),
        entity_type="service_report", entity_id=str(result.get("id") or ""),
    )


# ------------------------------------------------------- Phase 12 tickets

TICKET_LIST_PHRASES = (
    "รายการงาน", "รายการงานซ่อม", "งานซ่อม", "รายการซ่อม", "งานทั้งหมด",
    "งานค้าง", "tickets",
)
TICKET_MINE_PHRASES = ("งานของฉัน", "งานที่รับ", "my tickets")
TICKET_DETAIL_TRIGGERS = ("ข้อมูลงาน", "รายละเอียดงาน", "ticket")
TICKET_ASSIGN_TRIGGERS = ("มอบหมาย", "จ่ายงาน", "assign ticket")
TICKET_CLAIM_TRIGGERS = ("รับงาน", "claim")

TICKET_CODE_RE = re.compile(r"\b(T-\d{4}-\d{4})\b", re.IGNORECASE)

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


REPORT_LIST_PHRASES = ("รายงานของฉัน", "รายงานที่ส่ง", "ดูรายงาน", "my reports")

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
        member = await client.get_member(str(license_id), ctx.chann_uid)
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
            member = await client.get_member(str(license_id), ctx.chann_uid)
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

    lines = [f"{ticket.get('ticket_number')} · {_label(TICKET_STATUS_LABELS, ticket.get('status'), language)}"]
    if ticket.get("customer_name"):
        lines.append(f"ลูกค้า: {ticket['customer_name']}")
    if ticket.get("customer_phone"):
        lines.append(f"โทร: {ticket['customer_phone']}")
    if ticket.get("service_address"):
        lines.append(f"ที่อยู่: {ticket['service_address']}")
    if ticket.get("issue_description"):
        lines.append(f"อาการ: {ticket['issue_description']}")
    when = " ".join(
        str(x) for x in (ticket.get("scheduled_date"), ticket.get("scheduled_time")) if x
    )
    if when:
        lines.append(f"นัด: {when}")
    if ticket.get("assigned_to_name"):
        lines.append(f"ช่าง: {ticket['assigned_to_name']}")

    await _remember_entity(
        client, ctx, entity_type="ticket", entity_id=str(ticket["id"]),
        code=str(ticket.get("ticket_number") or ""),
    )
    return ChatReply(
        text="\n".join(lines),
        entity_type="ticket", entity_id=str(ticket["id"]),
    )


async def _handle_ticket_list(
    client: DataClient, *, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str, mine: bool = False,
) -> ChatReply:
    if "ticket.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))

    license_id = str(license_id)
    try:
        if mine:
            member = await client.get_member(license_id, ctx.chann_uid)
            if member is None:
                return ChatReply(text=_t(TICKET_EMPTY, language))
            # visible_to, not a plain list: a technician browsing without
            # it would read the address and phone number of every private
            # job in the tenant.
            tickets = await client.list_tickets(
                license_id, visible_to=str(member["id"]),
            )
        else:
            tickets = await client.list_tickets(license_id)
    except Exception:
        log.exception("ticket list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if not tickets:
        return ChatReply(text=_t(TICKET_EMPTY, language))

    shown = tickets[:LIST_LIMIT]
    lines = [
        f"{t.get('ticket_number')} · {_label(TICKET_STATUS_LABELS, t.get('status'), language)}"
        + (f" · {t.get('customer_name')}" if t.get("customer_name") else "")
        for t in shown
    ]
    return ChatReply(
        text="\n".join(lines) + _truncation_note(len(shown), len(tickets), language, "index"),
        list_card=_list_card(
            title="งานซ่อม", section="index", language=language,
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
                    "action_label": "ดู",
                    "action_text": f"ข้อมูลงาน {t.get('ticket_number')}",
                }
                for t in shown
            ],
        ),
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

    match = TICKET_CODE_RE.search(message or "")
    if not match:
        return ChatReply(text=_t(TICKET_NEEDS_CODE, language))
    code = match.group(1).upper()

    # Whatever follows the code, minus the code and the trigger, names the
    # target. Team names are tenant-chosen free text, so this cannot be a
    # closed vocabulary.
    lowered = message.lower()
    index = lowered.find(trigger.lower())
    target_text = message[index + len(trigger):] if index >= 0 else message
    target_text = TICKET_CODE_RE.sub("", target_text)
    for word in ("ให้ทีม", "ให้ช่าง", "ให้", "แก่", "to team", "to"):
        target_text = target_text.replace(word, " ")
    target_text = " ".join(target_text.split()).strip(" :·-")
    if not target_text:
        return ChatReply(text=_t(TICKET_NEEDS_TARGET, language))

    license_id = str(license_id)
    try:
        tickets = await client.list_tickets(license_id)
        ticket = next(
            (t for t in tickets if str(t.get("ticket_number", "")).upper() == code), None,
        )
        if ticket is None:
            return ChatReply(
                text=_t(NOT_FOUND_BY_CODE, language).format(what="งาน", code=code)
            )

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
            if person == "ambiguous":
                return ChatReply(text=_t(TICKET_TARGET_AMBIGUOUS, language))
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
    "th": "มีช่างหลายคนที่ชื่อตรงกัน ระบุให้ชัดขึ้นหรือมอบหมายให้ทีมแทน",
    "en": "Several technicians match that name — be more specific, or assign to a team.",
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
    """A member whose profile name matches, "ambiguous", or None.

    Matching on the profile rather than chann_uid: a CS person dispatching
    a job types "สมชาย", not an internal identifier they have never seen.
    """
    needle = (name or "").strip().lower()
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
    # Two people called สมชาย is not a reason to pick one — the job would
    # go to the wrong person's day.
    return matches[0] if len(matches) == 1 else "ambiguous"


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

    text = (
        f"งานใหม่ {ticket.get('ticket_number')} ({target_label})\n"
        f"{ticket.get('customer_name') or '—'}\n"
        f"{ticket.get('service_address') or '—'}\n"
        f"{ticket.get('issue_description') or ''}"
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
                entity_type="service_ticket",
                entity_id=str(ticket.get("id") or ""),
                # The technician OA, not sales: this is the channel they
                # actually work in.
                oa="technician",
            )
        except Exception:
            log.exception("could not notify %s about an assignment", chann_uid)


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
        member = await client.get_member(license_id, ctx.chann_uid)
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
            ]
            if len(claimable) != 1:
                return ChatReply(text=_t(TICKET_NEEDS_CODE, language))
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
        claimed = await client.claim_ticket(
            license_id, str(ticket["id"]), str(member["id"]), actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        return ChatReply(
            text=_t(TICKET_CLAIM_FAILED, language).format(detail=str(exc.detail))
        )
    except Exception:
        log.exception("ticket claim failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    return ChatReply(
        text=_t(TICKET_CLAIMED, language).format(
            code=code,
            customer=" ".join(
                p for p in (claimed.get("customer_name"), claimed.get("customer_phone")) if p
            ) or "—",
            address=claimed.get("service_address") or "—",
            when=_ticket_when(claimed),
        ),
        entity_type="service_ticket", entity_id=str(claimed.get("id") or ""),
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
    "index": "",
}


def dashboard_link(section: str) -> str | None:
    """A LIFF deep link to a dashboard page, or None when no LIFF id is
    configured.

    Returning None rather than a half-formed URL is deliberate: a link that
    opens an error page is worse than no link, because the person taps it,
    waits, and ends up somewhere broken instead of just reading the list.
    """
    from ..config import settings

    liff_id = (settings.liff_sales_id or "").strip()
    path = DASHBOARD_PATHS.get(section)
    if not liff_id or path is None:
        return None
    return f"https://liff.line.me/{liff_id}/{path}" if path else f"https://liff.line.me/{liff_id}"

CUSTOMER_LIST_PHRASES = ("รายชื่อลูกค้า", "รายการลูกค้า", "ดูลูกค้า", "ลูกค้าทั้งหมด", "customer list")
DEAL_LIST_PHRASES = ("รายการดีล", "รายชื่อดีล", "ดูดีล", "ดีลทั้งหมด", "deal list")
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
DEAL_OPEN_PHRASES = ("ดีลที่ยังไม่ปิด", "ดีลค้าง", "ดีลเปิดอยู่", "open deals")
PRODUCT_LIST_PHRASES = ("รายการสินค้า", "รายชื่อสินค้า", "ดูสินค้า", "สินค้าทั้งหมด", "product list")
QUOTE_LIST_PHRASES = ("รายการใบเสนอราคา", "ใบเสนอราคาทั้งหมด", "ดูใบเสนอราคา", "quote list")

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


def _matches_phrase(message: str, phrases: tuple[str, ...]) -> bool:
    compact = (message or "").strip().lower().replace(" ", "")
    return bool(compact) and any(compact == p.replace(" ", "") for p in phrases)


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
    shown: int, total: int,
) -> dict:
    """The structured form of a list reply.

    Each row carries its own action, which is the fix for a real problem in
    the first version: a single "view details" quick reply had to guess
    which record was meant, and guessing the first one is wrong more often
    than not.
    """
    note = f"{shown}/{total}" if total > shown else str(total)
    card = {"title": title, "rows": rows, "note": note}
    url = dashboard_link(section)
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

    rows = [
        f"{customer.get('customer_id')} · {_customer_name(customer)}",
        f"สถานะ: {_label(CUSTOMER_STAGE_LABELS, customer.get('stage'), language)}",
    ]
    for field_name, label in (
        ("phone", "โทร"), ("email", "อีเมล"), ("address", "ที่อยู่"), ("notes", "บันทึก"),
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
DEAL_CREATE_TRIGGERS = ("สร้างดีลให้", "เปิดดีลให้", "สร้างดีลกับ", "create deal for")


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
_QTY_RE = re.compile(r"(?:x|×|จำนวน)\s*(\d+)|(\d+)\s*(?:ตัว|ชิ้น|อัน|เครื่อง|ชุด)\b", re.I)
_PRICE_RE = re.compile(r"(?:ราคา|@|฿)\s*([\d,]+(?:\.\d{1,2})?)", re.I)

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

_NEW_PRICE_RE = re.compile(r"(?:เหลือ|เป็น|as|to)\s*([\d,]+(?:\.\d{1,2})?)", re.I)


async def _resolve_line_target(
    client: DataClient, license_id: str, ctx: ResolvedContext, message: str,
):
    """(kind, code, id, lines) for the quote or deal a line edit targets.

    An explicit code wins; otherwise the record just discussed. Quotes are
    checked before deals because someone editing prices is usually working
    on the offer, not the pipeline entry behind it.
    """
    quote_match = re.search(r"\b(Q-\d{4}-\d{4})\b", message or "", re.I)
    deal_match = re.search(r"\b(D-\d{4}-\d{4})\b", message or "", re.I)

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

    last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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
    "th": "เปิดงานซ่อม {code} ให้ {name} แล้ว\nอาการ: {issue}",
    "en": "Opened {code} for {name}: {issue}",
}
STAFF_TICKET_NEEDS_ISSUE = {
    "th": "แจ้งซ่อมเรื่องอะไรครับ พิมพ์อาการมาได้เลย",
    "en": "What is the problem?",
}


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
        last_ref = await client.get_last_customer_ref(ctx.chann_uid, ctx.oa)
        if last_ref:
            customer = {"id": last_ref["customer_id"], "first_name": last_ref["name"]}
    if customer is None:
        return ChatReply(text=_t(DEAL_NEEDS_TARGET_NAME, language))

    try:
        ticket = await client.create_ticket(
            license_id,
            {
                "issue_description": issue,
                "customer_id": str(customer.get("id") or ""),
                "customer_name": _display_name(customer),
                "customer_phone": customer.get("phone"),
                "service_address": fields.get("service_address") or customer.get("address"),
                "scheduled_date": fields.get("scheduled_date"),
                "scheduled_time": fields.get("scheduled_time"),
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
    return ChatReply(
        text=_t(STAFF_TICKET_CREATED, language).format(
            code=code, name=_display_name(customer), issue=issue,
        ),
        entity_type="ticket", entity_id=str(ticket["id"]),
        quick_replies=[
            ("มอบหมายอัตโนมัติ", f"มอบหมาย {code} ให้อัตโนมัติ"),
            ("ดูงาน", f"ข้อมูลงาน {code}"),
        ],
    )


SALES_SUMMARY_PHRASES = (
    "ยอดขาย", "สรุปยอด", "ยอดเดือนนี้", "สรุปการขาย", "ภาพรวมการขาย", "sales summary",
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
            ("ดีลเดือนนี้", "ดีลเดือนนี้"),
            ("ดีลเลยกำหนด", "ดีลเลยกำหนด"),
        ],
    )


async def _handle_ai_understood_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
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

    if entity == "followup" and action == "create":
        due = _joined(fields.get("due_date"), fields.get("due_time"))
        return await _handle_reminder_create(
            client, ctx=ctx, license_id=license_id,
            message=_joined("เตือน", due, target, fields.get("notes")),
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

    if entity == "warranty" and action == "read":
        serial = str(fields.get("serial_number") or "").strip()
        if serial:
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id,
                message=_joined("เช็คประกัน", serial), language=language,
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
            permission_keys, catalog, language,
            requested_action=action, requested_entity=entity,
        )
    )


async def _handle_line_item_intent(
    client: DataClient, *, intent: dict, ctx: ResolvedContext, license_id,
    permission_keys: list[str], language: str,
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

    # Rebuild a sentence the shared parser understands. Passing the
    # extracted values through the same code path as the typed version
    # means one set of rules about matching a line, one set about
    # ambiguity, and no way for the two to disagree.
    parts: list[str] = []
    name = str(fields.get("target_name") or "").strip()
    if name:
        parts.append(name)
    code = str(fields.get("code") or "").strip()
    if code:
        parts.append(code)

    if action == "delete":
        return await _handle_line_edit(
            client, ctx=ctx, license_id=license_id,
            message="ลบสินค้า " + " ".join(parts),
            trigger="ลบสินค้า", permission_keys=permission_keys,
            language=language, remove=True,
        )

    price = fields.get("quoted_unit_price")
    qty = fields.get("qty")
    if price is None and qty is None:
        # Recognised as a line edit but with nothing to change. Asking
        # beats guessing which of price or quantity was meant.
        return ChatReply(text=_t(LINE_NEEDS_TARGET, language))

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

QUOTE_DISCOUNT_SET = {
    "th": "ตั้งส่วนลด {discount} ให้ {code} แล้ว\nยอดสุทธิ {total}",
    "en": "{code} discounted by {discount}. Net {total}.",
}
QUOTE_DISCOUNT_NEEDS = {
    "th": "ลดเท่าไหร่ครับ เช่น \"ส่วนลด 10%\" หรือ \"ส่วนลด 500\"",
    "en": 'How much? e.g. "discount 10%" or "discount 500".',
}


QUOTE_VOID_TRIGGERS = ("ยกเลิกใบเสนอราคา", "ยกเลิก quote", "void quote", "ใบเสนอราคาไม่ใช้")
QUOTE_ACCEPT_TRIGGERS = ("ลูกค้าตกลง", "ลูกค้ารับใบเสนอราคา", "ตอบรับใบเสนอราคา", "quote accepted")

QUOTE_STATUS_SET = {
    "th": "เปลี่ยนสถานะ {code} เป็น {status} แล้ว",
    "en": "{code} is now {status}.",
}
QUOTE_STATUS_LABELS = {
    "draft": {"th": "ร่าง", "en": "draft"},
    "sent": {"th": "ส่งแล้ว", "en": "sent"},
    "accepted": {"th": "ลูกค้าตอบรับ", "en": "accepted"},
    "rejected": {"th": "ยกเลิก", "en": "void"},
    "expired": {"th": "หมดอายุ", "en": "expired"},
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
    match = re.search(r"\b(Q-\d{4}-\d{4})\b", message or "", re.I)
    code = match.group(1).upper() if match else None
    if not code:
        last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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
    amount = re.search(r"([\d,]+(?:\.\d{1,2})?)\s*(%|เปอร์เซ็นต์|บาท)?", message or "")
    if not amount:
        return ChatReply(text=_t(QUOTE_DISCOUNT_NEEDS, language))
    number = amount.group(1).replace(",", "")
    is_percent = amount.group(2) in ("%", "เปอร์เซ็นต์")

    match = re.search(r"\b(Q-\d{4}-\d{4})\b", message or "", re.I)
    code = match.group(1).upper() if match else None
    if not code:
        last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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

    new_price = None
    new_qty = None
    price_match = _NEW_PRICE_RE.search(text)
    if price_match and "ราคา" in trigger:
        new_price = price_match.group(1).replace(",", "")
        text = text.replace(price_match.group(0), " ")
    elif price_match and "จำนวน" in trigger:
        new_qty = int(float(price_match.group(1).replace(",", "")))
        text = text.replace(price_match.group(0), " ")
    else:
        qty_match = _QTY_RE.search(text)
        if qty_match and "จำนวน" in trigger:
            new_qty = max(1, int(qty_match.group(1) or qty_match.group(2)))
            text = text.replace(qty_match.group(0), " ")

    for word in ("ใน", "ของ", "ออกจาก", "จาก", "on", "from"):
        text = text.replace(word, " ")
    name = " ".join(text.split()).strip(" :·-,")

    if not lines:
        return ChatReply(
            text=_t(LINE_NONE_YET, language).format(
                where="ใบเสนอราคา" if kind == "quote" else "ดีล", code=code or "",
            )
        )

    line = _match_line(lines, name)
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
            return ChatReply(
                text=_t(LINE_REMOVED, language).format(
                    name=line.get("product_name"), where=where, code=code,
                )
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
    return ChatReply(
        text=_t(LINE_UPDATED, language).format(
            name=updated.get("product_name"), qty=qty,
            price=f"{unit:,.2f}", total=f"{unit * qty:,.2f}",
            where=where, code=code,
        )
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
    match = re.search(r"\b(D-\d{4}-\d{4})\b", text, re.IGNORECASE)
    if match:
        deal_code = match.group(1).upper()
        text = text.replace(match.group(1), " ")
    else:
        last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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
        qty = max(1, int(qty_match.group(1) or qty_match.group(2)))
        text = text.replace(qty_match.group(0), " ")

    name = " ".join(text.split()).strip(" :·-,")
    if not name:
        return ChatReply(text=_t(DEAL_PRODUCT_NEEDS_NAME, language))

    try:
        deals = await client.list_deals(license_id)
        deal = next(
            (d for d in deals if str(d.get("deal_id", "")).upper() == deal_code), None,
        )
        if deal is None:
            return ChatReply(
                text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code)
            )

        product = None
        if price is None:
            # Look it up rather than asking: a shop that has entered its
            # catalogue should not have to retype prices it already knows.
            try:
                products = await client.list_products(license_id)
            except Exception:
                products = []
            needle = name.lower()

            # Exact first — a product code or a full name is an unambiguous
            # answer and must not be beaten by a partial match on something
            # else.
            exact = [
                p for p in products
                if str(p.get("product_id") or "").lower() == needle
                or str(p.get("product_name") or "").lower() == needle
            ]
            # Then partial, because "พัดลม" is what someone types and
            # "พัดลมตั้งพื้น 16 นิ้ว" is what the catalogue calls it.
            # Requiring the full name would mean reading it off a screen
            # and copying it, which is the work chat is meant to remove.
            candidates = exact or [
                p for p in products
                if needle in str(p.get("product_name") or "").lower()
                or needle in str(p.get("product_id") or "").lower()
            ]

            if len(candidates) > 1:
                # Several models of the same thing at different prices.
                # Picking one silently puts the wrong price on a document
                # that goes to a customer — the one place a quiet guess is
                # least acceptable.
                shown = candidates[:LIST_LIMIT]
                lines = "\n".join(
                    f"· {c.get('product_name')}"
                    + (f" — {Decimal(str(c['unit_price'])):,.2f}"
                       if c.get("unit_price") is not None else "")
                    for c in shown
                )
                return ChatReply(
                    text=_t(DEAL_PRODUCT_AMBIGUOUS, language).format(
                        name=name, options=lines,
                    ),
                    # Labels drop the part every candidate shares. These
                    # buttons exist to tell near-identical products apart,
                    # and LINE's 20-character limit would otherwise cut off
                    # the only bit that differs — leaving four buttons all
                    # reading "พัดลมตั้งพื้น 16…".
                    quick_replies=[
                        (
                            _distinguishing_part(
                                str(c.get("product_name") or ""),
                                [str(o.get("product_name") or "") for o in shown],
                            ),
                            f"เพิ่มสินค้า {c.get('product_name')} "
                            f"{qty} ตัว เข้าดีล {deal_code}",
                        )
                        for c in shown[:4]
                    ],
                )

            product = candidates[0] if candidates else None
            if product and product.get("unit_price") is not None:
                price = str(product["unit_price"])
                name = str(product.get("product_name") or name)

        if price is None:
            # Not in the catalogue and no price given. Naming the gap and
            # showing the shape of an answer beats a bare "what price?",
            # which leaves someone guessing at the format too.
            return ChatReply(
                text=_t(PRODUCT_UNKNOWN_NEEDS_PRICE, language).format(name=name)
            )

        row = await client.add_deal_product(
            license_id, str(deal["id"]),
            {
                "product_name": name,
                "quoted_unit_price": price,
                "qty": qty,
                "product_id": str(product["id"]) if product else None,
            },
            actor_id=ctx.chann_uid,
        )
    except Exception:
        log.exception("adding a product to a deal failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    await _remember_entity(
        client, ctx, entity_type="deal", entity_id=str(deal["id"]), code=deal_code,
    )
    unit = Decimal(str(row.get("quoted_unit_price") or price))
    return ChatReply(
        text=_t(DEAL_PRODUCT_ADDED, language).format(
            name=row.get("product_name") or name, qty=qty, deal_id=deal_code,
            price=f"{unit:,.2f}", total=f"{unit * qty:,.2f}",
        ),
        entity_type="deal", entity_id=str(deal["id"]),
        quick_replies=[("สร้างใบเสนอราคา", f"สร้างใบเสนอราคาจากดีล {deal_code}")],
    )


QUOTE_CREATE_TRIGGERS = (
    "สร้างใบเสนอราคา", "ออกใบเสนอราคา", "ทำใบเสนอราคา", "create quote",
)

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
    permission_keys: list[str], language: str,
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
    deal_code = None
    match = re.search(r"\b(D-\d{4}-\d{4})\b", message or "", re.IGNORECASE)
    if match:
        deal_code = match.group(1).upper()
    else:
        last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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
            license_id, {"deal_id": deal["id"]}, actor_id=ctx.chann_uid,
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
        try:
            existing = await client.list_quote_products(license_id, str(row["id"]))
            for line in existing:
                await client.remove_quote_product(
                    license_id, str(row["id"]), str(line["id"]),
                    actor_id=ctx.chann_uid,
                )
        except Exception:
            log.exception("could not clear copied lines before an override")
            existing = []

        added = await _add_line_from_text(
            client, ctx=ctx, license_id=license_id, quote_id=str(row["id"]),
            text=override, language=language,
        )
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
        qty = max(1, int(qty_match.group(1) or qty_match.group(2)))
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


async def _handle_deal_create_direct(
    client: DataClient, *, ctx: ResolvedContext, license_id, name: str | None,
    permission_keys: list[str], language: str, rest: str | None = None,
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
        last_ref = await client.get_last_customer_ref(ctx.chann_uid, ctx.oa)
        if last_ref is None:
            return ChatReply(text=_t(DEAL_NEEDS_TARGET_NAME, language))
        contact = {"id": last_ref["customer_id"], "first_name": last_ref["name"]}
        used_context = True

    reply = await _apply_deal_create(
        client, contact=contact, fields={}, ctx=ctx,
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
    r"ดีล\s*(?:ที่)?\s*(?:เกิน|มากกว่า|สูงกว่า|over|above)\s*([\d,]+)", re.I,
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
    match = re.search(r"\b(D-\d{4}-\d{4})\b", message or "", re.I)
    code = match.group(1).upper() if match else None
    if not code:
        last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
        if last_ref and last_ref.get("entity_type") == "deal":
            code = str(last_ref.get("code") or "")
    if not code:
        return ChatReply(text=_t(QUOTE_NEEDS_DEAL, language))

    when = parse_thai_date(message, datetime.now(timezone.utc).date())
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

    today = datetime.now(timezone.utc).date()
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
        when = str(d.get("expected_close_date") or "")
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
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
    open_only: bool = False, for_customer: str | None = None,
) -> ChatReply:
    if "deal.read" not in set(permission_keys):
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
            # wrong person's pipeline, so ask instead.
            names = ", ".join(
                f"{_customer_name(c)} ({c.get('customer_id')})" for c in matches[:5]
            )
            return ChatReply(
                text=_t(DEAL_CUSTOMER_AMBIGUOUS, language).format(names=names)
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
            name = _customer_name(matched_customer)
            return ChatReply(
                text=_t(DEAL_NONE_FOR_CUSTOMER, language).format(name=name),
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
        rows.append(f"รวม: {subtotal:,.2f} บาท (ยังไม่รวมภาษี)")
    else:
        rows.append("ยังไม่มีรายการสินค้าในดีลนี้")

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


async def _handle_product_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    if "product.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        products = await client.list_products(str(license_id))
    except Exception:
        log.exception("product list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

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
        lines.append(f"{p.get('sku') or '-'} · {p.get('name') or '-'}{price_text}")
    text = "\n".join(lines) + _truncation_note(len(shown), len(products), language, "products")
    return ChatReply(
        text=text,
        quick_replies=[("รายการดีล", "รายการดีล")],
        quick_reply_url=_dashboard_button("products", language),
        list_card=_list_card(
            title="สินค้า", section="products", language=language,
            shown=len(shown), total=len(products),
            rows=[
                {
                    "title": str(p.get("name") or "-"),
                    "subtitle": " · ".join(
                        x for x in (
                            str(p.get("sku") or ""),
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
    "product_id": {"th": "รหัสสินค้า", "en": "product code"},
    "product_name": {"th": "ชื่อสินค้า", "en": "product name"},
    "unit_price": {"th": "ราคา", "en": "price"},
}


def ask_for_missing(missing: list[str], language: str = "th") -> str:
    """Spec 6.4 — ask only for what is actually absent.

    Reported live: an unrecognised field name (e.g. "last_name" straight
    from the AI's own JSON) was shown to the user verbatim —
    "กรุณาระบุlast_name, phone" — because this only ever joined the raw
    keys. Translates anything in MISSING_FIELD_LABELS; anything genuinely
    unknown still falls back to the raw key rather than hiding it, since a
    silently-dropped missing field would be worse than an ugly one.
    """
    labels = ", ".join(
        MISSING_FIELD_LABELS.get(m, {}).get(language) or str(m) for m in missing
    )
    return _t(ASK_MISSING, language).format(fields=labels)


CUSTOMER_HELP = {
    "th": (
        "พิมพ์คุยได้เลยครับ\n\n"
        "· แจ้งซ่อม — พิมพ์อาการที่เสียมาได้เลย เช่น \"แอร์ไม่เย็น\"\n"
        "· ดูสถานะงาน — พิมพ์ \"งานของฉัน\"\n\n"
        "ผมจะถามที่อยู่และวันเวลาที่สะดวกต่อ แล้วส่งเรื่องให้ทางร้าน"
    ),
    "en": (
        "Just type.\n\n"
        "· Report a fault — describe the problem, e.g. \"air con not cooling\"\n"
        "· Check a job — type \"my jobs\"\n\n"
        "I will ask for an address and a time, then pass it to the shop."
    ),
}

HELP_TRIGGERS = (
    "ช่วยเหลือ", "วิธีใช้", "ใช้ยังไง", "ทำอะไรได้บ้าง", "คำสั่ง",
    "help", "how to use", "commands", "?",
)

# What to show, grouped the way a salesperson's day is shaped rather than the
# way the permission catalogue is organised. Each entry is
# (permission key, example command, what it does) — the example is the point:
# a list of capabilities tells someone what exists, an example tells them what
# to type, and only one of those gets used.
HELP_SECTIONS = (
    ("ลูกค้า", (
        ("customer.read", "รายชื่อลูกค้า", "ดูลูกค้าทั้งหมด"),
        ("customer.read", "ค้นหาลูกค้า สมชาย", "ค้นหาด้วยชื่อหรือเบอร์"),
        ("customer.read", "ข้อมูลลูกค้า C-2026-0001", "ดูรายละเอียด"),
        ("customer.create", "สร้างลูกค้า สมชาย ใจดี 0812345678", "เพิ่มลูกค้าใหม่"),
    )),
    ("ดีลและใบเสนอราคา", (
        ("deal.read", "รายการดีล", "ดูดีลทั้งหมด"),
        ("deal.read", "ดีลที่ยังไม่ปิด", "เฉพาะที่ยังไม่จบ"),
        ("deal.read", "ข้อมูลดีล D-2026-0001", "ดูรายละเอียดพร้อมยอดรวม"),
        ("deal.create", "สร้างดีลให้ สมชาย", "เปิดดีลใหม่"),
        ("quote.create", "สร้างใบเสนอราคาจากดีล D-2026-0001", "ออกใบเสนอราคา"),
        ("quote.update", "ออกเอกสาร Q-2026-0001", "สร้าง PDF ส่งลูกค้า"),
    )),
    ("บันทึกและการติดตาม", (
        ("note.create", "บันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด", "จดบันทึก"),
        ("note.read", "ดูบันทึก C-2026-0001", "ดูบันทึกย้อนหลัง"),
        ("followup.create", "เตือน D-2026-0001 พรุ่งนี้", "ตั้งเตือน"),
        ("followup.create", "นัดดูสินค้าวันศุกร์ บ่าย 2", "นัดหมายพร้อมเวลา"),
        ("followup.read", "งานวันนี้", "ดูงานที่ต้องทำ"),
    )),
    ("งานซ่อม", (
        ("ticket.read", "รายการงาน", "ดูงานซ่อมทั้งหมด"),
        ("ticket.read", "งานของฉัน", "เฉพาะงานที่รับไว้"),
        ("ticket.update", "มอบหมาย T-2026-0001 ให้ทีม AC", "จ่ายงานให้ทีมช่าง"),
        ("ticket.update", "รับงาน T-2026-0001", "ช่างกดรับงาน"),
        ("ticket.update", "เช็คอิน T-2026-0001", "แจ้งว่าถึงหน้างานแล้ว"),
        ("ticket.update", "ปิดงาน T-2026-0001\nพบ: ...\nแก้: ...", "ปิดงานพร้อมรายงาน"),
    )),
    ("ทีมงาน", (
        ("member.manage", "เพิ่มช่าง", "ขอรหัสเชิญให้ช่างเข้าร่วมบริษัท"),
        ("setting.manage", "ตั้งกฎมอบหมาย ช่างแอร์ให้ทีม AC วันละ 5 งาน", "ตั้งกฎจ่ายงานอัตโนมัติ"),
        ("setting.manage", "ดูกฎมอบหมาย", "ดูกฎที่ตั้งไว้"),
    )),
    ("ตั้งค่า", (
        ("setting.manage", "ข้อมูลบริษัท", "ดูข้อมูลที่พิมพ์บนเอกสาร"),
        ("setting.manage", "ตั้งเลขผู้เสียภาษี 0105558123456", "แก้ทีละช่อง"),
    )),
)

HELP_INTRO = {
    "th": "พิมพ์คุยได้เลยเหมือนคุยกับผู้ช่วย ตัวอย่างคำสั่งที่ใช้บ่อย:",
    "en": "Just type naturally. Some things you can say:",
}
HELP_OUTRO = {
    "th": "\nเคล็ดลับ: เปิดดูลูกค้าหรือดีลก่อน แล้วพิมพ์บันทึกหรือนัดต่อได้เลยโดยไม่ต้องใส่รหัสซ้ำ",
    "en": "\nTip: open a customer or deal first, then add a note or reminder without repeating the code.",
}
HELP_NOTHING = {
    "th": "ยังไม่มีสิทธิ์ใช้งานคำสั่งใด ๆ — ติดต่อเจ้าของบริษัทเพื่อขอสิทธิ์",
    "en": "You do not have permission to use any commands yet — ask the company owner.",
}


def usage_help(permission_keys, language: str = "th") -> str:
    """Example commands this person can actually run.

    Deliberately separate from suggest_what_you_can_do, which lists the
    PERMISSIONS someone holds. Knowing you hold "followup.create" does not
    tell you to type "เตือน D-2026-0001 พรุ่งนี้" — and this product's whole
    interface is what you type, so the examples are the help.

    Filtered by permission for the same reason suggest_what_you_can_do is:
    showing someone a command that will refuse them is worse than not
    mentioning it.
    """
    held = set(permission_keys)
    lines: list[str] = []

    for title, entries in HELP_SECTIONS:
        usable = [(cmd, what) for key, cmd, what in entries if key in held]
        if not usable:
            continue
        lines.append(f"\n【{title}】")
        # De-duplicated on the command text: two permissions can legitimately
        # surface the same example, and printing it twice looks like a bug.
        seen = set()
        for cmd, what in usable:
            if cmd in seen:
                continue
            seen.add(cmd)
            lines.append(f"· {cmd}\n   → {what}")

    if not lines:
        return _t(HELP_NOTHING, language)
    return _t(HELP_INTRO, language) + "\n" + "\n".join(lines) + "\n" + _t(HELP_OUTRO, language)


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
                _filter_by_oa(permission_keys, ctx.oa), catalog, language,
                requested_action=resume_action, requested_entity="customer",
            ))
        return await _apply_customer_action(
            client, chosen_row=chosen, action=resume_action or "", fields=resume_fields,
            ctx=ctx, license_id=license_id, language=language,
        )
    if resume_entity == "deal":
        needed = required_permission(resume_action or "", "deal")
        if needed is None or needed not in set(permission_keys) or not _oa_allows(ctx.oa, needed):
            catalog = await client.permission_catalog()
            return ChatReply(text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language,
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
            str(fields.get("notes") or ""), datetime.now(timezone.utc).date(),
        )
        quick: list[tuple[str, str]] = []
        if suggested:
            reply_text += "\n" + _t(APPOINTMENT_OFFER, language).format(
                date=suggested.isoformat(),
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

    return ChatReply(text=_pending_execution_reply(intent, language), intent=intent)


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
    pending_intent."""
    await client.set_last_customer_ref(
        ctx.chann_uid, ctx.oa, customer_id=row["id"], name=_display_name(row),
        ttl_seconds=LAST_CUSTOMER_REF_TTL_S,
    )


# An hour, not ten minutes. Ten was long enough for a demo and too short
# for work: someone confirms a customer, takes a phone call, and comes
# back to open a deal — and was told they had no permission, because the
# context had expired and the message fell through to the AI.
LAST_ENTITY_REF_TTL_S = 3600


async def _remember_entity(
    client: DataClient, ctx: ResolvedContext, *, entity_type: str, entity_id, code: str,
) -> None:
    """Records "the record we were just looking at" — generalises
    _remember_customer to deals and quotes, for notes and reminders. See
    cache.k_last_entity_ref for why this is a separate key from
    last_customer_ref rather than reusing it."""
    try:
        await client.set_last_entity_ref(
            ctx.chann_uid, ctx.oa, entity_type=entity_type, entity_id=str(entity_id),
            code=code, ttl_seconds=LAST_ENTITY_REF_TTL_S,
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
    license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    action = intent.get("action")
    fields = intent.get("fields") or {}
    license_id = str(license_id)

    if action == "create":
        target_name = fields.get("target_name")
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
            return await _apply_deal_create(
                client, contact=contact, fields=fields, ctx=ctx,
                license_id=license_id, language=language, used_context=True,
            )
        contact, err = await _find_one_customer_by_name(
            client, license_id, target_name, language,
            ctx=ctx, resume_entity="deal", resume_action="create", resume_fields=fields,
        )
        if err is not None:
            return err
        return await _apply_deal_create(
            client, contact=contact, fields=fields, ctx=ctx,
            license_id=license_id, language=language,
        )

    return ChatReply(text=_pending_execution_reply(intent, language), intent=intent)


async def _apply_deal_create(
    client: DataClient, *, contact: dict, fields: dict, ctx: ResolvedContext,
    license_id: str, language: str, used_context: bool = False,
) -> ChatReply:
    """The actual deal-creation work, factored out of _handle_deal_intent
    so a resumed disambiguation (multiple customers shared a name, the
    user picked one from a numbered list) can reach it directly with the
    already-resolved contact, instead of repeating the name lookup."""
    try:
        row = await client.create_deal(
            license_id,
            {"contact_id": contact["id"], "notes": fields.get("notes")},
            actor_id=ctx.chann_uid,
        )
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
        ),
        entity_type="deal", entity_id=row["id"],
    )


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
    license_id, language: str,
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

    if action != "create":
        return ChatReply(text=_pending_execution_reply(intent, language), intent=intent)

    deal_code = (fields.get("deal_code") or "").strip().upper()
    if not deal_code:
        return ChatReply(text=_t(QUOTE_NEEDS_DEAL_CODE, language), intent=intent)

    deals = await client.list_deals(license_id)
    match = next((d for d in deals if d["deal_id"].upper() == deal_code), None)
    if match is None:
        return ChatReply(
            text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code), intent=intent,
        )

    try:
        row = await client.create_quote(
            license_id, {"deal_id": match["id"]}, actor_id=ctx.chann_uid,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return ChatReply(
                text=_t(QUOTE_DEAL_NOT_FOUND, language).format(deal_id=deal_code), intent=intent,
            )
        raise
    return ChatReply(
        text=_t(QUOTE_CREATED, language).format(quote_id=row["quote_id"], deal_id=deal_code),
        entity_type="quote", entity_id=row["id"], intent=intent,
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


async def maybe_handle_storefront(
    client: DataClient, *, message: str, ctx: ResolvedContext, language: str,
) -> ChatReply | None:
    """Returns a reply if this message was storefront browsing (a search, a
    confirmation of one just offered, or a selection from a list already
    shown), else None so the caller proceeds with its normal tenant-scoped
    handling."""
    pending = await client.get_pending_intent(ctx.chann_uid, ctx.oa)

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
        # A bare word ("พัดลม", no "ค้นหา" prefix) is genuinely ambiguous —
        # confirm before committing to "this was a product search" rather
        # than assuming it and listing results outright. A company code
        # (its own distinct alphabet) or anything too short to be a real
        # search term is never intercepted, so shop-code/shop-name lookup
        # in the registration flow is completely unaffected.
        text = (message or "").strip()
        if len(text) < 2 or COMPANY_CODE_RE.match(text.upper()):
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
        if any(t in message.lower() for t in TICKET_DETAIL_TRIGGERS) and (
            TICKET_CODE_RE.search(message or "")
            or _matches_phrase(message, TICKET_DETAIL_TRIGGERS)
        ):
            return await _handle_ticket_detail(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if _matches_phrase(message, REPORT_LIST_PHRASES):
            return await _handle_report_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        if _matches_phrase(message, TICKET_MINE_PHRASES):
            return await _handle_ticket_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language, mine=True,
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
        if in_progress and in_progress.get("entity") == "service_report":
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )

        # Check-in/out before claim: "ปิดงาน" and "รับงาน" are different
        # actions on the same ticket, and the shorter claim trigger must
        # not swallow a close.
        if any(t in message.lower() for t in CHECKOUT_TRIGGERS):
            return await _handle_check_out(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if any(t in message.lower() for t in CHECKIN_TRIGGERS):
            return await _handle_check_in(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        if any(t in message.lower() for t in TICKET_CLAIM_TRIGGERS):
            return await _handle_ticket_claim(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        assign_trigger = next(
            (t for t in TICKET_ASSIGN_TRIGGERS if t in message.lower()), None,
        )
        if assign_trigger:
            return await _handle_ticket_assign(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=assign_trigger, permission_keys=permission_keys,
                language=language,
            )

    # Customer OA. A customer is not a tenant member and holds no
    # permission keys at all, so every branch below would refuse them —
    # which is why this comes first and why it does not check permissions.
    # Their boundary is the shop they are linked to, which license_id
    # already is.
    if ctx.oa == "customer":
        if _matches_phrase(message, HELP_TRIGGERS):
            return ChatReply(
                text=_t(CUSTOMER_HELP, language),
                quick_replies=[("งานของฉัน", "งานของฉัน")],
            )
        # Editing your own profile is a customer's other legitimate reason
        # to type here, and it is allowed regardless of permissions
        # (Phase 8 — self-edit is always permitted). Catching everything as
        # a fault report turned "แก้เบอร์เป็น 08..." into a repair job,
        # which a test caught immediately.
        if any(t in message.lower() for t in SERIAL_REGISTER_TRIGGERS):
            return await _handle_warranty_register(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )
        if any(t in message.lower() for t in SERIAL_LOOKUP_TRIGGERS):
            return await _handle_serial_enquiry(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )
        if not _looks_like_profile_edit(message):
            return await _handle_customer_report(
                client, ctx=ctx, license_id=license_id, message=message,
                language=language,
            )

    # Help, before anything else and on every OA. Someone who types "ใช้ยังไง"
    # is telling you they are stuck; routing that through intent parsing to
    # maybe get a permission list back is not an answer.
    if _matches_phrase(message, HELP_TRIGGERS) or (message or "").strip() == "?":
        return ChatReply(
            text=usage_help(permission_keys, language),
            quick_replies=[("รายชื่อลูกค้า", "รายชื่อลูกค้า"), ("งานวันนี้", "งานวันนี้")],
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
        if any(t in message.lower() for t in NOTE_LIST_TRIGGERS):
            return await _handle_note_list(
                client, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )
        note_trigger = next((t for t in NOTE_TRIGGERS if t in message.lower()), None)
        if note_trigger:
            return await _handle_note_create(
                client, ctx=ctx, license_id=license_id, message=message, trigger=note_trigger,
                permission_keys=permission_keys, language=language,
                actor_id=ctx.chann_uid,
            )
        if _matches_phrase(message, REMINDER_LIST_TRIGGERS) or any(
            t in message.lower() for t in REMINDER_LIST_TRIGGERS
        ):
            return await _handle_reminder_list(
                client, ctx=ctx, license_id=license_id,
                permission_keys=permission_keys, language=language,
            )
        # Cancelling comes before creating: "ยกเลิกเตือน C-2026-0011" names
        # a record and contains the create verb, so the create matcher would
        # otherwise claim it and answer "ไม่เข้าใจวันที่".
        if _is_reminder_cancel_command(message):
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
        if _matches_phrase(message, CUSTOMER_LIST_PHRASES):
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
            return await _handle_quote_status(
                client, ctx=ctx, license_id=license_id, message=message, target="rejected",
                permission_keys=permission_keys, language=language,
            )
        if any(t in message.lower() for t in QUOTE_ACCEPT_TRIGGERS):
            return await _handle_quote_status(
                client, ctx=ctx, license_id=license_id, message=message, target="accepted",
                permission_keys=permission_keys, language=language,
            )
        if (
            any(t in message.lower() for t in QUOTE_DISCOUNT_TRIGGERS)
            or ("ลดราคา" in message.lower() and "%" in message)
        ):
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
            r"\b(D-\d{4}-\d{4})\b", message or "", re.IGNORECASE
        ):
            last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
            if not (last_ref and last_ref.get("entity_type") == "deal"):
                product_trigger = None
        if product_trigger:
            return await _handle_deal_product_add(
                client, ctx=ctx, license_id=license_id, message=message,
                trigger=product_trigger, permission_keys=permission_keys,
                language=language,
            )

        if any(t in message.lower() for t in QUOTE_CREATE_TRIGGERS):
            return await _handle_quote_create_direct(
                client, ctx=ctx, license_id=license_id, message=message,
                permission_keys=permission_keys, language=language,
            )

        create_for = _parse_after_trigger(message, DEAL_CREATE_TRIGGERS)
        if create_for:
            return await _handle_deal_create_direct(
                client, ctx=ctx, license_id=license_id, name=create_for,
                permission_keys=permission_keys, language=language,
                rest=_after_deal_conjunction(message),
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
            r"\b(D-\d{4}-\d{4})\b", message or "", re.IGNORECASE
        ):
            if await client.get_last_customer_ref(ctx.chann_uid, ctx.oa):
                return await _handle_deal_create_direct(
                    client, ctx=ctx, license_id=license_id, name=None,
                    permission_keys=permission_keys, language=language,
                    rest=_after_deal_conjunction(message) or _trailing_product(message),
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
        for_customer = _parse_after_trigger(message, DEAL_FOR_CUSTOMER_TRIGGERS)
        if for_customer:
            return await _handle_deal_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, for_customer=for_customer,
            )

        if _matches_phrase(message, DEAL_OPEN_PHRASES):
            return await _handle_deal_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, open_only=True,
            )
        if any(t in message.lower() for t in DEAL_CLOSE_DATE_TRIGGERS):
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
                threshold=Decimal(value_match.group(1).replace(",", "")),
            )
        for query_kind, phrases in DEAL_QUERY_PHRASES.items():
            if any(p in message.lower() for p in phrases):
                return await _handle_deal_query(
                    client, license_id=license_id, permission_keys=permission_keys,
                    language=language, kind=query_kind,
                )

        if _matches_phrase(message, DEAL_LIST_PHRASES):
            return await _handle_deal_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language,
            )
        if _matches_phrase(message, PRODUCT_LIST_PHRASES):
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
        if search_term is not None:
            if not search_term:
                return ChatReply(text=_t(SEARCH_NEEDS_TERM, language))
            return await _handle_customer_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, search_term=search_term,
            )

        customer_code = _parse_after_trigger(message, CUSTOMER_DETAIL_TRIGGERS)
        if customer_code is not None:
            return await _handle_customer_detail(
                client, license_id=license_id, code=customer_code,
                permission_keys=permission_keys, language=language, ctx=ctx,
            )

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

    # Company identity (Phase 10) — same closed-pattern reasoning, and one
    # step stronger: these values are printed on a legal document the
    # customer receives, so they must never pass through a model that could
    # "correct" a tax ID. Sales OA only, since this is a company-management
    # action with no meaning on the Customer or Technician channels.
    if ctx.oa == "sales" and _is_company_profile_view(message):
        return await _handle_company_profile_view(
            client, license_id=license_id, permission_keys=permission_keys,
            language=language,
        )

    company_updates = (
        _parse_company_profile_commands(message) if ctx.oa == "sales" else []
    )
    if company_updates:
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
            last_ref = await client.get_last_entity_ref(ctx.chann_uid, ctx.oa)
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
        if "deal.update" not in set(permission_keys):
            catalog = await client.permission_catalog()
            return ChatReply(text=suggest_what_you_can_do(
                _filter_by_oa(permission_keys, ctx.oa), catalog, language,
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
    if intent.get("entity") == "quote":
        return await _handle_quote_intent(
            client, intent=intent, ctx=ctx, license_id=license_id, language=language,
        )
    if intent.get("entity") == "report":
        return await _handle_sales_summary(
            client, license_id=license_id,
            permission_keys=permission_keys, language=language,
        )
    if intent.get("entity") in ("ticket", "service_report", "followup", "warranty"):
        return await _handle_ai_understood_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language,
        )
    if intent.get("entity") == "line_item":
        return await _handle_line_item_intent(
            client, intent=intent, ctx=ctx, license_id=license_id,
            permission_keys=permission_keys, language=language,
        )
    if intent.get("entity") == "note":
        return await _handle_note_intent(
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
