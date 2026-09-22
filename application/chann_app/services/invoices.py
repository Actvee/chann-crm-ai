"""Round 20V — the invoice and the receipt: the step after the quotation.

Owner, 21 ก.ย. 2569: "ทำข้อ 2 … รวมเอาเรื่อง invoice" — item 2 of the gap
list: ใบแจ้งหนี้/ใบเสร็จหลังใบเสนอราคา + สถานะชำระ (มัดจำ/จ่ายแล้ว/ค้าง).

What lives where, on purpose:

* The Data tier numbers the invoice, keeps the ledger and enforces the
  state machine (draft → issued → partially_paid → paid; void while
  nothing was paid). Nothing here re-implements a rule it holds.
* This module builds the frozen snapshot (through the quotation's own
  `build_line_items` / `compute_totals`, so the VAT rule is one rule),
  renders the two PDFs through the same engine the quote uses — the
  shop's own published template for the type, the built-in otherwise —
  stores first and records second (quote_issue.py's argument), and links
  the document back.

**Which quotes may be billed.** `sent` or `accepted`. A draft has not been
offered to anyone, so there is nothing to bill for yet; a rejected or
expired quote is an offer that no longer stands, and billing it would
demand money for a price the customer did not agree to. Accepting the
quote is not required: in practice a customer says yes on the phone and
the invoice IS the confirmation, so insisting on "ลูกค้าตอบรับ" first
would be a form for its own sake. Billing a deal directly (no quote) is
allowed for the same reason — the deal's lines are the agreed goods.

**Receipts.** One receipt per invoice, issued when it is paid in full;
the Data tier refuses the link before that. A deposit is acknowledged on
the invoice's own payment lines and in the reply, not by a document that
would have to say "received in part". A per-payment receipt is a later
step if a shop asks for it — the ledger already holds everything it
would print.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ..data_client import DataClient, DataTierError
from .documents.design import ActiveContentInTemplate, active_content_in
from .documents.fill import fill_template
from .documents.html import render_invoice_html, render_receipt_html
from .documents.selection import resolve_tenant_template
from .documents.snapshot import (
    build_invoice_snapshot,
    build_receipt_snapshot,
    restamp_invoice_snapshot,
)
from .pdf.base import PdfOptions, get_renderer
from .storage.base import get_document_store, sha256_hex

log = logging.getLogger(__name__)

INVOICE_DOCUMENT_TYPE = "invoice"
RECEIPT_DOCUMENT_TYPE = "receipt"
BUILTIN = {
    "invoice": {
        "code": "BUILTIN-INVOICE", "name": "ใบแจ้งหนี้ (แบบมาตรฐานของระบบ)",
        "version": 1, "module": "chann_app.services.documents.html:render_invoice_html",
    },
    "receipt": {
        "code": "BUILTIN-RECEIPT", "name": "ใบเสร็จรับเงิน (แบบมาตรฐานของระบบ)",
        "version": 1, "module": "chann_app.services.documents.html:render_receipt_html",
    },
}
#: Net 30, the same default the Data tier applies when no date is given.
DEFAULT_DUE_DAYS = 30
#: Quotes that may be billed — see the module docstring.
BILLABLE_QUOTE_STATUSES = ("sent", "accepted")
PAYMENT_METHODS = ("cash", "transfer", "promptpay", "card", "other")
#: The words people use for a method, in Thai and English, mapped onto
#: the closed set the ledger stores. Longest first so "พร้อมเพย์" is not
#: read as "โอน" by a shorter word inside it.
PAYMENT_METHOD_WORDS: tuple[tuple[str, str], ...] = (
    ("พร้อมเพย์", "promptpay"), ("promptpay", "promptpay"), ("พร้อมเพ", "promptpay"),
    ("เงินสด", "cash"), ("cash", "cash"), ("สด", "cash"),
    ("โอนเงิน", "transfer"), ("โอน", "transfer"), ("transfer", "transfer"), ("bank", "transfer"),
    ("บัตรเครดิต", "card"), ("บัตร", "card"), ("card", "card"), ("credit", "card"),
)

_SAFE_KEY = re.compile(r"[^A-Za-z0-9_-]+")
_CENTS = Decimal("0.01")


class QuoteNotBillable(RuntimeError):
    """The quote is not in a state that can be billed (see BILLABLE_QUOTE_STATUSES)."""


class InvoiceAlreadyIssued(RuntimeError):
    """This invoice already has a document; a second one must be asked for
    explicitly, for the reason QuoteAlreadyIssued gives."""


class InvoiceNotPaid(RuntimeError):
    """A receipt says "received in full"; until it is, that is false."""


class InvoiceNotOpen(RuntimeError):
    """A payment needs an issued, unpaid invoice."""


class PaymentInvalid(ValueError):
    """The amount or the method is not something the ledger takes."""


def money(value) -> Decimal:
    if isinstance(value, Decimal):
        raw = value
    elif isinstance(value, float):
        raw = Decimal(str(value))
    else:
        raw = Decimal(str(value if value not in (None, "") else "0").replace(",", ""))
    return raw.quantize(_CENTS)


def outstanding_of(invoice: dict) -> Decimal:
    """What is still owed. The Data tier sends it as `outstanding`; a row
    from an older fake or a partial dict falls back to the arithmetic."""
    if invoice.get("outstanding") not in (None, ""):
        return money(invoice["outstanding"])
    if str(invoice.get("status") or "") in ("void", "paid"):
        return Decimal("0.00")
    return money(money(invoice.get("total")) - money(invoice.get("paid_amount")))


def payment_method_from_words(text: str) -> str | None:
    """"โอน" → transfer, "เงินสด" → cash … or None when no method was said."""
    lowered = (text or "").lower()
    for word, method in PAYMENT_METHOD_WORDS:
        if word in lowered:
            return method
    return None


def document_key(*, license_id: str, kind: str, code: str, issued_at: datetime, sha256: str) -> str:
    """Object key for an invoice or receipt PDF — tenant-prefixed and
    digest-suffixed, like the quote's (see quote_issue.document_key)."""
    safe_code = _SAFE_KEY.sub("-", code or kind)
    folder = "invoices" if kind == INVOICE_DOCUMENT_TYPE else "receipts"
    return f"documents/{license_id}/{folder}/{issued_at:%Y/%m}/{safe_code}-{sha256[:12]}.pdf"


