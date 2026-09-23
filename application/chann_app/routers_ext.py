"""Round 21B — the external API: /api/ext/v1.

A separate FastAPI app so its OpenAPI shows only this surface. Every
route depends on `api_principal` (the Bearer key), then calls the same
DataClient methods and service functions the dashboard routes call, so
tenant scoping, status machines and "a deal needs products" are all
enforced once, in the code that already enforces them.

Owner, 23 ก.ย. 2569: "ทำ API เลย" — for the customer's accounting/ERP
system first, which is why every list has `updated_since`.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth.api_key import api_principal
from .data_client import DataClient, DataTierError
from .routers_admin import get_data_client
from .routers_phase2 import _propagate
from .services.authorization import TenantPrincipal

DESCRIPTION = """
API สำหรับระบบภายนอก (บัญชี / ERP) ของร้านที่ใช้ Chann CRM AI

* ขอ key จากเจ้าของร้าน: แดชบอร์ด > จัดการร้าน > API
* ส่ง `Authorization: Bearer chann_live_…` ทุกคำขอ
* รายการทุกชนิดรับ `limit` (≤200), `offset` และตอบ `{"items": [...], "total": n}`
* `updated_since` (ISO-8601) บนลูกค้า ดีล งานซ่อม ใบแจ้งหนี้ — ใช้ sync เป็นรอบ
* จำกัด 600 คำขอ/นาที/key — เกินตอบ 429 พร้อม `Retry-After`

