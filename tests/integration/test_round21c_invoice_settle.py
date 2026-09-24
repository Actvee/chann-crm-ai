"""Round 21C — when the bill is paid, the deal closes.

Owner, 23 ก.ย. 2569, verbatim: "ใบแจ้งหนี้จะอัพเดตไปที่ดีลตอนชำระเงินแล้ว".
Not when it is issued — when the money arrives, which is the same moment
the receipt becomes possible.
"""
from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, Deal, Invoice
from chann_data.repositories.invoices import InvoiceRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

SNAPSHOT = {
    "line_items": [
        {"line_no": 1, "product_name": "แอร์ 18000 BTU", "qty": 1,
         "unit_price": "22000.00", "line_total": "22000.00", "notes": None},
        {"line_no": 2, "product_name": "ค่าติดตั้ง", "qty": 1,
         "unit_price": "3000.00", "line_total": "3000.00", "notes": None},
    ],
    "totals": {"subtotal": "25000.00", "grand_total": "26750.00"},
}


def _world(engine, *, stage: str = "proposed", amount=None, detached: bool = False,
           discount=Decimal("0")):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CS{tag}"
    with Session(engine) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(engine) as s:
        license_id = RegistrationRepository(s).create_license(
            company_name=f"Settle {tag}", created_by_chann_uid=uid).id
        s.commit()
    scope = TenantScope(license_id=license_id)
    with Session(engine) as s:
        customer = CustomerRepository(s).create(
            scope, first_name="ลูกค้า", last_name="จ่าย", phone=f"08{tag[:8]}")
        s.flush()
        deals = DealRepository(s)
        deal = deals.create(scope, contact_id=customer.id, amount=amount)
        s.flush()
        deals.add_product(scope, deal.id, product_id=None, product_name="ของเดิม",
                          quoted_unit_price=Decimal("111.00"), qty=1)
        if stage != "new":
            deal.stage = stage
        s.flush()
        invoice = InvoiceRepository(s).create(
            scope, deal_id=None if detached else deal.id, contact_id=customer.id,
            subtotal=Decimal("25000.00"), discount_amount=discount, vat_rate=Decimal("0.07"),
            vat_amount=((Decimal("25000.00") - discount) * Decimal("0.07")).quantize(Decimal("0.01")),
            total=((Decimal("25000.00") - discount) * Decimal("1.07")).quantize(Decimal("0.01")),
            data_snapshot=SNAPSHOT, created_by=uid)
        s.flush()
        InvoiceRepository(s).issue(scope, invoice.id, issue_date=date(2026, 9, 23))
        s.commit()
        return scope, deal.id, invoice.id


class TestTheSettle:
    def test_paying_in_full_closes_the_deal_and_writes_the_invoice_onto_it(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db)
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            lines = DealRepository(s).products_of(deal_id)
            assert deal.stage == "won"
            assert deal.closed_at is not None
            # Ruling 23: the deal's value is pre-VAT, like every other deal
            # value on the platform (quoted_unit_price × qty) — the bill's
            # subtotal after discount, never its VAT-inclusive total.
            assert deal.amount == Decimal("25000.00")
            assert [(l.product_name, l.qty, l.quoted_unit_price) for l in lines] == [
                ("แอร์ 18000 BTU", 1, Decimal("22000.00")),
                ("ค่าติดตั้ง", 1, Decimal("3000.00")),
            ]

    def test_a_partial_payment_changes_nothing_on_the_deal(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db)
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("1000.00"), method="cash")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            assert deal.stage == "proposed"
            assert deal.closed_at is None

    def test_a_deal_still_at_new_is_closed_too_because_the_money_arrived(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db, stage="new")
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            assert s.get(Deal, deal_id).stage == "won"

    def test_a_lost_deal_is_not_resurrected(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db, stage="lost")
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            assert deal.stage == "lost"
            assert deal.amount is None

    def test_a_second_invoice_on_a_closed_deal_leaves_it_alone(self, migrated_db):
        scope, deal_id, first = _world(migrated_db)
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, first, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            repo = InvoiceRepository(s)
            second = repo.create(
                scope, deal_id=deal_id, subtotal=Decimal("500.00"),
                vat_rate=None, vat_amount=Decimal("0"), total=Decimal("500.00"),
                data_snapshot={"line_items": [
                    {"line_no": 1, "product_name": "ค่าล้างแอร์", "qty": 1,
                     "unit_price": "500.00", "line_total": "500.00"}]})
            s.flush()
            repo.issue(scope, second.id, issue_date=date(2026, 9, 24))
            repo.add_payment(scope, second.id, amount=Decimal("500.00"), method="cash")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            assert deal.amount == Decimal("25000.00")      # not 500
            assert len(DealRepository(s).products_of(deal_id)) == 2


