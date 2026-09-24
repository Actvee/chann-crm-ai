"""Round 21C — an invoice's lines and details can be corrected.

Owner, 23 ก.ย. 2569: "invoice ต้องแก้ไขรายการสินค้าหรือข้อมูลต่างๆได้เหมือน
quote ด้วย". A quote is editable while it is `draft`; an invoice is
editable until money has touched it (spec §3.3).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity
from chann_data.repositories.invoices import InvoiceConflict, InvoiceRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

SNAPSHOT = {
    "line_items": [
        {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
         "unit_price": "15000.00", "line_total": "30000.00", "notes": None},
    ],
    "totals": {"subtotal": "30000.00", "discount_amount": "0", "vat_rate": "0.07",
               "vat_amount": "2100.00", "grand_total": "32100.00"},
}


@pytest.fixture
def invoice(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CE{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        license_id = RegistrationRepository(s).create_license(
            company_name=f"Invoice edit {tag}", created_by_chann_uid=uid).id
        s.commit()
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as s:
        customer = CustomerRepository(s).create(
            scope, first_name="ลูกค้า", last_name="แก้", phone="0830000001")
        s.flush()
        deal = DealRepository(s).create(scope, contact_id=customer.id)
        s.flush()
        row = InvoiceRepository(s).create(
            scope, deal_id=deal.id, contact_id=customer.id,
            subtotal=Decimal("30000.00"), vat_rate=Decimal("0.07"),
            vat_amount=Decimal("2100.00"), total=Decimal("32100.00"),
            data_snapshot=SNAPSHOT, created_by=uid)
        s.commit()
        invoice_id = row.id
    return migrated_db, scope, invoice_id


class TestEditingADraft:
    def test_lines_and_totals_are_replaced_together(self, invoice):
        engine, scope, invoice_id = invoice
        changed = {**SNAPSHOT, "line_items": [
            {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 3,
             "unit_price": "15000.00", "line_total": "45000.00", "notes": None}]}
        with Session(engine) as s:
            row = InvoiceRepository(s).update_lines(
                scope, invoice_id, data_snapshot=changed,
                subtotal=Decimal("45000.00"), discount_amount=Decimal("0"),
                vat_rate=Decimal("0.07"), vat_amount=Decimal("3150.00"),
                total=Decimal("48150.00"))
            s.commit()
            assert row.total == Decimal("48150.00")
            assert row.data_snapshot["line_items"][0]["qty"] == 3

    def test_details_are_editable_too(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            row = InvoiceRepository(s).update_details(
                scope, invoice_id, note="ชำระภายใน 15 วัน", due_date=date(2026, 10, 8))
            s.commit()
            assert row.note == "ชำระภายใน 15 วัน"
            assert row.due_date == date(2026, 10, 8)


class TestTheLock:
    def test_an_edit_after_issue_marks_the_invoice_as_needing_reissue(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            repo.issue(scope, invoice_id, issue_date=date(2026, 9, 23))
            row = repo.update_details(scope, invoice_id, note="แก้หลังออกแล้ว")
            s.commit()
            assert row.data_snapshot.get("needs_reissue_at")

    def test_money_received_locks_it(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            repo.issue(scope, invoice_id, issue_date=date(2026, 9, 23))
            repo.add_payment(scope, invoice_id, amount=Decimal("1000.00"), method="cash")
            s.commit()
        with Session(engine) as s:
            with pytest.raises(InvoiceConflict, match="has a payment"):
                InvoiceRepository(s).update_details(scope, invoice_id, note="สายไป")

    def test_a_void_invoice_cannot_be_edited(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            repo.void(scope, invoice_id)
            s.commit()
        with Session(engine) as s:
            with pytest.raises(InvoiceConflict, match="void"):
                InvoiceRepository(s).update_details(scope, invoice_id, note="สายไป")

    def test_a_zero_total_invoice_auto_paid_at_issue_cannot_be_edited(self, invoice):
        """`issue()` marks a zero-total invoice `paid` on the spot
        (nothing was ever owed), while `paid_amount` stays 0 — `_editable`
        must refuse it on `status` alone, not only on `paid_amount > 0`."""
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            zero = repo.update_lines(
                scope, invoice_id, data_snapshot=SNAPSHOT,
                subtotal=Decimal("0"), discount_amount=Decimal("0"),
                vat_rate=Decimal("0"), vat_amount=Decimal("0"), total=Decimal("0"))
            s.commit()
            assert zero.total == Decimal("0")
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            row = repo.issue(scope, invoice_id, issue_date=date(2026, 9, 23))
            s.commit()
            assert row.status == "paid"
            assert row.paid_amount == Decimal("0.00")
        with Session(engine) as s:
            with pytest.raises(InvoiceConflict, match="paid"):
                InvoiceRepository(s).update_details(scope, invoice_id, note="สายไป")
        with Session(engine) as s:
            with pytest.raises(InvoiceConflict, match="paid"):
                InvoiceRepository(s).update_lines(
                    scope, invoice_id, data_snapshot=SNAPSHOT,
                    subtotal=Decimal("5000.00"), discount_amount=Decimal("0"),
                    vat_rate=Decimal("0.07"), vat_amount=Decimal("350.00"),
                    total=Decimal("5350.00"))
