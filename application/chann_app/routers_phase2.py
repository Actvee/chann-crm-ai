"""Phase 2 business API: roles, permissions, settings and owner transfer."""
from __future__ import annotations

import re

import hashlib
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from .data_client import DataClient, DataTierError
from .routers_admin import get_data_client, require_admin
from .services.authorization import TenantPrincipal, resolve_tenant_principal

router = APIRouter(prefix="/api/v1", tags=["phase2"])


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
    return HTTPException(status_code=code, detail=exc.detail)


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
    from .services.quote_issue import issue_quote_document
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
        )
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
