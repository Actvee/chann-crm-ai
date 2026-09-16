"""Round 19g — the purchase date is optional and the period is the product's
(tester note s-crm-9, owner 16 ก.ย. 2569). The model is stubbed as DEV's model
reads the sentences (ask-model.py)."""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "warranty.update"]
CUSTOMER = dict(oa="customer", primary_role="customer")
PRODUCTS = [{"id": "p-air", "product_id": "AIR12", "product_name": "แอร์", "unit_price": 12000, "warranty_months": 24},
            {"id": "p-fan", "product_id": "FAN01", "product_name": "พัดลม", "unit_price": 1500}]


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _shop() -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS])
    client._products = [dict(p) for p in PRODUCTS]
    return client


class TestRegisteringWithoutAPurchaseDate:
    async def test_no_date_means_no_end_date_and_says_how_to_add_it(self):
        client = _shop()
        reply, calls = await _say(client, "ลงทะเบียนสินค้า SN12345 แอร์ ให้ สมชาย", {"action": "create", "entity": "warranty", "fields": {"serial_number": "SN12345", "product_name": "แอร์", "target_name": "สมชาย"}, "missing": []})
        sent = next(c for c in calls if c[0] == "register_warranty")[2]
        assert sent.get("warranty_start") is None and sent.get("warranty_months") is None, sent
        assert "ยังไม่ระบุวันที่ซื้อ" in reply.text and "วันที่ซื้อ SN12345" in reply.text and "คุ้มครองถึง" not in reply.text, reply.text

    async def test_the_date_and_the_period_said_at_once_are_sent(self):
        client = _shop()
        reply, calls = await _say(client, "ลงทะเบียนสินค้า SN12345 แอร์ ให้ สมชาย ซื้อวันที่ 1 ก.ย. 2569 ประกัน 2 ปี", {"action": "create", "entity": "warranty", "fields": {"serial_number": "SN12345", "product_name": "แอร์", "target_name": "สมชาย", "warranty_end": "2028-09-01"}, "missing": []})
        sent = next(c for c in calls if c[0] == "register_warranty")[2]
        assert sent.get("warranty_start") == "2026-09-01" and sent.get("warranty_months") == 24, sent
        assert "คุ้มครองถึง" in reply.text and "2571" in reply.text, reply.text

    async def test_the_purchase_date_given_later_sets_the_end(self):
        client = _shop()
        await _say(client, "ลงทะเบียนสินค้า SN12345 แอร์ ให้ สมชาย", {"action": "create", "entity": "warranty", "fields": {"serial_number": "SN12345", "product_name": "แอร์", "target_name": "สมชาย"}, "missing": []})
        reply, calls = await _say(client, "วันที่ซื้อ SN12345 1 ก.ย. 2569", {"action": "update", "entity": "warranty", "fields": {"serial_number": "SN12345", "purchase_date": "2026-09-01"}, "missing": []})
        updated = next((c for c in calls if c[0] == "update_warranty"), None)
        assert updated is not None and updated[3]["warranty_start"] == "2026-09-01", (reply.text, calls)
        assert "บันทึกวันที่ซื้อ" in reply.text and "คุ้มครองถึง" in reply.text, reply.text

    async def test_without_the_update_key_the_date_is_refused(self):
        client = FakeDataClient(permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS])
        client._products = [dict(p) for p in PRODUCTS]
        reply, calls = await _say(client, "วันที่ซื้อ SN12345 1 ก.ย. 2569", {"action": "update", "entity": "warranty", "fields": {"serial_number": "SN12345", "purchase_date": "2026-09-01"}, "missing": []})
        assert not [c for c in calls if c[0] == "update_warranty"], reply.text


class TestTheProductCarriesItsOwnPeriod:
    async def test_the_period_is_saved_on_the_product(self):
        client = _shop()
        reply, calls = await _say(client, "สินค้า FAN01 รับประกัน 2 ปี", {"action": "update", "entity": "product", "fields": {"product_id": "FAN01", "warranty_period": "2 ปี"}, "missing": []})
        saved = next((c for c in calls if c[0] == "upsert_product"), None)
        assert saved is not None and saved[3].get("warranty_months") == 24, (reply.text, calls)
        assert "รับประกัน 24 เดือน" in reply.text, reply.text

    async def test_a_unit_of_that_product_gets_its_period(self):
        client = _shop()
        reply, calls = await _say(client, "ลงทะเบียนสินค้า SN777 แอร์ ให้ สมชาย ซื้อวันที่ 1 ก.ย. 2569", {"action": "create", "entity": "warranty", "fields": {"serial_number": "SN777", "product_name": "แอร์", "target_name": "สมชาย"}, "missing": []})
        sent = next(c for c in calls if c[0] == "register_warranty")[2]
        assert sent.get("product_id") == "p-air" and sent.get("warranty_months") is None, sent
        # the fake mirrors the Data Tier: the product's 24 months apply
        assert "2571" in reply.text, reply.text


class TestTheCustomerClaimsWithTheDate:
    async def test_the_customers_date_fills_in_what_the_shop_left_out(self):
        client = FakeDataClient(permission_keys=["warranty.read", "warranty.create", "ticket.read", "ticket.create"])
        client._warranties = [{"id": "w-1", "warranty_number": "W-2026-0001", "serial_number": "SN12345", "product_name": "แอร์", "status": "active", "warranty_start": None, "warranty_end": None, "customer_chann_uid": None}]
        reply, calls = await _say(client, "ลงทะเบียน SN12345 ซื้อเมื่อ 1 ก.ย. 2569", {"action": "create", "entity": "warranty", "fields": {"serial_number": "SN12345", "purchase_date": "2026-09-01"}, "missing": ["product_name", "target_name"]}, ctx=_ctx(**CUSTOMER))
        claimed = next((c for c in calls if c[0] == "claim_warranty"), None)
        assert claimed is not None and claimed[2].get("warranty_start") == "2026-09-01", (reply.text, calls)
        assert "2570" in reply.text, reply.text

    async def test_claiming_a_unit_without_a_date_starts_the_cover_today(self):
        # Round 19g left the start empty and said so. Owner, 16 ก.ย. 2569:
        # "ควรเริ่มวันรับประกันตั้งแต่ตอนที่ลงทะเบียนเลยถ้าไม่มีวันที่" — a
        # register full of units with no start cannot answer the only
        # question anyone asks it (round 19n).
        from chann_app.services.thai_datetime import local_today

        client = FakeDataClient(permission_keys=["warranty.read", "warranty.create", "ticket.read", "ticket.create"])
        client._warranties = [{"id": "w-1", "warranty_number": "W-2026-0001", "serial_number": "SN12345", "product_name": "แอร์", "status": "active", "warranty_start": None, "warranty_end": None, "customer_chann_uid": None}]
        reply, _ = await _say(client, "ลงทะเบียนสินค้า SN12345", {"action": "create", "entity": "warranty", "fields": {"serial_number": "SN12345"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert client._warranties[0]["warranty_start"] == local_today().isoformat()
        assert "เริ่มนับประกันตั้งแต่วันนี้" in reply.text, reply.text
