"""Round 19s — the rest of the sale-side notes from V4Comment.html, 16 ก.ย. 2569.

* s-crm-8  "ไม่สามารถแก้ไขราคาสินค้าได้": looking a product up then saying
  "แก้ราคาเป็น 10000" answered about a quote issued earlier.
* s-crm-18 "รูปแบบเบอร์โทรที่บันทึกไม่ถูกเปลี่ยน": 092.345.6781 stored as typed.
* s-job-8  "ในแชทของฝั่งช่างบอกแค่ว่าแนบภาพได้เรื่อยๆ" while the report shows four.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.data_client import DataClient
from chann_app.services import chat
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import KEYS, _reads

pytestmark = pytest.mark.asyncio

SALES_KEYS = sorted(set(KEYS) | {"product.manage", "quote.update", "deal.update", "customer.create", "customer.update"})


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestThePriceOfTheProductJustLookedUp:
    def _shop(self):
        client = FakeDataClient(permission_keys=SALES_KEYS, role="sales")
        client._products = [
            {"id": "p1", "product_id": "RAM32", "product_name": "RAM 32 GB", "unit_price": "8000.00"},
        ]
        return client

    async def test_a_bare_price_edits_the_product_in_view(self):
        client = self._shop()
        ctx = _ctx(oa="sales", primary_role="sales")
        await handle_chat_message(
            client, message="ขอดูข้อมูลสินค้า RAM 32", ctx=ctx,
            ai_client=_reads({"action": "read", "entity": "product",
                              "fields": {"product_name": "RAM 32"}, "missing": []}),
        )
        reply = await handle_chat_message(
            client, message="แก้ราคาเป็น 10000", ctx=ctx,
            # What the model actually returns for a bare price.
            ai_client=_reads({"action": "update", "entity": "line_item",
                              "fields": {"quoted_unit_price": 10000}, "missing": []}),
        )
        assert client._products[0]["unit_price"] in (10000, 10000.0, "10000"), client._products
        assert "RAM 32 GB" in reply.text, reply.text
        assert "ใบเสนอราคา" not in reply.text, reply.text

    async def test_a_named_line_on_a_quote_is_still_a_line_edit(self):
        client = self._shop()
        ctx = _ctx(oa="sales", primary_role="sales")
        reply = await handle_chat_message(
            client, message="แก้ราคา แอร์ ใน Q-2026-0001 เป็น 11000", ctx=ctx,
            ai_client=_reads({"action": "update", "entity": "line_item",
                              "fields": {"target_name": "แอร์", "quoted_unit_price": 11000}, "missing": []}),
        )
        # Whatever the outcome, it must not have touched the catalogue.
        assert client._products[0]["unit_price"] == "8000.00", client._products
        assert reply.text


class TestThePhoneIsStoredOneWay:
    @pytest.mark.parametrize("typed,stored", [
        ("092.345.6781", "0923456781"),
        ("092-345-6781", "0923456781"),
        ("+66 92 345 6781", "0923456781"),
        ("0923456781", "0923456781"),
    ])
    def test_however_it_was_typed(self, typed, stored):
        assert DataClient._with_a_tidy_phone({"phone": typed})["phone"] == stored

    def test_a_payload_without_a_phone_is_untouched(self):
        assert DataClient._with_a_tidy_phone({"first_name": "สมชาย"}) == {"first_name": "สมชาย"}


class TestTheReportSaysHowManyPicturesItUses:
    def test_the_two_limits_are_the_same_number(self):
        from chann_app.services.documents.report_snapshot import REPORT_PHOTO_LIMIT as on_paper

        assert chat.REPORT_PHOTO_LIMIT == on_paper

    def test_the_list_marks_the_ones_that_go_on_the_report(self):
        rows = [
            {"id": f"p{i}", "caption": f"รูป {i}", "photo_type": "evidence",
             "created_at": f"2026-09-16T0{i}:00:00+00:00"}
            for i in range(1, 7)
        ]
        text = chat._photo_list_text(rows, "T-2026-0001", "th")
        lines = [ln for ln in text.split("\n") if ln.startswith(("1.", "4.", "5."))]
        assert lines[0].endswith("อยู่ในรายงาน"), lines
        assert lines[1].endswith("อยู่ในรายงาน"), lines
        assert not lines[2].endswith("อยู่ในรายงาน"), lines
