"""PLAN_3OA B5 — the customer's home per spec pages 1–2: the storefront
("สินค้าทั้งหมด", a search, "สนใจ"), and purchase history — in chat and
over the routes the home screen calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import storefront  # noqa: E402
from chann_app.services.authorization import CUSTOMER_PERMISSION_KEYS, TenantPrincipal  # noqa: E402
from chann_app.services.chat import handle_chat_message, maybe_handle_storefront  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402

PRODUCTS = [
    {"product_id": "P-1", "product_name": "พัดลมไอเย็น", "sku": "F-1", "category": None,
     "unit_price": "3500", "license_id": LICENSE_ID, "company_name": "ร้านเย็นสบาย"},
    {"product_id": "P-2", "product_name": "แอร์ 12000 BTU", "sku": "A-1", "category": None,
     "unit_price": "15900", "license_id": "lic-other", "company_name": "ร้านแอร์ดี"},
]
MEMBERS = [
    {"id": "m-1", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"},
    {"id": "m-2", "chann_uid": "CHN-TECH", "role": "technician", "status": "active"},
]


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _customer_ctx():
    return _ctx(primary_role="customer", oa="customer")


class TestStorefrontInChat:
    async def test_all_products_is_the_storefront_not_the_shops_list(self):
        client = FakeDataClient(role="customer", permission_keys=[], storefront_results=PRODUCTS)
        reply = await maybe_handle_storefront(
            client, message="สินค้าทั้งหมด", ctx=_customer_ctx(), language="th",
        )
        assert reply is not None
        assert "พัดลมไอเย็น" in reply.text and "แอร์ 12000 BTU" in reply.text
        assert any(r[0] == "storefront_browse" for r in client.recorded)
        assert not any(r[0] == "list_products" for r in client.recorded)

    async def test_picking_one_records_interest_and_tells_the_shop(self):
        client = FakeDataClient(role="customer", permission_keys=[], storefront_results=PRODUCTS)
        client._members = list(MEMBERS)
        await maybe_handle_storefront(client, message="สินค้าทั้งหมด", ctx=_customer_ctx(), language="th")
        reply = await maybe_handle_storefront(client, message="1", ctx=_customer_ctx(), language="th")
        assert reply is not None and "ร้านเย็นสบาย" in reply.text
        assert any(
            r[0] == "storefront_record_interest" and r[3] == "พัดลมไอเย็น" for r in client.recorded
        )
        notes = [r for r in client.recorded if r[0] == "create_notification"]
        assert len(notes) == 1 and "พัดลมไอเย็น" in str(notes[0])

    async def test_an_empty_storefront_says_so(self):
        client = FakeDataClient(role="customer", permission_keys=[], storefront_results=[])
        reply = await maybe_handle_storefront(
            client, message="สินค้าทั้งหมด", ctx=_customer_ctx(), language="th",
        )
        assert reply is not None and "ยังไม่มีสินค้า" in reply.text


class TestOrdersInChat:
    def _shop(self, client):
        client._customers = [{
            "id": "c-1", "customer_id": "C-0001", "customer_chann_uid": "CHN-S-000001",
            "first_name": "สมชาย", "stage": "contact",
        }]
        client._deals = [{
            "id": "d-1", "deal_id": "D-2026-0001", "contact_id": "c-1", "stage": "won",
            "created_at": "2026-09-01T09:00:00+07:00", "archived_at": None,
            "products": [{"id": "dp-1", "product_name": "พัดลมไอเย็น", "qty": 2, "quoted_unit_price": "3500"}],
        }, {
            "id": "d-2", "deal_id": "D-2026-0002", "contact_id": "c-other", "stage": "new",
            "created_at": "2026-09-02T09:00:00+07:00", "archived_at": None, "products": [],
        }]

    async def test_history_lists_only_this_customers_deals(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        self._shop(client)
        reply = await handle_chat_message(client, message="ประวัติการซื้อ", ctx=_customer_ctx())
        assert "D-2026-0001" in reply.text and "พัดลมไอเย็น" in reply.text
        assert "D-2026-0002" not in reply.text
        assert ("list_deals", LICENSE_ID, None, "c-1") in client.recorded

    async def test_no_customer_record_means_no_history_not_an_error(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await handle_chat_message(client, message="ประวัติการซื้อ", ctx=_customer_ctx())
        assert "ยังไม่มีประวัติการซื้อ" in reply.text
        assert not any(r[0] == "create_ticket" for r in client.recorded)


class TestService:
    async def test_search_with_no_term_browses(self):
        client = FakeDataClient(role="customer", permission_keys=[], storefront_results=PRODUCTS)
        await storefront.search(client, q="  ", limit=500)
        assert ("storefront_browse", 50) in client.recorded
        await storefront.search(client, q="พัดลม", limit=5)
        assert ("storefront_search", "พัดลม", 5) in client.recorded


def _harness(principal: TenantPrincipal):
    client = FakeDataClient(role="customer", permission_keys=[], storefront_results=PRODUCTS)

    async def override_client():
        yield client

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


def _customer_principal():
    return TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="customer", is_owner=False,
        permission_keys=CUSTOMER_PERMISSION_KEYS, audience="customer",
    )


def _staff_principal():
    return TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000002", role="sales", is_owner=False,
        permission_keys=frozenset({"deal.read"}), audience="sales",
    )


class TestHomeRoutes:
    def test_search_and_browse(self):
        http, client = _harness(_customer_principal())
        response = http.get("/api/v1/storefront/products", params={"q": "พัดลม"})
        assert response.status_code == 200, response.text
        assert response.json()[0]["product_name"] == "พัดลมไอเย็น"
        response = http.get("/api/v1/storefront/products")
        assert response.status_code == 200
        assert ("storefront_browse", 20) in client.recorded

    def test_interest_is_for_customers_only(self):
        http, client = _harness(_customer_principal())
        client._members = list(MEMBERS)
        response = http.post(
            "/api/v1/storefront/interest",
            json={"license_id": LICENSE_ID, "product_name": "พัดลมไอเย็น", "company_name": "ร้านเย็นสบาย"},
        )
        assert response.status_code == 201, response.text
        assert any(r[0] == "storefront_record_interest" for r in client.recorded)
        assert any(r[0] == "create_notification" for r in client.recorded)

        http2, client2 = _harness(_staff_principal())
        response = http2.post(
            "/api/v1/storefront/interest", json={"license_id": LICENSE_ID, "product_name": "x"},
        )
        assert response.status_code == 403
        assert not any(r[0] == "storefront_record_interest" for r in client2.recorded)

    def test_my_orders(self):
        http, client = _harness(_customer_principal())
        TestOrdersInChat()._shop(client)
        response = http.get(f"/api/v1/licenses/{LICENSE_ID}/deals/mine")
        assert response.status_code == 200, response.text
        assert [d["deal_id"] for d in response.json()] == ["D-2026-0001"]