# --------------------------------------------------------------- creation


async def create_from_quote(
    client: DataClient, *, license_id: str, quote: dict, deal: dict, customer: dict,
    company: dict, actor_id: str | None = None, note: str | None = None,
) -> dict:
    """A draft invoice carrying the quote's lines, discount and VAT, frozen.

    The quote's OWN lines (quote_products), the way the quote PDF prints
    them — a discount agreed on this offer and not on the deal stays on
    the bill. The deal's lines are the fallback for a quote made before
    quotes owned their lines.
    """
    status = str(quote.get("status") or "").lower()
    if status not in BILLABLE_QUOTE_STATUSES:
        raise QuoteNotBillable(
            f"quote {quote.get('quote_id')} is {status or 'unknown'}; only a sent or accepted quote can be billed"
        )
    lines = quote.get("products")
    if not lines:
        try:
            lines = await client.list_quote_products(str(license_id), str(quote["id"]))
        except Exception:  # noqa: BLE001
            lines = []
    lines = lines or deal.get("products") or []
    discount = _quote_discount(quote, lines)
    snapshot = build_invoice_snapshot(
        lines=lines, customer=customer, company=company, quote=quote, deal=deal,
        discount=discount, invoice={"note": note or ""},
    )
    return await _create(
        client, license_id=license_id, snapshot=snapshot, quote_id=str(quote["id"]),
        deal_id=str(quote.get("deal_id") or deal.get("id") or "") or None,
        contact_id=str(deal.get("contact_id") or customer.get("id") or "") or None,
        note=note, actor_id=actor_id,
    )


async def create_from_deal(
    client: DataClient, *, license_id: str, deal: dict, customer: dict, company: dict,
    actor_id: str | None = None, note: str | None = None,
) -> dict:
    """A draft invoice straight from a deal's lines — for a sale that never
    had a quotation ("สร้างใบแจ้งหนี้ให้ดีล D-…")."""
    lines = deal.get("products") or []
    if not lines:
        raise QuoteNotBillable(f"deal {deal.get('deal_id')} has no products to bill")
    snapshot = build_invoice_snapshot(
        lines=lines, customer=customer, company=company, deal=deal,
        invoice={"note": note or ""},
    )
    return await _create(
        client, license_id=license_id, snapshot=snapshot, quote_id=None,
        deal_id=str(deal["id"]), contact_id=str(deal.get("contact_id") or customer.get("id") or "") or None,
        note=note, actor_id=actor_id,
    )


