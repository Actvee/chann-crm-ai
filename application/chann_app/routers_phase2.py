"""Phase 2 business API: roles, permissions, settings and owner transfer."""
from __future__ import annotations

import logging
import re
import uuid

import hashlib
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .config import settings
from .data_client import DataClient, DataTierError
from .routers_admin import get_data_client, require_admin
from .services import approval as approval_service
from .services import storefront as storefront_service
from .services import csv_import, live_chat
from .services.authorization import TenantPrincipal, resolve_tenant_principal

router = APIRouter(prefix="/api/v1", tags=["phase2"])
log = logging.getLogger(__name__)


class RoleWriteIn(BaseModel):
    role_name: str = Field(min_length=1, max_length=64)
    permission_keys: list[str]


class RolePolicyCompileIn(BaseModel):
    policy_prompt: str = Field(min_length=1, max_length=4000)


class SettingWriteIn(BaseModel):
    setting_value: dict | list | str | int | float | bool | None


class MemberRoleWriteIn(BaseModel):
    role_name: str = Field(min_length=1, max_length=64)


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
    )


def _require_same_tenant(principal: TenantPrincipal, license_id: str) -> None:
    if principal.license_id != license_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant scope mismatch")


def _propagate(exc: DataTierError) -> HTTPException:
    allowed = {400, 404, 409, 422, 503}
    code = exc.status_code if exc.status_code in allowed else 502
    # The structured body when the Data Tier sent one. Some refusals carry
    # data the caller must act on — a duplicate names the existing record
    # so a UI can offer to open it, and the dispatch gate names the fields
    # still missing. exc.detail is the str() of those, which arrives as
    # "{'error': 'duplicate', ...}" and forces the caller to parse a repr.
    return HTTPException(status_code=code, detail=exc.structured or exc.detail)


