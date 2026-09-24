"""Phase 2 business API: roles, permissions, settings and owner transfer."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator

from .config import settings
from .data_client import DataClient, DataTierError
from .routers_admin import get_data_client, require_admin
from .services import approval as approval_service
from .services import storefront as storefront_service
from .services import csv_import, entitlements, live_chat
from .services.chat_images import with_image_links
from .services.authorization import TenantPrincipal, resolve_tenant_principal
from .services.documents.selection import TEMPLATE_DOCUMENT_TYPES
from .services.identity import member_channel

router = APIRouter(prefix="/api/v1", tags=["phase2"])
log = logging.getLogger(__name__)

# The OOXML media type. Spelled once, because a Word file served as
# application/octet-stream downloads as an unopenable blob on a phone.
DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class RoleWriteIn(BaseModel):
    role_name: str = Field(min_length=1, max_length=64)
    permission_keys: list[str]


class RolePolicyCompileIn(BaseModel):
    policy_prompt: str = Field(min_length=1, max_length=4000)


class SettingWriteIn(BaseModel):
    setting_value: dict | list | str | int | float | bool | None


class MemberRoleWriteIn(BaseModel):
    """`role` is the members page's name for the field; `role_name` the
    roles page's. Either is accepted, unknown fields are ignored."""
    role: str | None = Field(default=None, max_length=64)
    role_name: str | None = Field(default=None, max_length=64)
    # Which OA's row (owner, 8 Sep 2026): the same person may be staff on
    # the Sales OA and a technician on the Technician OA, each with its
    # own role.
    channel: str = Field(default="sales", pattern="^(sales|technician)$")

    @property
    def effective_role(self) -> str:
        return (self.role or self.role_name or "").strip()

    @model_validator(mode="after")
    def _one_role(self):
        if not self.effective_role:
            raise ValueError("role is required")
        return self


class MemberStatusWriteIn(BaseModel):
    status: str = Field(pattern="^(active|removed)$")
    channel: str = Field(default="sales", pattern="^(sales|technician)$")


class MemberResetWriteIn(BaseModel):
    channel: str = Field(default="sales", pattern="^(sales|technician)$")


class TransferRequestIn(BaseModel):
    to_chann_uid: str


class BreakGlassIn(BaseModel):
    target_chann_uid: str


# Kept in Application, not Data. Phase 4 replaces this deterministic explicit
# key compiler with the OpenRouter-backed prompt-config adapter. It never
# guesses permissions from vague prose, which is the only fail-secure Phase 2
# behavior before AI Infrastructure exists.
PERMISSION_KEY_PATTERN = re.compile(r"[a-z_]+(?:\.[a-z_]+)+|reassign_records|view_reports")


async def get_tenant_principal(
    request: Request,
    x_liff_id_token: str = Header(default=""),
    x_liff_audience: str = Header(default="sales"),
    x_license_id: str = Header(default=""),
    client: DataClient = Depends(get_data_client),
) -> TenantPrincipal:
    return await resolve_tenant_principal(
        client,
        x_liff_id_token=x_liff_id_token,
        x_liff_audience=x_liff_audience,
        x_license_id=x_license_id,
        # A suspended shop is read-only (C4): refused here, for every route.
        method=request.method,
    )


def _require_same_tenant(principal: TenantPrincipal, license_id: str) -> None:
    if principal.license_id != license_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant scope mismatch")


def _document_filename(document: dict) -> str:
    """quote-Q-2026-0001.pdf / report-SR-2026-0001.pdf — the document's own
    code, so two downloads do not both land as quote.pdf (review D14)."""
    snapshot = document.get("data_snapshot") or {}
    kind = str(document.get("document_type") or "document")
    if kind == "service_report":
        prefix, code = "report", str((snapshot.get("report") or {}).get("report_id") or "")
    elif kind == "invoice":
        prefix, code = "invoice", str((snapshot.get("invoice") or {}).get("invoice_id") or "")
    elif kind == "receipt":
        prefix, code = "receipt", str((snapshot.get("invoice") or {}).get("invoice_id") or "")
    else:
        prefix, code = ("quote" if kind == "quote" else kind), str((snapshot.get("quote") or {}).get("quote_id") or "")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", code).strip("-")
    return f"{prefix}-{safe}.pdf" if safe else f"{prefix}.pdf"


# Data-tier refusals the sales pages need to say in the reader's language
# (review C11, 6 Sep 2026). The Data tier phrases them once, in English,
# for chat and logs; the page gets a code to look up instead of the prose.
_REASON_CODES = (
    ("no products", "deal_has_no_products"),
    ("cannot move a quote", "quote_transition_not_allowed"),
    ("already pending", "transfer_already_pending"),
    ("must be another member", "transfer_target_is_self"),
    ("only the current owner", "not_owner"),
    ("only the nominated new owner", "not_nominee"),
    ("no longer pending", "transfer_not_pending"),
    ("must be active in the tenant", "member_not_active"),
    ("already exists", "already_exists"),
    # Members page (8 Sep 2026): the owner's row is never removed or
    # demoted; a row is addressed per channel; an invite is for one OA.
    ("owner cannot be removed", "owner_protected"),
    ("not found on this channel", "member_not_on_channel"),
    ("invite is for the", "invite_wrong_oa"),
)


def _with_reason(exc: DataTierError) -> HTTPException:
    """_propagate, plus a `reason_code` when the message is one the UI
    translates. Structured bodies pass through untouched."""
    out = _propagate(exc)
    if isinstance(out.detail, str):
        text = out.detail.lower()
        for needle, code in _REASON_CODES:
            if needle in text:
                out.detail = {"error": code, "reason_code": code, "message": exc.detail}
                break
    return out


def _propagate(exc: DataTierError) -> HTTPException:
    allowed = {400, 404, 409, 422, 503}
    code = exc.status_code if exc.status_code in allowed else 502
    # The structured body when the Data Tier sent one. Some refusals carry
    # data the caller must act on — a duplicate names the existing record
    # so a UI can offer to open it, and the dispatch gate names the fields
    # still missing. exc.detail is the str() of those, which arrives as
    # "{'error': 'duplicate', ...}" and forces the caller to parse a repr.
    if exc.status_code == 403 and entitlements.is_plan_refusal(exc):
        # Round 21D: the Data tier's plan refusal is the caller's answer —
        # every other 403 from the Data tier stays a 502, as before.
        code = 403
    return HTTPException(status_code=code, detail=exc.structured or exc.detail)


@router.post("/licenses/{license_id}/roles/compile-policy")
async def compile_role_policy(
    license_id: str,
    payload: RolePolicyCompileIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
):
    _require_same_tenant(principal, license_id)
    principal.require("role.manage")
    principal.require_feature("feature.custom_roles")
    keys = sorted(set(PERMISSION_KEY_PATTERN.findall(payload.policy_prompt.lower())))
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "ambiguous policy: list explicit permission keys in Phase 2; "
                "AI policy interpretation becomes available in Phase 4"
            ),
        )
    return {
        "permission_keys": keys,
        "compiler": "deterministic_explicit_keys_phase2",
        "ai_used": False,
        "requires_user_confirmation": True,
    }