Errors are always `{"error": {"code": "...", "message": "..."}}`.
"""

ext_app = FastAPI(
    title="Chann CRM AI — Public API", version="1.0", description=DESCRIPTION,
    docs_url="/docs", redoc_url=None, openapi_url="/openapi.json",
)
log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(api_principal)])

_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
          405: "method_not_allowed", 409: "conflict", 422: "validation_error",
          423: "tenant_suspended", 429: "rate_limited",
          502: "upstream_error", 503: "unavailable"}


def _error_body(status_code: int, detail: Any) -> dict:
    # A message never falls back to `str(detail)`/`str(dict)` — a Python
    # repr like "{'error': 'tenant_suspended'}" reaching an ERP as the
    # human-readable message is not fixed by fixing that one call site;
    # the fallback is always the code string itself.
    #
    # Precedence is `reason_code` -> `error` -> `code`, NOT `code` first:
    # `_invoice_document_error`'s "invoice_state" branch (routers_phase2.py)
    # sets `detail["code"]` to the *business* id (an invoice/deal/quote id,
    # for a UI to say "on Q-2026-0001") alongside `reason_code`/`error` set
    # to the actual taxonomy code ("invoice_state"). Reading `code` first
    # leaked that record id to an ERP as the error code instead of a
    # stable, documented string it could branch on.
    if isinstance(detail, dict):
        code = str(detail.get("reason_code") or detail.get("error") or detail.get("code")
                   or _CODES.get(status_code, "error"))
        return {"error": {"code": code, "message": str(detail.get("message") or code)}}
    return {"error": {"code": _CODES.get(status_code, "error"), "message": str(detail)}}


# Registered on STARLETTE's HTTPException, not FastAPI's: an unknown path
# and a wrong method are raised by Starlette's own router, which knows
# nothing of fastapi.HTTPException, so a handler bound to the subclass let
# 404 and 405 out in Starlette's `{"detail": ...}` shape instead of the
# documented one (round 21B review I1). FastAPI's HTTPException subclasses
# this one, so every route-raised error still lands here.
@ext_app.exception_handler(StarletteHTTPException)
async def _http_error(_request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.status_code, exc.detail),
                        headers=dict(exc.headers or {}))


@ext_app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
    return JSONResponse(status_code=422, content={"error": {
        "code": "validation_error", "message": f"{where}: {first.get('msg', 'invalid')}".strip(": "),
    }})


@ext_app.middleware("http")
async def _rate_headers(request: Request, call_next):
    response = await call_next(request)
    rate = getattr(request.state, "rate_limit", None)
    if rate:
        response.headers["X-RateLimit-Limit"] = str(rate[0])
        response.headers["X-RateLimit-Remaining"] = str(rate[1])
    return response


def _page(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> tuple[int, int]:
    return limit, offset


def _as_utc(stamp: datetime | None) -> datetime | None:
    """`updated_since=2026-09-23T00:00:00` (no offset) is read as UTC.

    Documented in docs/API.md §5. Without this the naive value rode
    through to the Data tier as-is and every tier down the line guessed —
    an ERP polling every five minutes would silently re-read or skip
    rows by its own server's offset (round 21B review I4).
    """
    if stamp is not None and stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp


def _listing(response: Response, rows: list, total: int, limit: int, offset: int) -> dict:
    response.headers["X-Total-Count"] = str(total)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def _or_404(row: dict | None, what: str) -> dict:
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"{what} not found"})
    return row


# ------------------------------------------------------------------ me


@router.get("/me", summary="ร้านและ key นี้ · Who am I")
async def me(request: Request, principal: TenantPrincipal = Depends(api_principal),
            client: DataClient = Depends(get_data_client)):
    # No `principal.require(...)` here on purpose: /me is exempt by design —
    # it hands back only what the key already proves about itself (the
    # shop's name/status and the key's own identity), never any customer,
    # deal or other record data, so every key may call it regardless of
    # which permission keys it was granted.
    try:
        shop = await client.get_company_profile(principal.license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    key_id = principal.chann_uid.split(":", 1)[-1]
    key = getattr(request.state, "api_key", None) or {"id": key_id}
    return {
        # CompanyProfileOut is deliberately kept separate from LicenseOut
        # (data/chann_data/schemas.py) and carries no `license_code` — the
        # brief's provisional shape named it, but no in-scope DataClient
        # call returns a tenant-scoped license_code, so it rides as None
        # rather than inventing a call outside this task's file list.
        "shop": {"license_id": principal.license_id, "license_code": None,
                 "company_name": (shop or {}).get("company_name"), "status": principal.license_status},
        "key": key,
        "permissions": sorted(principal.permission_keys),
    }


# ------------------------------------------------------------------ customers


class CustomerCreate(BaseModel):
    first_name: str
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None
    stage: Literal["lead", "contact"] = "contact"


class CustomerPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None


@router.get("/customers", summary="รายชื่อลูกค้า · List customers")
async def list_customers(
    response: Response, q: str | None = None, stage: str | None = None,
    updated_since: datetime | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("customer.read")
    limit, offset = page
    try:
        rows, total = await client.list_customers_with_total(
            principal.license_id, stage, limit=limit, q=q, offset=offset, updated_since=_as_utc(updated_since),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/customers/{customer_id}", summary="ลูกค้าหนึ่งราย · One customer")
async def get_customer(customer_id: str, principal: TenantPrincipal = Depends(api_principal),
                       client: DataClient = Depends(get_data_client)):
    principal.require("customer.read")
    try:
        return _or_404(await client.get_customer(principal.license_id, customer_id), "customer")
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/customers", status_code=201, summary="เพิ่มลูกค้า · Create a customer")
async def create_customer(payload: CustomerCreate, principal: TenantPrincipal = Depends(api_principal),
                          client: DataClient = Depends(get_data_client)):
    principal.require("customer.create")
    body = payload.model_dump(exclude_none=True)
    stage = body.pop("stage", "contact")
    try:
        row = await client.create_customer(principal.license_id, body, actor_id=principal.chann_uid)
        # The Data tier's CustomerIn has no `stage` field at all —
        # CustomerRepository.create always lands a new row as "lead"
        # (data/chann_data/schemas.py, data/chann_data/repositories) — so
        # an ERP that asked for a customer, not a lead sitting unconfirmed
        # until someone opens the dashboard, is promoted through the same
        # call the "ยืนยันเป็นลูกค้า" button uses.
        if stage == "contact":
            row = await client.promote_customer(
                principal.license_id, str(row["id"]), actor_id=principal.chann_uid,
            )
    except DataTierError as exc:
        raise _propagate(exc)
    # The same sales rule the dashboard form, the CSV import, chat and
    # onboarding all ask afterwards (services/sales_dispatch.py). Without
    # it a customer an ERP pushed in belonged to nobody, while the same
    # customer typed on the dashboard was handed to a salesperson
    # (round 21B review C2). Best-effort: the customer exists already and
    # a routing failure must not undo it.
    try:
        from .services.sales_dispatch import route_new_customer

        await route_new_customer(
            client, principal.license_id, row, source="api", actor_chann_uid=principal.chann_uid,
        )
    except Exception:  # noqa: BLE001
        log.exception("could not route a customer created through the API")
    return row


@router.patch("/customers/{customer_id}", summary="แก้ไขลูกค้า · Update a customer")
async def update_customer(customer_id: str, payload: CustomerPatch,
                          principal: TenantPrincipal = Depends(api_principal),
                          client: DataClient = Depends(get_data_client)):
    principal.require("customer.update")
    try:
        return await client.update_customer(
            principal.license_id, customer_id, payload.model_dump(exclude_unset=True), actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------------------ deals


class DealCreate(BaseModel):
    customer_id: str
    amount: Decimal | None = None
    currency: str | None = None
    expected_close_date: date | None = None
    notes: str | None = None


class DealPatch(BaseModel):
    amount: Decimal | None = None
    currency: str | None = None
    expected_close_date: date | None = None
    notes: str | None = None


class DealStage(BaseModel):
    stage: str = Field(description="new | proposed | won | lost")
    lost_reason: str | None = None


@router.get("/deals", summary="รายการดีล · List deals")
async def list_deals(
    response: Response, q: str | None = None, stage: str | None = None,
    updated_since: datetime | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("deal.read")
    limit, offset = page
    try:
        rows, total = await client.list_deals_with_total(
            principal.license_id, stage, limit=limit, q=q, offset=offset, updated_since=_as_utc(updated_since),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/deals/{deal_id}", summary="ดีลพร้อมรายการสินค้า · One deal with its lines")
async def get_deal(deal_id: str, principal: TenantPrincipal = Depends(api_principal),
                   client: DataClient = Depends(get_data_client)):
    principal.require("deal.read")
    try:
        deal = _or_404(await client.get_deal(principal.license_id, deal_id), "deal")
    except DataTierError as exc:
        raise _propagate(exc)
    # There is no separate "list a deal's product lines" GET call: the Data
    # tier's DealOut (data/chann_data/schemas.py) already embeds them as
    # `products`, and every dashboard reader (services/invoices.py,
    # services/chat.py, services/sales_charts.py, services/documents/
    # snapshot.py) reads `deal.get("products")` rather than a second
    # request — so `items` is that same embedded field, not a new call.
    return {**deal, "items": deal.get("products") or []}


@router.post("/deals", status_code=201, summary="สร้างดีล · Create a deal")
async def create_deal(payload: DealCreate, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    principal.require("deal.create")
    body = payload.model_dump(mode="json", exclude_none=True)
    body["contact_id"] = body.pop("customer_id")
    try:
        return await client.create_deal(principal.license_id, body, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/deals/{deal_id}", summary="แก้ไขดีล · Update a deal")
async def update_deal(deal_id: str, payload: DealPatch, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    principal.require("deal.update")
    try:
        return await client.update_deal(
            principal.license_id, deal_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/deals/{deal_id}/stage", summary="ย้ายสถานะดีล · Move a deal's stage")
async def set_deal_stage(deal_id: str, payload: DealStage, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    principal.require("deal.update")
    try:
        return await client.transition_deal_stage(
            principal.license_id, deal_id, payload.stage, lost_reason=payload.lost_reason,
            allow_reopen=False, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------------------ documents


def _document_link(request: Request, license_id: str, document_id: str | None, what: str) -> dict:
    """A signed https link, the same one the dashboard's "เปิด PDF" uses."""
    from .auth.document_link import issue_document_token
    from .config import settings

    if not document_id:
        raise HTTPException(status_code=404, detail={"code": "not_issued", "message": f"{what} has no document yet"})
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail={"code": "unavailable", "message": "no public base URL configured"})
    token = issue_document_token(license_id, document_id)
    ttl_days = 7
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    return {"url": f"{base}/api/v1/documents/{token}", "expires_at": expires.isoformat()}