@router.post("/licenses/{license_id}/roles/compile-policy")
async def compile_role_policy(
    license_id: str,
    payload: RolePolicyCompileIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
):
    _require_same_tenant(principal, license_id)
    principal.require("role.manage")
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
    try:
        return await client.create_role(license_id, payload.model_dump(), actor_id=principal.chann_uid)
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
    try:
        return await client.update_role(license_id, role_name, payload.model_dump(), actor_id=principal.chann_uid)
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
        return await client.set_member_role(license_id, chann_uid, payload.role_name, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


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
        return await client.request_ownership_transfer(
            license_id, principal.chann_uid, payload.to_chann_uid
        )
    except DataTierError as exc:
        raise _propagate(exc)


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
        raise _propagate(exc)


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
    body = payload.model_dump(exclude_unset=True)
    if "vat_rate_percent" in body:
        percent = body.pop("vat_rate_percent")
        body["vat_rate"] = None if percent is None else percent / Decimal(100)

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
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("quote.read")
    try:
        return await client.list_quotes(license_id, status_filter)
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
    from .services.pdf.base import PdfOptions, get_renderer
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    renderer = get_renderer("smartbrowz")
    try:
        result = await renderer.render(
            render_quote_html(snapshot), PdfOptions(),
            idempotency_key=f"quote:{license_id}:{quote_id}",
        )
    except SmartBrowzNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except QuoteNotRenderable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except SmartBrowzNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
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


# ------------------------------------------------ Phase 10 dashboard reads
#
# Read-only projections for the LIFF dashboards. Master Spec 9.2 listed
# these from the start; every earlier phase shipped only the chat side.


@router.get("/licenses/{license_id}/customers")
async def list_customers(
    license_id: str,
    stage: str | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("customer.read")
    try:
        return await client.list_customers(license_id, stage)
    except DataTierError as exc:
        raise _propagate(exc)


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
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.read")
    try:
        return await client.list_deals(license_id, stage)
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/products")
async def list_products(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("product.manage")
    try:
        return await client.list_products(license_id)
    except DataTierError as exc:
        raise _propagate(exc)


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
            license_id, deal_id, payload.model_dump(exclude_unset=True),
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
    try:
        return await client.update_customer(
            license_id, customer_id, payload.model_dump(exclude_unset=True),
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
    try:
        return await client.promote_customer(
            license_id, customer_id, actor_id=principal.chann_uid,
        )
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
            "Content-Disposition": 'inline; filename="quote.pdf"',
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
    principal.require("quote.read")

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
            "Content-Disposition": 'inline; filename="document.pdf"',
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
            "Content-Disposition": 'inline; filename="quote.pdf"',
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
        "permission_keys": sorted(principal.permission_keys),
    }


# ------------------------------------------------------------ products (write)


class ProductIn(BaseModel):
    product_id: str
    product_name: str
    sku: str | None = None
    category: str | None = None
    unit_price: str | float | None = None
    description: str | None = None


class CsvBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)


def _csv_rejected(exc: csv_import.CsvRejected) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": "csv_rejected", "message": str(exc)},
    )


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
            license_id, product_id, payload.model_dump(exclude_none=True),
            actor_id=principal.chann_uid,
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
            return await client.claim_warranty(
                license_id,
                {"serial_number": payload.serial_number,
                 "customer_chann_uid": principal.chann_uid},
                actor_id=principal.chann_uid,
            )
        except DataTierError as exc:
            raise _propagate(exc)
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
        return await client.claim_warranty(
            license_id,
            {"serial_number": payload.serial_number, "customer_chann_uid": principal.chann_uid},
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/warranties")
async def list_warranties(
    license_id: str,
    serial_number: str | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The shop's book of registered units (staff). A customer principal
    gets only their own rows, the same as /mine."""
    _require_same_tenant(principal, license_id)
    principal.require("warranty.read")
    try:
        if principal.is_customer:
            return await client.list_warranties(license_id, customer_chann_uid=principal.chann_uid)
        return await client.list_warranties(license_id, serial_number=serial_number)
    except DataTierError as exc:
        raise _propagate(exc)


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
    principal.require("service_report.read")
    reissue = bool((payload or {}).get("reissue"))
    try:
        rows = await client.list_service_reports(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    report = next((r for r in rows if str(r.get("id")) == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    if principal.is_customer and str(report.get("customer_chann_uid") or "") not in ("", principal.chann_uid):
        raise HTTPException(status_code=404, detail="report not found")
    document_id = str(report.get("generated_document_id") or "")
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
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
    try:
        rows = await client.list_tickets(license_id, status=status, visible_to=visible_to)
        if principal.is_customer:
            # A customer sees their own repairs, never the shop's queue.
            rows = [r for r in rows if str(r.get("customer_chann_uid") or "") == principal.chann_uid]
        return rows
    except DataTierError as exc:
        raise _propagate(exc)


@router.get("/licenses/{license_id}/tickets/{ticket_id}/dispatch-check")
async def ticket_dispatch_check(
    license_id: str,
    ticket_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("ticket.read")
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
    try:
        return await client.claim_ticket(
            license_id, ticket_id, str(payload.get("member_id") or ""),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class PhotoUploadIn(BaseModel):
    """A data: URL from the browser (FileReader / canvas) — JSON, so no
    multipart dependency and the same shape the signature uses."""
    image: str
    photo_type: str = "evidence"


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
    return await photo_links(client, license_id=license_id, ticket_id=ticket_id)


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
            member = await client.get_member(license_id, principal.chann_uid)
            member_id = str((member or {}).get("id") or "") or None
        except Exception:  # noqa: BLE001
            member_id = None
    content, content_type = _decode_data_url(payload.image)
    photo_type = payload.photo_type if payload.photo_type in ("checkin", "checkout", "evidence") else "evidence"
    try:
        return await store_ticket_photo(
            client, license_id=license_id, ticket_id=ticket_id, content=content,
            content_type=content_type, photo_type=photo_type, uploaded_by_member_id=member_id,
        )
    except PhotoRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
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
    try:
        row = await client.reject_ticket(
            license_id, ticket_id, str(payload.get("member_id") or ""),
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
    try:
        report = await client.check_out_ticket(
            license_id, ticket_id,
            member_id=str(payload.get("member_id") or ""),
            # Check-out IS the service report: the Data Tier writes the
            # report row in the same transaction as the status change, so
            # the two can never disagree about whether a visit happened.
            report_data=payload.get("report_data") or {},
            gps_lat=payload.get("gps_lat"), gps_lng=payload.get("gps_lng"),
            photo_url=payload.get("photo_url"),
            actor_id=principal.chann_uid,
        )
        # Phase 14-B: the same hook chat calls — open the approval steps
        # and tell the first approver now. Best-effort: the check-out is
        # committed, and a LINE failure must not turn it into a 500.
        try:
            await approval_service.on_report_submitted(client, license_id=license_id, report=report)
        except Exception:  # noqa: BLE001
            log.exception("approval steps could not be opened for %s", report.get("report_id"))
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
    try:
        return await client.check_in_ticket(
            license_id, ticket_id,
            member_id=str(payload.get("member_id") or ""),
            gps_lat=payload.get("gps_lat"), gps_lng=payload.get("gps_lng"),
            photo_url=payload.get("photo_url"),
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
        return await client.list_service_reports(license_id, status=status)
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
            customers = await client.list_customers(license_id)
            customer = next(
                (c for c in customers if str(c.get("id")) == str(deal["contact_id"])), None,
            )
        return {"quote": quote, "deal": deal, "customer": customer}
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
    try:
        return await client.create_customer(
            license_id, payload.model_dump(exclude_none=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


class DealCreateIn(BaseModel):
    contact_id: str
    notes: str | None = None


@router.post("/licenses/{license_id}/deals", status_code=201)
async def create_deal(
    license_id: str,
    payload: DealCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("deal.create")
    try:
        return await client.create_deal(
            license_id, payload.model_dump(exclude_none=True),
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
            license_id, deal_id, payload.model_dump(exclude_none=True),
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
    try:
        return await client.create_quote(
            license_id, payload.model_dump(), actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


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
    body = payload.model_dump(exclude_none=True)
    if principal.is_customer:
        # Filed from the customer app: the ticket is theirs, whatever the
        # body says — that is what makes it show on their own list.
        body["customer_chann_uid"] = principal.chann_uid
    try:
        return await client.create_ticket(
            license_id, body,
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/tickets/{ticket_id}/assign")
async def assign_ticket_from_dashboard(
    license_id: str,
    ticket_id: str,
    payload: dict,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Dispatch from the queue the dispatcher is already looking at,
    instead of switching to LINE to type a code they can see on screen."""
    _require_same_tenant(principal, license_id)
    principal.require("ticket.update")
    target_type = str(payload.get("target_type") or "")
    target_ref = str(payload.get("target_ref") or "")
    try:
        row = await client.assign_ticket(
            license_id, ticket_id, target_type=target_type, target_ref=target_ref,
            actor_id=principal.chann_uid,
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
    fields = {
        k: v for k, v in (payload or {}).items()
        if k in ("customer_name", "customer_phone", "service_address", "serial_number",
                 "issue_description", "scheduled_date", "scheduled_time")
        and v not in (None, "")
    }
    if not fields:
        raise HTTPException(status_code=422, detail="nothing to update")
    try:
        return await client.update_ticket(license_id, ticket_id, fields, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


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
    principal.require("ticket.update")
    new_status = str((payload or {}).get("status") or "")
    if new_status not in ("cancelled", "completed", "open"):
        raise HTTPException(status_code=422, detail="status must be cancelled, completed or open")
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
    principal.require("ticket.read")
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
    try:
        members = await client.list_members(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    technicians = [
        m for m in members
        if str(m.get("status") or "active") == "active"
        and ("technician" in str(m.get("role") or "").lower() or "ช่าง" in str(m.get("role") or ""))
    ]
    return await _with_names(client, technicians)


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
    try:
        await client.remove_team_member(license_id, team_id, member_id)
    except DataTierError as exc:
        raise _propagate(exc)


async def _with_names(client: DataClient, members: list[dict]) -> list[dict]:
    out = []
    for m in members:
        chann_uid = str(m.get("chann_uid") or "")
        try:
            profile = await client.get_profile(chann_uid) or {}
        except Exception:  # noqa: BLE001 — a missing profile is a nameless row, not a failure
            profile = {}
        name = " ".join(
            p for p in (profile.get("first_name"), profile.get("last_name")) if p
        )
        out.append({**m, "id": str(m.get("id") or ""), "display_name": name or chann_uid,
                    "phone": profile.get("phone")})
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
            license_id, quote_id, payload.model_dump(exclude_none=True),
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
            license_id, quote_id, line_id, payload.model_dump(exclude_unset=True),
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
        raise _propagate(exc)


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
    principal.require("quote.read")

    try:
        document = await client.get_generated_document(license_id, document_id)
    except DataTierError as exc:
        raise _propagate(exc)
    if document is None:
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
            license_id, deal_id, line_id, payload.model_dump(exclude_unset=True),
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
    template_name: str
    html: str
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
    """
    from .services.documents.fill import unknown_placeholders
    from .services.storage.base import DocumentStoreNotConfigured, get_document_store

    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")

    html = payload.html or ""
    if not html.strip():
        raise HTTPException(status_code=400, detail="the template is empty")
    if len(html) > 512_000:
        # Half a megabyte of HTML is not a quote layout; it is an embedded
        # image someone should be hosting instead.
        raise HTTPException(status_code=400, detail="template is too large")

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
        key = (
            f"{license_id}/templates/{existing['id']}/"
            f"{uuid.uuid4().hex}.html"
        )
        stored = await store.put(
            key=key, content=html.encode("utf-8"), content_type="text/html",
        )

        version = await client.create_document_template_version(
            license_id,
            str(existing["id"]),
            {
                "source_docx_path": "upload://html",
                "intermediate_model": {"kind": "html_upload"},
                "mapping_schema": {"kind": "placeholders"},
                "compiled_template_path": stored.path,
            },
            actor_id=principal.chann_uid,
        )
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)

    return {
        "template_id": str(existing["id"]),
        "version_id": str(version["id"]),
        "version": version.get("version"),
        "status": version.get("status"),
        # Reported, not rejected: a placeholder that resolves to nothing
        # may be deliberate, and refusing the upload over one would make
        # the feature unusable for a layout with an optional field.
        "unknown_placeholders": unknown_placeholders(html, _template_sample()),
    }


def _template_sample() -> dict:
    """A representative snapshot, for checking placeholders.

    Shaped like build_quote_snapshot's output rather than invented, so
    "this placeholder resolves" here means it resolves on a real document.
    """
    return {
        "company": {
            "legal_name": "", "address": "", "phone": "", "email": "",
            "tax_id": "", "logo_url": "",
        },
        "customer": {"name": "", "phone": "", "email": "", "address": ""},
        "quote": {"quote_id": "", "status": "", "valid_until": ""},
        "deal": {"deal_id": ""},
        "line_items": [{
            "name": "", "qty": "", "unit_price": "", "line_total": "", "notes": "",
        }],
        "totals": {
            "subtotal": "", "discount_applicable": "", "discount_amount": "",
            "net_total": "", "vat_applicable": "", "vat_rate": "",
            "vat_rate_percent": "", "vat_amount": "", "grand_total": "",
        },
        "issued_at": "",
    }


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
    try:
        return await client.publish_document_template_version(
            license_id, template_id, version_id, actor_id=principal.chann_uid,
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


@router.get("/licenses/{license_id}/document-templates/{template_id}/versions")
async def list_document_template_versions(
    license_id: str,
    template_id: str,
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
        return await client.list_document_template_versions(license_id, template_id)
    except DataTierError as exc:
        raise _propagate(exc)


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
            license_id, quote_id, payload.model_dump(exclude_unset=True),
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
        return await client.list_notes(license_id, entity_type, entity_id)
    except DataTierError as exc:
        raise _propagate(exc)



# ---------------------------------------------------------------- Phase 15
# Live chat (PLAN_3OA B6). The customer's side is scoped to their own
# conversation by construction; the shop's side is gated by the
# chat_session.* keys the Data tier already defines for Sales/CS.

class ChatLineBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


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
    try:
        await live_chat.sweep(client)
    except Exception:
        logging.getLogger(__name__).exception("chat sweep from the dashboard failed")
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


@router.get("/licenses/{license_id}/chat-sessions/{session_id}/messages")
async def list_chat_messages(
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
    return {"session": session, "messages": rows}


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
        member = await client.get_member(license_id, principal.chann_uid)
        return await live_chat.agent_reply(
            client, license_id=license_id, session=session, agent_chann_uid=principal.chann_uid,
            member_id=str(member.get("id")) if member else None, text=payload.content,
        )
    except DataTierError as exc:
        raise _propagate(exc)


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