@router.get("/licenses/{license_id}/roles")
async def list_roles(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("role.manage")
    try:
        return await client.list_roles(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/roles", status_code=201)
async def create_role(
    license_id: str,
    payload: RoleWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("role.manage")
    principal.require_feature("feature.custom_roles")
    try:
        return await client.create_role(license_id, payload.model_dump(mode="json"), actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/roles/{role_name}")
async def update_role(
    license_id: str,
    role_name: str,
    payload: RoleWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("role.manage")
    principal.require_feature("feature.custom_roles")
    try:
        return await client.update_role(license_id, role_name, payload.model_dump(mode="json"), actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete("/licenses/{license_id}/roles/{role_name}", status_code=204)
async def delete_role(
    license_id: str,
    role_name: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("role.manage")
    try:
        await client.delete_role(license_id, role_name, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/members/{chann_uid}/role")
async def set_member_role(
    license_id: str,
    chann_uid: str,
    payload: MemberRoleWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("member.manage")
    try:
        return await client.set_member_role(
            license_id, chann_uid, payload.effective_role, actor_id=principal.chann_uid,
            channel=payload.channel,
        )
    except DataTierError as exc:
        raise _with_reason(exc)


@router.patch("/licenses/{license_id}/members/{chann_uid}/status")
async def set_member_status(
    license_id: str,
    chann_uid: str,
    payload: MemberStatusWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Remove a member from one OA (status "removed" — the row stays for
    the audit trail and can be reactivated) or bring them back. The
    owner's row answers 409 `owner_protected`. A removed technician is
    taken off their teams and their open jobs go back to the queue; the
    dispatchers are told which ones."""
    _require_same_tenant(principal, license_id)
    principal.require("member.manage")
    try:
        member = await client.set_member_status(
            license_id, chann_uid, status=payload.status, channel=payload.channel,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _with_reason(exc)
    unassigned = list(member.pop("unassigned_tickets", None) or [])
    if unassigned:
        await _notify_dispatchers_of_unassigned(client, license_id, chann_uid, unassigned)
    return (await _with_names(client, [member]))[0]


@router.post("/licenses/{license_id}/members/{chann_uid}/reset")
async def reset_member(
    license_id: str,
    chann_uid: str,
    payload: MemberResetWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Forget the member's in-progress chat on one OA — a stuck
    onboarding or a half-finished flow starts clean on their next
    message. The membership itself is untouched."""
    _require_same_tenant(principal, license_id)
    principal.require("member.manage")
    try:
        member = await client.reset_member(
            license_id, chann_uid, channel=payload.channel, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _with_reason(exc)
    return (await _with_names(client, [member]))[0]


async def _notify_dispatchers_of_unassigned(
    client: DataClient, license_id: str, removed_chann_uid: str, tickets: list[dict],
) -> None:
    """Tell everyone who dispatches (ticket.assign, by permission) that a
    removed technician's jobs are back in the queue. Best effort: the
    removal already stands."""
    from .services.notify import send_notification

    numbers = ", ".join(str(t.get("ticket_number") or "") for t in tickets if t.get("ticket_number"))
    try:
        members = await client.list_members(license_id)
    except Exception:  # noqa: BLE001
        log.exception("could not list members to announce unassigned jobs")
        return
    for m in members:
        uid = str(m.get("chann_uid") or "")
        if not uid or uid == removed_chann_uid or str(m.get("status") or "active") != "active":
            continue
        if str(m.get("channel") or "sales") != "sales":
            continue
        try:
            context = await client.authorization_context(license_id, uid, channel="sales")
        except Exception:  # noqa: BLE001
            context = None
        if not context or "ticket.assign" not in set(context.get("permission_keys") or []):
            continue
        try:
            line_target = await client.line_target_of(uid)
            await send_notification(
                client,
                license_id=license_id,
                target_chann_uid=uid,
                target_line_user_id=line_target,
                type="ticket_unassigned",
                message=f"ช่างถูกนำออกจากร้าน งาน {numbers} กลับเข้าคิวรอมอบหมายใหม่",
                message_en=f"A technician was removed; job(s) {numbers} are back in the queue for dispatch",
                entity_type="service_ticket",
                entity_id=str(tickets[0].get("id") or "") if len(tickets) == 1 else None,
            )
        except Exception:  # noqa: BLE001
            log.exception("could not tell %s about unassigned jobs", uid)


@router.get("/licenses/{license_id}/settings")
async def list_settings(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        return await client.list_license_settings(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.put("/licenses/{license_id}/settings/{setting_key}")
async def put_setting(
    license_id: str,
    setting_key: str,
    payload: SettingWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    # Ruling 29: the live-chat minutes are feature.live_chat (403
    # plan_required); every other key stays setting.manage only.
    setting_feature = entitlements.SETTING_KEY_FEATURE.get(setting_key)
    if setting_feature:
        principal.require_feature(setting_feature)
    try:
        return await client.put_license_setting(license_id, setting_key, payload.setting_value, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete("/licenses/{license_id}/settings/{setting_key}", status_code=204)
async def delete_setting(
    license_id: str,
    setting_key: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        await client.delete_license_setting(license_id, setting_key, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/ownership-transfers", status_code=201)
async def request_owner_transfer(
    license_id: str,
    payload: TransferRequestIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    if not principal.is_owner:
        raise HTTPException(status_code=403, detail="only the current owner can transfer ownership")
    try:
        transfer = await client.request_ownership_transfer(
            license_id, principal.chann_uid, payload.to_chann_uid
        )
    except DataTierError as exc:
        raise _with_reason(exc)
    # The nominee hears about it (E6, 6 Sep 2026): notify.py has mapped
    # `transfer_request` to the Sales OA since Phase 6 and nothing ever
    # sent one, so a transfer sat pending until the nominee happened to
    # open the company page. Best-effort — the request stands either way.
    try:
        from .services.notify import send_notification

        await send_notification(
            client,
            license_id=license_id,
            target_chann_uid=payload.to_chann_uid,
            target_line_user_id=await client.line_target_of(payload.to_chann_uid),
            type="transfer_request",
            message="เจ้าของร้านขอโอนความเป็นเจ้าของร้านให้คุณ เปิดเมนูทีมขายแล้วกด \"รับโอน\" เพื่อยืนยัน",
            message_en="The shop owner wants to hand ownership of the shop to you. Open the sales menu and tap \"Accept\" to confirm.",
            entity_type="ownership_transfer",
            entity_id=str(transfer.get("id") or "") or None,
        )
    except Exception:  # noqa: BLE001
        log.exception("could not tell the nominee about an ownership transfer")
    return transfer


@router.get("/licenses/{license_id}/ownership-transfers")
async def list_owner_transfers(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Pending transfers this person is party to: the owner sees the one
    they opened, the nominee sees the one waiting for them. Nobody else
    learns that a handover is under way."""
    _require_same_tenant(principal, license_id)
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        rows = await client.list_ownership_transfers(license_id, status="pending")
    except DataTierError as exc:
        raise _propagate(exc)
    return [
        r for r in rows
        if principal.is_owner or str(r.get("to_chann_uid") or "") == principal.chann_uid
    ]


@router.post("/licenses/{license_id}/ownership-transfers/{transfer_id}/accept")
async def accept_owner_transfer(
    license_id: str,
    transfer_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    try:
        return await client.accept_ownership_transfer(
            license_id, transfer_id, principal.chann_uid, actor_id=principal.chann_uid
        )
    except DataTierError as exc:
        raise _with_reason(exc)


@router.get("/licenses/{license_id}/members")
async def list_members_with_names(
    license_id: str,
    include_removed: bool = False,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Every active member with a display name — the pool an owner picks a
    successor from and a sales group is filled from. Behind the keys
    that manage people, or ownership itself.

    One item per (person, channel): the same chann_uid appears twice when
    they are staff on the Sales OA and a technician on the Technician OA.
    `?include_removed=1` adds the removed rows, so the members page can
    reactivate them."""
    _require_same_tenant(principal, license_id)
    _staff_only(principal)
    if not principal.is_owner:
        # reassign_records too (round 20V): handing a customer or a deal to
        # a colleague means picking the colleague, and the picker is this
        # list. Names only — the same rows the roster shows.
        principal.require_any("member.manage", "team.manage", "role.manage", "reassign_records")
    try:
        members = await client.list_members(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    rows = [
        m for m in members
        if include_removed or str(m.get("status") or "active") == "active"
    ]
    return await _with_names(client, rows)


@router.post("/platform/licenses/{license_id}/break-glass/transfer-owner")
async def platform_break_glass_transfer(
    license_id: str,
    payload: BreakGlassIn,
    claims: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    if "platform.admin.break_glass" not in claims.get("permissions", []):
        raise HTTPException(status_code=403, detail="permission required: platform.admin.break_glass")
    try:
        return await client.force_transfer_owner(license_id, payload.target_chann_uid, actor_id=claims.get("sub"))
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------- Phase 10 company profile


class CompanyProfileWriteIn(BaseModel):
    """Every field optional — this is a partial update.

    `vat_rate_percent` is taken as a PERCENT here (7 means 7%) even though
    the database stores a fraction, because that is what a person types and
    what the UI shows. The conversion happens in one place, below, rather
    than being left to each caller to remember.
    """

    legal_name: str | None = None
    tax_id: str | None = None
    company_address: str | None = None
    company_phone: str | None = None
    company_email: str | None = None
    open_hours: str | None = None
    vat_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)


@router.get("/licenses/{license_id}/company-profile")
async def get_company_profile(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        return await client.get_company_profile(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/company-profile")
async def patch_company_profile(
    license_id: str,
    payload: CompanyProfileWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """`setting.manage`, not a broader key: this is the tenant's own legal
    identity on documents that go to customers, so a member who can create
    a quote still must not be able to change the tax ID printed on it."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")

    # exclude_unset, so omitting a key leaves it alone while sending an
    # explicit null clears it. That distinction is the whole point for
    # vat_rate, where cleared means "no longer VAT-registered".
    body = payload.model_dump(mode="json", exclude_unset=True)
    if "vat_rate_percent" in body:
        percent = body.pop("vat_rate_percent")
        # `mode="json"` hands a Decimal back as a STRING. Round 20d added
        # that to all fifteen dumps to stop Decimals reaching the JSON
        # encoder, and this is the one site that then did ARITHMETIC on the
        # result — so saving VAT has answered 500 ever since with
        # "unsupported operand type(s) for /: 'str' and 'decimal.Decimal'"
        # (owner, 18 ก.ย. 2569). Decimal(str(...)) parses it without going
        # near a float, and the quotient leaves as a string because the very
        # next thing it meets is the JSON encoder that started all this.
        body["vat_rate"] = (
            None if percent is None else str(Decimal(str(percent)) / Decimal(100))
        )

    try:
        return await client.update_company_profile(
            license_id, body, actor_id=principal.chann_uid
        )
    except DataTierError as exc:
        raise _propagate(exc)


# --------------------------------------------- Phase 10 quote PDF rendering


@router.get("/licenses/{license_id}/quotes")
async def list_quotes(
    license_id: str,
    status_filter: str | None = None,
    q: str | None = None,
    deal_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("quote.read")
    try:
        # `deal_id` (round 20X): the invoice form lists one deal's quotes.
        rows, total = await client.list_quotes_with_total(
            license_id, status_filter, limit=limit, q=q, offset=offset, deal_id=deal_id,
        )
        if response is not None:
            response.headers["X-Total-Count"] = str(total)
        return rows
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/quotes/{quote_id}/pdf")
async def render_quote_pdf(
    license_id: str,
    quote_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Render a quote to PDF and return the bytes.

    Deliberately does NOT write a `generated_documents` row. That table's
    whole purpose is proving which file a customer actually received, and
    its `output_path` is NOT NULL because a row without a stored object
    cannot prove anything. Object storage is not provisioned yet
    (`create_application_bucket` is false), so recording here would mean
    writing an audit row that points at nothing — worse than not recording,
    because it would look authoritative later.

    So this endpoint is the review/preview path: it renders the real
    document, through the real provider, from the real frozen snapshot, and
    hands it straight to the person who asked. Issuing (store + record +
    move the quote's status) is the separate step that storage unblocks.
    """
    from fastapi.responses import Response

    from .services.documents.html import render_quote_html
    from .services.documents.snapshot import QuoteNotRenderable, build_quote_snapshot
    from .services.pdf.base import PdfOptions, RendererUnavailable, get_renderer
    from .services.pdf.smartbrowz import SmartBrowzNotConfigured, SmartBrowzRenderError

    _require_same_tenant(principal, license_id)
    principal.require("quote.read")

    try:
        quote = await client.get_quote(license_id, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        deal = await client.get_deal(license_id, str(quote["deal_id"]))
        if deal is None:
            raise HTTPException(status_code=404, detail="deal not found")
        customer = await client.get_customer(license_id, str(deal["contact_id"]))
        if customer is None:
            raise HTTPException(status_code=404, detail="customer not found")
        company = await client.get_company_profile(license_id)
    except DataTierError as exc:
        raise _propagate(exc)

    try:
        snapshot = build_quote_snapshot(
            quote=quote, deal=deal, customer=customer, company=company,
        )
    except QuoteNotRenderable as exc:
        # 409, not 500: nothing is broken, the tenant has not finished
        # filling in details only they can supply. The message names the
        # missing fields so the reply can say what to do next.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_company_incomplete(company, exc),
        )

    renderer = get_renderer("smartbrowz")
    try:
        result = await renderer.render(
            render_quote_html(snapshot), PdfOptions(),
            idempotency_key=f"quote:{license_id}:{quote_id}",
        )
    except SmartBrowzNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except RendererUnavailable as exc:
        # Transient, and the sentence already says "try again".
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except SmartBrowzRenderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not result.content:
        # A render that "succeeded" with no bytes must fail loudly rather
        # than return an empty file the caller might send to a customer.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="renderer returned no document content",
        )

    return Response(
        content=result.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'inline; filename="{quote.get("quote_id") or quote_id}.pdf"',
            # The digest of exactly these bytes, so the reviewer can match a
            # downloaded file against what the server produced even though
            # nothing is recorded yet.
            "X-Document-Sha256": hashlib.sha256(result.content).hexdigest(),
        },
    )


@router.post("/licenses/{license_id}/quotes/{quote_id}/issue")
async def issue_quote(
    license_id: str,
    quote_id: str,
    allow_reissue: bool = False,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Render, store and record a quote as an issued document.

    `quote.update`, not `quote.read`: unlike the preview above this changes
    state — it puts an immutable object in storage and writes an audit row
    asserting the customer was sent exactly those bytes.
    """
    from .services.documents.snapshot import QuoteNotRenderable
    from .services.pdf.base import RendererUnavailable
    from .services.pdf.smartbrowz import SmartBrowzNotConfigured, SmartBrowzRenderError
    from .services.quote_issue import QuoteAlreadyIssued, issue_quote_document
    from .services.storage.base import DocumentStoreError, DocumentStoreNotConfigured

    _require_same_tenant(principal, license_id)
    principal.require("quote.update")

    try:
        quote = await client.get_quote(license_id, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        deal = await client.get_deal(license_id, str(quote["deal_id"]))
        if deal is None:
            raise HTTPException(status_code=404, detail="deal not found")
        customer = await client.get_customer(license_id, str(deal["contact_id"]))
        if customer is None:
            raise HTTPException(status_code=404, detail="customer not found")
        company = await client.get_company_profile(license_id)
    except DataTierError as exc:
        raise _propagate(exc)

    try:
        document = await issue_quote_document(
            client, license_id=license_id, quote=quote, deal=deal,
            customer=customer, company=company, actor_id=principal.chann_uid,
            allow_reissue=allow_reissue,
        )
    except QuoteAlreadyIssued as exc:
        # 409 with a distinct message: the caller can retry with
        # allow_reissue once a human has confirmed, which is not true of the
        # other 409 (incomplete company data) that needs data entry first.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "already_issued", "reason_code": "already_issued", "message": str(exc)},
        )
    except QuoteNotRenderable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_company_incomplete(company, exc),
        )
    except SmartBrowzNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except RendererUnavailable as exc:
        # Transient, and the sentence already says "try again".
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except SmartBrowzRenderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except DocumentStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)

    return {
        "generated_document_id": document.get("id"),
        "output_path": document.get("output_path"),
        "sha256": document.get("sha256"),
        "renderer": document.get("renderer"),
    }


def _company_incomplete(company: dict, exc: Exception) -> dict:
    """The 409 body for a quote that cannot be rendered yet: the fields the
    company profile still lacks, by name, so the page can translate them."""
    return {
        "error": "company_incomplete",
        "reason_code": "company_incomplete",
        "missing": list((company or {}).get("missing_for_documents") or []),
        "message": str(exc),
    }


# ------------------------------------------------ Phase 10 dashboard reads
#
# Read-only projections for the LIFF dashboards. Master Spec 9.2 listed
# these from the start; every earlier phase shipped only the chat side.


@router.get("/licenses/{license_id}/customers")
async def list_customers(
    license_id: str,
    stage: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("customer.read")
    # A linked customer holds customer.read for their OWN history
    # (/deals/mine, /warranties/mine, and get_customer below, which checks
    # the row is theirs). This route is the shop's contact book — every
    # other customer's name and phone number — and it had no such check:
    # reproduced 11 ก.ย. 2569, a customer principal got the full list, 200.
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        rows, total = await client.list_customers_with_total(
            license_id, stage, limit=limit, q=q, offset=offset,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    # The body shape is unchanged — a bare array, as every caller expects.
    # The count rides in a header so a screen can say "200 of 3,000" instead
    # of believing a capped page is the whole book (round 20j).
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/licenses/{license_id}/customers/{customer_id}")
async def get_customer(
    license_id: str,
    customer_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """One customer (review C10, 6 Sep 2026): the detail pages loaded the
    whole list to find one row. A linked customer may read only their own."""
    _require_same_tenant(principal, license_id)
    principal.require("customer.read")
    try:
        row = await client.get_customer(license_id, customer_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if row is None or (
        principal.is_customer
        and str(row.get("customer_chann_uid") or "") != principal.chann_uid
    ):
        raise HTTPException(status_code=404, detail="customer not found")
    return row


# ---------------------------------------------------------------- B5
# The customer's home, spec pages 1–2: the storefront and their history.
class StorefrontInterestBody(BaseModel):
    license_id: str
    product_name: str
    company_name: str | None = None


@router.get("/storefront/products")
async def storefront_products(
    q: str = "",
    limit: int = 20,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Product info across every active shop — the same cross-tenant read
    the chat's "ค้นหา …" makes. Nothing tenant-specific comes back, so any
    signed-in person may look (a staff member browsing is harmless)."""
    try:
        return await storefront_service.search(client, q=q, limit=max(1, min(limit, 50)))
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/storefront/interest", status_code=201)
async def storefront_interest(
    payload: StorefrontInterestBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """"สนใจ" — a lead in the shop the customer picked, and that shop told.
    Customers only: a staff member's tap would create a lead under their
    own identity in someone else's tenant."""
    if not principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "customers_only"})
    try:
        row = await storefront_service.record_interest(
            client, chann_uid=principal.chann_uid, license_id=payload.license_id,
            product_name=payload.product_name, company_name=payload.company_name,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {"id": row.get("id"), "license_id": payload.license_id, "product_name": payload.product_name}


@router.get("/licenses/{license_id}/deals/mine")
async def my_orders(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """This customer's deals in this shop — purchase history (spec page 2).
    Scoped to the caller by construction, like warranties/mine."""
    _require_same_tenant(principal, license_id)
    principal.require("customer.read")
    try:
        return await storefront_service.my_orders(
            client, license_id=license_id, chann_uid=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/deals")
async def list_deals(
    license_id: str,
    stage: str | None = None,
    q: str | None = None,
    contact_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.read")
    try:
        if contact_id:
            # One customer's deals, for the panel beside a conversation
            # (owner, 20 ก.ย. 2569). The Data tier answers this by contact
            # rather than by page; a customer has a handful, not thousands.
            rows = await client.list_deals(license_id, stage, contact_id=contact_id)
            total = len(rows)
        else:
            rows, total = await client.list_deals_with_total(
                license_id, stage, limit=limit, q=q, offset=offset,
            )
    except DataTierError as exc:
        raise _propagate(exc)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/licenses/{license_id}/product-categories")
async def list_product_categories(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The filter's options, from the database rather than from the page.

    Same permission as the catalogue itself: a category name is a fact
    about the catalogue, and anyone who may read one may read the other.
    """
    _require_same_tenant(principal, license_id)
    principal.require_any("product.read", "product.manage")
    try:
        return await client.list_product_categories(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/products")
async def list_products(
    license_id: str,
    category: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """product.read OR product.manage (review C8): a salesperson picking a
    catalogue line for a deal needs the list, not the right to change it.
    product.manage keeps working so roles built before product.read
    existed lose nothing.

    `q` is answered by the database; the screen used to search the page it
    already held, which finds nothing past the ceiling (20 ก.ย. 2569).
    """
    _require_same_tenant(principal, license_id)
    principal.require_any("product.read", "product.manage")
    try:
        rows, total = await client.list_products_with_total(
            license_id, category=category, q=q,
            limit=max(1, min(limit, 1000)), offset=offset,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return rows


# ----------------------------------------------- Phase 10 dashboard writes
#
# The dashboard has to be able to DO the things chat can do, not just show
# them. Each of these is the same domain call the chat handler makes, behind
# the same permission — two front doors onto one set of rules, never two
# implementations of the rules.


class DealStageWriteIn(BaseModel):
    stage: str
    allow_reopen: bool = False
    lost_reason: str | None = None


@router.post("/licenses/{license_id}/deals/{deal_id}/stage")
async def set_deal_stage(
    license_id: str,
    deal_id: str,
    payload: DealStageWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Move a deal along the stage machine.

    Reopening a closed deal needs deal.reopen on top of deal.update — the
    same rule chat enforces. Checked here rather than delegated, because
    the Data tier takes allow_reopen as a parameter and would happily obey
    a caller that simply set it.
    """
    _require_same_tenant(principal, license_id)
    principal.require("deal.update")
    if payload.allow_reopen:
        principal.require("deal.reopen")
    try:
        return await client.transition_deal_stage(
            license_id, deal_id, payload.stage, lost_reason=payload.lost_reason,
            allow_reopen=payload.allow_reopen, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class DealWriteIn(BaseModel):
    notes: str | None = None
    # user review (4 Sep 2026): the dashboard edits these too
    amount: Decimal | None = None
    currency: str | None = None
    expected_close_date: date | None = None
    # review (6 Sep 2026): the detail page offered this field and the
    # edit was dropped on the floor with "saved"
    lost_reason: str | None = None


@router.patch("/licenses/{license_id}/deals/{deal_id}")
async def update_deal(
    license_id: str,
    deal_id: str,
    payload: DealWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.update")
    try:
        return await client.update_deal(
            license_id, deal_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete("/licenses/{license_id}/deals/{deal_id}/products/{deal_product_id}")
async def remove_deal_product(
    license_id: str,
    deal_id: str,
    deal_product_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.update")
    try:
        await client.remove_deal_product(
            license_id, deal_id, deal_product_id, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {"removed": True}


class CustomerWriteIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone_is_a_number(cls, value: str | None) -> str | None:
        from .services.phone import phone_problem

        problem = phone_problem(value)
        if problem == "letters":
            raise ValueError("phone must contain digits only (spaces, dashes, + allowed)")
        if problem == "length":
            raise ValueError("phone must have 9-15 digits")
        return (value or "").strip() or None


@router.patch("/licenses/{license_id}/customers/{customer_id}")
async def update_customer(
    license_id: str,
    customer_id: str,
    payload: CustomerWriteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("customer.update")
    # Staff only, and checked here rather than left to the key list: the
    # customer principal used to carry customer.update, so a linked
    # customer could PATCH ANY row in the shop — reproduced 11 ก.ย. 2569,
    # 200, another person's name and phone rewritten. Their own details go
    # through /api/liff/{audience}/profile, which is scoped by construction.
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        return await client.update_customer(
            license_id, customer_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/customers/{customer_id}/promote")
async def promote_customer(
    license_id: str,
    customer_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Lead -> contact (spec 9.5). customer.update-level, matching chat:
    the spec defines no separate permission for confirming a lead."""
    _require_same_tenant(principal, license_id)
    principal.require("customer.update")
    # Lead -> Contact is the shop's judgement about the shop's pipeline.
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        return await client.promote_customer(
            license_id, customer_id, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/customers/{customer_id}/archive")
async def archive_customer(
    license_id: str,
    customer_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """User review (4 Sep 2026): delete a lead from the dashboard. The
    platform's soft delete — archived rows leave every list and keep their
    history — behind customer.archive, the same key chat checks."""
    _require_same_tenant(principal, license_id)
    principal.require("customer.archive")
    try:
        return await client.archive_customer(license_id, customer_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/deals/{deal_id}/archive")
async def archive_deal(
    license_id: str,
    deal_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The platform's soft delete for a deal, behind deal.archive — the
    same call chat makes after "ยืนยันลบ". Chat could archive a deal and
    the dashboard could not, which the parity rule forbids; it surfaced
    when the owner asked for several at once (20 ก.ย. 2569)."""
    _require_same_tenant(principal, license_id)
    principal.require("deal.archive")
    try:
        return await client.archive_deal(license_id, deal_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/deals/{deal_id}")
async def get_deal(
    license_id: str,
    deal_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.read")
    try:
        deal = await client.get_deal(license_id, deal_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if deal is None:
        raise HTTPException(status_code=404, detail="deal not found")
    return deal


@router.get("/documents/{token}")
async def download_document(
    token: str,
    client: DataClient = Depends(get_data_client),
):
    """Serve an issued document to whoever holds a valid link token.

    Deliberately NOT behind the LIFF guard: this URL is sent into a LINE
    chat and opened by tapping it, where no ID token can be attached. The
    token in the path is the authorisation, and it names one document for a
    limited time — see auth/document_link.py for why GCS signed URLs are not
    used instead.
    """
    from fastapi.responses import Response

    from .auth.document_link import DocumentLinkInvalid, decode_document_token
    from .services.storage.base import (
        DocumentStoreError, DocumentStoreNotConfigured, get_document_store,
    )

    try:
        license_id, document_id = decode_document_token(token)
    except DocumentLinkInvalid as exc:
        # 404 rather than 401: an expired or forged token should not confirm
        # that a document with that id exists.
        raise HTTPException(status_code=404, detail=f"link is not valid: {exc}")

    try:
        document = await client.get_generated_document(license_id, document_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    try:
        content = await get_document_store().get(path=str(document.get("output_path") or ""))
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DocumentStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{_document_filename(document)}"',
            # The digest the audit row recorded, so a recipient can verify
            # the bytes match what the system says it issued.
            "X-Document-Sha256": str(document.get("sha256") or ""),
        },
    )


@router.get("/licenses/{license_id}/documents/{document_id}")
async def get_document_bytes(
    license_id: str,
    document_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """A stored document by its own id.

    Addressed by document rather than by quote so the caller does not
    depend on the quote→document link having been written and read back
    first. When that lagged, someone who had just issued a document was
    told there was not one.
    """
    from fastapi.responses import Response

    from .services.storage.base import (
        DocumentStoreError, DocumentStoreNotConfigured, get_document_store,
    )

    _require_same_tenant(principal, license_id)
    principal.require_any("quote.read", "invoice.read")

    try:
        document = await client.get_generated_document(license_id, document_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if document is None or not await _document_readable_by(client, principal, license_id, document):
        raise HTTPException(status_code=404, detail="document not found")

    try:
        content = await get_document_store().get(path=str(document.get("output_path") or ""))
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DocumentStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{_document_filename(document)}"',
            "X-Document-Sha256": str(document.get("sha256") or ""),
        },
    )


@router.get("/licenses/{license_id}/quotes/{quote_id}/document")
async def get_quote_document(
    license_id: str,
    quote_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The stored document for a quote, as bytes.

    Distinct from /pdf, which RENDERS a fresh preview through SmartBrowz and
    therefore fails with 503 whenever that provider is unavailable. This
    returns the file that was actually issued — the one the audit trail
    names — which needs no renderer at all.
    """
    from fastapi.responses import Response

    from .services.storage.base import (
        DocumentStoreError, DocumentStoreNotConfigured, get_document_store,
    )

    _require_same_tenant(principal, license_id)
    principal.require("quote.read")

    try:
        quote = await client.get_quote(license_id, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        document_id = quote.get("generated_document_id")
        if not document_id:
            raise HTTPException(
                status_code=404, detail="this quote has no issued document yet"
            )
        document = await client.get_generated_document(license_id, str(document_id))
    except DataTierError as exc:
        raise _propagate(exc)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    try:
        content = await get_document_store().get(path=str(document.get("output_path") or ""))
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DocumentStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{_document_filename(document)}"',
            "X-Document-Sha256": str(document.get("sha256") or ""),
        },
    )


@router.get("/licenses/{license_id}/me/permissions")
async def my_permissions(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
):
    """What this person may do in this tenant.

    The dashboard needs it to decide which fields are editable. Showing an
    edit control that will 403 on save is worse than showing the value as
    read-only: the person fills the form, loses the work, and learns nothing
    about why.

    Returns the keys the principal already carries — no extra lookup, and no
    role name, since two tenants can both have a role called "sales" with
    entirely different permissions.
    """
    _require_same_tenant(principal, license_id)
    return {
        "chann_uid": principal.chann_uid,
        "is_owner": principal.is_owner,
        # Round 21D: the keys the person may USE — role grants minus what
        # the shop's plan locks. Every existing page check reads this and
        # is plan-aware without being edited.
        "permission_keys": sorted(principal.permission_keys),
        "license_status": principal.license_status,
        # Round 21D (pre-flight ruling R-C): what the role grants before the
        # plan is applied, and which of those the plan locks. The nav shows
        # a plan-locked entry to anyone holding the permission that would
        # open it (spec §5.4) — `held_keys` tells "no permission" from
        # "plan-locked"; held_keys == permission_keys ∪ plan_locked_keys.
        "held_keys": sorted(principal.permission_keys | principal.plan_locked_keys),
        "plan": principal.plan.as_payload(),
        "plan_locked_keys": sorted(principal.plan_locked_keys),
        # The upgrade contact, for whoever can act on it (the owner, or a
        # holder of setting.manage — spec §8.3).
        "sales_contact": entitlements.sales_contact()
        if (principal.is_owner or "setting.manage" in principal.permission_keys) else None,
    }


@router.get("/licenses/{license_id}/plan")
async def license_plan(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Round 21D — the plan card (company page) and the members page's
    "ผู้ใช้ n/limit": the plan and how much of it is used. Chat's twin is
    ("read", "plan")."""
    _require_same_tenant(principal, license_id)
    principal.require_any("setting.manage", "member.manage")
    try:
        out = await client.license_plan(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return {"plan": entitlements.PlanView.from_payload(out.get("plan")).as_payload(),
            "usage": out.get("usage") or {}}


# ------------------------------------------------------------ products (write)


class ProductIn(BaseModel):
    product_id: str
    product_name: str
    sku: str | None = None
    category: str | None = None
    unit_price: str | float | None = None
    description: str | None = None
    # The product's own warranty period, the default for every unit
    # registered under it (0030).
    warranty_months: int | None = None


class CsvBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)


def _csv_rejected(exc: csv_import.CsvRejected) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": "csv_rejected", "message": str(exc)},
    )


@router.post("/licenses/{license_id}/customers/import")
async def import_customers(
    license_id: str,
    payload: CsvBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Leads from a spreadsheet (user review, 4 Sep 2026): one verdict per
    row, duplicates named, bad phone numbers refused."""
    _require_same_tenant(principal, license_id)
    principal.require("customer.create")
    try:
        return await csv_import.import_customers(
            client, license_id=license_id, text=payload.csv, actor_id=principal.chann_uid,
        )
    except csv_import.CsvRejected as exc:
        raise _csv_rejected(exc)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/products/import")
async def import_products(
    license_id: str,
    payload: CsvBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """A spreadsheet export into the catalogue (owner, 4 Sep): one verdict
    per row; the file as a whole is refused only when it cannot be read."""
    _require_same_tenant(principal, license_id)
    principal.require("product.manage")
    try:
        return await csv_import.import_products(
            client, license_id=license_id, text=payload.csv, actor_id=principal.chann_uid,
        )
    except csv_import.CsvRejected as exc:
        raise _csv_rejected(exc)
    except DataTierError as exc:
        raise _propagate(exc)


@router.put("/licenses/{license_id}/products/{product_id}")
async def upsert_product(
    license_id: str,
    product_id: str,
    payload: ProductIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Create or update a product from the dashboard.

    The list endpoint has existed since Phase 7 with no way to add
    anything, so the catalogue could only be built through chat — which is
    fine for one product and miserable for twenty.
    """
    _require_same_tenant(principal, license_id)
    principal.require("product.manage")
    try:
        return await client.upsert_product(
            license_id, product_id, payload.model_dump(mode="json", exclude_none=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/products/{product_id}/archive")
async def archive_product(
    license_id: str,
    product_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Retire a product from the catalogue.

    Spec 7.5 requires delete to BE an archive, and the Data tier has done it
    that way since Phase 7 — `products.archived_at`, five foreign keys into
    products.id, and a partial index for the active ones. What was missing
    was any way to reach it: no Application route, no dashboard control, and
    in chat "ลบสินค้า FAN001" was claimed by the road that removes a line
    from a DEAL (owner's backlog, 11 ก.ย. 2569).

    POST .../archive rather than DELETE .../{id}: the verb is the archive,
    the row survives, and check-parity reads the trailing segment as the
    action — a bare DELETE would be recorded as "delete", which this is not.
    """
    _require_same_tenant(principal, license_id)
    principal.require("product.manage")
    try:
        return await client.archive_product(
            license_id, product_id, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


# ----------------------------------------------------------------- warranties


class WarrantyClaimIn(BaseModel):
    serial_number: str


class WarrantyRegisterIn(BaseModel):
    serial_number: str
    product_id: str | None = None
    product_name: str | None = None
    # Staff registering a sold unit may name the customer record it
    # belongs to; the customer later claims it by serial (3 Sep).
    contact_id: str | None = None
    customer_chann_uid: str | None = None
    # The Data Tier's own names: coverage starts on the purchase date and
    # runs warranty_months. Inventing "purchase_date" here would have
    # been the MemberOut-never-sends-id seam bug again, one tier over.
    warranty_start: str | None = None
    warranty_months: int | None = None


@router.post("/licenses/{license_id}/warranties/import")
async def import_warranties(
    license_id: str,
    payload: CsvBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The register of sold units from a spreadsheet (owner, 4 Sep). A
    serial the shop already holds is reported as a duplicate, not lost."""
    _require_same_tenant(principal, license_id)
    principal.require("warranty.create")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "staff_only"})
    try:
        return await csv_import.import_warranties(
            client, license_id=license_id, text=payload.csv, actor_id=principal.chann_uid,
        )
    except csv_import.CsvRejected as exc:
        raise _csv_rejected(exc)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/warranties", status_code=201)
async def register_warranty(
    license_id: str,
    payload: WarrantyRegisterIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """A customer registering their own purchase from the LIFF dashboard.

    Chat has registered warranties since the warranty phase landed; the
    dashboard had no route at all, so the customer home screen the owner
    asked for (2 Sep) had nothing to submit to.
    """
    _require_same_tenant(principal, license_id)
    principal.require("warranty.create")
    if principal.is_customer:
        # Owner rule (3 Sep): a customer cannot invent a unit. Their
        # "register" is a claim on a serial the shop recorded.
        try:
            claimed = await client.claim_warranty(
                license_id,
                {"serial_number": payload.serial_number,
                 "customer_chann_uid": principal.chann_uid,
                 "warranty_start": payload.warranty_start},
                actor_id=principal.chann_uid,
            )
        except DataTierError as exc:
            raise _propagate(exc)
        await _announce_registration(client, license_id, claimed)
        return claimed
    try:
        return await client.register_warranty(
            license_id,
            {
                "serial_number": payload.serial_number,
                "product_id": payload.product_id,
                "product_name": payload.product_name,
                "contact_id": payload.contact_id,
                "warranty_start": payload.warranty_start,
                "warranty_months": payload.warranty_months,
                "customer_chann_uid": payload.customer_chann_uid,
            },
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/warranties/{warranty_id}")
async def set_warranty_purchase(
    license_id: str,
    warranty_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The purchase date given after registration, from the warranties
    page (0030) — chat says "วันที่ซื้อ SN… 1 ก.ย. 2569"."""
    _require_same_tenant(principal, license_id)
    principal.require("warranty.update")
    try:
        return await client.update_warranty(
            license_id, warranty_id,
            {
                "warranty_start": payload.get("warranty_start"),
                "warranty_months": payload.get("warranty_months"),
                # Round 19t: the end date itself, for cover that does not
                # follow from start + period.
                "warranty_end": payload.get("warranty_end"),
            },
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


async def _announce_registration(client: DataClient, license_id: str, warranty: dict) -> None:
    """Round 20Z — tell the shop when a registration put someone new on
    its customer list. Best effort: the unit is already registered."""
    from .services.onboarding import after_warranty_registered

    try:
        await after_warranty_registered(client, license_id=str(license_id), warranty=warranty)
    except Exception:  # noqa: BLE001
        log.exception("could not announce the customer behind a registration")


@router.post("/licenses/{license_id}/warranties/claim")
async def claim_warranty(
    license_id: str,
    payload: WarrantyClaimIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The customer attaching themselves to a unit the shop registered —
    404 when the shop has no such serial, 409 when another customer holds
    it. Same call chat's "ลงทะเบียนสินค้า" makes on the customer OA."""
    _require_same_tenant(principal, license_id)
    principal.require("warranty.create")
    try:
        claimed = await client.claim_warranty(
            license_id,
            {"serial_number": payload.serial_number, "customer_chann_uid": principal.chann_uid},
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    await _announce_registration(client, license_id, claimed)
    return claimed


@router.get("/licenses/{license_id}/warranties")
async def list_warranties(
    license_id: str,
    serial_number: str | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The shop's book of registered units (staff). A customer principal
    gets only their own rows, the same as /mine.

    `serial_number` stays an exact lookup — it answers "this unit". `q` is
    the shop searching its own book (20 ก.ย. 2569).
    """
    _require_same_tenant(principal, license_id)
    principal.require("warranty.read")
    try:
        if principal.is_customer:
            return await client.list_warranties(license_id, customer_chann_uid=principal.chann_uid)
        if serial_number:
            return await client.list_warranties(license_id, serial_number=serial_number)
        rows, total = await client.list_warranties_with_total(
            license_id, q=q,
            limit=max(1, min(limit, 500)) if limit else None, offset=offset,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/licenses/{license_id}/warranties/mine")
async def my_warranties(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    # Scoped to the caller by construction — a customer sees their own
    # registrations, never the shop's whole book.
    _require_same_tenant(principal, license_id)
    principal.require("warranty.read")
    try:
        return await client.list_warranties(
            license_id, customer_chann_uid=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/service-reports/{report_id}/document")
async def issue_service_report_document(
    license_id: str,
    report_id: str,
    payload: dict | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The report's PDF (13.4/13.5): produced at approval, and here on
    demand — the existing document's link, or a fresh issue when the
    report has none (or `reissue` is asked for). Same call chat's
    "ออกรายงาน SR-…" makes."""
    from .services.chat import document_download_url
    from .services.report_issue import ReportAlreadyIssued, ReportNotApproved, issue_for_report

    _require_same_tenant(principal, license_id)
    # A customer holds ticket.read, not service_report.read: they may open
    # the paper for their own job (review D4, 6 Sep 2026), never issue it.
    principal.require("ticket.read" if principal.is_customer else "service_report.read")
    reissue = bool((payload or {}).get("reissue")) and not principal.is_customer
    try:
        rows = await client.list_service_reports(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    report = next((r for r in rows if str(r.get("id")) == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    if principal.is_customer:
        # The report row carries no customer; its ticket does.
        try:
            tickets = await client.list_tickets(license_id)
        except DataTierError as exc:
            raise _propagate(exc)
        if not any(
            str(t.get("id")) == str(report.get("ticket_id") or "")
            and str(t.get("customer_chann_uid") or "") == principal.chann_uid
            for t in tickets
        ):
            raise HTTPException(status_code=404, detail="report not found")
    document_id = str(report.get("generated_document_id") or "")
    if principal.is_customer and not document_id:
        raise HTTPException(status_code=409, detail={"error": "not_issued", "message": "no document has been issued for this report"})
    if document_id and not reissue:
        document = await client.get_generated_document(license_id, document_id) or {}
    else:
        try:
            document = await issue_for_report(
                client, license_id=license_id, report_id=report_id,
                actor_id=principal.chann_uid, allow_reissue=reissue,
            )
        except ReportNotApproved as exc:
            raise HTTPException(status_code=409, detail={"error": "not_approved", "message": str(exc)})
        except ReportAlreadyIssued as exc:
            raise HTTPException(status_code=409, detail={"error": "already_issued", "message": str(exc)})
        except DataTierError as exc:
            raise _propagate(exc)
        except Exception as exc:  # noqa: BLE001 — provider/storage failure, phrased for the page
            log.exception("service report document failed")
            raise HTTPException(status_code=502, detail={"error": "render_failed", "message": str(exc)[:200]})
    return {
        "document_id": str(document.get("id") or ""),
        "sha256": str(document.get("sha256") or ""),
        "url": document_download_url(license_id, str(document.get("id") or "")),
    }


# ----------------------------------------------------------------- approvals
#
# Phase 14-B. Every route here is a thin wrapper around services/approval.py,
# which is also what chat calls — Master Spec 14.6's chat-vs-dashboard
# parity is a property of the code, not of a test that hopes the two
# paths stayed in step.


@router.get("/licenses/{license_id}/approvals/pending")
async def pending_approvals(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """What is waiting on THIS person: the step, the report and its ticket."""
    _require_same_tenant(principal, license_id)
    principal.require("approval.view")
    try:
        steps = await approval_service.pending_for_actor(
            client, license_id=license_id, chann_uid=principal.chann_uid,
        )
        if not steps:
            return []
        reports = {str(r.get("id")): r for r in await client.list_service_reports(license_id)}
        tickets = {str(t.get("id")): t for t in await client.list_tickets(license_id)}
    except DataTierError as exc:
        raise _propagate(exc)
    out = []
    for step in steps:
        report = reports.get(str(step.get("entity_id")))
        ticket = tickets.get(str((report or {}).get("ticket_id") or "")) if report else None
        out.append({"step": step, "report": report, "ticket": ticket})
    return out


@router.post("/licenses/{license_id}/approvals/{step_id}/approve")
async def approve_step(
    license_id: str,
    step_id: str,
    payload: dict | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("approval.approve")
    try:
        return await approval_service.act(
            client, license_id=license_id, step_id=step_id, approve=True,
            actor_chann_uid=principal.chann_uid,
            reason=str((payload or {}).get("reason") or "") or None,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/approvals/{step_id}/reject")
async def reject_step(
    license_id: str,
    step_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("approval.reject")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        # The technician has to be told what to fix; a reject with no
        # reason is a dead end for them. Chat enforces the same.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "reason_required"},
        )
    try:
        return await approval_service.act(
            client, license_id=license_id, step_id=step_id, approve=False,
            actor_chann_uid=principal.chann_uid, reason=reason,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/approval-workflows/{entity_type}")
async def get_approval_workflow(
    license_id: str,
    entity_type: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("approval.view")
    try:
        workflow = await approval_service.current_workflow(
            client, license_id=license_id, entity_type=entity_type,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {
        **(workflow or {}),
        "summary": approval_service.describe_workflow((workflow or {}).get("rules_json") or {}),
    }


@router.put("/licenses/{license_id}/approval-workflows/{entity_type}")
async def put_approval_workflow(
    license_id: str,
    entity_type: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Replace the flow — from a typed policy (the same model call chat
    uses) or from structured rules the config page assembled."""
    from .services.ai.approval_policy import policy_to_workflow, validate_workflow

    _require_same_tenant(principal, license_id)
    principal.require("approval.manage")
    try:
        roles = [str(r.get("role_name")) for r in await client.list_roles(license_id) if r.get("role_name")]
    except DataTierError as exc:
        raise _propagate(exc)

    policy = str(payload.get("policy") or "").strip()
    rules = payload.get("rules_json")
    if policy:
        rules, problems = await policy_to_workflow(policy, roles=roles)
        if rules is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "policy_not_understood", "problems": problems},
            )
    elif isinstance(rules, dict):
        problems = validate_workflow(rules, roles=roles)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "workflow_invalid", "problems": problems},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "policy_or_rules_required"},
        )
    try:
        saved = await approval_service.replace_workflow(
            client, license_id=license_id, rules_json=rules,
            actor_chann_uid=principal.chann_uid, entity_type=entity_type,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {**(saved or {}), "summary": approval_service.describe_workflow(rules)}


# ---------------------------------------------------------------------------
# Round 20V — four things that were built and that no person could reach
# (owner, 21 ก.ย. 2569: "ทำได้ในโค้ด แต่คนหาไม่เจอ"). Assignment rules had
# Data routes and a chat command to SET one and nothing to show or switch
# one off; the satisfaction surveys were collected and never read back.


def _rule_with_words(row: dict | None) -> dict | None:
    from .services.ai.assignment_policy import describe_rule

    if not row:
        return None
    return {**row, "summary": describe_rule(row.get("rules_json") or {}, "th")}


@router.get("/licenses/{license_id}/assignment-rules")
async def list_assignment_rules(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The active rule per scope, in words as well as JSON. setting.manage
    — the same key chat's "ตั้งกฎมอบหมาย" checks, because the spec gives
    assignment rules no permission of their own."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        rows = await client.get_assignment_rules(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return [_rule_with_words(r) for r in rows if r.get("is_active")]


@router.put("/licenses/{license_id}/assignment-rules")
async def put_assignment_rule(
    license_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Replace the active rule for a scope — from a typed policy (the same
    model call chat uses) or from the rule JSON the company page edited.
    Either way the rule is validated against the engine's closed
    vocabulary and its teams must exist, exactly as chat insists."""
    from .services.ai.assignment_policy import policy_to_rule, team_problems
    from .services.assignment_validation import validate_rule

    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        teams = [str(t.get("team_name")) for t in await client.list_technician_teams(license_id) if t.get("team_name")]
        groups = [str(g.get("group_name")) for g in await client.list_sales_groups(license_id) if g.get("group_name")]
    except DataTierError as exc:
        raise _propagate(exc)

    policy = str(payload.get("policy") or "").strip()
    rule = payload.get("rules_json")
    scope_hint = str(payload.get("scope") or "").strip() or None
    if policy:
        rule, problems = await policy_to_rule(
            policy, teams=teams, sales_groups=groups, scope_hint=scope_hint,
        )
        if rule is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "policy_not_understood", "problems": problems},
            )
    elif isinstance(rule, dict):
        rule = dict(rule)
        rule.setdefault("version", 1)
        if scope_hint:
            rule["scope"] = scope_hint
        problems = validate_rule(rule) or team_problems(rule, teams=teams, sales_groups=groups)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "rule_invalid", "problems": problems},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "policy_or_rules_required"},
        )
    # Ruling 26 (round 21D task 11): a technician-scope rule belongs to
    # feature.service — setting.manage is always on, so the plan is the
    # gate here, not the permission key.
    scope_feature = entitlements.ASSIGNMENT_RULE_SCOPE_FEATURE.get(str(rule.get("scope") or "technician"))
    if scope_feature:
        principal.require_feature(scope_feature)
    try:
        saved = await client.upsert_assignment_rule(
            license_id, scope=str(rule.get("scope") or "technician"), rules_json=rule,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _rule_with_words(saved)


@router.delete("/licenses/{license_id}/assignment-rules/{rule_scope}")
async def delete_assignment_rule(
    license_id: str,
    rule_scope: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Switch the rule for a scope off. 404 when none is active."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    if rule_scope not in ("technician", "sales"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"error": "scope_invalid"})
    try:
        row = await client.deactivate_assignment_rule(license_id, rule_scope, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "no_active_rule"})
    return _rule_with_words(row)


@router.get("/licenses/{license_id}/surveys/summary")
async def survey_summary(
    license_id: str,
    days: int = 30,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The satisfaction figures for the reports page and chat: view_reports,
    like the AI reports beside it."""
    _require_same_tenant(principal, license_id)
    principal.require("view_reports")
    principal.require_feature("feature.service")
    if days not in (30, 90, 365):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"error": "days_invalid"})
    try:
        return await client.survey_summary(license_id, days=days)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/surveys/pending")
async def pending_survey(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The customer's own unanswered survey, for the home-screen card
    (parity with the quick reply chat pushes)."""
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    try:
        survey, ticket = await approval_service.pending_survey_for_customer(
            client, license_id=license_id, customer_chann_uid=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {"survey": survey, "ticket": ticket}


@router.post("/licenses/{license_id}/surveys/{survey_id}/answer")
async def answer_survey(
    license_id: str,
    survey_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    try:
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"error": "score_required"},
        )
    try:
        return await approval_service.answer_survey(
            client, license_id=license_id, survey_id=survey_id, score=score,
            comment=str(payload.get("comment") or "") or None,
            actor_chann_uid=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------------------- tickets
#
# Phase 12/13 built these in the Data tier and nowhere else, so every
# dashboard call returned 404 through the proxy. The Data tier is not
# reachable from a browser — everything the dashboard uses has to exist
# here too.


@router.get("/licenses/{license_id}/tickets")
async def list_tickets(
    license_id: str,
    status: str | None = None,
    visible_to: str | None = None,
    q: str | None = None,
    contact_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    if _field_scoped(principal):
        # A technician's list is what a technician may see, whatever the
        # query said: the Data tier warns that without visible_to the
        # address and phone of every private job come back, and the
        # reports page called this route bare (review D3, 6 Sep 2026).
        visible_to = await _member_of(client, license_id, principal)
    try:
        rows, total = await client.list_tickets_with_total(
            license_id, status=status, visible_to=visible_to, q=q, contact_id=contact_id,
            limit=max(1, min(limit, 500)) if limit else None, offset=offset,
        )
        if principal.is_customer:
            # A customer sees their own repairs, never the shop's queue.
            rows = [r for r in rows if str(r.get("customer_chann_uid") or "") == principal.chann_uid]
            # ...so the tenant's total is not theirs to be told, either. A
            # count from the server here would say how many jobs the shop
            # has (20 ก.ย. 2569).
            total = len(rows)
        if response is not None:
            response.headers["X-Total-Count"] = str(total)
        # The machine each job is about, composed here rather than stored
        # on the ticket: `TicketOut` is the ticket's own row, and a copy of
        # the product name on it would be a second thing to keep in step
        # (owner, 10 ก.ย. 2569). One warranty-book read for the whole list.
        from .services import ticket_machine

        return await ticket_machine.attach_to(client, license_id, rows)
    except DataTierError as exc:
        raise _propagate(exc)


def _field_scoped(principal: TenantPrincipal) -> bool:
    """A field technician, as opposed to a dispatcher: staff without
    `customer.read`. The technician role template is defined by that
    absence (permissions.py: "a field technician has no business with
    customer records"), and a person who may not open a customer record
    may not read the shop's whole queue of names and addresses either.
    Owners, admins and CS all hold customer.read."""
    return not principal.is_customer and "customer.read" not in principal.permission_keys


async def _member_of(client: DataClient, license_id: str, principal: TenantPrincipal) -> str:
    """The caller's own member id. Every ticket action used to take
    `member_id` from the request body, so anyone with ticket.update could
    claim, check in and file a service report as a colleague (review, 6
    Sep 2026)."""
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        # The row of the OA this app is for: a ticket is assigned to the
        # technician row, and the sales row of the same person is a
        # different member (owner, 8 Sep 2026).
        member = await client.get_member(
            license_id, principal.chann_uid, channel=member_channel(principal.audience),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    member_id = str((member or {}).get("id") or "")
    if not member_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not a member of this shop")
    return member_id


def _staff_only(principal: TenantPrincipal) -> None:
    """Routes that list the shop's people or dispatch state are not for a
    linked customer, whose ticket.read is scoped to their own jobs."""
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")


def _owner_only(principal: TenantPrincipal) -> None:
    """Round 21B: API keys are the owner's alone — an admin with
    setting.manage runs the shop's settings, not its outside access."""
    if not principal.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "error": "owner_only", "reason_code": "owner_only",
            "message": "only the shop owner manages API keys",
        })


@router.get("/licenses/{license_id}/tickets/{ticket_id}/dispatch-check")
async def ticket_dispatch_check(
    license_id: str,
    ticket_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    _staff_only(principal)
    try:
        return await client.ticket_dispatch_check(license_id, ticket_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/tickets/{ticket_id}/claim")
async def claim_ticket(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    member_id = await _member_of(client, license_id, principal)
    try:
        return await client.claim_ticket(
            license_id, ticket_id, member_id,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class PhotoUploadIn(BaseModel):
    """A data: URL from the browser (FileReader / canvas) — JSON, so no
    multipart dependency and the same shape the signature uses."""
    image: str
    photo_type: str = "evidence"
    # What the list calls it. The dashboard sends the file's own name.
    caption: str | None = None


class PhotoPatchIn(BaseModel):
    caption: str | None = None


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    import base64

    head, _, body = (data_url or "").partition(",")
    if not head.startswith("data:") or not body:
        raise HTTPException(status_code=422, detail="image must be a data: URL")
    content_type = head[5:].split(";")[0] or "image/jpeg"
    try:
        return base64.b64decode(body), content_type
    except Exception:
        raise HTTPException(status_code=422, detail="image is not valid base64")


@router.get("/licenses/{license_id}/tickets/{ticket_id}/photos")
async def ticket_photos(
    request: Request,
    license_id: str,
    ticket_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """13.1 — the job's pictures with an hour-long link each. A customer
    sees only their own job's."""
    from .services.photos import photo_links

    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    if principal.is_customer:
        rows = await client.list_tickets(license_id)
        if not any(str(t.get("id")) == ticket_id and str(t.get("customer_chann_uid") or "") == principal.chann_uid for t in rows):
            raise HTTPException(status_code=404, detail="ticket not found")
    # The request origin is the fallback when PUBLIC_BASE_URL is unset (dev),
    # so the gallery is never a list of unreachable links.
    return await photo_links(client, license_id=license_id, ticket_id=ticket_id,
                             base_url=str(request.base_url))


@router.post("/licenses/{license_id}/tickets/{ticket_id}/photos", status_code=201)
async def upload_ticket_photo(
    license_id: str,
    ticket_id: str,
    payload: PhotoUploadIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """13.1 — a picture from the home screen (the twin of sending one in
    chat). Technicians attach to any job they can see; a customer to
    their own."""
    from .services.photos import PhotoRefused, store_ticket_photo

    _require_same_tenant(principal, license_id)
    principal.require("ticket.create" if principal.is_customer else "ticket.update")
    member_id = None
    if principal.is_customer:
        rows = await client.list_tickets(license_id)
        if not any(str(t.get("id")) == ticket_id and str(t.get("customer_chann_uid") or "") == principal.chann_uid for t in rows):
            raise HTTPException(status_code=404, detail="ticket not found")
    else:
        try:
            member = await client.get_member(
                license_id, principal.chann_uid, channel=member_channel(principal.audience),
            )
            member_id = str((member or {}).get("id") or "") or None
        except Exception:  # noqa: BLE001
            member_id = None
    content, content_type = _decode_data_url(payload.image)
    photo_type = payload.photo_type if payload.photo_type in ("checkin", "checkout", "evidence") else "evidence"
    try:
        return await store_ticket_photo(
            client, license_id=license_id, ticket_id=ticket_id, content=content,
            content_type=content_type, photo_type=photo_type, uploaded_by_member_id=member_id,
            caption=payload.caption,
        )
    except PhotoRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)


async def _photo_of_my_ticket(client: DataClient, principal: TenantPrincipal, license_id: str, ticket_id: str) -> None:
    """A customer may only touch pictures on their own job."""
    if not principal.is_customer:
        return
    rows = await client.list_tickets(license_id)
    if not any(
        str(t.get("id")) == ticket_id and str(t.get("customer_chann_uid") or "") == principal.chann_uid
        for t in rows
    ):
        raise HTTPException(status_code=404, detail="ticket not found")


@router.patch("/licenses/{license_id}/tickets/{ticket_id}/photos/{photo_id}")
async def name_ticket_photo(
    license_id: str,
    ticket_id: str,
    photo_id: str,
    payload: PhotoPatchIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """13.1 — what the picture is called in the list."""
    _require_same_tenant(principal, license_id)
    # ticket.update for everyone, customers included — which no customer
    # holds. Attaching a picture is theirs to do; what is already on the
    # job is the shop's record, and chat draws the same line.
    principal.require("ticket.update")
    await _photo_of_my_ticket(client, principal, license_id, ticket_id)
    try:
        return await client.name_ticket_photo(
            license_id, ticket_id, photo_id, payload.caption, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete("/licenses/{license_id}/tickets/{ticket_id}/photos/{photo_id}")
async def delete_ticket_photo(
    license_id: str,
    ticket_id: str,
    photo_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """13.1 — take a picture off the job (owner, 16 ก.ย. 2569: attaching
    one was possible from the first day, removing one never was)."""
    from .services.photos import remove_ticket_photo

    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    await _photo_of_my_ticket(client, principal, license_id, ticket_id)
    try:
        return await remove_ticket_photo(
            client, license_id=license_id, ticket_id=ticket_id, photo_id=photo_id,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/tickets/{ticket_id}/reject")
async def reject_ticket(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """12.4: the technician a job was given to says no. It returns to the
    dispatcher's queue and is never passed on automatically. The CS owner
    is told by chat's handler; this route is the home screen's twin."""
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    member_id = await _member_of(client, license_id, principal)
    try:
        row = await client.reject_ticket(
            license_id, ticket_id, member_id,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    try:
        from .services.chat import notify_ticket_rejected
        await notify_ticket_rejected(
            client, license_id, row, reason=str(payload.get("reason") or ""),
        )
    except Exception:  # noqa: BLE001 — the rejection stands; the notice is best effort
        log.exception("could not tell the dispatcher about a rejected ticket")
    return row


@router.post("/licenses/{license_id}/tickets/{ticket_id}/check-out")
async def check_out_ticket(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Finish a visit from the technician dashboard.

    The screen could start a visit (check-in) but not end one — chat
    reached the Data Tier directly, so no Application route existed for
    the dashboard to call, and check-out sat on the parity backlog until
    the owner asked for a technician home screen (2 Sep).
    """
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    member_id = await _member_of(client, license_id, principal)
    try:
        report = await client.check_out_ticket(
            license_id, ticket_id,
            member_id=member_id,
            # Check-out IS the service report: the Data Tier writes the
            # report row in the same transaction as the status change, so
            # the two can never disagree about whether a visit happened.
            report_data=payload.get("report_data") or {},
            gps_lat=payload.get("gps_lat"), gps_lng=payload.get("gps_lng"),
            photo_url=None,  # photos arrive through the upload route, never as a caller-named path
            actor_id=principal.chann_uid,
        )
        # Literally the same hook chat calls — open the approval steps,
        # tell the first approver, and tell the CUSTOMER their job is
        # finished. Calling `on_report_submitted` here instead meant this
        # screen and chat each carried their own idea of what follows a
        # check-out, and the customer notice would have had to be written
        # twice. Best-effort: the check-out is committed, and a LINE
        # failure must not turn it into a 500.
        try:
            from .services.chat import after_check_out
            await after_check_out(client, license_id, report)
        except Exception:  # noqa: BLE001
            log.exception("the follow-up to check-out %s could not be completed", report.get("report_id"))
        return report
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/tickets/{ticket_id}/check-in")
async def check_in_ticket(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    member_id = await _member_of(client, license_id, principal)
    try:
        return await client.check_in_ticket(
            license_id, ticket_id,
            member_id=member_id,
            gps_lat=payload.get("gps_lat"), gps_lng=payload.get("gps_lng"),
            photo_url=None,  # photos arrive through the upload route, never as a caller-named path
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/service-reports")
async def list_service_reports(
    license_id: str,
    status: str | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    try:
        rows = await client.list_service_reports(license_id, status=status)
        if principal.is_customer:
            # Their own jobs' reports only — a linked customer could read
            # every report in the shop (review, 6 Sep 2026).
            mine = {
                str(t.get("id")) for t in await client.list_tickets(license_id)
                if str(t.get("customer_chann_uid") or "") == principal.chann_uid
            }
            rows = [r for r in rows if str(r.get("ticket_id") or "") in mine]
        elif _field_scoped(principal):
            # "รายงานของฉัน" means mine: the reports I filed, and those on
            # jobs given to me — not every visit in the shop, with each
            # customer's address on it (review D3, 6 Sep 2026).
            member_id = await _member_of(client, license_id, principal)
            assigned = {
                str(t.get("id")) for t in await client.list_tickets(license_id, visible_to=member_id)
                if str(t.get("assigned_to_ref") or "") == member_id
            }
            rows = [
                r for r in rows
                if str(r.get("technician_member_id") or "") == member_id
                or str(r.get("ticket_id") or "") in assigned
            ]
        return rows
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------------ Round 20V
# Invoices, payments and receipts — the step after the quotation. Owner,
# 21 ก.ย. 2569: "ทำข้อ 2 … รวมเอาเรื่อง invoice". A customer principal may
# list and read only the bills whose contact carries their chann_uid, and
# never writes; staff act by the four invoice.* keys.


async def _document_readable_by(
    client: DataClient, principal: TenantPrincipal, license_id: str, document: dict,
) -> bool:
    """Staff read any of the shop's documents. A customer reads only the
    invoice and receipt PDFs of their OWN bills — found through the
    contact row, the way tickets and warranties are scoped."""
    if not principal.is_customer:
        return True
    if str(document.get("source_entity_type") or "") != "invoice":
        return False
    try:
        invoice = await client.get_invoice(license_id, str(document.get("source_entity_id") or ""))
    except DataTierError:
        return False
    return invoice is not None and await _invoice_is_mine(client, principal, license_id, invoice)


async def _invoice_is_mine(
    client: DataClient, principal: TenantPrincipal, license_id: str, invoice: dict,
) -> bool:
    if not invoice.get("contact_id"):
        return False
    try:
        customer = await client.get_customer(license_id, str(invoice["contact_id"]))
    except DataTierError:
        return False
    return bool(customer) and str(customer.get("customer_chann_uid") or "") == principal.chann_uid


async def _invoice_or_404(client: DataClient, principal: TenantPrincipal, license_id: str, invoice_id: str) -> dict:
    try:
        invoice = await client.get_invoice(license_id, invoice_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if invoice is None or (principal.is_customer and not await _invoice_is_mine(client, principal, license_id, invoice)):
        raise HTTPException(status_code=404, detail="invoice not found")
    return invoice


async def _invoice_customer_has_line(client: DataClient, license_id: str, invoice: dict) -> bool | None:
    """Round 21C, ruling 18: the same question `services/document_send.py`'s
    `send_document_to_customer` answers before it pushes a document (a
    customer that is not linked raises `CustomerNotLinked`) — answered
    once here, on the invoice's own GET, so the dashboard's send button
    reads it straight off the invoice instead of asking again with a
    second request that can race, be skipped, or answer a stale invoice.

    The predicate itself is `document_send.customer_has_line`, the one
    the push uses: this fetches the customer, it does not decide what
    "linked" means.
    """
    from .services.document_send import customer_line_status

    contact_id = invoice.get("contact_id")
    if not contact_id:
        return False
    try:
        customer = await client.get_customer(license_id, str(contact_id))
    except DataTierError as exc:
        # Unknown, not "not linked" (21E re-review 2, I-2): the send route
        # asks again and gives the true answer.
        log.warning("could not read the customer of invoice %s: %s", invoice.get("invoice_id"), exc)
        return None
    return await customer_line_status(client, customer or {})


def _invoice_document_error(exc: Exception, *, code: str) -> HTTPException:
    """The quote's error taxonomy, for the invoice and the receipt: the
    dashboard already translates these statuses (409 company_incomplete /
    already_issued, 503 not configured, 504 slow renderer, 502 provider)."""
    from .services.documents.snapshot import QuoteNotRenderable
    from .services.invoices import InvoiceAlreadyIssued, InvoiceNotOpen, InvoiceNotPaid, QuoteNotBillable
    from .services.pdf.base import RendererUnavailable
    from .services.pdf.smartbrowz import SmartBrowzNotConfigured, SmartBrowzRenderError
    from .services.storage.base import DocumentStoreError, DocumentStoreNotConfigured

    if isinstance(exc, InvoiceAlreadyIssued):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "error": "already_issued", "reason_code": "already_issued", "message": str(exc),
        })
    if isinstance(exc, (InvoiceNotPaid, InvoiceNotOpen, QuoteNotBillable)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "error": "invoice_state", "reason_code": "invoice_state", "message": str(exc), "code": code,
        })
    if isinstance(exc, QuoteNotRenderable):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "error": "company_incomplete", "reason_code": "company_incomplete", "message": str(exc),
        })
    if isinstance(exc, (SmartBrowzNotConfigured, DocumentStoreNotConfigured)):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, RendererUnavailable):
        # Transient, and the sentence already says "try again".
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, (SmartBrowzRenderError, DocumentStoreError)):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, DataTierError):
        return _propagate(exc)
    log.exception("invoice document step failed for %s", code)
    return HTTPException(status_code=500, detail=str(exc)[:200])


class InvoiceCreateIn(BaseModel):
    quote_id: str | None = None
    deal_id: str | None = None
    note: str | None = None


class InvoicePaymentBody(BaseModel):
    amount: str | float | int | None = None
    full: bool = False
    method: str = "transfer"
    reference: str | None = None
    note: str | None = None
    paid_at: datetime | None = None


@router.get("/licenses/{license_id}/invoices/summary")
async def invoice_summary(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """What the shop is owed — the overview tile. Staff only: a customer's
    own balance is the sum of their rows, not the shop's book."""
    _require_same_tenant(principal, license_id)
    principal.require("invoice.read")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        return await client.invoice_summary(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/invoices")
async def list_invoices(
    license_id: str,
    status_filter: str | None = None,
    q: str | None = None,
    contact_id: str | None = None,
    deal_id: str | None = None,
    quote_id: str | None = None,
    overdue: bool = False,
    limit: int = 500,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("invoice.read")
    try:
        # `deal_id` (round 20X): the deal page's bills, and the invoices
        # page opened from it with the same filter in the URL.
        rows, total = await client.list_invoices_with_total(
            license_id, status=status_filter, q=q, contact_id=contact_id, deal_id=deal_id,
            # Round 21A: a quote page asking whether it has been billed.
            quote_id=quote_id, overdue=overdue,
            # Scoped by construction for a customer: their contact's rows only.
            customer_chann_uid=principal.chann_uid if principal.is_customer else None,
            limit=limit, offset=offset,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/licenses/{license_id}/invoices/{invoice_id}")
async def get_invoice(
    license_id: str,
    invoice_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("invoice.read")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    return {**invoice, "customer_has_line": await _invoice_customer_has_line(client, license_id, invoice)}


async def _create_invoice(
    client: DataClient, principal: TenantPrincipal, license_id: str, *,
    quote_id: str | None, deal_id: str | None, note: str | None,
) -> dict:
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.create")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    if not quote_id and not deal_id:
        raise HTTPException(status_code=422, detail="quote_id or deal_id is required")
    try:
        quote = None
        if quote_id:
            quote = await client.get_quote(license_id, quote_id)
            if quote is None:
                raise HTTPException(status_code=404, detail="quote not found")
            deal_id = str(quote["deal_id"])
        deal = await client.get_deal(license_id, str(deal_id))
        if deal is None:
            raise HTTPException(status_code=404, detail="deal not found")
        customer = await client.get_customer(license_id, str(deal["contact_id"]))
        if customer is None:
            raise HTTPException(status_code=404, detail="customer not found")
        company = await client.get_company_profile(license_id)
        if quote is not None:
            return await invoice_service.create_from_quote(
                client, license_id=license_id, quote=quote, deal=deal, customer=customer,
                company=company, actor_id=principal.chann_uid, note=note,
            )
        return await invoice_service.create_from_deal(
            client, license_id=license_id, deal=deal, customer=customer, company=company,
            actor_id=principal.chann_uid, note=note,
        )
    except DataTierError as exc:
        raise _with_reason(exc)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, HTTPException):
            raise
        raise _invoice_document_error(exc, code=quote_id or deal_id or "")


@router.post("/licenses/{license_id}/invoices", status_code=201)
async def create_invoice(
    license_id: str,
    payload: InvoiceCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """A draft invoice from a quote or straight from a deal."""
    _require_same_tenant(principal, license_id)
    principal.require("invoice.create")
    return await _create_invoice(
        client, principal, license_id, quote_id=payload.quote_id, deal_id=payload.deal_id, note=payload.note,
    )


@router.post("/licenses/{license_id}/quotes/{quote_id}/invoice", status_code=201)
async def create_invoice_from_quote(
    license_id: str,
    quote_id: str,
    payload: InvoiceCreateIn | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The button on the quote: "ออกใบแจ้งหนี้". Same call as POST /invoices
    with quote_id; here so the quote page needs no body."""
    _require_same_tenant(principal, license_id)
    principal.require("invoice.create")
    return await _create_invoice(
        client, principal, license_id, quote_id=quote_id, deal_id=None,
        note=(payload.note if payload else None),
    )


@router.post("/licenses/{license_id}/invoices/{invoice_id}/issue")
async def issue_invoice(
    license_id: str,
    invoice_id: str,
    allow_reissue: bool = False,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Render, store and record the invoice PDF; draft → issued."""
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        company = await client.get_company_profile(license_id)
        invoice, document = await invoice_service.issue_invoice_document(
            client, license_id=license_id, invoice=invoice, company=company,
            actor_id=principal.chann_uid, allow_reissue=allow_reissue,
        )
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))
    return {
        "invoice": invoice,
        "generated_document_id": document.get("id"),
        "sha256": document.get("sha256"),
        "renderer": document.get("renderer"),
    }


@router.post("/licenses/{license_id}/invoices/{invoice_id}/payments", status_code=201)
async def record_invoice_payment(
    license_id: str,
    invoice_id: str,
    payload: InvoicePaymentBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """One receipt of money: a deposit, an instalment, or `full` for the
    balance. Returns the invoice as it now stands, payments included."""
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    if not payload.full and payload.amount in (None, ""):
        raise HTTPException(status_code=422, detail="amount is required unless full=true")
    try:
        return await invoice_service.record_payment(
            client, license_id=license_id, invoice=invoice, amount=payload.amount,
            method=payload.method, reference=payload.reference, note=payload.note,
            paid_at=payload.paid_at, actor_id=principal.chann_uid, full=payload.full,
        )
    except invoice_service.PaymentInvalid as exc:
        raise HTTPException(status_code=422, detail={
            "error": "payment_invalid", "reason_code": "payment_invalid", "message": str(exc),
        })
    except DataTierError as exc:
        raise _with_reason(exc)
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))


@router.post("/licenses/{license_id}/invoices/{invoice_id}/receipt")
async def issue_invoice_receipt(
    license_id: str,
    invoice_id: str,
    allow_reissue: bool = False,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The receipt PDF for a bill paid in full — and one LINE line to the
    customer with the link."""
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        company = await client.get_company_profile(license_id)
        invoice, document = await invoice_service.issue_receipt_document(
            client, license_id=license_id, invoice=invoice, company=company,
            actor_id=principal.chann_uid, allow_reissue=allow_reissue,
        )
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))
    notified = await invoice_service.notify_customer_receipt(
        client, license_id=license_id, invoice=invoice, document=document,
    )
    return {
        "invoice": invoice,
        "receipt_document_id": document.get("id"),
        "sha256": document.get("sha256"),
        "customer_notified": notified,
    }


@router.post("/licenses/{license_id}/invoices/{invoice_id}/void")
async def void_invoice(
    license_id: str,
    invoice_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Void — its own key, and only while nothing was paid (the Data tier
    says so with a 409 the page translates)."""
    _require_same_tenant(principal, license_id)
    principal.require("invoice.void")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        return await client.void_invoice(license_id, invoice_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _with_reason(exc)


class DocumentSendBody(BaseModel):
    kind: str = "invoice"   # "invoice" | "receipt"; the quote route ignores it


def _document_send_error(exc: Exception) -> HTTPException:
    """The refusals of `services/document_send.py` as the dashboard reads
    them. Both are refusals with a cure — add the customer's LINE, or issue
    the document — so neither is a 500."""
    from .services.document_send import (
        CustomerNotLinked, DocumentNotIssued, DocumentNotSendable, DocumentSendFailed,
    )

    if isinstance(exc, DocumentSendFailed):
        # LINE did not take the push. Not a refusal the shop can cure, and
        # never a "sent": a 502 with what LINE said, so the screen can say
        # "ส่งไม่สำเร็จ ลองใหม่อีกครั้ง" (round 21E).
        # The reason as a code; LINE's raw answer stays in the log.
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                             detail={"error": "push_failed", "reason": exc.reason})

    if isinstance(exc, DocumentNotSendable):
        # The same shape as "not issued" — the cure is again "fix the
        # document first" — with the reason as the code, so the screen can
        # say which: void / needs_reissue / quote_closed (final review C1).
        return HTTPException(status_code=422, detail={"error": exc.reason})
    if isinstance(exc, CustomerNotLinked):
        return HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail={"error": "customer_not_linked", "customer": exc.customer_name})
    if isinstance(exc, DocumentNotIssued):
        return HTTPException(status_code=422, detail={"error": "not_issued"})
    # Nothing else can arrive: both call sites catch exactly those two. A
    # third exception here is a bug in the caller, and a bug dressed as a
    # 500 body reads like a refusal the shop could act on.
    raise exc


@router.post("/licenses/{license_id}/invoices/{invoice_id}/send")
async def send_invoice_to_customer(
    license_id: str, invoice_id: str, payload: DocumentSendBody | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Hand the bill (or its receipt) to the customer on LINE."""
    from .services import document_send, invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    principal.require_feature("feature.customer_line_link")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    kind = (payload.kind if payload else "invoice") or "invoice"
    if kind not in ("invoice", "receipt"):
        raise HTTPException(status_code=422, detail="kind must be invoice or receipt")
    document_id = invoice.get("receipt_document_id") if kind == "receipt" else invoice.get("generated_document_id")
    try:
        customer, company = await invoice_service.invoice_parties(client, license_id, invoice)
        return await document_send.send_document_to_customer(
            client, license_id=license_id, kind=kind, record=invoice,
            document_id=str(document_id) if document_id else None,
            customer=customer, company=company, actor_id=principal.chann_uid)
    except (document_send.CustomerNotLinked, document_send.DocumentNotIssued,
            document_send.DocumentSendFailed) as exc:
        raise _document_send_error(exc)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/quotes/{quote_id}/send")
async def send_quote_to_customer(
    license_id: str, quote_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Hand the quotation to the customer on LINE."""
    from .services import document_send

    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    principal.require_feature("feature.customer_line_link")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    try:
        quote = await client.get_quote(license_id, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        deal = await client.get_deal(license_id, str(quote.get("deal_id") or ""))
        customer = await client.get_customer(license_id, str((deal or {}).get("contact_id") or "")) or {}
        company = await client.get_company_profile(license_id)
        return await document_send.send_document_to_customer(
            client, license_id=license_id, kind="quote", record=quote,
            document_id=str(quote.get("generated_document_id") or "") or None,
            customer=customer, company=company, actor_id=principal.chann_uid)
    except (document_send.CustomerNotLinked, document_send.DocumentNotIssued,
            document_send.DocumentSendFailed) as exc:
        raise _document_send_error(exc)
    except DataTierError as exc:
        raise _propagate(exc)


class InvoiceLineIn(BaseModel):
    product_name: str
    qty: int = 1
    unit_price: str | float
    notes: str | None = None


class InvoiceLinesBody(BaseModel):
    lines: list[InvoiceLineIn]


class InvoiceDetailsBody(BaseModel):
    note: str | None = None
    due_date: date | None = None


@router.patch("/licenses/{license_id}/invoices/{invoice_id}/lines")
async def update_invoice_lines(
    license_id: str, invoice_id: str, payload: InvoiceLinesBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Correct what is on the bill. Editable until money has touched it —
    the invoice's answer to the quote's "draft only" (round 21C)."""
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        company = await client.get_company_profile(license_id)
        return await invoice_service.edit_lines(
            client, license_id=license_id, invoice=invoice,
            lines=[line.model_dump(mode="json") for line in payload.lines],
            company=company, actor_id=principal.chann_uid,
        )
    except invoice_service.InvoiceLocked as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except invoice_service.InvoiceLinesEmpty as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/invoices/{invoice_id}")
async def update_invoice_details(
    license_id: str, invoice_id: str, payload: InvoiceDetailsBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    if principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff only")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        return await invoice_service.edit_details(
            client, license_id=license_id, invoice=invoice,
            note=payload.note, due_date=payload.due_date, actor_id=principal.chann_uid,
        )
    except invoice_service.InvoiceLocked as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)


# -------------------------------------------------------------------- quotes


@router.get("/licenses/{license_id}/quotes/{quote_id}")
async def get_quote_detail(
    license_id: str,
    quote_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """One quote with the deal and line items behind it.

    A quote row on its own says almost nothing — a code, a status and a
    deal id. What someone opening it wants to know is what is ON it, which
    lives on the deal.
    """
    _require_same_tenant(principal, license_id)
    principal.require("quote.read")
    try:
        quote = await client.get_quote(license_id, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        deal = await client.get_deal(license_id, str(quote["deal_id"]))
        customer = None
        if deal and deal.get("contact_id"):
            customer = await client.get_customer(license_id, str(deal["contact_id"]))
        # The send button's question, answered the way the push answers it
        # (a uid AND a LINE user behind it — round 21E review, Important 1),
        # so the page stops deriving it from the uid alone.
        # None when it could not be asked — the page still opens (I-2).
        from .services.document_send import customer_line_status
        has_line = await customer_line_status(client, customer or {})
        return {"quote": quote, "deal": deal, "customer": customer, "customer_has_line": has_line}
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/permissions/catalog")
async def permissions_catalog(
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Every permission, with its human label and group.

    The roles page needs it to offer a pick-list. Without it the only way
    to grant a permission was to type its key into a textarea — a shop
    owner had to know that "customer.read" exists and spell it exactly,
    and a typo silently granted nothing.

    Behind a tenant principal but not a specific permission: knowing which
    capabilities the platform has is not itself sensitive, and anyone who
    can reach this is already a member of a tenant.
    """
    try:
        return await client.permission_catalog()
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------- creating records
#
# The dashboard could edit everything and create nothing: customers, deals,
# quotes and tickets all had a PATCH and no POST, so a shop had to open
# LINE to add anything at all. Chat is the primary interface by design, but
# "primary" is not "only" — someone entering ten products or copying a
# customer list wants a form.


class CustomerCreateIn(BaseModel):
    first_name: str | None = None
    last_name: str
    phone: str
    email: str | None = None
    address: str | None = None
    notes: str | None = None
    stage: str = "lead"


@router.post("/licenses/{license_id}/customers", status_code=201)
async def create_customer(
    license_id: str,
    payload: CustomerCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """last_name and phone are required by the Data tier (Phase 9): a
    contact you cannot ring is not a contact, and one first name is not
    enough to tell two customers apart."""
    _require_same_tenant(principal, license_id)
    principal.require("customer.create")
    body = payload.model_dump(mode="json", exclude_none=True)
    body["owner_member_id"] = await _member_of(client, license_id, principal)
    try:
        row = await client.create_customer(
            license_id, body,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    # The sales rule, when the shop set one (round 18): a customer added by
    # the owner or CS from the dashboard is handed to a salesperson the
    # same way a job is handed to a technician.
    from .services.sales_dispatch import route_new_customer

    await route_new_customer(
        client, license_id, row, source="dashboard", actor_chann_uid=principal.chann_uid,
    )
    return row


class OwnerIn(BaseModel):
    owner_member_id: str | None = None


@router.patch("/licenses/{license_id}/customers/{customer_id}/owner")
async def set_customer_owner(
    license_id: str,
    customer_id: str,
    payload: OwnerIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """reassign_records: hand a customer to a colleague."""
    _require_same_tenant(principal, license_id)
    principal.require("reassign_records")
    try:
        return await client.set_customer_owner(license_id, customer_id, payload.owner_member_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/deals/{deal_id}/owner")
async def set_deal_owner(
    license_id: str,
    deal_id: str,
    payload: OwnerIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("reassign_records")
    try:
        return await client.set_deal_owner(license_id, deal_id, payload.owner_member_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


class DealCreateIn(BaseModel):
    contact_id: str
    notes: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    expected_close_date: date | None = None


@router.post("/licenses/{license_id}/deals", status_code=201)
async def create_deal(
    license_id: str,
    payload: DealCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.create")
    body = payload.model_dump(mode="json", exclude_none=True)
    body["owner_member_id"] = await _member_of(client, license_id, principal)
    try:
        return await client.create_deal(
            license_id, body,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class DealProductIn(BaseModel):
    product_name: str
    quoted_unit_price: str | float
    qty: int = 1
    product_id: str | None = None
    notes: str | None = None


@router.post("/licenses/{license_id}/deals/{deal_id}/products", status_code=201)
async def add_deal_product(
    license_id: str,
    deal_id: str,
    payload: DealProductIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Line items, which a quote now requires at least one of.

    Without this the dashboard could delete a line item but never add one,
    so a deal that lost its last product could not be quoted again from
    the dashboard at all.
    """
    _require_same_tenant(principal, license_id)
    principal.require("deal.update")
    try:
        return await client.add_deal_product(
            license_id, deal_id, payload.model_dump(mode="json", exclude_none=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class QuoteCreateIn(BaseModel):
    deal_id: str


@router.post("/licenses/{license_id}/quotes", status_code=201)
async def create_quote(
    license_id: str,
    payload: QuoteCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("quote.create")
    body = payload.model_dump(mode="json")
    body["owner_member_id"] = await _member_of(client, license_id, principal)
    try:
        return await client.create_quote(
            license_id, body, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _with_reason(exc)


class TicketCreateIn(BaseModel):
    issue_description: str
    customer_name: str | None = None
    customer_phone: str | None = None
    contact_id: str | None = None
    service_address: str | None = None
    serial_number: str | None = None
    scheduled_date: str | None = None
    scheduled_time: str | None = None
    visibility: str = "public"


@router.post("/licenses/{license_id}/tickets", status_code=201)
async def create_ticket(
    license_id: str,
    payload: TicketCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """CS logging a fault reported by phone or in person — which is how
    most of them still arrive."""
    _require_same_tenant(principal, license_id)
    principal.require("ticket.create")
    body = payload.model_dump(mode="json", exclude_none=True)
    if principal.is_customer:
        # Filed from the customer app: the ticket is theirs, whatever the
        # body says — that is what makes it show on their own list.
        body["customer_chann_uid"] = principal.chann_uid
        # A customer's report waits for the shop (owner, 16 ก.ย. 2569):
        # private until CS assigns it or opens it to the technicians.
        body["visibility"] = "private"
    else:
        # Logged by staff: the CS who took the call owns it (principle 6),
        # which is what the "ticket_owner" approval step keys on.
        body["owner_member_id"] = await _member_of(client, license_id, principal)
    try:
        row = await client.create_ticket(
            license_id, body,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    # The dispatchers hear about it — the home-screen report used to reach
    # nobody (review, 6 Sep 2026). Best-effort. Each dispatcher is told in
    # their own language: the notice carries both texts and
    # send_notification picks by the RECIPIENT's display preference; the
    # language passed here is the caller's, and decides nothing for them
    # (review D16 — tested in test_routes_h2).
    try:
        from .services.chat import _notify_new_ticket
        await _notify_new_ticket(client, license_id, str(row.get("id") or ""), "th")
    except Exception:  # noqa: BLE001
        log.exception("could not announce a new ticket")
    return row


@router.post("/licenses/{license_id}/tickets/{ticket_id}/assign")
async def assign_ticket_from_dashboard(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Dispatch from the queue the dispatcher is already looking at,
    instead of switching to LINE to type a code they can see on screen.

    ticket.assign is the catalogue's own name for this (review C9); roles
    built on ticket.update before the key was enforced keep working."""
    _require_same_tenant(principal, license_id)
    principal.require_any("ticket.assign", "ticket.update")
    target_type = str(payload.get("target_type") or "")
    target_ref = str(payload.get("target_ref") or "")
    try:
        row = await client.assign_ticket(
            license_id, ticket_id, target_type=target_type, target_ref=target_ref,
            actor_id=principal.chann_uid,
            # Whoever dispatches an unowned job takes it (round 19p).
            by_member_id=await _member_of(client, license_id, principal),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    # The technician (or team) hears about it in LINE, exactly as after
    # chat's "มอบหมาย …" — this route used to dispatch silently (owner
    # plan B1, 3 Sep), which made the dashboard the one place a job could
    # be given to someone without telling them.
    try:
        from .services.chat import _notify_assigned_ticket
        label = target_ref
        if target_type == "technician_team":
            teams = await client.list_technician_teams(license_id)
            label = next((str(t.get("team_name")) for t in teams if str(t.get("id")) == target_ref), target_ref)
        await _notify_assigned_ticket(client, license_id, row, label, "th")
    except Exception:  # noqa: BLE001 — the assignment stands; the notice is best effort
        log.exception("could not tell the assignee about a dashboard dispatch")
    return row


@router.post("/licenses/{license_id}/tickets/{ticket_id}/release")
async def release_ticket_from_dashboard(
    license_id: str,
    ticket_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Open a held job to every technician from the queue (round 19f) —
    the other half of dispatching, next to assigning it to one of them."""
    _require_same_tenant(principal, license_id)
    principal.require_any("ticket.assign", "ticket.update")
    try:
        row = await client.release_ticket(
            license_id, ticket_id, actor_id=principal.chann_uid,
            by_member_id=await _member_of(client, license_id, principal),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    try:
        from .services.chat import _notify_released_ticket
        await _notify_released_ticket(client, license_id, row, "th")
    except Exception:  # noqa: BLE001 — the release stands; the notice is best effort
        log.exception("could not tell the technicians about a released job")
    return row


@router.patch("/licenses/{license_id}/tickets/{ticket_id}")
async def update_ticket_from_dashboard(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Fill in what the dispatch gate says is missing — name, phone,
    address, appointment, serial — from the queue itself. The Data Tier
    owns the allowed-field list; anything else is ignored there."""
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    # An empty value clears the field (review C16, 6 Sep 2026): the old
    # filter dropped blanks, so a wrong serial could never be removed and
    # a form with every box empty was a 422. The Data tier already treats
    # "" as NULL; only a body with no known field at all is refused.
    fields = {
        k: (None if v in (None, "") else v) for k, v in (payload or {}).items()
        if k in ("customer_name", "customer_phone", "service_address", "serial_number",
                 "issue_description", "scheduled_date", "scheduled_time")
    }
    if not fields:
        raise HTTPException(status_code=422, detail="nothing to update")
    try:
        row = await client.update_ticket(license_id, ticket_id, fields, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
    if any(k in fields for k in ("scheduled_date", "scheduled_time", "service_address")):
        # A moved appointment or address reaches the technician and the
        # customer, as the customer's own reschedule already did.
        try:
            from .services.chat import _notify_ticket_change, _ticket_when
            code = str(row.get("ticket_number") or "")
            when = _ticket_when(row)
            await _notify_ticket_change(
                client, license_id, ticket_id,
                f"งาน {code} เปลี่ยนนัด/ที่อยู่: {when or '-'} · {row.get('service_address') or '-'}", "th",
                text_en=f"Job {code} changed: {when or '-'} · {row.get('service_address') or '-'}",
                customer_text=f"ร้านปรับนัดงาน {code} ของคุณเป็น {when or '-'}",
                customer_text_en=f"The shop moved your job {code} to {when or '-'}",
            )
        except Exception:  # noqa: BLE001
            log.exception("could not announce a dashboard ticket edit")
    return row


@router.patch("/licenses/{license_id}/tickets/{ticket_id}/status")
async def set_ticket_status_from_dashboard(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Cancel (or close) from the queue. The assigned technician is told,
    as chat's cancellation does — a cancelled job nobody mentions is a
    drive to an empty house."""
    _require_same_tenant(principal, license_id)
    # ticket.close is what the catalogue calls ending a job (review C9);
    # ticket.update stays accepted for the roles that already have it.
    principal.require_any("ticket.close", "ticket.update")
    new_status = str((payload or {}).get("status") or "")
    # Cancel only. "completed" from here skipped check-in, the report and
    # approval — the whole Phase 13 gate (review, 6 Sep 2026); "open"
    # reopened a job under a technician's feet.
    if new_status != "cancelled":
        raise HTTPException(status_code=422, detail="status must be cancelled")
    try:
        row = await client.set_ticket_status(license_id, ticket_id, new_status, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
    if new_status == "cancelled":
        try:
            from .services.chat import _notify_ticket_change
            await _notify_ticket_change(
                client, license_id, ticket_id,
                f"งาน {row.get('ticket_number') or ''} ถูกยกเลิกโดยร้าน", "th",
                text_en=f"Job {row.get('ticket_number') or ''} was cancelled by the shop",
                customer_text=f"ร้านยกเลิกงาน {row.get('ticket_number') or ''} ของคุณ หากมีข้อสงสัยกด \"คุยกับร้าน\"",
                customer_text_en=f"The shop cancelled your job {row.get('ticket_number') or ''}. Tap \"talk to the shop\" with any question.",
            )
        except Exception:  # noqa: BLE001
            log.exception("could not announce a dashboard cancellation")
    return row


@router.get("/licenses/{license_id}/technician-teams")
async def list_technician_teams(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    _staff_only(principal)
    # ticket.read is itself feature.service (entitlements.PERMISSION_FEATURE):
    # on a plan without service it is plan-locked, and require() answers
    # plan_required. No separate require_feature here — past this line the
    # plan has service by construction, so one would never fire.
    principal.require("ticket.read")
    try:
        return await client.list_technician_teams(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


# Technician teams (Phase 7 organisation, used by Phase 12 dispatch).
# The Data Tier has had these since Phase 7; nothing above it called
# them, so a shop could not form a team except by chat — and chat could
# not either. Names come from the members' profiles, which is what a
# person managing a team reads, not chann_uids.


class TeamCreateIn(BaseModel):
    team_name: str


class TeamMemberAddIn(BaseModel):
    member_id: str
    is_lead: bool = False


@router.post("/licenses/{license_id}/technician-teams", status_code=201)
async def create_technician_team(
    license_id: str,
    payload: TeamCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    principal.require_feature("feature.service")
    try:
        return await client.create_technician_team(license_id, payload.team_name.strip())
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete("/licenses/{license_id}/technician-teams/{team_id}", status_code=204)
async def delete_technician_team(
    license_id: str,
    team_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    principal.require_feature("feature.service")
    try:
        await client.delete_technician_team(license_id, team_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/technician-teams/{team_id}/members")
async def list_technician_team_members(
    license_id: str,
    team_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    # ticket.read carries the plan check (see list_technician_teams).
    principal.require("ticket.read")
    _staff_only(principal)
    try:
        members = await client.list_team_members(license_id, team_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return await _with_names(client, members)


@router.get("/licenses/{license_id}/technicians")
async def list_technicians(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Every active member whose role is a technician role, with names —
    the pool a team is built from and a job is dispatched to."""
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    _staff_only(principal)
    try:
        members = await client.list_members(license_id)
        roles = await client.list_roles(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    technician_roles = _technician_role_names(roles)
    # The Technician-OA rows only (owner, 8 Sep 2026): a job dispatched
    # to someone's sales row would never show on their technician app.
    technicians = [
        m for m in members
        if str(m.get("status") or "active") == "active"
        and str(m.get("channel") or "technician") == "technician"
        and str(m.get("role") or "") in technician_roles
    ]
    return await _with_names(client, technicians)


def _technician_role_names(roles: list[dict]) -> set[str]:
    """Which of the tenant's roles are field roles.

    By what the role can do, not only by what it is called (review C15,
    6 Sep 2026): a shop that named its role "ทีมติดตั้ง" had no
    technicians to dispatch to. A role is a field role when it can work
    a job and file the report but not approve one — approvals belong to
    CS/owner/admin, who also hold ticket.update and would otherwise be
    offered as dispatch targets. The name test stays for the default
    template and for roles a shop deliberately named that way."""
    out: set[str] = set()
    for role in roles:
        name = str(role.get("role_name") or "")
        keys = set(role.get("permission_keys") or [])
        by_name = "technician" in name.lower() or "ช่าง" in name
        by_capability = (
            {"ticket.update", "service_report.create"} <= keys
            and "approval.approve" not in keys
            and "role.manage" not in keys
        )
        if name and (by_name or by_capability):
            out.add(name)
    return out


@router.post("/licenses/{license_id}/technician-teams/{team_id}/members", status_code=201)
async def add_technician_team_member(
    license_id: str,
    team_id: str,
    payload: TeamMemberAddIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    principal.require_feature("feature.service")
    try:
        return await client.add_team_member(
            license_id, team_id, payload.member_id, is_lead=payload.is_lead,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete(
    "/licenses/{license_id}/technician-teams/{team_id}/members/{member_id}", status_code=204,
)
async def remove_technician_team_member(
    license_id: str,
    team_id: str,
    member_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    principal.require_feature("feature.service")
    try:
        await client.remove_team_member(license_id, team_id, member_id)
    except DataTierError as exc:
        raise _propagate(exc)


# Sales groups (Phase 7, Master Spec 7.5). E7 (6 Sep 2026): the Data Tier
# could add and remove members since Phase 7 and nothing called it, so a
# group could be created and never populated. Same key as chat's
# "สร้างกลุ่มเซลส์" (team.manage), same shape as the technician teams above.


class SalesGroupCreateIn(BaseModel):
    group_name: str


class SalesGroupMemberIn(BaseModel):
    member_id: str


@router.get("/licenses/{license_id}/sales-groups")
async def list_sales_groups(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    _staff_only(principal)
    principal.require("team.manage")
    try:
        return await client.list_sales_groups(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/sales-groups", status_code=201)
async def create_sales_group(
    license_id: str,
    payload: SalesGroupCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    try:
        return await client.create_sales_group(license_id, payload.group_name.strip())
    except DataTierError as exc:
        raise _with_reason(exc)


@router.delete("/licenses/{license_id}/sales-groups/{group_id}", status_code=204)
async def delete_sales_group(
    license_id: str,
    group_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    try:
        await client.delete_sales_group(license_id, group_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/sales-groups/{group_id}/members")
async def list_sales_group_members(
    license_id: str,
    group_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    _staff_only(principal)
    principal.require("team.manage")
    try:
        members = await client.list_sales_group_members(license_id, group_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return await _with_names(client, members)


@router.post("/licenses/{license_id}/sales-groups/{group_id}/members", status_code=201)
async def add_sales_group_member(
    license_id: str,
    group_id: str,
    payload: SalesGroupMemberIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    try:
        return await client.add_sales_group_member(license_id, group_id, payload.member_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete(
    "/licenses/{license_id}/sales-groups/{group_id}/members/{member_id}", status_code=204,
)
async def remove_sales_group_member(
    license_id: str,
    group_id: str,
    member_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("team.manage")
    try:
        await client.remove_sales_group_member(license_id, group_id, member_id)
    except DataTierError as exc:
        raise _propagate(exc)


async def _with_names(client: DataClient, members: list[dict]) -> list[dict]:
    """The members list a screen reads, with each person's own name.

    The names now arrive ON the member rows: the Data tier loads the
    identity anyway to fill display_name, so first_name/last_name/phone
    cost it nothing to send (round 20h). Before that this asked the Data
    tier for every member's profile in turn — one HTTP round trip per
    member, on every screen and reply that lists people.

    The per-member read is kept as a fallback for a row that predates the
    wider schema, so a stale Data tier degrades to the old behaviour
    instead of showing a list of chann_uids.
    """
    out = []
    for m in members:
        chann_uid = str(m.get("chann_uid") or "")
        profile: dict = {}
        if m.get("first_name") or m.get("last_name") or m.get("phone"):
            profile = {
                "first_name": m.get("first_name"),
                "last_name": m.get("last_name"),
                "phone": m.get("phone"),
            }
        else:
            try:
                profile = await client.get_profile(chann_uid) or {}
            except Exception:  # noqa: BLE001 — a missing profile is a nameless row, not a failure
                profile = {}
        name = " ".join(
            p for p in (profile.get("first_name"), profile.get("last_name")) if p
        )
        out.append({
            **m,
            "id": str(m.get("id") or ""),
            "display_name": name or str(m.get("display_name") or "") or chann_uid,
            "phone": profile.get("phone"),
            "channel": str(m.get("channel") or "sales"),
            "status": str(m.get("status") or "active"),
            "is_owner": bool(m.get("is_owner", False)),
            "joined_at": m.get("joined_at"),
        })
    return out


# --------------------------------------------------------- quote line items


class QuoteLineIn(BaseModel):
    product_name: str
    quoted_unit_price: str | float
    qty: int = 1
    notes: str | None = None


class QuoteLinePatchIn(BaseModel):
    product_name: str | None = None
    quoted_unit_price: str | float | None = None
    qty: int | None = None
    notes: str | None = None


@router.get("/licenses/{license_id}/quotes/{quote_id}/products")
async def list_quote_products(
    license_id: str,
    quote_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("quote.read")
    try:
        return await client.list_quote_products(license_id, quote_id)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/quotes/{quote_id}/products", status_code=201)
async def add_quote_product(
    license_id: str,
    quote_id: str,
    payload: QuoteLineIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Add a line to THIS quote only.

    The deal keeps what the customer is buying; the quote keeps what they
    were offered. Adding a sweetener to one offer must not rewrite either.
    """
    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    try:
        return await client.add_quote_product(
            license_id, quote_id, payload.model_dump(mode="json", exclude_none=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/quotes/{quote_id}/products/{line_id}")
async def update_quote_product(
    license_id: str,
    quote_id: str,
    line_id: str,
    payload: QuoteLinePatchIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    try:
        return await client.update_quote_product(
            license_id, quote_id, line_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete(
    "/licenses/{license_id}/quotes/{quote_id}/products/{line_id}", status_code=204,
)
async def remove_quote_product(
    license_id: str,
    quote_id: str,
    line_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    try:
        await client.remove_quote_product(
            license_id, quote_id, line_id, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class QuoteStatusPatchIn(BaseModel):
    status: str


@router.patch("/licenses/{license_id}/quotes/{quote_id}/status")
async def set_quote_status(
    license_id: str,
    quote_id: str,
    payload: QuoteStatusPatchIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Mark a quote accepted, rejected or expired.

    Needed most for the case nobody plans for: a quote issued with the
    wrong contents. It cannot be edited once issued — that is deliberate,
    the customer is holding it — so the only honest options are to void
    this one and issue a replacement. Without this endpoint there was no
    way to do the first half, and the wrong quote stayed "sent" forever.
    """
    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    try:
        return await client.set_quote_status(
            license_id, quote_id, payload.status, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _with_reason(exc)


@router.get("/licenses/{license_id}/documents/{document_id}/link")
async def get_document_link(
    license_id: str,
    document_id: str,
    request: Request,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """A plain https link to a document, for opening in a browser.

    The dashboard used to fetch the PDF as a blob and render an anchor at
    the resulting blob: URL. That cannot work inside LINE: its in-app
    browser refuses blob: URLs and answers "ไม่สามารถเปิดลิงก์ได้" — and
    LIFF is the only place this dashboard runs.

    A signed link avoids the problem entirely. It carries its own
    authorisation, so it needs no LIFF headers, opens like any other URL,
    and can be forwarded to a customer as-is.
    """
    from .auth.document_link import issue_document_token

    _require_same_tenant(principal, license_id)
    principal.require_any("quote.read", "invoice.read")

    try:
        document = await client.get_generated_document(license_id, document_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if document is None or not await _document_readable_by(client, principal, license_id, document):
        raise HTTPException(status_code=404, detail="document not found")

    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        # Fall back to the URL this very request arrived on.
        #
        # PUBLIC_BASE_URL is not set in dev — it has no default and is not
        # in terraform.tfvars.example — so this endpoint answered 503 and
        # "ดูเอกสาร" appeared to do nothing at all (2 Sep). The request
        # already carries the tier's own externally reachable origin,
        # which is exactly what the setting would have said; requiring an
        # operator to configure what the request can tell us was the
        # mistake. The setting still wins when set, for the case it exists
        # for: a custom domain in front of Cloud Run.
        base = str(request.base_url).rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503, detail="no base URL for document links",
        )

    token = issue_document_token(license_id, document_id)
    return {
        "url": f"{base}/api/v1/documents/{token}",
        "sha256": document.get("sha256"),
    }


@router.patch("/licenses/{license_id}/deals/{deal_id}/products/{line_id}")
async def update_deal_product(
    license_id: str,
    deal_id: str,
    line_id: str,
    payload: QuoteLinePatchIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Same shape as a quote line, because it is the same edit."""
    _require_same_tenant(principal, license_id)
    principal.require("deal.update")
    try:
        return await client.update_deal_product(
            license_id, deal_id, line_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/service-reports/{report_id}/status")
async def set_service_report_status(
    license_id: str,
    report_id: str,
    payload: QuoteStatusPatchIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Approve or reject what a technician filed.

    ticket.update rather than a new permission: whoever dispatches work is
    who reviews it, and inventing a separate key would mean every existing
    tenant's CS role silently losing the ability on the day this shipped.
    """
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    # Approving from a SCREEN must be the same act as approving in chat.
    # It was not: this wrote the report's status straight to the database
    # and left every approval step `pending`, so the report read "อนุมัติ
    # แล้ว" while it still sat in "รอการอนุมัติ" — and the document, the
    # survey and the notifications that the chat road performs never
    # happened at all (owner, 17 ก.ย. 2569, SR-2026-0003). Route the
    # decision through the one road when the report has a step waiting.
    if payload.status in ("approved", "rejected"):
        acted = await approval_service.act_on_the_current_step(
            client, license_id=license_id, report_id=report_id,
            approve=payload.status == "approved", actor_chann_uid=principal.chann_uid,
        )
        if acted is not None:
            return acted
    try:
        return await client.set_service_report_status(
            license_id, report_id, payload.status, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/pipeline")
async def pipeline_summary(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The numbers a shop owner opens the dashboard to see.

    deal.read, not a reporting permission: anyone who can see the deals
    can already add them up, and inventing a separate key would hide the
    total from the people whose pipeline it is.
    """
    _require_same_tenant(principal, license_id)
    principal.require("deal.read")
    try:
        return await client.pipeline_summary(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


# ---------------------------------------------------- tenant PDF templates
#
# A shop uploads the HTML it wants its quotes to look like. Placeholders
# only — see services/documents/fill.py for why a template language is not
# on offer.


class TemplateUploadIn(BaseModel):
    """A template as either of the two things a shop actually has.

    `docx_base64` is base64 rather than a multipart upload because the
    Presentation tier proxies every call through one JSON seam
    (`presentation/lib/api.ts`); a multipart body would need a second
    seam, and the file is capped small enough (see docx.MAX_DOCX_BYTES)
    that the ~33% base64 overhead is not what limits it.
    """

    template_name: str
    # Exactly one of these. `html` stays required-in-practice for the
    # HTML path so every existing caller keeps working unchanged.
    html: str | None = None
    docx_base64: str | None = None
    filename: str | None = None
    # Which kind of document this layout is for. Defaulted to "quote"
    # because that is what every caller sent before the field could be
    # chosen; the dashboard now asks. Only the types with a real issue
    # path are accepted (see documents/selection.py) — a template for a
    # type nothing renders is a file a shop would maintain for nothing.
    document_type: str = "quote"


@router.post("/licenses/{license_id}/document-templates/upload", status_code=201)
async def upload_document_template(
    license_id: str,
    payload: TemplateUploadIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Store a shop's own layout as an unpublished draft.

    Draft, never live on upload: a template goes onto documents customers
    receive, and the person who wrote it should see it rendered before
    anyone else does.

    The response lists any placeholder that will come out blank, so that
    is discovered here rather than on a quote already sent.

    Word or HTML. The owner, 9 Sep 2026: "ไฟล์ที่ควรอัพเข้าไม่ใช่ html
    แต่ควรรองรับเป็น word" — nobody in a shop writes HTML, and the
    quotation they want is already a .docx on their computer. A .docx is
    converted to the HTML the fill engine understands
    (`services/documents/docx.py`) and the original bytes are kept, so
    `source_docx_path` points at the file they actually uploaded and they
    can download it back. HTML uploads are unchanged.
    """
    from .services.documents.design import (
        TemplateRejected, frame, sanitise, split_frame,
    )
    from .services.documents.docx import (
        DocxConversionError, convert_docx_to_html, docx_warnings,
    )
    from .services.documents.fill import unknown_blocks, unknown_placeholders
    from .services.storage.base import DocumentStoreNotConfigured, get_document_store

    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    principal.require_feature("feature.custom_documents")

    if payload.document_type not in TEMPLATE_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "ยังไม่รองรับแบบฟอร์มของเอกสารประเภทนี้ "
                f"(unsupported document_type {payload.document_type!r}; "
                f"expected one of {', '.join(TEMPLATE_DOCUMENT_TYPES)})"
            ),
        )

    source_name = (payload.filename or "").strip()
    docx_bytes: bytes | None = None
    # Sentences for the shop about a file that WAS accepted: things that
    # will not come out the way the Word file looks. Reported, never
    # refused — a header that is only a page number is fine to lose.
    warnings: list[str] = []

    if payload.docx_base64:
        try:
            docx_bytes = base64.b64decode(payload.docx_base64, validate=True)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=(
                    "อ่านไฟล์ที่อัปโหลดไม่สำเร็จ ลองเลือกไฟล์แล้วอัปโหลดใหม่อีกครั้ง "
                    "(the uploaded file could not be decoded)"
                ),
            )
        try:
            html = convert_docx_to_html(
                docx_bytes, filename=source_name or "template.docx",
            )
        except DocxConversionError as exc:
            # 400 with the converter's own sentence: "something went
            # wrong" on a Word file leaves the shop with nothing to try.
            raise HTTPException(status_code=400, detail=exc.detail)
        # Headers and footers: mammoth does not read them, so a logo
        # placed there vanishes from the template. Said here, at upload,
        # rather than discovered on the first quotation.
        warnings.extend(docx_warnings(docx_bytes))
    else:
        html = payload.html or ""
        if not html.strip():
            raise HTTPException(status_code=400, detail="the template is empty")
        if len(html) > 512_000:
            # Half a megabyte of HTML is not a quote layout; it is an embedded
            # image someone should be hosting instead.
            raise HTTPException(status_code=400, detail="template is too large")
        # The SAME whitelist rebuild the AI-designed path uses, and for the
        # same reason: what is stored has to be assembled out of tags and
        # attributes that were each checked. Review v3, T02: this branch
        # used to store the body verbatim, so `<script>`, an `onclick=`
        # handler and an `<iframe>` all went into the document store
        # unchanged while the AI path next door rejected them. Nothing
        # about an upload makes a shop's markup more trustworthy than a
        # model's — it is less so, because a person chose it.
        # A document this system framed — the chat designer's own draft on
        # its way to storage, or a stored template downloaded and uploaded
        # again — is unwrapped first, because the frame legitimately does
        # the one thing a shop's stylesheet may not (see split_frame).
        # What was INSIDE it is checked exactly like anything else.
        framed = split_frame(html)
        source = (
            f"<style>{framed[1]}</style>{framed[0]}" if framed is not None else html
        )
        try:
            body, css = sanitise(source)
        except TemplateRejected as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "แบบฟอร์มนี้มีสิ่งที่ระบบไม่อนุญาตให้เก็บไว้ จึงไม่ได้บันทึกอะไรไว้เลย: "
                    + " · ".join(exc.reasons)
                    + " — แบบฟอร์มเก็บได้เฉพาะข้อความ ตาราง และการจัดหน้า"
                ),
            )
        html = frame(body, css)

    try:
        templates = await client.list_document_templates(
            license_id, document_type=payload.document_type,
        )
        existing = next(
            (t for t in templates if t.get("template_name") == payload.template_name),
            None,
        )
        if existing is None:
            existing = await client.create_document_template(
                license_id,
                {
                    "document_type": payload.document_type,
                    "template_code": f"tenant-{uuid.uuid4().hex[:8]}",
                    "template_name": payload.template_name,
                },
                actor_id=principal.chann_uid,
            )

        store = get_document_store()
        batch = uuid.uuid4().hex
        stored = await store.put(
            key=f"{license_id}/templates/{existing['id']}/{batch}.html",
            content=html.encode("utf-8"), content_type="text/html",
        )

        # The uploaded Word file, kept as it arrived. Not an audit nicety:
        # the compiled HTML is what renders, so without the original a
        # shop that wants to change one line of their layout has nothing
        # to open in Word — they would have to rebuild it from scratch.
        source_path = "upload://html"
        intermediate: dict = {"kind": "html_upload"}
        if docx_bytes is not None:
            source = await store.put(
                key=f"{license_id}/templates/{existing['id']}/{batch}.docx",
                content=docx_bytes, content_type=DOCX_CONTENT_TYPE,
            )
            source_path = source.path
            intermediate = {
                "kind": "docx_upload",
                "filename": source_name or "template.docx",
                "source_bytes": len(docx_bytes),
            }

        version = await client.create_document_template_version(
            license_id,
            str(existing["id"]),
            {
                "source_docx_path": source_path,
                "intermediate_model": intermediate,
                "mapping_schema": {"kind": "placeholders"},
                "compiled_template_path": stored.path,
                # Who uploaded it. The Data tier's column is a
                # license_members.id; the tier resolves this uid to the
                # member row within the license, because the Application
                # tier knows the person only by chann_uid.
                "created_by_chann_uid": principal.chann_uid,
            },
            actor_id=principal.chann_uid,
        )
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)

    blocks = unknown_blocks(html)
    if blocks:
        # A `{{#something}}` that is not the line-item block prints as
        # literal text on every document made from this template.
        warnings.append(
            "พบเครื่องหมายบล็อกที่ระบบไม่รู้จัก จะพิมพ์ออกมาเป็นข้อความตามที่เขียน: "
            + ", ".join("{{" + b + "}}" for b in blocks)
            + " — บล็อกที่ใช้ได้มีเพียง {{#line_items}} … {{/line_items}}"
        )

    return {
        "template_id": str(existing["id"]),
        "version_id": str(version["id"]),
        "version": version.get("version"),
        "status": version.get("status"),
        # Reported, not rejected: a placeholder that resolves to nothing
        # may be deliberate, and refusing the upload over one would make
        # the feature unusable for a layout with an optional field.
        "source_kind": "docx" if docx_bytes is not None else "html",
        "unknown_placeholders": unknown_placeholders(
            html, _template_sample(payload.document_type),
        ),
        # Sentences, in Thai, about what will differ from the Word file
        # (see `docx_warnings`) and about block markers that will print
        # raw. The dashboard shows them under the blank-placeholder list.
        "warnings": warnings,
    }


def _template_sample(document_type: str = "quote") -> dict:
    """A representative snapshot, for checking placeholders.

    Built by the REAL snapshot builders (services/documents/samples.py),
    not written out here. The hand-written version this replaced had
    drifted: it offered `company.legal_name` and `item.name`, which are
    the shapes of the ROWS the builder reads, not of the snapshot it
    produces (`company.name`, `item.product_name`). So the upload told
    shops those placeholders were fine and their quotes printed blanks
    where the company name should be. A sample that is not produced by
    the real builder cannot answer a question about the real builder.
    """
    from .services.documents.samples import sample_snapshot

    return sample_snapshot(document_type)


@router.post(
    "/licenses/{license_id}/document-templates/{template_id}/versions/{version_id}/publish"
)
async def publish_document_template(
    license_id: str,
    template_id: str,
    version_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Make a draft the layout new documents use.

    Documents already issued keep the version they were rendered with —
    that is what template_version_id on generated_documents is for, and
    why publishing cannot change what a customer already holds.
    """
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    principal.require_feature("feature.custom_documents")
    try:
        # The Data tier addresses a version by its own id — the template
        # is not in the path. Passing template_id here made this route
        # raise TypeError on every call, so the publish button on the
        # templates page has never worked; no test reached it because
        # nothing published a template end to end until now. The version
        # still has to belong to this template, so it is looked up
        # through the template's own list first.
        versions = await client.list_document_template_versions(license_id, template_id)
        if not any(str(v.get("id")) == str(version_id) for v in versions):
            raise HTTPException(status_code=404, detail="template version not found")
        return await client.publish_document_template_version(
            license_id, version_id, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post(
    "/licenses/{license_id}/document-templates/{template_id}/versions/{version_id}/archive"
)
async def archive_document_template(
    license_id: str,
    template_id: str,
    version_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Retire a layout the shop no longer wants used.

    The other half of publish, and the only way out of a published
    version: the Data tier route and the client method both existed with
    no caller, so a shop could put a layout into use and never take it
    back out (whole-system reach audit, round 20k).

    Nothing already issued changes — a generated document names the
    version it was rendered from. What changes is the NEXT document:
    `documents/selection.usable_version` takes the highest published
    version, so archiving that one hands rendering to the next published
    version, or to the built-in layout when there is none. It never
    fails an issue, which is the standing rule for templates.
    """
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        # Same guard as publish: the Data tier addresses a version by its
        # own id, so the template in the path proves nothing until it is
        # checked against the template's own list.
        versions = await client.list_document_template_versions(license_id, template_id)
        if not any(str(v.get("id")) == str(version_id) for v in versions):
            raise HTTPException(status_code=404, detail="template version not found")
        return await client.archive_document_template_version(
            license_id, version_id, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/document-templates")
async def list_document_templates(
    license_id: str,
    document_type: str = "quote",
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        return await client.list_document_templates(
            license_id, document_type=document_type,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/document-templates/in-use")
async def document_templates_in_use(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Which layout each kind of document is being rendered from, right now.

    Answered by the same function the renderer calls
    (`documents/selection.py`), not by re-deriving the rule here — the
    page's whole job is to tell a shop what their customers are about to
    receive, and a second implementation of "which one wins" would
    eventually disagree with the first.

    `source` is "tenant" or "builtin". "builtin" is a normal, expected
    answer: a shop that has uploaded nothing, has switched their template
    off, or whose only published version was archived, is issuing
    documents with the system's standard layout.
    """
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")

    from .services.documents.selection import resolve_tenant_template

    # No error branch: the resolver treats a Data tier failure as "no
    # tenant template" and says so, because that is what the renderer
    # would do a minute later. This page must not claim a shop is on a
    # layout the next document would not use.
    in_use: dict[str, dict] = {}
    for document_type in TEMPLATE_DOCUMENT_TYPES:
        template, version = await resolve_tenant_template(
            client, license_id, document_type,
        )
        in_use[document_type] = {
            "source": "tenant" if version is not None else "builtin",
            "template_id": str(template["id"]) if template else None,
            "template_name": (template or {}).get("template_name"),
            "version_id": str(version["id"]) if version else None,
            "version": (version or {}).get("version"),
        }
    return in_use


class TemplateActiveIn(BaseModel):
    is_active: bool = True


@router.post("/licenses/{license_id}/document-templates/{template_id}/active")
async def set_document_template_active(
    license_id: str,
    template_id: str,
    payload: TemplateActiveIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Choose the template this document type is rendered from.

    `is_active: true` makes this the one and turns off every other
    template of the same type in this shop; `false` takes it out of use,
    which puts the shop back on the built-in layout unless they pick
    another. Nothing already issued changes — a generated document names
    the version that rendered it, which is what
    `generated_documents.template_version_id` is for.

    `setting.manage`, like every other template route, and audited by the
    Data tier the same way (one row for the template chosen, one for each
    that stopped being active).
    """
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    principal.require_feature("feature.custom_documents")
    try:
        return await client.set_document_template_active(
            license_id, template_id, is_active=payload.is_active,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/document-templates/{template_id}/versions")
async def list_document_template_versions(
    license_id: str,
    template_id: str,
    request: Request,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Every version of a template, so a draft can be found and published.

    Includes superseded ones: a shop that published something wrong needs
    to see the version it had before in order to go back to it.
    """
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    try:
        versions = await client.list_document_template_versions(license_id, template_id)
    except DataTierError as exc:
        raise _propagate(exc)

    # A link back to the Word file this version was made from, when there
    # is one. The compiled HTML is what renders, so without this a shop
    # that wants to change one line of their layout has nothing to open.
    #
    # Offered only when a real .docx was uploaded. The test used to be
    # "not upload://", which is a list of the paths that are NOT files —
    # and it missed `builtin://none`, the path a built-in version carries,
    # so the built-in layout showed a download button that answered
    # "stored path 'builtin://none' does not belong to bucket …" (owner,
    # 9 Sep 2026). `intermediate_model.kind == "docx_upload"` is the
    # positive signal: it is written by the upload route at the moment the
    # original bytes are stored, and by nothing else.
    from .services.assets import asset_link

    # The same fallback the document-link route uses: PUBLIC_BASE_URL has
    # no default and is not set in dev, and without it `asset_link`
    # answers None — so the download button silently vanished on every
    # environment but production. The request already carries the origin
    # it arrived on; the setting still wins when set (a custom domain in
    # front of Cloud Run).
    base = (settings.public_base_url or "").rstrip("/") or str(request.base_url).rstrip("/")

    for version in versions:
        model = version.get("intermediate_model") or {}
        source = str(version.get("source_docx_path") or "")
        version["source_docx_url"] = (
            asset_link(
                source, content_type=DOCX_CONTENT_TYPE,
                filename=str(model.get("filename") or "template.docx"),
                base_url=base or None,
            )
            if str(model.get("kind") or "") == "docx_upload"
            else None
        )
    return versions


@router.post(
    "/licenses/{license_id}/document-templates/{template_id}"
    "/versions/{version_id}/preview"
)
async def preview_document_template(
    license_id: str,
    template_id: str,
    version_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Look at a template version before anyone receives a document made from it.

    The owner's report, 9 Sep 2026: "ตัวอย่างของใบเสนอราคาที่เป็นต้นแบบ
    กดเปิดดูไม่ได้". There was no way to open a template at all — the
    only button on a version was publish, so the first time anyone saw a
    layout rendered was on a quote already sent to a customer.

    HTML, not a PDF. It goes through the same path a real document takes
    — the stored compiled template, `fill_template`, a snapshot built by
    the real snapshot builder — so what is shown is what will print. What
    it does NOT do is call SmartBrowz: a PDF round-trip costs an external
    request and a credential per look, and the difference between the two
    is the page geometry, not the content or the values. The reviewer is
    checking "is my company name in the right place and are the totals
    filled in", and HTML answers that. `GET /quotes/{id}/pdf` remains the
    way to see the real PDF of a real quote.

    `setting.manage`, like every other template route. Preview does NOT
    publish (10.7): a draft becomes `previewed`, and nothing else moves.
    A version that is already previewed or published renders just the
    same — you can always look — it simply has no status left to change.
    """
    from .services.documents.design import active_content_in
    from .services.documents.fill import fill_template, unknown_placeholders
    from .services.documents.samples import sample_snapshot
    from .services.storage.base import (
        DocumentStoreError, DocumentStoreNotConfigured, get_document_store,
    )

    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")

    try:
        templates = await client.list_document_templates(license_id)
        template = next(
            (t for t in templates if str(t.get("id")) == str(template_id)), None,
        )
        if template is None:
            raise HTTPException(status_code=404, detail="template not found")
        versions = await client.list_document_template_versions(license_id, template_id)
        version = next(
            (v for v in versions if str(v.get("id")) == str(version_id)), None,
        )
        if version is None:
            # Found through the template's own version list rather than by
            # id alone, so a version id from another tenant OR from
            # another template of this tenant is a 404 either way.
            raise HTTPException(status_code=404, detail="template version not found")
    except DataTierError as exc:
        raise _propagate(exc)

    document_type = str(template.get("document_type") or "quote")
    compiled = str(version.get("compiled_template_path") or "")
    if not compiled or compiled.startswith("builtin://"):
        # The built-in layout is not a stored file; it is code. Rendering
        # it here would mean a second renderer, which is the one thing
        # this endpoint exists not to be.
        raise HTTPException(
            status_code=409,
            detail=(
                "แบบฟอร์มมาตรฐานของระบบไม่มีไฟล์ให้ดูตัวอย่าง "
                "(the built-in layout has no uploaded file to preview)"
            ),
        )

    try:
        raw = await get_document_store().get(path=compiled)
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DocumentStoreError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "หาไฟล์แบบฟอร์มรุ่นนี้ไม่พบ อาจถูกลบไปแล้ว ลองอัปโหลดใหม่ "
                f"(stored template file is missing: {exc})"
            ),
        )

    snapshot = sample_snapshot(document_type)
    source = raw.decode("utf-8", errors="replace")

    # Uploads are rebuilt from a whitelist now (review v3, T02), but this
    # endpoint also serves versions stored BEFORE that was true — the
    # review asked for the old ones, not only the new. A stored template
    # holding active content is not handed out: the dashboard renders what
    # this returns, and one of its buttons opens it in the phone's own
    # browser. Refusing is better than quietly rewriting someone's layout
    # behind their back, because the shop needs to know their file is not
    # the file the system will print.
    active = active_content_in(source)
    if active:
        raise HTTPException(
            status_code=409,
            detail=(
                "แบบฟอร์มรุ่นนี้มีส่วนที่ระบบไม่อนุญาตให้แสดงหรือพิมพ์ ("
                + ", ".join(active)
                + ") จึงยังเปิดดูไม่ได้ กรุณาอัปโหลดไฟล์นี้ใหม่อีกครั้ง "
                "ระบบจะกรองส่วนนั้นออกให้ตอนอัปโหลด "
                "(this stored version contains active content and is not "
                "served; upload it again and it will be filtered)"
            ),
        )
    html = fill_template(source, snapshot)

    # Mark it previewed, and only from draft — mark_previewed refuses any
    # other status by design, and a reviewer looking at an already
    # published version must not get an error for looking.
    status_now = str(version.get("status") or "")
    if status_now == "draft":
        try:
            marked = await client.preview_document_template_version(
                license_id, version_id, actor_id=principal.chann_uid,
            )
            status_now = str(marked.get("status") or status_now)
        except DataTierError:
            # Looking must not fail because the bookkeeping did.
            log.exception("could not mark template version %s previewed", version_id)

    return {
        "template_id": str(template_id),
        "version_id": str(version_id),
        "version": version.get("version"),
        "status": status_now,
        "document_type": document_type,
        "html": html,
        # The same advisory the upload gives, recomputed against the
        # version actually stored — an old draft uploaded before a
        # placeholder was renamed says so here rather than on paper.
        "unknown_placeholders": unknown_placeholders(source, snapshot),
    }


class QuoteTermsPatchIn(BaseModel):
    valid_until: str | None = None
    discount_percent: str | float | None = None
    discount_amount: str | float | None = None


@router.patch("/licenses/{license_id}/quotes/{quote_id}/terms")
async def set_quote_terms(
    license_id: str,
    quote_id: str,
    payload: QuoteTermsPatchIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """When the offer expires, and what came off the price."""
    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    try:
        return await client.set_quote_terms(
            license_id, quote_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


# --------------------------------------------------- related lists
#
# What a record is connected to, for the panel under it. Every CRM shows
# a record's activities and notes on the record; until now both existed
# only in chat, so opening a customer told you nothing about the
# appointment made with them ten minutes earlier.


@router.get("/licenses/{license_id}/follow-ups")
async def list_follow_ups(
    license_id: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    status: str | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("followup.read")
    try:
        rows = await client.list_follow_ups(license_id, status=status)
    except DataTierError as exc:
        raise _propagate(exc)
    # Narrowed here rather than in the Data Tier: its list endpoint has no
    # entity filter, and adding one for a per-tenant list this size is a
    # migration's worth of ceremony for a filter the browser can do.
    if entity_type and entity_id:
        rows = [
            r for r in rows
            if str(r.get("entity_type") or "") == entity_type
            and str(r.get("entity_id") or "") == entity_id
        ]
    return rows


class NoteIn(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    body: str


class NoteBodyIn(BaseModel):
    body: str


@router.post("/licenses/{license_id}/notes", status_code=201)
async def create_note(
    license_id: str,
    payload: NoteIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Write a note from the dashboard.

    Chat could write notes from the first week of Phase 6; the dashboard
    could only read them, so the panel that shows a record's history was
    the one place you could not add to it.
    """
    _require_same_tenant(principal, license_id)
    principal.require("note.create")
    try:
        return await client.create_note(
            license_id,
            {
                "entity_type": payload.entity_type,
                "entity_id": str(payload.entity_id),
                "body": payload.body,
            },
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/notes/{note_id}")
async def update_note(
    license_id: str,
    note_id: uuid.UUID,
    payload: NoteBodyIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("note.update")
    try:
        return await client.update_note(
            license_id, str(note_id), payload.body, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.delete("/licenses/{license_id}/notes/{note_id}", status_code=204)
async def delete_note(
    license_id: str,
    note_id: uuid.UUID,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    # Deleting is an edit down to nothing, so it takes the same key
    # rather than a note.delete that the catalogue has never had.
    _require_same_tenant(principal, license_id)
    principal.require("note.update")
    try:
        await client.delete_note(license_id, str(note_id), actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
    return None


@router.get("/licenses/{license_id}/notes")
async def list_notes(
    license_id: str,
    entity_type: str,
    entity_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    # Notes hang off a customer, deal or quote, and reading them needs
    # the same permission as reading the record they hang off.
    principal.require(f"{entity_type}.read")
    try:
        rows = await client.list_notes(license_id, entity_type, entity_id)
    except DataTierError as exc:
        raise _propagate(exc)
    # Who wrote it (review C18, 6 Sep 2026): the row carries a chann_uid
    # and the panel showed nothing, so every note read as anonymous.
    names: dict[str, str] = {}
    for row in rows:
        uid = str(row.get("author_chann_uid") or "")
        if uid and uid not in names:
            try:
                profile = await client.get_profile(uid) or {}
            except Exception:  # noqa: BLE001 — a nameless note, not a failure
                profile = {}
            names[uid] = " ".join(
                p for p in (profile.get("first_name"), profile.get("last_name")) if p
            )
        row["author_display_name"] = names.get(uid) or None
    return rows



# ---------------------------------------------------------------- Phase 15
# Live chat (PLAN_3OA B6). The customer's side is scoped to their own
# conversation by construction; the shop's side is gated by the
# chat_session.* keys the Data tier already defines for Sales/CS.

class ChatLineBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ChatImageBody(BaseModel):
    """A picture into the conversation (round 20T): a data: URL from the
    browser, as the job photos travel, and the words to go with it."""
    image: str
    caption: str | None = Field(default=None, max_length=1000)


class ChatOpenBody(BaseModel):
    content: str | None = Field(default=None, max_length=4000)
    product_id: str | None = None


async def _chat_session_for(
    client: DataClient, principal: TenantPrincipal, license_id: str, session_id: str,
) -> dict:
    try:
        session = await client.get_chat_session(license_id, session_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if session is None or (
        principal.is_customer and str(session.get("customer_chann_uid")) != principal.chann_uid
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "chat_session_not_found"})
    return session


#: The dashboard polls the live tab every 8 seconds, and the sweep is a
#: CROSS-TENANT write pass — overdue answers escalated, dead conversations
#: closed, LINE pushes sent — so awaiting it made every poll wait for work
#: that has nothing to do with drawing a list, and every open dashboard in
#: the platform started one (owner, 18 ก.ย. 2569: "กดดูระหว่าง เปิดอยู่ กับ
#: ทั้งหมด ยังโหลดช้าอยู่"). Cloud Scheduler runs the same sweep every five
#: minutes; this is only the safety net for a deployment without one, so
#: once a minute is plenty and the list must never wait for it.
_SWEEP_EVERY_S = 60.0
_last_sweep_at = 0.0


def _sweep_soon(client: DataClient) -> None:
    """Start the clock tick if it is due, and return immediately."""
    global _last_sweep_at

    now = time.monotonic()
    if now - _last_sweep_at < _SWEEP_EVERY_S:
        return
    _last_sweep_at = now

    async def _run() -> None:
        # Its OWN client, never the request's (round 20W): the request that
        # started this task returns at once, its DataClient is closed by
        # the dependency's `finally`, and the sweep then died mid-flight
        # with httpcore.ReadError — 40 times on DEV over 20–21 ก.ย. 2569,
        # each time after the Data tier had already stamped the overdue
        # rows, which is why no "ยังไม่ได้รับคำตอบ" warning ever reached the
        # shop.
        own = DataClient()
        try:
            await live_chat.sweep(own)
        except Exception:  # noqa: BLE001 — the scheduler runs it again in five minutes
            logging.getLogger(__name__).exception("chat sweep from the dashboard failed")
        finally:
            await own.aclose()

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:  # pragma: no cover — no loop means no request either
        logging.getLogger(__name__).debug("no running loop for the chat sweep")


@router.get("/licenses/{license_id}/chat-sessions")
async def list_chat_sessions(
    license_id: str,
    status_filter: str | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Staff: every conversation of the shop (live first). Customer: their
    own. `status_filter` = live | closed | timeout | all (default live)."""
    _require_same_tenant(principal, license_id)
    wanted = status_filter or "live"
    if principal.is_customer:
        try:
            rows = await client.list_chat_sessions(
                license_id, status=None if wanted == "all" else wanted,
                customer_chann_uid=principal.chann_uid, limit=20,
            )
        except DataTierError as exc:
            raise _propagate(exc)
        return rows
    principal.require("chat_session.view")
    # The dashboard list is also the platform's most frequent clock tick:
    # overdue answers are escalated and dead conversations closed here,
    # so a shop without a scheduler still gets both.
    #
    # Only for the LIVE view, though. The sweep is a write pass over the
    # open conversations, and running it before every list made opening
    # "ทั้งหมด" — which then reads up to 200 rows, mostly closed ones —
    # wait for a write pass whose only subject is the live ones
    # (owner, 17 ก.ย. 2569: "กดทั้งหมดแล้วมันโหลดแชทที่ปิดไปแล้วขึ้นมาช้า").
    # Cloud Scheduler calls /platform/chat/sweep every five minutes as
    # well, so nothing is lost by skipping it on the closed tabs.
    if wanted == "live":
        _sweep_soon(client)
    try:
        return await client.list_chat_sessions(
            license_id, status=None if wanted == "all" else wanted, limit=200,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/chat-sessions", status_code=201)
async def open_chat_session(
    license_id: str,
    payload: ChatOpenBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """"คุยกับร้าน" from the home screen — the same start as the chat's."""
    _require_same_tenant(principal, license_id)
    if not principal.is_customer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "customers_only"})
    principal.require("customer.read")  # the customer set always carries it; staff never reach here
    try:
        session, created, _unseen = await live_chat.start_session(
            client, license_id=license_id, chann_uid=principal.chann_uid,
            first_message=payload.content, product_id=payload.product_id,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {**session, "created": created}


class ShopChatStartBody(BaseModel):
    customer_chann_uid: str
    content: str | None = Field(default=None, max_length=4000)


@router.post("/licenses/{license_id}/chat-sessions/start", status_code=201)
async def start_chat_session_from_shop(
    license_id: str,
    payload: ShopChatStartBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The shop opens the conversation from a job or the customer list
    (round 19g) — the customer hears in LINE; answers still come from the
    chats page."""
    _require_same_tenant(principal, license_id)
    principal.require("chat_session.reply")
    member_id = await _member_of(client, license_id, principal)
    try:
        session, created = await live_chat.start_session_by_shop(
            client, license_id=license_id, customer_chann_uid=payload.customer_chann_uid,
            member_id=member_id, agent_chann_uid=principal.chann_uid, first_message=payload.content,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return {**session, "created": created}


@router.get("/licenses/{license_id}/chat-sessions/{session_id}/messages")
async def list_chat_messages(
    request: Request,
    license_id: str,
    session_id: str,
    since: str | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    if not principal.is_customer:
        principal.require("chat_session.view")
    session = await _chat_session_for(client, principal, license_id, session_id)
    try:
        rows = await client.list_chat_messages(license_id, session_id, since=since)
        # Reading is acknowledging: the list's unread count is for the
        # other side's lines the reader has not yet opened.
        await client.mark_chat_read(
            license_id, session_id, reader="customer" if principal.is_customer else "agent",
        )
    except DataTierError as exc:
        raise _propagate(exc)
    # A picture on the thread comes back as a link the page can show
    # (round 20T); the request origin is the fallback base, as for the
    # job photos.
    return {"session": session, "messages": with_image_links(rows, base_url=str(request.base_url))}


@router.post("/licenses/{license_id}/chat-sessions/{session_id}/messages", status_code=201)
async def send_chat_message(
    license_id: str,
    session_id: str,
    payload: ChatLineBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Customer: a line into their conversation. Staff: the shop's answer —
    it reaches the customer's LINE, and the sender owns the conversation."""
    _require_same_tenant(principal, license_id)
    session = await _chat_session_for(client, principal, license_id, session_id)
    if principal.is_customer and str(session.get("status")) not in ("open", "assigned"):
        # The customer reopens with "คุยกับร้าน"; the shop may answer a
        # parked conversation — that answer invites the customer back.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "chat_session_closed"})
    try:
        if principal.is_customer:
            return await live_chat.customer_message(
                client, license_id=license_id, session=session, chann_uid=principal.chann_uid,
                text=payload.content,
            )
        principal.require("chat_session.reply")
        member = await client.get_member(
                license_id, principal.chann_uid, channel=member_channel(principal.audience),
            )
        return await live_chat.agent_reply(
            client, license_id=license_id, session=session, agent_chann_uid=principal.chann_uid,
            member_id=str(member.get("id")) if member else None, text=payload.content,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/chat-sessions/{session_id}/images", status_code=201)
async def send_chat_image(
    request: Request,
    license_id: str,
    session_id: str,
    payload: ChatImageBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Round 20T — a picture into the conversation, with or without words.
    The same rules as a line of text: a customer only into a live one, the
    shop into any (a parked one gets the reopen invitation). The picture
    is normalised and stored first; the row and the LINE push follow."""
    from .services.chat_images import chat_image_link, store_chat_image
    from .services.photos import PhotoRefused

    _require_same_tenant(principal, license_id)
    session = await _chat_session_for(client, principal, license_id, session_id)
    if principal.is_customer and str(session.get("status")) not in ("open", "assigned"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "chat_session_closed"})
    if not principal.is_customer:
        principal.require("chat_session.reply")
    content, content_type = _decode_data_url(payload.image)
    try:
        path = await store_chat_image(
            license_id=license_id, session_id=session_id, content=content, content_type=content_type,
        )
    except PhotoRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — the store said no; the row must not pretend
        log.exception("chat picture could not be stored")
        raise HTTPException(status_code=502, detail=f"picture could not be stored: {exc}")
    caption = (payload.caption or "").strip()
    link = chat_image_link(path, base_url=str(request.base_url))
    try:
        if principal.is_customer:
            row = await live_chat.customer_message(
                client, license_id=license_id, session=session, chann_uid=principal.chann_uid,
                text=caption, image_path=path,
            )
        else:
            member = await client.get_member(
                license_id, principal.chann_uid, channel=member_channel(principal.audience),
            )
            row = await live_chat.agent_reply(
                client, license_id=license_id, session=session, agent_chann_uid=principal.chann_uid,
                member_id=str(member.get("id")) if member else None, text=caption,
                image_path=path, image_url=link,
            )
    except DataTierError as exc:
        raise _propagate(exc)
    return {**row, "image_url": link}


@router.post("/licenses/{license_id}/chat-sessions/{session_id}/close")
async def close_chat_session(
    license_id: str,
    session_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    session = await _chat_session_for(client, principal, license_id, session_id)
    if not principal.is_customer:
        principal.require("chat_session.reply")
    try:
        return await live_chat.close_session(
            client, license_id=license_id, session=session,
            by="customer" if principal.is_customer else "agent", actor_chann_uid=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)



# ======================================================================== Phase 17
# Ad-hoc AI reports from the dashboard. Same permission as the chat path
# (view_reports), same whitelist, same Data tier door.

class AiReportAskBody(BaseModel):
    message: str
    language: str | None = None


class AiReportRunBody(BaseModel):
    spec: dict
    language: str | None = None


@router.post("/licenses/{license_id}/reports/ai")
async def ai_report_ask(
    license_id: str,
    body: AiReportAskBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Plain language in, a spec + result + files out (or a clarifying
    question). The model only ever produces the spec.

    Final review I3: the five come FIRST, by the same helper and in the
    same order as chat (`chat.basic_report_asked_for`), so a sentence that
    is one of them is answered free here exactly as it is on LINE —
    `{"basic": report, "text": …, "free": True}`, no picture, no credit.
    None keeps the AI road below."""
    from .services import basic_reports, reports_ai
    from .services.ai.client import AINotConfigured, AIUnavailable
    from .services.chat import basic_report_asked_for

    _require_same_tenant(principal, license_id)
    principal.require("view_reports")
    language = body.language or "th"
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message is required")
    key = await basic_report_asked_for(body.message, language=language)
    if key is not None:
        if key in entitlements.SERVICE_BASIC_REPORTS:
            # Owner decision Q4: Starter sees three basic reports.
            principal.require_feature("feature.service")
        try:
            report = await basic_reports.fetch(client, license_id=license_id, key=key)
        except DataTierError as exc:
            raise _propagate(exc)
        return {"basic": report, "text": basic_reports.as_text(report, language), "free": True}
    # Round 21D: every other question is the AI road — locked on Starter,
    # before the model is asked anything (spec §5.6).
    principal.require_feature(entitlements.AI_REPORTS)
    try:
        out = await reports_ai.handle_report_request(
            client, license_id=license_id, message=body.message, language=language,
            actor_id=principal.chann_uid, company_name=_company_name_of(principal),
            # The dashboard draws its own bars, but the picture is what a
            # person forwards to a colleague — same chart the chat sends.
            with_chart=True,
        )
    except reports_ai.ReportSpecInvalid as exc:
        return {"error": "spec_invalid", "message": reports_ai.INVALID[language if language in reports_ai.INVALID else "th"].format(reason=str(exc))}
    except (AINotConfigured, AIUnavailable):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI is not available right now")
    except DataTierError as exc:
        raise _propagate(exc)
    return await _charge_for_the_question(
        client, license_id, await _charge_for_the_picture(client, license_id, out))


async def _charge_for_the_picture(client, license_id: str, out: dict) -> dict:
    """Spend one of the month's AI charts, but only if there IS a picture.

    `chart_quota.spend_one` was called from exactly one place in the whole
    system - the chat road - so the dashboard drew AI charts for free and
    a shop that had used its month simply opened the dashboard and carried
    on. The allowance in the Chann admin screen counted the chat only, and
    the "used X/30" it showed was not the truth (whole-system reach audit,
    18 September 2026).

    Over the allowance the numbers, the table and the files all still come
    back - only the image is withheld, which is the owner's standing rule:
    running out of chart quota must never stop a shop finding out its own
    numbers.
    """
    from .services import chart_quota

    if not out.get("chart"):
        return out
    quota = await chart_quota.spend_one(client, license_id=license_id)
    out["quota"] = chart_quota.receipt(quota, charged_for="picture")
    if not quota.get("allowed"):
        out["chart"] = None
    return out


async def _charge_for_the_question(client, license_id: str, out: dict) -> dict:
    """Round 21C: an ad-hoc question costs a credit. The rule lives in
    `chart_quota.charge_for_the_question`, shared with the LINE road, so
    the dashboard and the chat cannot charge the same question differently.
    """
    from .services import chart_quota

    return await chart_quota.charge_for_the_question(client, license_id=license_id, out=out)


@router.get("/licenses/{license_id}/reports/ai/options")
async def ai_report_options(
    license_id: str,
    language: str = "th",
    principal: TenantPrincipal = Depends(get_tenant_principal),
):
    """What the spec editor is allowed to offer — straight from the whitelist
    the validator uses, so the two can never drift apart."""
    from .services import reports_ai

    _require_same_tenant(principal, license_id)
    principal.require("view_reports")
    return reports_ai.spec_options(language if language in ("th", "en") else "th")


@router.post("/licenses/{license_id}/reports/ai/run")
async def ai_report_run(
    license_id: str,
    body: AiReportRunBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Run an edited spec straight from the dashboard — still whitelisted.

    Final review I2: the spec editor asks the model nothing about the
    question, so the question credit is never spent here. The one model
    call this route can make is the picture's design, which only the
    designed road takes (a result with rows → `chart_plan`); that picture
    is charged as a picture, as it was before round 21C. A single-number
    card is drawn by code and costs nothing."""
    from .services import reports_ai

    _require_same_tenant(principal, license_id)
    principal.require("view_reports")
    principal.require_feature(entitlements.AI_REPORTS)
    language = body.language or "th"
    try:
        spec = reports_ai.validate_query_spec(body.spec)
        result = await client.run_report_query(license_id, spec, actor_id=principal.chann_uid)
    except reports_ai.ReportSpecInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)
    text = reports_ai.report_text(spec, result, language)
    files = await reports_ai.publish_files(spec, result, language, license_id=license_id, company_name=_company_name_of(principal))
    chart, plottable = await reports_ai.publish_chart_for(spec, result, language, license_id=license_id)
    out = {"spec": spec, "result": result, "text": text, "files": files,
           "chart": chart, "plottable": plottable}
    if not result.get("rows"):
        return out
    return await _charge_for_the_picture(client, license_id, out)


def _company_name_of(principal: TenantPrincipal) -> str:
    return str(getattr(principal, "company_name", "") or "")


@router.get("/licenses/{license_id}/reports/basic/{key}")
async def basic_report(
    license_id: str, key: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """One of the five fixed reports. No model call, and — deliberately —
    no `_charge_for_the_picture`: these never cost a credit (spec §5)."""
    from .services import basic_reports

    _require_same_tenant(principal, license_id)
    principal.require("view_reports")
    if key in entitlements.SERVICE_BASIC_REPORTS:
        principal.require_feature("feature.service")
    try:
        return await basic_reports.fetch(client, license_id=license_id, key=key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)


# ==================================================================== audit (3.4/3.5)


@router.get("/licenses/{license_id}/audit-log")
async def tenant_audit_log(
    license_id: str,
    entity_type: str | None = None,
    actor_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The shop's own audit trail. The Data Tier route existed with no
    caller, so `audit_log.view` was a permission that unlocked nothing
    (review, 6 Sep 2026)."""
    _require_same_tenant(principal, license_id)
    principal.require("audit_log.view")
    try:
        rows, total = await client.list_audit_log_with_total(
            license_id, entity_type=entity_type, actor_type=actor_type,
            limit=max(1, min(int(limit), 500)), offset=max(0, int(offset)),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    names = await _actor_names(client, license_id)
    return [
        {**row, "actor_name": names.get(str(row.get("actor_id") or ""), "")}
        for row in rows
    ]


async def _actor_names(client: DataClient, license_id: str) -> dict[str, str]:
    """chann_uid -> the name a person recognises.

    An audit row stores the actor's chann_uid, because that is what the
    Data tier holds. A page of "U1a2b3c\u2026 แก้ไขลูกค้า" tells a shop
    owner nothing, so the names are resolved here \u2014 once per request,
    not once per row.
    """
    try:
        members = await client.list_members(license_id)
    except DataTierError:
        return {}
    named = await _with_names(client, members)
    return {
        str(m.get("chann_uid") or ""): str(m.get("display_name") or "")
        for m in named
        if m.get("chann_uid")
    }


# --------------------------------------------------------------- invites
#
# A shop could issue a technician invite from chat since Phase 6.5 and then
# had no way to see which codes were still out, or to cancel one that had
# leaked \u2014 list_invites and revoke_invite existed in the Data tier and
# in DataClient with no caller above them (audit, 17 ก.ย. 2569). A code is
# a key to the shop; a key you cannot take back is the gap.


def _invite_status(row: dict, *, now: datetime) -> str:
    """open | used | revoked | expired \u2014 what a person needs to know."""
    if row.get("revoked_at"):
        return "revoked"
    if int(row.get("used_count") or 0) >= int(row.get("max_uses") or 1):
        return "used"
    expires = row.get("expires_at")
    if expires:
        when = expires if isinstance(expires, datetime) else datetime.fromisoformat(str(expires))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when <= now:
            return "expired"
    return "open"


@router.get("/licenses/{license_id}/invites")
async def tenant_invites(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Every invite code this shop has issued, newest first, each carrying
    the one thing the list is read for: whether it still works."""
    _require_same_tenant(principal, license_id)
    _staff_only(principal)
    principal.require("member.manage")
    try:
        rows = await client.list_invites(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    now = datetime.now(timezone.utc)
    return [{**row, "status": _invite_status(row, now=now)} for row in rows]


@router.post("/licenses/{license_id}/invites/{invite_id}/revoke")
async def tenant_revoke_invite(
    license_id: str,
    invite_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Cancel a code. Idempotent in the Data tier, so cancelling twice is
    not an error \u2014 the person pressing the button wants the code dead,
    and it is."""
    _require_same_tenant(principal, license_id)
    _staff_only(principal)
    principal.require("member.manage")
    try:
        row = await client.revoke_invite(license_id, invite_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
    return {**row, "status": _invite_status(row, now=datetime.now(timezone.utc))}


# ------------------------------------------------------------ round 21B: API keys


class ApiKeyCreateBody(BaseModel):
    name: str


def _api_docs_url() -> str | None:
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/api/ext/v1/docs" if base else None


@router.get("/licenses/{license_id}/api-keys")
async def list_api_keys(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The shop's live keys (revoked ones are kept for the audit trail
    but not shown) and where the outside party reads the docs."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    _owner_only(principal)
    try:
        rows = await client.list_api_keys(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return {"keys": [r for r in rows if not r.get("revoked_at")], "docs_url": _api_docs_url()}


@router.post("/licenses/{license_id}/api-keys", status_code=201)
async def create_api_key(
    license_id: str,
    payload: ApiKeyCreateBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The one response that carries the key. Nothing stores it after this."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    # Owner-only first: a non-owner is refused for who they are on every
    # plan, never pointed at an upgrade they could not act on.
    _owner_only(principal)
    # Round 21D (ruling R-D): MAKING a key is the External API feature;
    # listing and revoking stay open on every plan, so a downgraded
    # owner can still see and revoke what an outside system holds.
    principal.require_feature("feature.external_api")
    name = " ".join(payload.name.split())
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    try:
        return await client.create_api_key(
            license_id, {"name": name, "created_by_chann_uid": principal.chann_uid}, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/api-keys/{key_id}/revoke")
async def revoke_api_key(
    license_id: str,
    key_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    _owner_only(principal)
    try:
        return await client.revoke_api_key(license_id, key_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
