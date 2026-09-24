"""Round 21C — editing an invoice recomputes its money in one place."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app import routers_phase2  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import invoices  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402


class FakeClient:
    def __init__(self, invoice):
        self.invoice = invoice
        self.lines_payload = None
        self.details_payload = None

    async def get_invoice(self, license_id, invoice_id):
        return self.invoice

    async def update_invoice_lines(self, license_id, invoice_id, payload, actor_id=None):
        self.lines_payload = payload
        return {**self.invoice, **{k: payload[k] for k in ("subtotal", "total")}}

    async def update_invoice_details(self, license_id, invoice_id, payload, actor_id=None):
        self.details_payload = payload
        return {**self.invoice, "note": payload.get("note")}

    async def get_customer(self, license_id, customer_id):
        return {"id": customer_id, "first_name": "สมชาย", "last_name": "ใจดี"}

    async def get_company_profile(self, license_id):
        return {"company_name": "ร้านทดสอบ", "vat_rate": "0.07"}


class ConflictClient(FakeClient):
    """Stands in for a live Data tier that refuses a VOID invoice: the
    Application tier never pre-checks void (only `paid_amount`), so this is
    the only way that refusal is ever seen — as a 409 from the client, the
    same way a real `InvoiceConflict` arrives from `_editable()`."""

    def __init__(self, invoice, message):
        super().__init__(invoice)
        self._message = message

    async def update_invoice_lines(self, license_id, invoice_id, payload, actor_id=None):
        raise DataTierError(409, self._message)

    async def update_invoice_details(self, license_id, invoice_id, payload, actor_id=None):
        raise DataTierError(409, self._message)


STAFF = TenantPrincipal(
    license_id="L1", chann_uid="CHN-OWNER", role="member", is_owner=False,
    permission_keys=frozenset({"invoice.update", "invoice.read"}), audience="sales",
)


INVOICE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "invoice_id": "INV-2026-0001", "status": "draft", "paid_amount": "0",
    "contact_id": "22222222-2222-2222-2222-222222222222", "deal_id": None,
    "total": "32100.00", "vat_rate": "0.07",
    "data_snapshot": {"line_items": [
        {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
         "unit_price": "15000.00", "line_total": "30000.00"}]},
}


class TestEditLines:
    @pytest.mark.asyncio
    async def test_the_totals_sent_are_computed_from_the_new_lines(self):
        client = FakeClient(INVOICE)
        await invoices.edit_lines(
            client, license_id="L1", invoice=INVOICE,
            lines=[{"product_name": "แอร์ 12000 BTU", "qty": 3, "unit_price": "15000.00"}],
            company={"company_name": "ร้านทดสอบ", "vat_rate": "0.07"},
        )
        sent = client.lines_payload
        assert Decimal(sent["subtotal"]) == Decimal("45000.00")
        assert Decimal(sent["vat_amount"]) == Decimal("3150.00")
        assert Decimal(sent["total"]) == Decimal("48150.00")
        assert sent["data_snapshot"]["line_items"][0]["qty"] == 3

    @pytest.mark.asyncio
    async def test_an_empty_line_set_is_refused_before_the_data_tier(self):
        client = FakeClient(INVOICE)
        with pytest.raises(invoices.InvoiceLinesEmpty):
            await invoices.edit_lines(client, license_id="L1", invoice=INVOICE, lines=[],
                                      company={"vat_rate": "0.07"})
        assert client.lines_payload is None

    @pytest.mark.asyncio
    async def test_a_paid_invoice_is_refused_before_the_data_tier(self):
        paid = {**INVOICE, "status": "partially_paid", "paid_amount": "1000.00"}
        client = FakeClient(paid)
        with pytest.raises(invoices.InvoiceLocked):
            await invoices.edit_details(client, license_id="L1", invoice=paid, note="สายไป")
        assert client.details_payload is None


class TestVoidSurfacesTheSameLockedShapeAsPaid:
    """Ruling 1: a void invoice was never pre-checked in the Application
    tier (only `paid_amount` is), so its refusal can only be seen once the
    route is asked — through the identical `except DataTierError as exc:
    raise _propagate(exc)` every other invoice route already uses. These
    call the route functions directly (a staff principal, no HTTP layer
    needed) and compare the resulting HTTPException to the paid case's."""

    LINES_PAYLOAD = routers_phase2.InvoiceLinesBody(lines=[
        routers_phase2.InvoiceLineIn(product_name="แอร์ 12000 BTU", qty=3, unit_price="15000.00"),
    ])

    @pytest.mark.asyncio
    async def test_lines_route_locks_void_the_same_way_as_paid(self):
        paid = {**INVOICE, "status": "partially_paid", "paid_amount": "1000.00"}
        with pytest.raises(HTTPException) as paid_exc:
            await routers_phase2.update_invoice_lines(
                license_id="L1", invoice_id=paid["id"], payload=self.LINES_PAYLOAD,
                principal=STAFF, client=FakeClient(paid),
            )
        voided = {**INVOICE, "status": "void", "paid_amount": "0"}
        with pytest.raises(HTTPException) as void_exc:
            await routers_phase2.update_invoice_lines(
                license_id="L1", invoice_id=voided["id"], payload=self.LINES_PAYLOAD,
                principal=STAFF,
                client=ConflictClient(voided, f"invoice {voided['invoice_id']} is void and cannot be edited"),
            )
        assert paid_exc.value.status_code == void_exc.value.status_code == 409
        assert isinstance(paid_exc.value.detail, str) and isinstance(void_exc.value.detail, str)

    @pytest.mark.asyncio
    async def test_details_route_locks_void_the_same_way_as_paid(self):
        paid = {**INVOICE, "status": "partially_paid", "paid_amount": "1000.00"}
        with pytest.raises(HTTPException) as paid_exc:
            await routers_phase2.update_invoice_details(
                license_id="L1", invoice_id=paid["id"],
                payload=routers_phase2.InvoiceDetailsBody(note="สายไป"),
                principal=STAFF, client=FakeClient(paid),
            )
        voided = {**INVOICE, "status": "void", "paid_amount": "0"}
        with pytest.raises(HTTPException) as void_exc:
            await routers_phase2.update_invoice_details(
                license_id="L1", invoice_id=voided["id"],
                payload=routers_phase2.InvoiceDetailsBody(note="สายไป"),
                principal=STAFF,
                client=ConflictClient(voided, f"invoice {voided['invoice_id']} is void and cannot be edited"),
            )
        assert paid_exc.value.status_code == void_exc.value.status_code == 409
        assert isinstance(paid_exc.value.detail, str) and isinstance(void_exc.value.detail, str)