async def billable_deals(client: DataClient, license_id: str, contact_id: str) -> list[dict]:
    """The customer's deals an invoice can be made from, newest first as
    the Data tier lists them.

    Owner, 22 ก.ย. 2569: "ใบแจ้งหนี้จะไม่ผูกกับลูกค้าโดยตรง อย่างน้อยจะมีดีล
    เกิดขึ้น" — a bill never stands on a customer alone; it hangs off a
    deal (and, when there is one, that deal's quotation). So "ออกใบแจ้งหนี้
    ให้ สมชาย" means "from สมชาย's deal": the one deal that has lines when
    there is one, a choice when there are several, and a plain "สร้างดีล
    ก่อน" when there is none. A lost deal is not offered — its lines were
    the goods the customer did NOT buy. A won deal is: a won deal with no
    invoice yet is exactly the one that needs a bill.
    """
    rows = await client.list_deals(str(license_id), contact_id=str(contact_id))
    return [
        d for d in rows
        if d.get("products") and str(d.get("stage") or "").lower() != "lost"
    ]


def _quote_discount(quote: dict, lines: list[dict]) -> Decimal:
    """The quote's discount as an amount — the snapshot builder's rule."""
    subtotal = sum(
        (money(item.get("quoted_unit_price")) * int(item.get("qty") or 0) for item in lines),
        Decimal("0"),
    )
    if quote.get("discount_percent") is not None:
        return money(subtotal * Decimal(str(quote["discount_percent"])) / 100)
    if quote.get("discount_amount") is not None:
        return money(quote["discount_amount"])
    return Decimal("0")


async def _create(
    client: DataClient, *, license_id: str, snapshot: dict, quote_id: str | None,
    deal_id: str | None, contact_id: str | None, note: str | None, actor_id: str | None,
) -> dict:
    totals = snapshot["totals"]
    payload = {
        "quote_id": quote_id, "deal_id": deal_id, "contact_id": contact_id,
        "subtotal": totals["subtotal"],
        "discount_amount": totals.get("discount_amount") or "0",
        "vat_rate": totals.get("vat_rate"),
        "vat_amount": totals.get("vat_amount") or "0",
        "total": totals["grand_total"],
        "currency": "THB", "note": note or None,
        "data_snapshot": snapshot, "created_by": actor_id,
    }
    invoice = await client.create_invoice(str(license_id), payload, actor_id=actor_id)
    # The number the Data tier allocated goes into the stored snapshot's
    # own copy on the first render (restamp); the row's snapshot is left as
    # created so the two never disagree about anything but the stamp.
    return invoice


# ---------------------------------------------------------------- render


async def _resolve_template(
    client: DataClient, license_id: str, kind: str, snapshot: dict, *, actor_id: str | None,
) -> tuple[str, str]:
    """(template_version_id, html): the shop's own template for this
    type when it has one in use, the built-in otherwise — quote_issue's
    rule, through the same `documents/selection.py`."""
    _template, version = await resolve_tenant_template(client, license_id, kind)
    if version is not None:
        try:
            raw = await get_document_store().get(path=str(version.get("compiled_template_path") or ""))
            stored = raw.decode("utf-8")
            active = active_content_in(stored)
            if active:
                raise ActiveContentInTemplate(", ".join(active))
            html = fill_template(stored, snapshot)
        except ActiveContentInTemplate as exc:
            log.error("tenant %s template %s holds active content (%s); using the built-in", kind, version.get("id"), exc)
        except Exception:
            log.exception("tenant %s template %s could not be used; using the built-in", kind, version.get("id"))
        else:
            return str(version["id"]), html
    version_id = await _ensure_builtin_template_version(client, license_id, kind, actor_id=actor_id)
    render = render_invoice_html if kind == INVOICE_DOCUMENT_TYPE else render_receipt_html
    return version_id, render(snapshot)


