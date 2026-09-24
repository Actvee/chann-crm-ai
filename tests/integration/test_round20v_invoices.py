"""Round 20V — invoices, payments and receipts on a real database.

Owner, 21 ก.ย. 2569: "ทำข้อ 2 … รวมเอาเรื่อง invoice" — the bill after the
quotation, with what was paid (a deposit, the balance) and what is overdue.
Everything here runs against the migrated schema from empty: the numbering,
the copy from a quote, the ledger arithmetic including the over-payment
refusal, the void rules, the derived "overdue", and tenant isolation.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.invoices import (  # noqa: E402
    InvoiceConflict,
    InvoiceNotFound,
    InvoiceRepository,
    is_overdue,
)
from chann_data.repositories.phase10 import QuoteRepository  # noqa: E402
from chann_data.repositories.phase9 import CustomerRepository, DealRepository  # noqa: E402
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402


def _tenant(migrated_db, *, with_customer_uid: str | None = None):
    """A tenant with one customer, one deal with two lines, one quote."""
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-IV-{suffix}", line_user_id=f"line-iv-{suffix}", primary_role="sales",
        ))
        if with_customer_uid:
            session.add(ChannIdentity(
                chann_uid=with_customer_uid, line_user_id=f"line-cu-{suffix}", primary_role="customer",
            ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"Invoices {suffix}", created_by_chann_uid=f"CHN-IV-{suffix}",
        )
        session.commit()
        license_id = lic.id
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as session:
        customers = CustomerRepository(session)
        customer = customers.create(
            scope, first_name="สมชาย", last_name="ใจดี", phone="0812345678",
        )
        if with_customer_uid:
            customer.customer_chann_uid = with_customer_uid
        deals = DealRepository(session)
        deal = deals.create(scope, contact_id=customer.id)
        deals.add_product(
            scope, deal.id, product_id=None, product_name="แอร์ 12000 BTU",
            quoted_unit_price="15000.00", qty=2,
        )
        deals.add_product(
            scope, deal.id, product_id=None, product_name="ค่าติดตั้ง",
            quoted_unit_price="2000.00", qty=1,
        )
        quote = QuoteRepository(session).create(scope, deal_id=deal.id)
        session.commit()
        return {
            "scope": scope, "customer_id": customer.id, "deal_id": deal.id,
            "quote_id": quote.id, "session": lambda: Session(migrated_db),
        }


def _snapshot() -> dict:
    return {
        "line_items": [
            {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
             "unit_price": "15000.00", "line_total": "30000.00"},
            {"line_no": 2, "product_name": "ค่าติดตั้ง", "qty": 1,
             "unit_price": "2000.00", "line_total": "2000.00"},
        ],
        "totals": {"subtotal": "32000.00", "vat_applicable": True, "vat_rate": "0.07",
                   "vat_amount": "2240.00", "grand_total": "34240.00"},
    }


def _create(repo, t, **kw):
    return repo.create(
        t["scope"], quote_id=t["quote_id"], subtotal="32000.00", discount_amount="0",
        vat_rate="0.07", vat_amount="2240.00", total="34240.00", data_snapshot=_snapshot(),
        created_by="CHN-IV-x", **kw,
    )


class TestNumberingAndCopy:
    def test_invoices_are_numbered_per_tenant_per_year(self, migrated_db):
        a, b = _tenant(migrated_db), _tenant(migrated_db)
        year = date.today().year
        with a["session"]() as session:
            repo = InvoiceRepository(session)
            first = _create(repo, a)
            session.commit()
            assert first.invoice_id == f"INV-{year}-0001"
            assert first.status == "draft" and first.paid_amount == Decimal("0.00")
        with b["session"]() as session:
            # The other tenant starts at 0001 too: a new shop's first bill
            # must not read INV-2026-0847.
            other = _create(InvoiceRepository(session), b)
            session.commit()
            assert other.invoice_id == f"INV-{year}-0001"

    def test_the_quote_deal_and_customer_are_carried_and_the_money_is_frozen(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            row = _create(InvoiceRepository(session), t)
            session.commit()
            assert row.deal_id == t["deal_id"] and row.contact_id == t["customer_id"]
            assert row.total == Decimal("34240.00") and row.vat_rate == Decimal("0.0700")
            assert row.data_snapshot["line_items"][0]["product_name"] == "แอร์ 12000 BTU"

    def test_one_live_invoice_per_quote(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            first = _create(repo, t)
            session.commit()
            first_id = first.id
            with pytest.raises(InvoiceConflict, match="already has invoice"):
                _create(repo, t)
            session.rollback()
            # A voided one steps aside for a corrected bill.
            repo.void(t["scope"], first_id)
            session.commit()
            second = _create(repo, t)
            session.commit()
            assert second.invoice_id.endswith("-0002")


class TestTheLedger:
    def test_payments_add_up_and_settle_the_bill(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            row = _create(repo, t)
            session.commit()
            row_id = row.id
            with pytest.raises(InvoiceConflict, match="not been issued"):
                repo.add_payment(t["scope"], row_id, amount="1000")
            session.rollback()
            row = repo.issue(t["scope"], row_id)
            session.commit()
            assert row.status == "issued" and row.issue_date is not None
            assert row.due_date == row.issue_date + timedelta(days=30)

            row, deposit, closed = repo.add_payment(
                t["scope"], row_id, amount="10000", method="cash", reference="มัดจำ",
            )
            assert closed is None
            session.commit()
            assert deposit.amount == Decimal("10000.00") and deposit.method == "cash"
            assert row.status == "partially_paid" and row.paid_amount == Decimal("10000.00")

            with pytest.raises(InvoiceConflict, match="exceeds the outstanding"):
                repo.add_payment(t["scope"], row_id, amount="30000")
            session.rollback()
            with pytest.raises(InvoiceConflict, match="more than zero"):
                repo.add_payment(t["scope"], row_id, amount="0")
            session.rollback()
            with pytest.raises(InvoiceConflict, match="unknown payment method"):
                repo.add_payment(t["scope"], row_id, amount="1", method="bitcoin")
            session.rollback()

            row, _, _ = repo.add_payment(t["scope"], row_id, amount="24240.00", method="transfer")
            session.commit()
            assert row.status == "paid" and row.paid_amount == Decimal("34240.00")
            assert [p.amount for p in repo.list_payments(t["scope"], row_id)] == [
                Decimal("10000.00"), Decimal("24240.00"),
            ]
            with pytest.raises(InvoiceConflict, match="already paid in full"):
                repo.add_payment(t["scope"], row_id, amount="1")
            session.rollback()

    def test_void_only_while_nothing_was_paid(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            row = repo.issue(t["scope"], _create(repo, t).id)
            repo.add_payment(t["scope"], row.id, amount="500")
            session.commit()
            row_id = row.id
            with pytest.raises(InvoiceConflict, match="has payments recorded"):
                repo.void(t["scope"], row_id)
            session.rollback()
            with pytest.raises(InvoiceConflict, match="not paid in full"):
                repo.set_receipt_document(t["scope"], row_id, uuid.uuid4())
            session.rollback()

    def test_a_void_invoice_takes_no_payment_and_cannot_be_issued(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            row = repo.void(t["scope"], _create(repo, t).id)
            session.commit()
            row_id = row.id
            assert row.status == "void"
            with pytest.raises(InvoiceConflict, match="is void"):
                repo.add_payment(t["scope"], row_id, amount="1")
            session.rollback()
            with pytest.raises(InvoiceConflict, match="already void"):
                repo.issue(t["scope"], row_id)
            session.rollback()


class TestOverdueIsDerived:
    def test_overdue_is_a_reading_not_a_column(self, migrated_db):
        t = _tenant(migrated_db)
        today = date(2026, 9, 21)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            late = repo.issue(
                t["scope"], _create(repo, t).id,
                issue_date=date(2026, 8, 1), due_date=date(2026, 8, 31),
            )
            session.commit()
            assert is_overdue(late, today=today) is True
            assert is_overdue(late, today=date(2026, 8, 31)) is False, "due today is not overdue"
            assert [r.id for r in repo.list_for_license(t["scope"], overdue=True, today=today)] == [late.id]
            assert repo.count_for_license(t["scope"], overdue=True, today=today) == 1
            # Paid: the date passed, nothing is owed, nothing is overdue.
            repo.add_payment(t["scope"], late.id, amount="34240.00")
            session.commit()
            assert is_overdue(late, today=today) is False
            assert repo.count_for_license(t["scope"], overdue=True, today=today) == 0
            summary = repo.summary(t["scope"], today=today)
            assert summary == {"open_count": 0, "outstanding_total": Decimal("0.00"), "overdue_count": 0}

    def test_the_summary_adds_what_is_owed(self, migrated_db):
        t = _tenant(migrated_db)
        today = date(2026, 9, 21)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            row = repo.issue(
                t["scope"], _create(repo, t).id,
                issue_date=date(2026, 9, 1), due_date=date(2026, 9, 10),
            )
            repo.add_payment(t["scope"], row.id, amount="4240.00")
            session.commit()
            assert repo.summary(t["scope"], today=today) == {
                "open_count": 1, "outstanding_total": Decimal("30000.00"), "overdue_count": 1,
            }


class TestListAndIsolation:
    def test_search_status_and_customer_filters_count_the_same_rows(self, migrated_db):
        t = _tenant(migrated_db, with_customer_uid=f"CHN-C-{uuid.uuid4().hex[:8]}")
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            first = repo.issue(t["scope"], _create(repo, t).id)
            session.commit()
            rows = repo.list_for_license(t["scope"], q="สมชาย")
            assert [r.id for r in rows] == [first.id], "the customer's name finds their bill"
            assert repo.count_for_license(t["scope"], q="สมชาย") == 1
            assert repo.count_for_license(t["scope"], q=first.invoice_id.lower()) == 1
            assert repo.count_for_license(t["scope"], status="issued") == 1
            assert repo.count_for_license(t["scope"], status="paid") == 0
            assert repo.count_for_license(t["scope"], contact_id=t["customer_id"]) == 1
            from chann_data.models import Customer
            uid = session.get(Customer, t["customer_id"]).customer_chann_uid
            assert repo.count_for_license(t["scope"], customer_chann_uid=uid) == 1
            assert repo.count_for_license(t["scope"], customer_chann_uid="CHN-C-nobody") == 0

    def test_another_tenant_sees_nothing_and_can_touch_nothing(self, migrated_db):
        a, b = _tenant(migrated_db), _tenant(migrated_db)
        with a["session"]() as session:
            row = _create(InvoiceRepository(session), a)
            session.commit()
            row_id = row.id
        with b["session"]() as session:
            repo = InvoiceRepository(session)
            assert repo.get(b["scope"], row_id) is None
            assert repo.list_for_license(b["scope"]) == []
            for call in (
                lambda: repo.issue(b["scope"], row_id),
                lambda: repo.add_payment(b["scope"], row_id, amount="1"),
                lambda: repo.void(b["scope"], row_id),
            ):
                with pytest.raises(InvoiceNotFound):
                    call()
                session.rollback()
            # Nor can B bill A's quotation.
            with pytest.raises(InvoiceNotFound, match="quote not found"):
                repo.create(b["scope"], quote_id=a["quote_id"], total="1")
            session.rollback()

    def test_the_http_shape_carries_the_derived_fields(self, migrated_db):
        """The Out schema, through the real router helper: outstanding and
        is_overdue are computed, the payments ride with the detail."""
        from chann_data.routers.internal import _invoice_out

        t = _tenant(migrated_db)
        with t["session"]() as session:
            repo = InvoiceRepository(session)
            row = repo.issue(
                t["scope"], _create(repo, t).id,
                issue_date=date(2020, 1, 1), due_date=date(2020, 1, 31),
            )
            repo.add_payment(t["scope"], row.id, amount="240.00", method="promptpay")
            session.commit()
            out = _invoice_out(row, repo.list_payments(t["scope"], row.id))
            assert out.outstanding == Decimal("34000.00") and out.is_overdue is True
            assert out.payments[0].method == "promptpay"
            assert _invoice_out(row).payments == []