# --------------------------------------------------------------- the payload
#
# Review finding 1 (fix round 1): the repository knew which deal this payment
# closed and the route dropped it, so chat had to GUESS by reading the deal
# back — and a second invoice paid in full on an already-won deal made it
# report a close that never happened, with a value from the wrong bill. The
# route now carries the answer, and "non-None" means "THIS transaction closed
# it": nothing else can produce it.


@pytest.fixture
def api(migrated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from chann_data import config as config_module
    from chann_data.db import get_session
    from chann_data.main import app

    monkeypatch.setattr(config_module.settings, "admin_secret", "test-internal-secret")
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), {"X-Internal-Secret": "test-internal-secret"}
    finally:
        app.dependency_overrides.pop(get_session, None)


def _pay(api, scope, invoice_id, amount, method="transfer"):
    http, headers = api
    return http.post(
        f"/internal/v1/licenses/{scope.license_id}/invoices/{invoice_id}/payments",
        json={"amount": str(amount), "method": method}, headers=headers,
    )


class TestThePaymentRouteSaysWhichDealItClosed:
    def test_the_settling_payment_returns_the_deal_it_closed(self, migrated_db, api):
        scope, deal_id, invoice_id = _world(migrated_db)
        response = _pay(api, scope, invoice_id, "26750.00")
        assert response.status_code == 201, response.text
        closed = response.json()["closed_deal"]
        assert closed is not None
        assert closed["id"] == str(deal_id)
        assert Decimal(closed["amount"]) == Decimal("25000.00")      # pre-VAT (ruling 23)
        assert closed["stage"] == "won"
        assert closed["deal_id"].startswith("D-")
        # The lines it was paid for travel with it: "products": [] would
        # read as "this deal has nothing on it", which is the opposite of
        # what just happened.
        assert [(p["product_name"], p["qty"]) for p in closed["products"]] == [
            ("แอร์ 18000 BTU", 1), ("ค่าติดตั้ง", 1),
        ]

    def test_a_partial_payment_returns_null(self, migrated_db, api):
        scope, _deal_id, invoice_id = _world(migrated_db)
        response = _pay(api, scope, invoice_id, "1000.00", method="cash")
        assert response.status_code == 201, response.text
        assert response.json()["closed_deal"] is None

    def test_a_second_bill_on_an_already_closed_deal_returns_null(self, migrated_db, api):
        """The falsehood this field exists to make impossible: paid in full,
        deal already won, nothing written — so nothing may be claimed."""
        scope, deal_id, first = _world(migrated_db)
        assert _pay(api, scope, first, "26750.00").json()["closed_deal"] is not None
        with Session(migrated_db) as s:
            repo = InvoiceRepository(s)
            second = repo.create(
                scope, deal_id=deal_id, subtotal=Decimal("500.00"),
                vat_rate=None, vat_amount=Decimal("0"), total=Decimal("500.00"),
                data_snapshot={"line_items": [
                    {"line_no": 1, "product_name": "ค่าล้างแอร์", "qty": 1,
                     "unit_price": "500.00", "line_total": "500.00"}]})
            s.flush()
            repo.issue(scope, second.id, issue_date=date(2026, 9, 24))
            s.commit()
            second_id = second.id
        response = _pay(api, scope, second_id, "500.00", method="cash")
        assert response.status_code == 201, response.text
        assert response.json()["closed_deal"] is None

    def test_an_invoice_with_no_deal_returns_null(self, migrated_db, api):
        scope, _deal_id, invoice_id = _world(migrated_db, detached=True)
        response = _pay(api, scope, invoice_id, "26750.00")
        assert response.status_code == 201, response.text
        assert response.json()["closed_deal"] is None

    def test_every_other_invoice_route_still_answers_null(self, migrated_db, api):
        """The field is optional everywhere else — one route fills it."""
        http, headers = api
        scope, _deal_id, invoice_id = _world(migrated_db)
        one = http.get(f"/internal/v1/licenses/{scope.license_id}/invoices/{invoice_id}",
                       headers=headers)
        assert one.status_code == 200, one.text
        assert one.json()["closed_deal"] is None