class TestCustomerGuard:
    """Ruling 11: a customer principal must be refused before the client is
    ever asked, exactly like the sibling invoice-mutation routes
    (issue/pay/receipt/void)."""

    CUSTOMER = TenantPrincipal(
        license_id="L1", chann_uid="CHN-S-000001", role="customer", is_owner=False,
        permission_keys=frozenset({"invoice.update", "invoice.read"}), audience="customer",
    )

    @pytest.mark.asyncio
    async def test_lines_route_refuses_a_customer_before_the_client_is_asked(self):
        client = FakeClient(INVOICE)
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.update_invoice_lines(
                license_id="L1", invoice_id=INVOICE["id"],
                payload=routers_phase2.InvoiceLinesBody(lines=[
                    routers_phase2.InvoiceLineIn(product_name="แอร์ 12000 BTU", qty=1, unit_price="15000.00"),
                ]),
                principal=self.CUSTOMER, client=client,
            )
        assert exc.value.status_code == 403
        assert client.lines_payload is None

    @pytest.mark.asyncio
    async def test_details_route_refuses_a_customer_before_the_client_is_asked(self):
        client = FakeClient(INVOICE)
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.update_invoice_details(
                license_id="L1", invoice_id=INVOICE["id"],
                payload=routers_phase2.InvoiceDetailsBody(note="สายไป"),
                principal=self.CUSTOMER, client=client,
            )
        assert exc.value.status_code == 403
        assert client.details_payload is None
