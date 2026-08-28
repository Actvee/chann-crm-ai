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
from decimal import Decimal, InvalidOperation

from ..data_client import DataClient
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
    # The document exists either way at this point. A signing failure must
    # therefore never read as "issuing failed" — it is a delivery problem
    # with a working fallback, and saying otherwise would invite a re-issue
    # that duplicates a real customer-facing file.
    try:
        url = await get_document_store().signed_url(
            path=str(document.get("output_path") or ""),
            expires_seconds=QUOTE_LINK_TTL_SECONDS,
        )
    except (DocumentStoreError, DocumentStoreNotConfigured):
        log.exception("issued document could not be signed for delivery")
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

# Dashboard paths, resolved to liff.line.me deep links so a tap opens
# inside LINE with the session already established.
DASHBOARD_PATHS = {
    "customers": "/liff/sales/customers",
    "deals": "/liff/sales/deals",
    "products": "/liff/sales/products",
    "quotes": "/liff/sales/quotes",
    "company": "/liff/sales/company",
    "index": "/liff/sales",
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
    if not liff_id or not path:
        return None
    return f"https://liff.line.me/{liff_id}{path}"

CUSTOMER_LIST_PHRASES = ("รายชื่อลูกค้า", "รายการลูกค้า", "ดูลูกค้า", "ลูกค้าทั้งหมด", "customer list")
DEAL_LIST_PHRASES = ("รายการดีล", "รายชื่อดีล", "ดูดีล", "ดีลทั้งหมด", "deal list")
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
        return ChatReply(
            text=_t(NOT_FOUND_BY_CODE, language).format(
                what="ลูกค้า" if language == "th" else "customer", code=code
            )
        )

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


async def _handle_deal_list(
    client: DataClient, *, license_id, permission_keys: list[str], language: str,
    open_only: bool = False,
) -> ChatReply:
    if "deal.read" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        deals = await client.list_deals(str(license_id))
    except Exception:
        log.exception("deal list failed")
        return ChatReply(text=_t(COMPANY_SAVE_FAILED, language))

    if open_only:
        # "Open" means not yet resolved either way. Filtering on the two
        # terminal stages rather than listing the open ones means a stage
        # added later is treated as open by default, which is the safer
        # direction to be wrong in for a work queue.
        deals = [d for d in deals if str(d.get("stage") or "").lower() not in ("won", "lost")]

    if not deals:
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
        return ChatReply(
            text=_t(CUSTOMER_CREATED, language).format(name=f" {_display_name(row)} "),
            entity_type="customer", entity_id=row["id"], intent=intent,
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
        if _matches_phrase(message, DEAL_OPEN_PHRASES):
            return await _handle_deal_list(
                client, license_id=license_id, permission_keys=permission_keys,
                language=language, open_only=True,
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
                permission_keys=permission_keys, language=language,
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
                permission_keys=permission_keys, language=language,
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