# ------------------------------------------------------------- the audit rows
#
# Two rows are written about the deal when a payment settles the bill: it
# closed (`status`) and its contents were replaced (`update`). The "before"
# stage used to be the literal placeholder "open" — a sentence about the
# deal that nobody had checked, and false for every deal that was sitting
# at "proposed". The row now says where the deal actually came from.


def _deal_audit(engine, deal_id) -> dict:
    from sqlalchemy import select

    from chann_data.models import AuditLog

    with Session(engine) as s:
        rows = s.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "deal", AuditLog.entity_id == deal_id,
            ).order_by(AuditLog.created_at)
        ).scalars().all()
        return {row.action: dict(row.field_changes or {}) for row in rows}


class TestWhatTheDealsAuditRowsSay:
    def test_the_status_row_records_the_stage_the_deal_really_came_from(self, migrated_db, api):
        scope, deal_id, invoice_id = _world(migrated_db, stage="proposed")
        assert _pay(api, scope, invoice_id, "26750.00").status_code == 201
        rows = _deal_audit(migrated_db, deal_id)
        assert set(rows) == {"status", "update"}
        assert rows["status"]["stage"] == {"old": "proposed", "new": "won"}
        assert rows["status"]["closed_by_invoice"]["new"].startswith("INV-")
        assert rows["status"]["closed_by_invoice"]["old"] is None

    def test_a_deal_still_at_new_says_new_not_open(self, migrated_db, api):
        scope, deal_id, invoice_id = _world(migrated_db, stage="new")
        assert _pay(api, scope, invoice_id, "26750.00").status_code == 201
        assert _deal_audit(migrated_db, deal_id)["status"]["stage"] == {
            "old": "new", "new": "won"}

    def test_the_update_row_records_the_amount_and_which_bill_it_came_from(self, migrated_db, api):
        scope, deal_id, invoice_id = _world(migrated_db)
        response = _pay(api, scope, invoice_id, "26750.00")
        assert response.status_code == 201, response.text
        invoice_number = response.json()["invoice_id"]
        update = _deal_audit(migrated_db, deal_id)["update"]
        assert update["amount"] == {"old": None, "new": "25000.00"}
        assert update["from_invoice"] == {"old": None, "new": invoice_number}
        # The VAT-inclusive total the customer paid is kept beside it.
        assert update["invoice_total"] == {"old": None, "new": "26750.00"}

    def test_the_update_row_records_the_real_old_amount(self, migrated_db, api):
        # Carried item (b): "before" was `{}`, so amount.old was always None
        # even for a deal somebody had typed a value on.
        scope, deal_id, invoice_id = _world(migrated_db, amount=Decimal("30000.00"))
        assert _pay(api, scope, invoice_id, "26750.00").status_code == 201
        update = _deal_audit(migrated_db, deal_id)["update"]
        assert update["amount"] == {"old": "30000.00", "new": "25000.00"}

    def test_a_partial_payment_writes_nothing_about_the_deal(self, migrated_db, api):
        scope, deal_id, invoice_id = _world(migrated_db)
        assert _pay(api, scope, invoice_id, "1000.00", method="cash").status_code == 201
        assert _deal_audit(migrated_db, deal_id) == {}


class TestTheDealValueIsPreVat:
    """Ruling 23 (final review I4): after the write-back the deal is worth
    the bill's subtotal AFTER discount and BEFORE VAT — the same basis as a
    deal valued by its lines, so pipeline totals never mix the two."""

    def test_a_discount_is_taken_off_and_vat_is_not_added(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db, discount=Decimal("1000.00"))
        with Session(migrated_db) as s:
            invoice = s.get(Invoice, invoice_id)
            assert invoice.total == Decimal("25680.00")
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=invoice.total, method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            assert s.get(Deal, deal_id).amount == Decimal("24000.00")