async def _ensure_builtin_template_version(
    client: DataClient, license_id: str, kind: str, *, actor_id: str | None,
) -> str:
    """The built-in layout as a real template row, created on first use —
    idempotent by (template_code, version), like the quote's."""
    spec = BUILTIN[kind]
    templates = await client.list_document_templates(license_id, document_type=kind)
    template = next((t for t in templates if t.get("template_code") == spec["code"]), None)
    if template is None:
        template = await client.create_document_template(
            license_id,
            {"document_type": kind, "template_code": spec["code"], "template_name": spec["name"]},
            actor_id=actor_id,
        )
    template_id = str(template["id"])
    versions = await client.list_document_template_versions(license_id, template_id)
    existing = next((v for v in versions if v.get("version") == spec["version"]), None)
    if existing is not None:
        return str(existing["id"])
    version = await client.create_document_template_version(
        license_id, template_id,
        {
            "source_docx_path": "builtin://none",
            "intermediate_model": {"kind": "builtin", "module": spec["module"], "version": spec["version"]},
            "mapping_schema": {"kind": "builtin"},
            "compiled_template_path": f"builtin://{kind}/v{spec['version']}",
        },
        actor_id=actor_id,
    )
    version_id = str(version["id"])
    await client.publish_document_template_version(license_id, version_id, actor_id=actor_id)
    return version_id


async def _render_store_record(
    client: DataClient, *, license_id: str, kind: str, invoice: dict, snapshot: dict,
    issued_at: datetime, actor_id: str | None,
) -> dict:
    """Render → store → record, in that order (quote_issue.py says why),
    raising the same typed errors the quote's callers already translate."""
    template_version_id, html = await _resolve_template(
        client, license_id, kind, snapshot, actor_id=actor_id,
    )
    renderer = get_renderer("smartbrowz")
    result = await renderer.render(
        html, PdfOptions(), idempotency_key=f"{kind}:{license_id}:{invoice.get('id')}",
    )
    if not result.content:
        raise RuntimeError("renderer returned no document content")
    digest = sha256_hex(result.content)
    code = str(invoice.get("invoice_id") or "")
    key = document_key(
        license_id=license_id, kind=kind, code=code if kind == INVOICE_DOCUMENT_TYPE else f"RCPT-{code}",
        issued_at=issued_at, sha256=digest,
    )
    store = get_document_store()
    stored = await store.put(key=key, content=result.content, content_type="application/pdf")
    return await client.record_generated_document(
        license_id,
        {
            "document_type": kind,
            "source_entity_type": "invoice",
            "source_entity_id": str(invoice["id"]),
            "template_version_id": template_version_id,
            "data_snapshot": snapshot,
            "output_path": stored.path,
            "sha256": stored.sha256,
            "renderer": result.renderer,
        },
        actor_id=actor_id,
    )


async def issue_invoice_document(
    client: DataClient, *, license_id: str, invoice: dict, company: dict,
    actor_id: str | None = None, allow_reissue: bool = False, due_date: date | None = None,
) -> tuple[dict, dict]:
    """Render, store, record, and move the invoice from draft to issued.
    Returns (invoice as it now stands, the generated_documents row).

    The issue and due dates are decided HERE, before rendering, and handed
    to the Data tier with the document id: the PDF must print the same
    dates the row keeps. A re-issue (allow_reissue) on an already-issued
    invoice keeps its dates and only replaces the document link.
    """
    if invoice.get("generated_document_id") and not allow_reissue:
        raise InvoiceAlreadyIssued(f"invoice {invoice.get('invoice_id')} already has an issued document")
    if str(invoice.get("status") or "") == "void":
        raise InvoiceNotOpen(f"invoice {invoice.get('invoice_id')} is void")
    missing = company.get("missing_for_documents") or []
    if missing:
        from .documents.snapshot import QuoteNotRenderable

        raise QuoteNotRenderable("company profile is incomplete: " + ", ".join(missing))

    issued_at = datetime.now(timezone.utc)
    is_draft = str(invoice.get("status") or "draft") == "draft"
    if is_draft:
        issue_on = _bangkok_today()
        due_on = due_date or (issue_on + timedelta(days=DEFAULT_DUE_DAYS))
        stamped = {**invoice, "issue_date": issue_on.isoformat(), "due_date": due_on.isoformat(), "status": "issued"}
    else:
        issue_on, due_on = None, None
        stamped = invoice
    snapshot = restamp_invoice_snapshot(invoice.get("data_snapshot") or {}, invoice=stamped, issued_at=issued_at)
    if not snapshot.get("line_items"):
        # A row made without a snapshot (an older fake, a manual insert)
        # cannot be printed as a demand for money it cannot itemise.
        raise RuntimeError(f"invoice {invoice.get('invoice_id')} has no frozen lines to print")

    document = await _render_store_record(
        client, license_id=license_id, kind=INVOICE_DOCUMENT_TYPE, invoice=invoice,
        snapshot=snapshot, issued_at=issued_at, actor_id=actor_id,
    )
    # The document IS issued at this point; a failure below is logged and
    # the row returned as it was, for the reason quote_issue gives — an
    # error here would invite a re-issue that duplicates a real file.
    try:
        if is_draft:
            invoice = await client.issue_invoice(
                license_id, str(invoice["id"]), document_id=str(document["id"]),
                issue_date=issue_on, due_date=due_on, actor_id=actor_id,
            )
        else:
            invoice = await client.link_invoice_document(
                license_id, str(invoice["id"]), str(document["id"]), actor_id=actor_id,
            )
    except Exception:
        log.exception("document %s was issued for invoice %s but the row was not updated", document.get("id"), invoice.get("invoice_id"))
    return invoice, document


