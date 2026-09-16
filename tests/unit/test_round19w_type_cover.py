"""Round 19w — a product TYPE's warranty period, told apart from one unit's.

Owner, 16 ก.ย. 2569, on the product list:

    พิม  : พัดลมตั้งระยะเวลารับประกันเป็น 6 เดือน
    ระบบ : ขอหมายเลขเครื่อง (serial)

    "แล้วจะตั้งระยะเวลารับประกันของแต่ละประเภทสินค้ายังไง ที่ไม่ใช่ไปตั้ง
     สินค้าแต่ละชิ้น ควรออกแบบส่วนนี้ให้แยกกันชัดเจน"

Asked before writing anything (ask-model, สinค้าเดียวกัน, 16 ก.ย.), the
deployed model already separates the two by itself:

    "พัดลมตั้งระยะเวลารับประกันเป็น 6 เดือน"
        → update/warranty {product_name: "พัดลม", warranty_months: 6}
    "ประกัน SN12345678 เป็น 24 เดือน"
        → update/warranty {serial_number: "SN12345678", warranty_months: 24}

So the reading needed no new keyword in front of the model — only the
conversion downstream of it. Both landed on the unit handler, which asked
for a serial; that included "สินค้า AC รับประกัน 2 ปี", the sentence round
19u shipped a checklist for, so a type's period was never once settable
by typing.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = ["warranty.update", "warranty.read", "product.manage", "product.read"]
CATALOGUE = [
    {"id": "p1", "product_id": "FAN001", "product_name": "พัดลมตั้งพื้น", "unit_price": "1200.00"},
    {"id": "p2", "product_id": "AC", "product_name": "แอร์ 12000 BTU",
     "unit_price": "18000.00", "warranty_months": 12},
]


def _shop() -> FakeDataClient:
    client = FakeDataClient(permission_keys=KEYS, role="sales")
    client._products = [dict(p) for p in CATALOGUE]
    return client


async def _say(client, message, ai):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role="sales", oa="sales"),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


def _reads_type(name, months):
    return {"action": "update", "entity": "warranty",
            "fields": {"product_name": name, "warranty_months": months}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestTheTypesPeriod:
    async def test_the_owners_sentence_sets_the_type_not_a_unit(self):
        client = _shop()
        reply = await _say(client, "พัดลมตั้งระยะเวลารับประกันเป็น 6 เดือน", _reads_type("พัดลม", 6))
        assert "serial" not in reply.text, reply.text
        saved = [w for w in client.recorded if w[0] == "upsert_product"]
        assert saved, client.recorded
        assert saved[0][3]["warranty_months"] == 6, saved
        assert saved[0][2] == "FAN001", "the name must resolve to the catalogue row"

    async def test_the_sentence_round_19u_promised_works_too(self):
        """"สินค้า AC รับประกัน 2 ปี" was in round 19u's own checklist."""
        client = _shop()
        reply = await _say(client, "สินค้า AC รับประกัน 2 ปี", _reads_type("AC", 24))
        assert [w for w in client.recorded if w[0] == "upsert_product"], reply.text
        assert "24" in reply.text, reply.text

    async def test_the_answer_says_which_of_the_two_it_changed(self):
        client = _shop()
        reply = await _say(client, "พัดลมตั้งประกัน 6 เดือน", _reads_type("พัดลม", 6))
        assert "สินค้า พัดลมตั้งพื้น" in reply.text, reply.text
        assert "หลังจากนี้" in reply.text, "it must say existing units keep theirs"
        assert "ประกัน <S/N>" in reply.text, "and name the sentence for one unit"

    async def test_a_name_no_product_has_is_said_so_not_asked_for_a_serial(self):
        client = _shop()
        reply = await _say(client, "เครื่องซักผ้าตั้งประกัน 6 เดือน", _reads_type("เครื่องซักผ้า", 6))
        assert "ไม่พบสินค้าชื่อ" in reply.text, reply.text
        assert not [w for w in client.recorded if w[0] == "upsert_product"], client.recorded

    async def test_without_product_permission_it_is_not_written(self):
        client = FakeDataClient(permission_keys=["warranty.update", "warranty.read"], role="sales")
        client._products = [dict(p) for p in CATALOGUE]
        reply = await _say(client, "พัดลมตั้งประกัน 6 เดือน", _reads_type("พัดลม", 6))
        assert not [w for w in client.recorded if w[0] == "upsert_product"], client.recorded
        assert "serial" in reply.text, reply.text

    async def test_a_period_with_no_product_still_asks_for_the_serial(self):
        """Nothing to aim at: neither a machine nor a type was named."""
        client = _shop()
        reply = await _say(
            client, "ตั้งประกัน 6 เดือน",
            {"action": "update", "entity": "warranty",
             "fields": {"warranty_months": 6}, "missing": []},
        )
        assert "serial" in reply.text, reply.text


class TestTheProductListSaysHow:
    async def test_the_list_names_both_sentences(self):
        client = _shop()
        reply = await _say(
            client, "รายการสินค้า",
            {"action": "read", "entity": "product", "fields": {}, "missing": []},
        )
        assert "ตั้งประกัน" in reply.text and "<S/N>" in reply.text, reply.text
        assert len(reply.text.split("\n")) <= 15, "a LINE bubble this long is not read"


class TestTheUnitSideSaysItIsOneUnit:
    """The separation has to read from both ends, or the shop learns only
    half of it."""

    def _with_a_unit(self):
        client = _shop()
        client._warranties = [{
            "id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์ 12000 BTU",
            "status": "active", "warranty_start": "2026-01-01", "warranty_end": "2027-01-01",
        }]
        return client

    async def test_setting_one_units_period_says_so_and_names_the_type_sentence(self):
        client = self._with_a_unit()
        reply = await _say(
            client, "ประกัน SN12345678 เป็น 24 เดือน",
            {"action": "update", "entity": "warranty",
             "fields": {"serial_number": "SN12345678", "warranty_months": 24}, "missing": []},
        )
        assert [w for w in client.recorded if w[0] == "update_warranty"], client.recorded
        assert "เฉพาะเครื่องนี้" in reply.text, reply.text
        assert "แอร์ 12000 BTU ตั้งประกัน 24 เดือน" in reply.text, reply.text
        assert not [w for w in client.recorded if w[0] == "upsert_product"], (
            "one unit must never move the whole type"
        )

    async def test_a_purchase_date_alone_does_not_lecture_about_types(self):
        client = self._with_a_unit()
        reply = await _say(
            client, "วันที่ซื้อ SN12345678 1 ก.ย. 2569",
            {"action": "update", "entity": "warranty",
             "fields": {"serial_number": "SN12345678", "purchase_date": "2026-09-01"}, "missing": []},
        )
        assert "เฉพาะเครื่องนี้" not in reply.text, reply.text
