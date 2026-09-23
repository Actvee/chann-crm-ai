"""Round 20V — invoices, payments and receipts (the step after the quote).

Owner, 21 ก.ย. 2569: "ทำข้อ 2 … รวมเอาเรื่อง invoice" — ใบแจ้งหนี้/ใบเสร็จ
หลังใบเสนอราคา + สถานะชำระ (มัดจำ/จ่ายแล้ว/ค้าง).

What this file decides, and what it leaves to the Application tier, is the
split the quotation already uses: the Application builds the frozen
snapshot (lines, company, customer, totals through `compute_totals`, so
the VAT rule is the same one the quote prints) and renders the PDFs; this
tier numbers the invoice, keeps the ledger, and enforces the state
machine —

    draft ──issue──▶ issued ──payment──▶ partially_paid ──payment──▶ paid
      │                │                       │
      └────void────────┴───────void (only while nothing was paid)

A payment is accepted on an issued or partially paid invoice only: a draft
is not yet a demand for anything, and a void one never was. A payment may
not exceed what is left, because "paid 6,000 of 5,000" is not a state a
ledger can be in. Void is refused once money has been received — the money
is real, and the honest record of a mistaken invoice with a deposit on it
is a paid-out correction, not a row that pretends nothing happened.

"Overdue" is derived (`is_overdue` on the way out, `overdue=True` on the
way in), never stored: a stored flag would need a nightly sweep and would
lag Bangkok's date by seven hours (see localtime.py).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    INVOICE_STATUSES,
    PAYMENT_METHODS,
    Customer,
    Deal,
    GeneratedDocument,
    Invoice,
    InvoicePayment,
    Quote,
)
from .localtime import bangkok_today
from .locks import serialise
from .search import like_any, page, since
from .tenant_scope import TenantScope

#: Statuses on which money is still owed (and on which "overdue" can apply).
OPEN_INVOICE_STATUSES = ("issued", "partially_paid")
#: Net 30. The default when an invoice is issued without a due date named —
#: a bill with no due date is one that is never overdue.
DEFAULT_DUE_DAYS = 30
_CENTS = Decimal("0.01")


class InvoiceConflict(RuntimeError):
    """Well-formed but not allowed in the current state."""


class InvoiceNotFound(LookupError):
    pass


def _money(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "0")).quantize(_CENTS)
    except (InvalidOperation, ValueError) as exc:
        raise InvoiceConflict(f"not an amount: {value!r}") from exc


def is_overdue(row: Invoice, *, today: date | None = None) -> bool:
    """The one definition of overdue, shared by the Out schema and the
    list filter so a row cannot be counted overdue and shown as not."""
    if row.status not in OPEN_INVOICE_STATUSES or row.due_date is None:
        return False
    return row.due_date < (today or bangkok_today())


class InvoiceRepository:
    def __init__(self, session: Session):
        self._s = session

    # ------------------------------------------------------------ create

    def create(
        self, scope: TenantScope, *, quote_id: uuid.UUID | None = None,
        deal_id: uuid.UUID | None = None, contact_id: uuid.UUID | None = None,
        subtotal=0, discount_amount=0, vat_rate=None, vat_amount=0, total=0,
        currency: str = "THB", note: str | None = None, data_snapshot: dict | None = None,
        created_by: str | None = None,
    ) -> Invoice:
        """A draft invoice, numbered. Every foreign record named must belong
        to this tenant — a caller cannot bill another shop's customer by
        guessing an id."""
        if quote_id is not None:
            quote = self._s.execute(
                select(Quote).where(Quote.id == quote_id, Quote.license_id == scope.license_id)
            ).scalars().first()
            if quote is None:
                raise InvoiceNotFound("quote not found in this tenant")
            # One live invoice per quotation. A second demand for the same
            # offer is a mistake nine times in ten; the tenth voids the
            # first and asks again.
            live = self._s.execute(
                select(Invoice.invoice_id).where(
                    Invoice.license_id == scope.license_id, Invoice.quote_id == quote_id,
                    Invoice.status != "void",
                )
            ).scalars().first()
            if live:
                raise InvoiceConflict(f"quote already has invoice {live}")
            deal_id = deal_id or quote.deal_id
        if deal_id is not None:
            deal = self._s.execute(
                select(Deal).where(Deal.id == deal_id, Deal.license_id == scope.license_id)
            ).scalars().first()
            if deal is None:
                raise InvoiceNotFound("deal not found in this tenant")
            contact_id = contact_id or deal.contact_id
        if contact_id is not None:
            customer = self._s.execute(
                select(Customer.id).where(
                    Customer.id == contact_id, Customer.license_id == scope.license_id,
                )
            ).first()
            if customer is None:
                raise InvoiceNotFound("customer not found in this tenant")

        amount_total = _money(total)
        if amount_total < 0:
            raise InvoiceConflict("an invoice total cannot be negative")

        row = Invoice(
            id=uuid.uuid4(), license_id=scope.license_id,
            invoice_id=self._unique_invoice_id(scope.license_id),
            quote_id=quote_id, deal_id=deal_id, contact_id=contact_id,
            status="draft", currency=(currency or "THB")[:3].upper(),
            subtotal=_money(subtotal), discount_amount=_money(discount_amount),
            vat_rate=(Decimal(str(vat_rate)) if vat_rate not in (None, "") else None),
            vat_amount=_money(vat_amount), total=amount_total,
            paid_amount=Decimal("0.00"), note=(note or None),
            data_snapshot=data_snapshot, created_by=created_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def _unique_invoice_id(self, license_id: uuid.UUID) -> str:
        """Per tenant, per year — QuoteRepository._unique_quote_id's
        pattern, under its own advisory lock key."""
        year = datetime.now(timezone.utc).year
        serialise(self._s, f"{license_id}:invoice")
        for _ in range(50):
            existing = self._s.execute(
                select(Invoice.invoice_id).where(
                    Invoice.license_id == license_id,
                    Invoice.invoice_id.like(f"INV-{year}-%"),
                )
            ).scalars().all()
            used = {
                int(code.rsplit("-", 1)[1]) for code in existing
                if code.rsplit("-", 1)[1].isdigit()
            }
            next_n = (max(used) + 1) if used else 1
            candidate = f"INV-{year}-{next_n:04d}"
            clash = self._s.execute(
                select(Invoice.id).where(
                    Invoice.license_id == license_id, Invoice.invoice_id == candidate,
                )
            ).first()
            if clash is None:
                return candidate
        raise InvoiceConflict("could not allocate a unique invoice_id")

    # -------------------------------------------------------------- read

    def get(self, scope: TenantScope, invoice_id: uuid.UUID) -> Invoice | None:
        return self._s.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.license_id == scope.license_id)
        ).scalars().first()

    def get_by_code(self, scope: TenantScope, code: str) -> Invoice | None:
        return self._s.execute(
            select(Invoice).where(
                Invoice.license_id == scope.license_id,
                func.upper(Invoice.invoice_id) == (code or "").strip().upper(),
            )
        ).scalars().first()

    def _narrow(
        self, query, scope: TenantScope, *, status: str | None, contact_id: uuid.UUID | None,
        customer_chann_uid: str | None, q: str | None, overdue: bool, today: date | None,
        deal_id: uuid.UUID | None = None, quote_id: uuid.UUID | None = None,
        updated_since: datetime | None = None,
    ):
        """The one place an invoice list is narrowed — page and count alike."""
        query = query.where(Invoice.license_id == scope.license_id)
        if status:
            query = query.where(Invoice.status == status)
        if contact_id is not None:
            query = query.where(Invoice.contact_id == contact_id)
        if deal_id is not None:
            # Round 20X: the bills of one deal, for the deal page's button
            # and the invoices page opened from it (owner, 22 ก.ย. 2569:
            # an invoice always hangs off a deal).
            query = query.where(Invoice.deal_id == deal_id)
        if quote_id is not None:
            # Round 21A: so a quote page can say whether it has been billed
            # instead of offering "ออกใบแจ้งหนี้" for ever (owner, 22 ก.ย.).
            query = query.where(Invoice.quote_id == quote_id)
        if customer_chann_uid:
            # The customer's own bills: found through the contact row that
            # carries their chann_uid, the way tickets and warranties are.
            query = query.where(Invoice.contact_id.in_(
                select(Customer.id).where(
                    Customer.license_id == scope.license_id,
                    Customer.customer_chann_uid == customer_chann_uid,
                )
            ))
        if overdue:
            query = query.where(
                Invoice.status.in_(OPEN_INVOICE_STATUSES),
                Invoice.due_date.is_not(None),
                Invoice.due_date < (today or bangkok_today()),
            )
        clause = like_any(q, Invoice.invoice_id, Invoice.note)
        if clause is not None:
            # A name typed into the search box finds the customer's bills too.
            by_customer = Invoice.contact_id.in_(
                select(Customer.id).where(
                    Customer.license_id == scope.license_id,
                    like_any(q, Customer.first_name, Customer.last_name, Customer.phone),
                )
            )
            query = query.where(or_(clause, by_customer))
        return since(query, Invoice, updated_since)

    def list_for_license(
        self, scope: TenantScope, *, status: str | None = None,
        contact_id: uuid.UUID | None = None, customer_chann_uid: str | None = None,
        q: str | None = None, overdue: bool = False, today: date | None = None,
        deal_id: uuid.UUID | None = None, quote_id: uuid.UUID | None = None,
        updated_since: datetime | None = None,
        limit: int | None = None, offset: int | None = None,
    ) -> list[Invoice]:
        """Invoices, newest first, capped and counted like every other list
        since round 20N. `id` breaks the created_at tie so a page boundary
        cannot repeat or skip a row."""
        query = self._narrow(
            select(Invoice), scope, status=status, contact_id=contact_id,
            customer_chann_uid=customer_chann_uid, q=q, overdue=overdue, today=today,
            deal_id=deal_id, quote_id=quote_id, updated_since=updated_since,
        )
        query = query.order_by(Invoice.created_at.desc(), Invoice.id.desc())
        return list(self._s.execute(page(query, limit=limit, offset=offset)).scalars())

    def count_for_license(
        self, scope: TenantScope, *, status: str | None = None,
        contact_id: uuid.UUID | None = None, customer_chann_uid: str | None = None,
        q: str | None = None, overdue: bool = False, today: date | None = None,
        deal_id: uuid.UUID | None = None, quote_id: uuid.UUID | None = None,
        updated_since: datetime | None = None,
    ) -> int:
        return int(self._s.execute(self._narrow(
            select(func.count()).select_from(Invoice), scope, status=status,
            contact_id=contact_id, customer_chann_uid=customer_chann_uid, q=q,
            overdue=overdue, today=today, deal_id=deal_id, quote_id=quote_id,
            updated_since=updated_since,
        )).scalar() or 0)

    def summary(self, scope: TenantScope, *, today: date | None = None) -> dict:
        """What the shop is owed right now — one query, for the overview
        tile and for "ยอดค้างชำระ" in chat."""
        today = today or bangkok_today()
        open_rows = self._s.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Invoice.total - Invoice.paid_amount), 0),
                func.coalesce(func.sum(case(
                    (and_(Invoice.due_date.is_not(None), Invoice.due_date < today), 1),
                    else_=0,
                )), 0),
            ).where(
                Invoice.license_id == scope.license_id,
                Invoice.status.in_(OPEN_INVOICE_STATUSES),
            )
        ).one()
        return {
            "open_count": int(open_rows[0] or 0),
            "outstanding_total": _money(open_rows[1]),
            "overdue_count": int(open_rows[2] or 0),
        }

    def list_payments(self, scope: TenantScope, invoice_id: uuid.UUID) -> list[InvoicePayment]:
        return list(self._s.execute(
            select(InvoicePayment).where(
                InvoicePayment.invoice_id == invoice_id,
                InvoicePayment.license_id == scope.license_id,
            ).order_by(InvoicePayment.paid_at.asc(), InvoicePayment.created_at.asc())
        ).scalars())

    # ------------------------------------------------------------- write

    def _document_of_tenant(self, scope: TenantScope, document_id: uuid.UUID) -> GeneratedDocument:
        document = self._s.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.id == document_id,
                GeneratedDocument.license_id == scope.license_id,
            )
        ).scalars().first()
        if document is None:
            raise InvoiceNotFound("generated document not found")
        return document

    def issue(
        self, scope: TenantScope, invoice_id: uuid.UUID, *, document_id: uuid.UUID | None = None,
        issue_date: date | None = None, due_date: date | None = None,
    ) -> Invoice:
        """draft → issued. The dates are taken from the caller when it has
        printed them on the PDF already (they must match the document), and
        defaulted here otherwise: today, net 30."""
        row = self.get(scope, invoice_id)
        if row is None:
            raise InvoiceNotFound("invoice not found in this tenant")
        if row.status != "draft":
            raise InvoiceConflict(f"invoice {row.invoice_id} is already {row.status}")
        if document_id is not None:
            row.generated_document_id = self._document_of_tenant(scope, document_id).id
        row.issue_date = issue_date or bangkok_today()
        row.due_date = due_date or (row.issue_date + timedelta(days=DEFAULT_DUE_DAYS))
        if row.due_date < row.issue_date:
            raise InvoiceConflict("a due date cannot come before the issue date")
        # An invoice for nothing is settled the moment it exists.
        row.status = "paid" if row.total <= 0 else "issued"
        self._s.flush()
        return row

    def link_document(self, scope: TenantScope, invoice_id: uuid.UUID, document_id: uuid.UUID) -> Invoice:
        """A re-issued invoice PDF replaces the link; the old document row
        stays as evidence of what was sent before."""
        row = self.get(scope, invoice_id)
        if row is None:
            raise InvoiceNotFound("invoice not found in this tenant")
        row.generated_document_id = self._document_of_tenant(scope, document_id).id
        self._s.flush()
        return row

    def add_payment(
        self, scope: TenantScope, invoice_id: uuid.UUID, *, amount, method: str = "transfer",
        paid_at: datetime | None = None, reference: str | None = None, note: str | None = None,
        recorded_by: str | None = None,
    ) -> tuple[Invoice, InvoicePayment]:
        row = self.get(scope, invoice_id)
        if row is None:
            raise InvoiceNotFound("invoice not found in this tenant")
        if row.status == "void":
            raise InvoiceConflict(f"invoice {row.invoice_id} is void")
        if row.status == "draft":
            raise InvoiceConflict(f"invoice {row.invoice_id} has not been issued yet")
        if row.status == "paid":
            raise InvoiceConflict(f"invoice {row.invoice_id} is already paid in full")
        value = _money(amount)
        if value <= 0:
            raise InvoiceConflict("a payment must be more than zero")
        outstanding = _money(row.total - row.paid_amount)
        if value > outstanding:
            raise InvoiceConflict(
                f"payment {value} exceeds the outstanding {outstanding} on {row.invoice_id}"
            )
        method = (method or "transfer").strip().lower()
        if method not in PAYMENT_METHODS:
            raise InvoiceConflict(f"unknown payment method: {method!r}")
        payment = InvoicePayment(
            id=uuid.uuid4(), license_id=scope.license_id, invoice_id=row.id,
            amount=value, method=method,
            paid_at=paid_at or datetime.now(timezone.utc),
            reference=(reference or None), note=(note or None), recorded_by=recorded_by,
        )
        self._s.add(payment)
        self._s.flush()
        # Recomputed from the ledger, not incremented: two concurrent
        # deposits must not lose one.
        row.paid_amount = _money(self._s.execute(
            select(func.coalesce(func.sum(InvoicePayment.amount), 0)).where(
                InvoicePayment.invoice_id == row.id,
            )
        ).scalar())
        row.status = "paid" if row.paid_amount >= row.total else "partially_paid"
        self._s.flush()
        return row, payment

    def void(self, scope: TenantScope, invoice_id: uuid.UUID) -> Invoice:
        row = self.get(scope, invoice_id)
        if row is None:
            raise InvoiceNotFound("invoice not found in this tenant")
        if row.status == "void":
            raise InvoiceConflict(f"invoice {row.invoice_id} is already void")
        if row.paid_amount > 0:
            raise InvoiceConflict(
                f"invoice {row.invoice_id} has payments recorded and cannot be voided"
            )
        row.status = "void"
        self._s.flush()
        return row

    def set_receipt_document(
        self, scope: TenantScope, invoice_id: uuid.UUID, document_id: uuid.UUID,
    ) -> Invoice:
        row = self.get(scope, invoice_id)
        if row is None:
            raise InvoiceNotFound("invoice not found in this tenant")
        if row.status != "paid":
            # The receipt says "received in full"; until it is, that
            # sentence is false. A deposit is acknowledged on the invoice's
            # own payment lines, not by a receipt.
            raise InvoiceConflict(f"invoice {row.invoice_id} is not paid in full")
        row.receipt_document_id = self._document_of_tenant(scope, document_id).id
        self._s.flush()
        return row


def invoice_statuses() -> tuple[str, ...]:
    return INVOICE_STATUSES