async def record_payment(
    client: DataClient, *, license_id: str, invoice: dict, amount, method: str | None = None,
    reference: str | None = None, note: str | None = None, paid_at: datetime | None = None,
    actor_id: str | None = None, full: bool = False,
) -> dict:
    """One receipt of money on the ledger. `full=True` pays whatever is
    outstanding; otherwise `amount` is checked here for shape (a number,
    more than zero, not more than what is owed) so the reply can say why
    before the Data tier is asked — which enforces the same rules again."""
    status = str(invoice.get("status") or "")
    if status not in ("issued", "partially_paid"):
        raise InvoiceNotOpen(f"invoice {invoice.get('invoice_id')} is {status or 'unknown'}")
    owed = outstanding_of(invoice)
    if full:
        value = owed
    else:
        try:
            value = money(amount)
        except (InvalidOperation, ValueError) as exc:
            raise PaymentInvalid(f"not an amount: {amount!r}") from exc
    if value <= 0:
        raise PaymentInvalid("a payment must be more than zero")
    if value > owed:
        raise PaymentInvalid(f"payment {value} exceeds the outstanding {owed}")
    chosen = (method or "transfer").strip().lower()
    if chosen not in PAYMENT_METHODS:
        raise PaymentInvalid(f"unknown payment method: {method!r}")
    return await client.add_invoice_payment(
        str(license_id), str(invoice["id"]),
        {
            "amount": str(value), "method": chosen, "reference": reference or None,
            "note": note or None, "paid_at": paid_at.isoformat() if paid_at else None,
            "recorded_by": actor_id,
        },
        actor_id=actor_id,
    )


async def issue_receipt_document(
    client: DataClient, *, license_id: str, invoice: dict, company: dict, language: str = "th",
    actor_id: str | None = None, allow_reissue: bool = False,
) -> tuple[dict, dict]:
    """The receipt PDF for an invoice paid in full. Returns (invoice, document)."""
    if str(invoice.get("status") or "") != "paid":
        raise InvoiceNotPaid(f"invoice {invoice.get('invoice_id')} is not paid in full")
    if invoice.get("receipt_document_id") and not allow_reissue:
        raise InvoiceAlreadyIssued(f"invoice {invoice.get('invoice_id')} already has a receipt")
    payments = invoice.get("payments")
    if payments is None:
        fresh = await client.get_invoice(str(license_id), str(invoice["id"]))
        payments = (fresh or {}).get("payments") or []
        invoice = fresh or invoice
    issued_at = datetime.now(timezone.utc)
    snapshot = build_receipt_snapshot(
        invoice=invoice, payments=payments, company=company, language=language, issued_at=issued_at,
    )
    document = await _render_store_record(
        client, license_id=license_id, kind=RECEIPT_DOCUMENT_TYPE, invoice=invoice,
        snapshot=snapshot, issued_at=issued_at, actor_id=actor_id,
    )
    try:
        invoice = await client.set_invoice_receipt_document(
            license_id, str(invoice["id"]), str(document["id"]), actor_id=actor_id,
        )
    except Exception:
        log.exception("receipt %s was issued for invoice %s but the row was not updated", document.get("id"), invoice.get("invoice_id"))
    return invoice, document