# ------------------------------------------------------------------ quotes


@router.get("/quotes", summary="รายการใบเสนอราคา · List quotes")
async def list_quotes(
    response: Response, deal_id: str | None = None, status_: str | None = Query(default=None, alias="status"),
    q: str | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("quote.read")
    limit, offset = page
    try:
        rows, total = await client.list_quotes_with_total(
            principal.license_id, status_, limit=limit, q=q, offset=offset, deal_id=deal_id,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/quotes/{quote_id}", summary="ใบเสนอราคาพร้อมรายการ · One quote with its lines")
async def get_quote(quote_id: str, principal: TenantPrincipal = Depends(api_principal),
                    client: DataClient = Depends(get_data_client)):
    principal.require("quote.read")
    try:
        quote = _or_404(await client.get_quote(principal.license_id, quote_id), "quote")
        deal = await client.get_deal(principal.license_id, str(quote["deal_id"]))
        customer = await client.get_customer(principal.license_id, str(deal["contact_id"])) if deal and deal.get("contact_id") else None
        items = (deal or {}).get("products") or []
    except DataTierError as exc:
        raise _propagate(exc)
    return {"quote": quote, "deal": deal, "customer": customer, "items": items}


@router.get("/quotes/{quote_id}/pdf", summary="ลิงก์ PDF ใบเสนอราคา · Quote PDF link")
async def quote_pdf(quote_id: str, request: Request, principal: TenantPrincipal = Depends(api_principal),
                    client: DataClient = Depends(get_data_client)):
    principal.require("quote.read")
    try:
        quote = _or_404(await client.get_quote(principal.license_id, quote_id), "quote")
    except DataTierError as exc:
        raise _propagate(exc)
    return _document_link(request, principal.license_id, quote.get("generated_document_id"), "quote")


# ------------------------------------------------------------------ invoices


class InvoiceCreate(BaseModel):
    deal_id: str | None = None
    quote_id: str | None = None
    note: str | None = None
    issue: bool = Field(default=False, description="true = ออกเอกสาร PDF ทันที (draft → issued)")


class PaymentIn(BaseModel):
    amount: Decimal | None = None
    full: bool = False
    method: str | None = Field(default=None, description="cash | transfer | card | other")
    reference: str | None = None
    note: str | None = None
    paid_at: datetime | None = None


@router.get("/invoices", summary="รายการใบแจ้งหนี้ · List invoices")
async def list_invoices(
    response: Response, status_: str | None = Query(default=None, alias="status"),
    deal_id: str | None = None, quote_id: str | None = None, overdue: bool = False,
    updated_since: datetime | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("invoice.read")
    limit, offset = page
    try:
        rows, total = await client.list_invoices_with_total(
            principal.license_id, status=status_, deal_id=deal_id, quote_id=quote_id, overdue=overdue,
            limit=limit, offset=offset, updated_since=_as_utc(updated_since),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/invoices/{invoice_id}", summary="ใบแจ้งหนี้พร้อมการชำระ · One invoice with payments")
async def get_invoice(invoice_id: str, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_or_404

    principal.require("invoice.read")
    return await _invoice_or_404(client, principal, principal.license_id, invoice_id)


@router.post("/invoices", status_code=201, summary="ออกใบแจ้งหนี้จากดีลหรือใบเสนอราคา · Create an invoice")
async def create_invoice(payload: InvoiceCreate, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _create_invoice, _invoice_document_error
    from .services import invoices as invoice_service

    principal.require("invoice.create")
    if payload.issue:
        # Checked before the write, not after: a caller with create-but-not
        # -update must not end up with an orphan draft invoice on record
        # just because the permission that gates issuing it comes second.
        principal.require("invoice.update")
    invoice = await _create_invoice(
        client, principal, principal.license_id, quote_id=payload.quote_id, deal_id=payload.deal_id, note=payload.note,
    )
    if not payload.issue:
        return invoice
    try:
        company = await client.get_company_profile(principal.license_id)
        invoice, _document = await invoice_service.issue_invoice_document(
            client, license_id=principal.license_id, invoice=invoice, company=company,
            actor_id=principal.chann_uid, allow_reissue=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))
    return invoice


@router.post("/invoices/{invoice_id}/payments", status_code=201, summary="บันทึกรับชำระ · Record a payment")
async def record_payment(invoice_id: str, payload: PaymentIn, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_document_error, _invoice_or_404, _with_reason
    from .services import invoices as invoice_service

    principal.require("invoice.update")
    invoice = await _invoice_or_404(client, principal, principal.license_id, invoice_id)
    if not payload.full and payload.amount in (None, ""):
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "amount is required unless full=true"})
    try:
        return await invoice_service.record_payment(
            client, license_id=principal.license_id, invoice=invoice, amount=payload.amount,
            method=payload.method, reference=payload.reference, note=payload.note,
            paid_at=payload.paid_at, actor_id=principal.chann_uid, full=payload.full,
        )
    except invoice_service.PaymentInvalid as exc:
        raise HTTPException(status_code=422, detail={"code": "payment_invalid", "message": str(exc)})
    except DataTierError as exc:
        raise _with_reason(exc)
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))


@router.get("/invoices/{invoice_id}/pdf", summary="ลิงก์ PDF ใบแจ้งหนี้ · Invoice PDF link")
async def invoice_pdf(invoice_id: str, request: Request, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_or_404

    principal.require("invoice.read")
    invoice = await _invoice_or_404(client, principal, principal.license_id, invoice_id)
    return _document_link(request, principal.license_id, invoice.get("generated_document_id"), "invoice")


@router.get("/invoices/{invoice_id}/receipt-pdf", summary="ลิงก์ PDF ใบเสร็จ · Receipt PDF link")
async def receipt_pdf(invoice_id: str, request: Request, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_or_404

    principal.require("invoice.read")
    invoice = await _invoice_or_404(client, principal, principal.license_id, invoice_id)
    return _document_link(request, principal.license_id, invoice.get("receipt_document_id"), "receipt")


# ------------------------------------------------------------------ tickets


class TicketCreate(BaseModel):
    customer_id: str | None = None
    problem: str
    address: str | None = None
    appointment_at: datetime | None = None


def _ticket_body(payload: TicketCreate) -> dict:
    # The ext caller thinks in one appointment moment; the dashboard's own
    # TicketCreateIn (routers_phase2.py) has always split that into a date
    # and a time, so this is the same seam CustomerCreate's `stage` crosses
    # above — one field here, two keys on the wire.
    body = {
        "contact_id": payload.customer_id,
        "issue_description": payload.problem,
        "service_address": payload.address,
        "scheduled_date": payload.appointment_at.date().isoformat() if payload.appointment_at else None,
        "scheduled_time": payload.appointment_at.strftime("%H:%M") if payload.appointment_at else None,
        # Every ext-created ticket is staff-filed, never a customer's own
        # report — the account behind the key has no `is_customer` case at
        # all (TenantPrincipal.audience is always "api", auth/api_key.py) —
        # so it gets the same default the dashboard's non-customer branch
        # leaves in place, spelled out rather than left to the Data tier
        # to guess.
        "visibility": "public",
    }
    return {k: v for k, v in body.items() if v is not None}


@router.get("/tickets", summary="รายการงานซ่อม · List tickets")
async def list_tickets(
    response: Response, status: str | None = None, customer_id: str | None = None,
    q: str | None = None, updated_since: datetime | None = None,
    page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("ticket.read")
    limit, offset = page
    try:
        rows, total = await client.list_tickets_with_total(
            principal.license_id, status=status, contact_id=customer_id, q=q,
            limit=limit, offset=offset, updated_since=_as_utc(updated_since),
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/tickets/{ticket_id}", summary="งานซ่อมหนึ่งรายการ · One ticket")
async def get_ticket(ticket_id: str, principal: TenantPrincipal = Depends(api_principal),
                     client: DataClient = Depends(get_data_client)):
    principal.require("ticket.read")
    try:
        return _or_404(await client.get_ticket(principal.license_id, ticket_id), "ticket")
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/tickets", status_code=201, summary="แจ้งงานซ่อม · Log a ticket")
async def create_ticket(payload: TicketCreate, principal: TenantPrincipal = Depends(api_principal),
                        client: DataClient = Depends(get_data_client)):
    principal.require("ticket.create")
    try:
        row = await client.create_ticket(principal.license_id, _ticket_body(payload), actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
    # The dispatchers hear about it, exactly as after the dashboard's own
    # ticket create (routers_phase2.py) and chat's — an ERP-filed fault
    # used to sit in the database until somebody opened the dashboard for
    # unrelated reasons (round 21B review C2). Best-effort, and the
    # language passed is the caller's; send_notification picks each
    # recipient's own.
    try:
        from .services.chat import _notify_new_ticket

        await _notify_new_ticket(client, principal.license_id, str(row.get("id") or ""), "th")
    except Exception:  # noqa: BLE001
        log.exception("could not announce a new ticket filed through the API")
    return row


# ------------------------------------------------------------------ warranties


class WarrantyCreate(BaseModel):
    serial_number: str
    product_id: str | None = None
    customer_id: str | None = None
    purchase_date: date | None = None
    warranty_months: int | None = None


async def _find_warranty(client: DataClient, license_id: str, warranty_id: str) -> dict | None:
    # No single-warranty getter exists on DataClient (or on the dashboard
    # side it fronts) — the dashboard's own warranties page only ever
    # lists and patches by id, never fetches one alone — so this reads the
    # same page the dashboard would and picks the row out of it, the way
    # services/chat.py's `_find_one_product` already does for the
    # catalogue below.
    rows, _ = await client.list_warranties_with_total(license_id, limit=1000)
    return next((r for r in rows if str(r.get("id")) == str(warranty_id)), None)


@router.get("/warranties", summary="รายการประกัน · List warranties")
async def list_warranties(
    response: Response, q: str | None = None, status: str | None = None,
    page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("warranty.read")
    limit, offset = page
    try:
        rows, total = await client.list_warranties_with_total(
            principal.license_id, status=status, q=q, limit=limit, offset=offset,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/warranties/{warranty_id}", summary="ประกันหนึ่งรายการ · One warranty")
async def get_warranty(warranty_id: str, principal: TenantPrincipal = Depends(api_principal),
                       client: DataClient = Depends(get_data_client)):
    principal.require("warranty.read")
    try:
        row = await _find_warranty(client, principal.license_id, warranty_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return _or_404(row, "warranty")


@router.post("/warranties", status_code=201, summary="ลงทะเบียนประกัน · Register a warranty")
async def register_warranty(payload: WarrantyCreate, principal: TenantPrincipal = Depends(api_principal),
                            client: DataClient = Depends(get_data_client)):
    principal.require("warranty.create")
    body = {
        "serial_number": payload.serial_number,
        "product_id": payload.product_id,
        "contact_id": payload.customer_id,
        "warranty_start": payload.purchase_date.isoformat() if payload.purchase_date else None,
        "warranty_months": payload.warranty_months,
    }
    try:
        return await client.register_warranty(principal.license_id, body, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------------------ products


class ProductCreate(BaseModel):
    name: str
    product_id: str | None = None
    sku: str | None = None
    category: str | None = None
    unit_price: Decimal | str | None = None
    description: str | None = None
    warranty_months: int | None = None


class ProductPatch(BaseModel):
    name: str | None = None
    sku: str | None = None
    category: str | None = None
    unit_price: Decimal | str | None = None
    description: str | None = None
    warranty_months: int | None = None


async def _find_product(client: DataClient, license_id: str, product_id: str) -> dict | None:
    # Same gap as warranties: upsert_product (data_client.py) is a PUT
    # keyed by the shop's own product_id, and nothing on either side reads
    # a single row back. Archived rows are included on purpose — a caller
    # fetching or patching a product it just archived should get the row,
    # not a 404 that reads as "never existed".
    rows, _ = await client.list_products_with_total(license_id, include_archived=True, limit=1000)
    return next(
        (r for r in rows if str(r.get("id")) == str(product_id) or str(r.get("product_id")) == str(product_id)),
        None,
    )


@router.get("/products", summary="รายการสินค้า · List products")
async def list_products(
    response: Response, q: str | None = None, category: str | None = None,
    include_archived: bool = False, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require_any("product.read", "product.manage")
    limit, offset = page
    try:
        rows, total = await client.list_products_with_total(
            principal.license_id, category=category, q=q, include_archived=include_archived,
            limit=limit, offset=offset,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/products/{product_id}", summary="สินค้าหนึ่งรายการ · One product")
async def get_product(product_id: str, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    principal.require_any("product.read", "product.manage")
    try:
        row = await _find_product(client, principal.license_id, product_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return _or_404(row, "product")


@router.post("/products", status_code=201, summary="เพิ่มสินค้า · Create a product")
async def create_product(payload: ProductCreate, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    principal.require("product.manage")
    # schemas.ProductIn (the Data tier's own model) requires product_id in
    # the body as well as the URL — every dashboard and chat caller either
    # already has a code (a SKU import) or the shop typed one. An ERP has
    # neither by default, so one is minted here rather than 422ing on a
    # field the request never mentioned.
    code = payload.product_id or payload.sku or f"P{uuid.uuid4().hex[:10].upper()}"
    # upsert_product is a PUT keyed by that code, so a POST naming a code
    # the shop already uses used to overwrite the row and answer 201 as if
    # it had created one — an ERP re-sending its catalogue silently
    # rewrote prices and names. Creating says so; PATCH is the edit road
    # (round 21B review I3).
    try:
        clash = await _find_product(client, principal.license_id, code)
    except DataTierError as exc:
        raise _propagate(exc)
    if clash is not None:
        raise HTTPException(status_code=409, detail={
            "code": "conflict",
            "message": f"product_id already exists: {code}. Use PATCH /products/{code} to change it.",
        })
    body = {
        "product_id": code, "product_name": payload.name, "sku": payload.sku,
        "category": payload.category,
        "unit_price": str(payload.unit_price) if payload.unit_price is not None else None,
        "description": payload.description, "warranty_months": payload.warranty_months,
    }
    try:
        return await client.upsert_product(principal.license_id, code, body, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/products/{product_id}", summary="แก้ไขสินค้า · Update a product")
async def update_product(product_id: str, payload: ProductPatch, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    principal.require("product.manage")
    try:
        existing = await _find_product(client, principal.license_id, product_id)
    except DataTierError as exc:
        raise _propagate(exc)
    existing = _or_404(existing, "product")
    fields = payload.model_dump(exclude_unset=True)
    code = str(existing.get("product_id") or product_id)
    unit_price = existing.get("unit_price")
    if "unit_price" in fields:
        unit_price = str(fields["unit_price"]) if fields["unit_price"] is not None else None
    body = {
        "product_id": code,
        "product_name": fields.get("name", existing.get("product_name")),
        "sku": fields.get("sku", existing.get("sku")),
        "category": fields.get("category", existing.get("category")),
        "unit_price": unit_price,
        "description": fields.get("description", existing.get("description")),
        "warranty_months": fields.get("warranty_months", existing.get("warranty_months")),
    }
    try:
        return await client.upsert_product(principal.license_id, code, body, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


ext_app.include_router(router)
