"""Round 21A — a quote knows whether it has been billed.

Owner, 22 ก.ย. 2569: "ควรดูได้เหมือนกันว่ามีใบแจ้งหนี้แล้วหรือยัง ถ้ามีควร
กดไปดูได้ และจากใบแจ้งหนี้ก็ควรกดไปที่ record ที่เกี่ยวข้องได้ด้วย".

The page could not answer that question: nothing let it ask for the
invoices of ONE quote. The filter now exists on all three tiers, beside
the deal filter round 20X added.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


def _harness(keys=("invoice.read",)):
    client = FakeDataClient(role="sales", permission_keys=list(keys))
    client._invoices = [
        {"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "issued", "total": "100.00",
         "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
         "contact_id": "CUST-1", "deal_id": "DEAL-1", "quote_id": "Q-1"},
        {"id": "INV-B", "invoice_id": "INV-2026-0002", "status": "issued", "total": "200.00",
         "paid_amount": "0.00", "outstanding": "200.00", "is_overdue": False,
         "contact_id": "CUST-1", "deal_id": "DEAL-1", "quote_id": "Q-2"},
    ]

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="sales", is_owner=False,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


class TestTheInvoicesOfOneQuote:
    def test_the_filter_narrows_to_that_quote(self):
        http, _client = _harness()
        response = http.get(f"/api/v1/licenses/{LICENSE_ID}/invoices?quote_id=Q-1")
        assert response.status_code == 200, response.text
        assert [row["invoice_id"] for row in response.json()] == ["INV-2026-0001"]

    def test_a_quote_with_no_bill_answers_an_empty_list_not_an_error(self):
        http, _client = _harness()
        response = http.get(f"/api/v1/licenses/{LICENSE_ID}/invoices?quote_id=Q-NONE")
        assert response.status_code == 200 and response.json() == []

    def test_the_deal_filter_still_works_beside_it(self):
        http, _client = _harness()
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/invoices?deal_id=DEAL-1").json()
        assert {row["invoice_id"] for row in rows} == {"INV-2026-0001", "INV-2026-0002"}

    def test_reading_bills_needs_the_key(self):
        http, _client = _harness(keys=("quote.read",))
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/invoices?quote_id=Q-1").status_code == 403