async def void_invoice(client: DataClient, *, license_id: str, invoice: dict, actor_id: str | None = None) -> dict:
    """Void, through the Data tier's rule (nothing paid). Raises
    DataTierError 409 with the reason when it refuses."""
    return await client.void_invoice(str(license_id), str(invoice["id"]), actor_id=actor_id)


async def find_by_code(client: DataClient, license_id: str, code: str) -> dict | None:
    """The invoice named by its INV- code, or None."""
    wanted = (code or "").strip().upper()
    if not wanted:
        return None
    rows, _total = await client.list_invoices_with_total(str(license_id), q=wanted, limit=50)
    return next((r for r in rows if str(r.get("invoice_id") or "").upper() == wanted), None)


async def invoice_parties(client: DataClient, license_id: str, invoice: dict) -> tuple[dict, dict]:
    """(customer, company) for an invoice — the two records a document or
    a push needs; empty dicts when a link is gone."""
    customer: dict = {}
    if invoice.get("contact_id"):
        try:
            customer = await client.get_customer(str(license_id), str(invoice["contact_id"])) or {}
        except DataTierError:
            customer = {}
    company = await client.get_company_profile(str(license_id))
    return customer, company


def _bangkok_today() -> date:
    """The shop's calendar day, not UTC's — at 23:30 in Bangkok an invoice
    dated "yesterday" would be overdue a day early."""
    return datetime.now(timezone(timedelta(hours=7))).date()


RECEIPT_PUSH = {
    "th": "ใบเสร็จรับเงินของใบแจ้งหนี้ {invoice_id} ({company}) ยอด {total} บาท ชำระครบแล้ว ขอบคุณครับ\nเปิดใบเสร็จ (ลิงก์ใช้ได้ 7 วัน):\n{url}",
    "en": "Your receipt for invoice {invoice_id} ({company}), {total} baht, paid in full — thank you.\nOpen the receipt (link valid 7 days):\n{url}",
}
RECEIPT_PUSH_NO_LINK = {
    "th": "ใบเสร็จรับเงินของใบแจ้งหนี้ {invoice_id} ({company}) ยอด {total} บาท ชำระครบแล้ว ขอบคุณครับ — เปิดดูได้จากหน้าลูกค้า",
    "en": "Your receipt for invoice {invoice_id} ({company}), {total} baht, paid in full — thank you. It is on your customer page.",
}


def baht(value) -> str:
    return f"{money(value):,.2f}"


async def notify_customer_receipt(
    client: DataClient, *, license_id: str, invoice: dict, document: dict,
) -> bool:
    """One LINE line to the customer who paid, with the receipt link, on
    the customer OA — recorded as a notification row like the job
    completion push, so it can be counted and never raises. Returns
    whether a push was attempted (a walk-in customer has no LINE)."""
    from .chat import document_download_url
    from .notify import send_notification

    customer, company = await invoice_parties(client, license_id, invoice)
    uid = str(customer.get("customer_chann_uid") or "")
    if not uid:
        return False
    url = document_download_url(str(license_id), str(document.get("id") or ""))
    table = RECEIPT_PUSH if url else RECEIPT_PUSH_NO_LINK
    values = {
        "invoice_id": invoice.get("invoice_id") or "", "company": company.get("company_name") or company.get("legal_name") or "",
        "total": baht(invoice.get("total")), "url": url or "",
    }
    try:
        line_uid = await client.line_target_of(uid)
        await send_notification(
            client, license_id=str(license_id), target_chann_uid=uid, target_line_user_id=line_uid,
            type="receipt_issued", message=table["th"].format(**values), message_en=table["en"].format(**values),
            entity_type="invoice", entity_id=str(invoice.get("id") or ""),
            # The customer reads it in LINE and on their home page's invoice
            # list, which shows the receipt link itself; no bell row on a
            # dashboard the customer does not have (live_chat's choice).
            delivery_dashboard=False, oa="customer",
        )
        return True
    except Exception:  # noqa: BLE001
        log.exception("could not tell the customer about the receipt of %s", invoice.get("invoice_id"))
        return False
