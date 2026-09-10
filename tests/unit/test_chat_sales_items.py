"""Sales OA line items, the way the owner typed them (live test, 8 Sep 2026).

Every line of the transcript, with the reply it should have had:

  1. "ลูกค้าสนใจอยากได้พัดลม 1 ตัว" right after adding the customer offers a
     deal with that line, and "ใช่" opens it.
  2. "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ" puts a NEW line on the
     deal — never "ไม่พบสินค้า"; without a price the catalogue is checked,
     then the price asked with the item remembered.
  3. "เป็นสินค้ารายการใหม่" / "ใช่" / "1500" finish the remembered add.
  4. "เพิ่มพัดลมอีก 3 ตัว" / "เพิ่มอีก 1 ตัว" ADD to the quantity; "แก้พัดลม
     เป็น 3 ตัว" sets it; "ลดพัดลม 1 ตัว" takes off; zero deletes the line.
  5. A flow abandoned without a confirmation says so, and the half-made
     customer is offered when their name comes up for the deal.
  6. "ลบสินค้าพัดลมออก" deletes "พัดลม", not "พัดลมออก" — one normaliser
     for the words around a product's name, sizes kept inside it.
  7. "ขอข้อมูลดีลล่าสุด" is the deal in play, never a deal called "ล่าสุด".
  8. Every line reply carries the deal total; an empty deal shows 0.00.
  9. The product card shows the product's name (the payload key is
     product_name).
 10. "มีสินค้าอะไรบ้างที่เป็น พัดลม" searches the catalogue; the model's
     "view products" lands there too.
 11. A reply-quoted message is parsed on its own text only.
 12. English equivalents.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message, handle_reply  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402

SALES_KEYS = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
ME = "CHN-S-000001"


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "openrouter_model", "m")


def _suggest():
    return httpx.AsyncClient(transport=_ai(json.dumps(
        {"action": "suggest", "entity": None, "fields": {}, "missing": []}, ensure_ascii=False,
    )))


def _crafted(body: dict):
    return httpx.AsyncClient(transport=_ai(json.dumps(body, ensure_ascii=False)))


async def say(client, message, *, ai=None, language="th"):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(oa="sales"), language=language, ai_client=ai or _suggest(),
    )


def _sales(products=None):
    c = FakeDataClient(permission_keys=list(SALES_KEYS), role="sales")
    c._members = [{"id": "member-1", "chann_uid": ME, "role": "member", "status": "active"}]
    c._products = products if products is not None else [
        {"id": "p1", "product_id": "FAN001", "product_name": "พัดลม", "unit_price": "1500.00"},
    ]
    return c


def _writes(client, name):
    return [r for r in client.recorded if r[0] == name]


def _lines(client, deal_index=0):
    return client._deals[deal_index]["products"]


async def _customer_and_deal(client, *, name=("จรสิงค์", "กิ่งปัญญา"), lines=()):
    """A customer, a deal in context, and the given (name, qty, price) lines."""
    cust = await client.create_customer("L1", {"first_name": name[0], "last_name": name[1], "phone": "0576788866"})
    await client.set_last_customer_ref(ME, "sales", customer_id=cust["id"], name=" ".join(name))
    deal = await client.create_deal("L1", {"contact_id": cust["id"]})
    for product_name, qty, price in lines:
        await client.add_deal_product("L1", deal["id"], {"product_name": product_name, "qty": qty, "quoted_unit_price": price})
    await client.set_last_entity_ref(ME, "sales", entity_type="deal", entity_id=deal["id"], code=deal["deal_id"])
    return cust, deal


# -------------------------------------------------------------- 1. the customer wants something

class TestCustomerInterestOpensADeal:
    async def test_interest_right_after_the_customer_offers_a_deal_with_the_item(self):
        client = _sales()
        reply = await say(client, "ลูกค้าใหม่ จรสิงค์ กิ่งปัญญา 0576788866", ai=_crafted(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "จรสิงค์", "last_name": "กิ่งปัญญา", "phone": "0576788866"}, "missing": []}))
        assert "เพิ่มลูกค้า จรสิงค์ กิ่งปัญญา เรียบร้อยแล้ว" in reply.text
        reply = await say(client, "ลูกค้าสนใจอยากได้พัดลม 1 ตัว")
        assert reply.text == "ต้องการสร้างดีลสำหรับ จรสิงค์ กิ่งปัญญา และเพิ่มพัดลม 1 ตัวใช่ไหมครับ?"
        assert [send for _label, send in reply.quick_replies] == ["ใช่", "ไม่ใช่"]
        assert not _writes(client, "create_deal") and client._pending["entity"] == "deal_item_confirm"

        reply = await say(client, "ใช่")
        assert _writes(client, "create_deal") and _writes(client, "add_deal_product")
        assert "สร้างดีล D-2026-0001 สำหรับ จรสิงค์ กิ่งปัญญา เรียบร้อยแล้ว" in reply.text
        assert "เพิ่ม พัดลม × 1 ราคา 1,500.00 เข้าดีล D-2026-0001 แล้ว" in reply.text
        assert "ยอดรวมดีล 1,500.00" in reply.text
        assert client._pending is None

    @pytest.mark.parametrize("message", ["อยากได้พัดลม 2 ตัว", "ต้องการพัดลม 1 ตัว", "ขอพัดลม 3 ตัว", "ลูกค้าสนใจพัดลม", "เขาอยากได้พัดลม 1 ตัว"])
    async def test_the_variants_all_offer(self, message):
        client = _sales()
        cust = await client.create_customer("L1", {"first_name": "จรสิงค์", "last_name": "กิ่งปัญญา", "phone": "0576788866"})
        await client.set_last_customer_ref(ME, "sales", customer_id=cust["id"], name="จรสิงค์ กิ่งปัญญา")
        reply = await say(client, message)
        assert reply.text.startswith("ต้องการสร้างดีลสำหรับ จรสิงค์ กิ่งปัญญา") and "พัดลม" in reply.text

    async def test_no_declines_and_nothing_is_created(self):
        client = _sales()
        cust = await client.create_customer("L1", {"first_name": "จรสิงค์", "last_name": "กิ่งปัญญา", "phone": "0576788866"})
        await client.set_last_customer_ref(ME, "sales", customer_id=cust["id"], name="จรสิงค์ กิ่งปัญญา")
        await say(client, "ลูกค้าสนใจอยากได้พัดลม 1 ตัว")
        reply = await say(client, "ไม่ใช่")
        assert "ยังไม่ได้สร้างดีล" in reply.text and not _writes(client, "create_deal") and client._pending is None

    async def test_without_a_customer_in_context_the_model_still_gets_it(self):
        client = _sales()
        reply = await say(client, "ลูกค้าสนใจอยากได้พัดลม 1 ตัว")
        assert "ต้องการสร้างดีล" not in reply.text and client._pending is None

    async def test_a_remark_that_is_not_an_order_is_not_an_offer(self):
        """"ลูกค้าสนใจเรื่องการซื้อบ้าน" is a note (existing behaviour), not a fan."""
        client = _sales()
        cust = await client.create_customer("L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        await client.set_last_customer_ref(ME, "sales", customer_id=cust["id"], name="สมชาย ใจดี")
        reply = await say(client, "ลูกค้าสนใจเรื่องการซื้อบ้าน")
        assert "ต้องการสร้างดีล" not in reply.text

    async def test_a_customer_with_a_deal_open_gets_the_item_on_that_deal(self):
        client = _sales()
        client._raises = None
        cust, deal = await _customer_and_deal(client)
        await say(client, "ลูกค้าสนใจอยากได้พัดลม 1 ตัว")
        from chann_app.data_client import DataTierError
        client._raises = DataTierError(409, "duplicate", {"error": "duplicate", "existing_code": deal["deal_id"]})
        reply = await say(client, "ใช่")
        assert "มีดีล D-2026-0001 เปิดอยู่แล้ว" in reply.text
        assert "เพิ่ม พัดลม × 1 ราคา 1,500.00 เข้าดีล D-2026-0001 แล้ว" in reply.text
        assert len(_lines(client)) == 1

    async def test_english(self):
        client = _sales(products=[{"id": "p1", "product_id": "FAN001", "product_name": "fan", "unit_price": "1500.00"}])
        cust = await client.create_customer("L1", {"first_name": "John", "last_name": "Smith", "phone": "0812345678"})
        await client.set_last_customer_ref(ME, "sales", customer_id=cust["id"], name="John Smith")
        reply = await say(client, "customer wants 2 fans", language="en")
        assert reply.text == "Create a deal for John Smith and add 2 fans?"


# -------------------------------------------------------------- 2/3/4. adding, more of, fewer of, none of

class TestAddingANewLine:
    async def test_add_with_price_and_count_lands_as_a_new_line(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        reply = await say(client, "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ")
        assert "ไม่พบ" not in reply.text
        assert reply.text == "เพิ่ม ทีวี 40 นิ้ว × 2 ราคา 4,000.00 เข้าดีล D-2026-0001 แล้ว\nรวม 8,000.00 · ยอดรวมดีล 10,000.00"
        line = _lines(client)[-1]
        assert (line["product_name"], line["qty"], line["quoted_unit_price"]) == ("ทีวี 40 นิ้ว", 2, "4000")

    async def test_add_with_a_deal_code(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        client._last_entity_ref = None
        reply = await say(client, "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ใน D-2026-0001")
        assert reply.text.startswith("เพิ่ม ทีวี 40 นิ้ว × 1 ราคา 4,000.00 เข้าดีล D-2026-0001 แล้ว")
        assert "ยอดรวมดีล 6,000.00" in reply.text

    async def test_the_model_reading_it_as_a_price_update_lands_on_the_same_add(self):
        """The AI path converges: an "update" of a line that is not there,
        with a price, is the same new line."""
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        client._products = []
        reply = await say(client, "เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ", ai=_crafted(
            {"action": "update", "entity": "line_item",
             "fields": {"target_name": "ทีวี 40 นิ้ว", "quoted_unit_price": "4000", "qty": 2}, "missing": []}))
        assert reply.text.startswith("เพิ่ม ทีวี 40 นิ้ว × 2 ราคา 4,000.00 เข้าดีล D-2026-0001 แล้ว")

    async def test_unknown_item_without_a_price_is_held_and_the_price_finishes_it(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00")])
        reply = await say(client, "เพิ่มพัดลม 18 นิ้ว 2 ตัว")
        assert reply.text == 'ยังไม่มี พัดลม 18 นิ้ว ในดีล D-2026-0001 ต้องการเพิ่มเป็นรายการใหม่ใช่ไหมครับ? กรุณาระบุราคาต่อชิ้น เช่น "1500"'
        assert client._pending["entity"] == "line_item_add" and client._pending["fields"]["name"] == "พัดลม 18 นิ้ว"
        assert not [r for r in _writes(client, "add_deal_product") if r[3]["product_name"] == "พัดลม 18 นิ้ว"]

        reply = await say(client, "เป็นสินค้ารายการใหม่")
        assert reply.text == 'รับทราบครับ จะเพิ่ม พัดลม 18 นิ้ว × 2 เป็นสินค้ารายการใหม่ในดีล D-2026-0001 กรุณาระบุราคาต่อชิ้น เช่น "1500"'
        assert client._pending["entity"] == "line_item_add"

        reply = await say(client, "1500")
        assert reply.text == "รับทราบครับ เพิ่มเป็นสินค้ารายการใหม่: พัดลม 18 นิ้ว × 2 ราคา 1,500.00 ในดีล D-2026-0001 แล้ว รวม 3,000.00 · ยอดรวมดีล 4,500.00"
        assert client._pending is None
        assert [l["product_name"] for l in _lines(client)] == ["พัดลม", "พัดลม 18 นิ้ว"]

    @pytest.mark.parametrize("answer", ["ราคา 1500", "1,500 บาท", "1500 บาท", "@1500"])
    async def test_the_price_answer_in_its_usual_shapes(self, answer):
        client = _sales()
        await _customer_and_deal(client)
        await say(client, "เพิ่มพัดลม 18 นิ้ว 2 ตัว")
        reply = await say(client, answer)
        assert "พัดลม 18 นิ้ว × 2 ราคา 1,500.00" in reply.text and client._pending is None

    async def test_a_price_edit_of_a_missing_line_offers_it_as_new_and_yes_adds_it(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        reply = await say(client, "ปรับราคาทีวี 40 นิ้ว เป็น 4000")
        assert reply.text == 'ไม่พบสินค้า "ทีวี 40 นิ้ว" ในดีล D-2026-0001 ต้องการเพิ่มเป็นรายการใหม่ (1 × 4,000.00) ใช่ไหมครับ?'
        assert [send for _l, send in reply.quick_replies] == ["ใช่", "ไม่ใช่"]
        reply = await say(client, "ใช่")
        assert reply.text == "รับทราบครับ เพิ่มเป็นสินค้ารายการใหม่: ทีวี 40 นิ้ว × 1 ราคา 4,000.00 ในดีล D-2026-0001 แล้ว รวม 4,000.00 · ยอดรวมดีล 6,000.00"

    async def test_no_drops_the_held_line(self):
        client = _sales()
        await _customer_and_deal(client)
        await say(client, "เพิ่มพัดลม 18 นิ้ว 2 ตัว")
        reply = await say(client, "ไม่ใช่")
        assert reply.text == "ยังไม่ได้เพิ่ม พัดลม 18 นิ้ว ครับ" and client._pending is None and not _lines(client)

    async def test_another_command_drops_the_question_and_is_answered(self):
        client = _sales()
        await _customer_and_deal(client)
        await say(client, "เพิ่มพัดลม 18 นิ้ว 2 ตัว")
        reply = await say(client, "รายการดีล")
        assert "D-2026-0001" in reply.text and client._pending is None

    async def test_the_catalogue_price_is_used_when_the_item_is_known(self):
        client = _sales()
        await _customer_and_deal(client)
        reply = await say(client, "เพิ่มพัดลม 2 ตัว")
        assert reply.text == "เพิ่ม พัดลม × 2 ราคา 1,500.00 เข้าดีล D-2026-0001 แล้ว\nรวม 3,000.00 · ยอดรวมดีล 3,000.00"

    async def test_with_no_deal_in_play_the_catalogue_reading_is_untouched(self):
        """"เพิ่มสินค้า พัดลม FAN001 ราคา 1500" with no deal is catalogue creation (existing behaviour)."""
        client = _sales()
        reply = await say(client, "เพิ่มสินค้า พัดลม FAN001 ราคา 1500", ai=_crafted(
            {"action": "create", "entity": "product",
             "fields": {"product_id": "FAN001", "product_name": "พัดลม", "unit_price": "1500"}, "missing": []}))
        assert _writes(client, "upsert_product") and not _writes(client, "add_deal_product")


class TestMoreOfALine:
    async def test_more_adds_to_the_quantity_and_states_delta_and_totals(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        adds_before = len(_writes(client, "add_deal_product"))
        reply = await say(client, "เพิ่มพัดลมอีก 3 ตัว")
        assert reply.text == "เพิ่มพัดลมอีก 3 ตัวแล้ว ตอนนี้พัดลมรวมเป็น 4 × 2,000.00 = 8,000.00 ในดีล D-2026-0001 · ยอดรวมดีล 8,000.00"
        assert _lines(client)[0]["qty"] == 4 and len(_writes(client, "add_deal_product")) == adds_before

    async def test_more_without_a_name_is_the_line_last_touched(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00"), ("ทีวี 40 นิ้ว", 2, "4000.00")])
        await say(client, "เพิ่มพัดลมอีก 3 ตัว")
        reply = await say(client, "เพิ่มอีก 1 ตัว")
        assert reply.text == "เพิ่มพัดลมอีก 1 ตัวแล้ว ตอนนี้พัดลมรวมเป็น 5 × 2,000.00 = 10,000.00 ในดีล D-2026-0001 · ยอดรวมดีล 18,000.00"

    async def test_more_without_a_name_on_a_one_line_deal(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        for message, qty in (("เพิ่มอีก 1 ตัว", 2), ("อีก 2", 4), ("เอาเพิ่ม 1", 5)):
            reply = await say(client, message)
            assert f"รวมเป็น {qty} × 2,000.00" in reply.text, (message, reply.text)

    async def test_more_without_a_name_on_a_deal_with_several_lines_and_no_memory_asks(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00"), ("ทีวี 40 นิ้ว", 2, "4000.00")])
        reply = await say(client, "เพิ่มอีก 1 ตัว")
        assert "หมายถึงรายการไหน" in reply.text and "· พัดลม" in reply.text and "· ทีวี 40 นิ้ว" in reply.text

    async def test_more_of_a_line_that_is_not_there_is_a_new_line(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00")])
        reply = await say(client, "ใส่สินค้า พัดลม 18 นิ้ว อีก 3 ตัว")
        assert reply.text.startswith("ยังไม่มี พัดลม 18 นิ้ว ในดีล D-2026-0001 ต้องการเพิ่มเป็นรายการใหม่ใช่ไหมครับ?")
        assert client._pending["fields"]["qty"] == 3

    async def test_adding_the_same_catalogue_product_again_is_more_of_it(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00")])
        reply = await say(client, "เพิ่มสินค้า พัดลม 2 ตัว")
        assert "เพิ่มพัดลมอีก 2 ตัวแล้ว ตอนนี้พัดลมรวมเป็น 3 × 1,500.00" in reply.text and len(_lines(client)) == 1

    async def test_the_model_reading_more_as_set_still_adds(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        reply = await say(client, "ขอเพิ่มพัดลมอีกสัก 3 ตัวนะ", ai=_crafted(
            {"action": "update", "entity": "line_item", "fields": {"target_name": "พัดลม", "qty": 3}, "missing": []}))
        assert "รวมเป็น 4 × 2,000.00" in reply.text


class TestSettingAndReducingALine:
    async def test_explicit_set_stays_set(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 5, "2000.00")])
        for message in ("แก้พัดลมเป็น 3 ตัว", "พัดลม 3 ตัว", "เปลี่ยนจำนวนเป็น 3"):
            _lines(client)[0]["qty"] = 5
            reply = await say(client, message)
            assert reply.text == "แก้ พัดลม เป็น 3 × 2,000.00 = 6,000.00 ในดีล D-2026-0001 แล้ว · ยอดรวมดีล 6,000.00", (message, reply.text)

    async def test_a_bare_name_and_count_for_a_line_not_on_the_deal_is_left_to_the_model(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 5, "2000.00")])
        reply = await say(client, "ตู้เย็น 3 ตัว")
        assert "แก้" not in reply.text and _lines(client)[0]["qty"] == 5

    async def test_fewer_takes_off(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 5, "2000.00")])
        reply = await say(client, "ลดพัดลม 1 ตัว")
        assert reply.text == "ลดพัดลมลง 1 ตัวแล้ว ตอนนี้พัดลมเหลือ 4 × 2,000.00 = 8,000.00 ในดีล D-2026-0001 · ยอดรวมดีล 8,000.00"
        reply = await say(client, "เอาออก 2 ตัว")
        assert "เหลือ 2 × 2,000.00" in reply.text

    async def test_fewer_to_zero_deletes_the_line_and_says_so(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00"), ("ทีวี 40 นิ้ว", 2, "4000.00")])
        reply = await say(client, "ลดพัดลม 1 ตัว")
        assert reply.text == "ลดพัดลมลง 1 ตัว เหลือ 0 จึงลบพัดลมออกจากดีล D-2026-0001 แล้ว · ยอดรวมดีล 8,000.00"
        assert [l["product_name"] for l in _lines(client)] == ["ทีวี 40 นิ้ว"]

    async def test_price_change_carries_the_deal_total(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00"), ("ทีวี 40 นิ้ว", 2, "4000.00")])
        reply = await say(client, "ปรับราคาพัดลมเป็น 2000")
        assert reply.text == "แก้ พัดลม เป็น 1 × 2,000.00 = 2,000.00 ในดีล D-2026-0001 แล้ว · ยอดรวมดีล 10,000.00"


class TestAQuantityThatCannotBeCounted:
    """Review v3, B05. The minus sign and the decimal point were dropped
    while the number was being pulled out of the sentence, so the guard
    that refuses a quantity of nothing never saw one: "อีก -1 ตัว" added a
    fan and "อีก 1.5 ตัว" added five. Every case here reads the line back.
    """

    async def test_a_negative_delta_is_refused_and_the_line_is_untouched(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 2, "1000.00")])
        for message in ("เพิ่มพัดลมอีก -1 ตัว", "ลดพัดลม -1 ตัว"):
            reply = await say(client, message)
            assert reply.text == "จำนวนที่เพิ่มหรือลดต้องมากกว่า 0 ครับ รายการเดิมยังอยู่เท่าเดิม", message
            assert _lines(client)[0]["qty"] == 2, message

    async def test_half_a_fan_is_refused_and_the_line_is_untouched(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 2, "1000.00")])
        reply = await say(client, "เพิ่มพัดลมอีก 1.5 ตัว")
        assert reply.text == 'จำนวนต้องเป็นจำนวนเต็มครับ เช่น "2 ตัว" รายการเดิมยังอยู่เท่าเดิม'
        assert _lines(client)[0]["qty"] == 2

    async def test_zero_is_still_refused(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 2, "1000.00")])
        reply = await say(client, "เพิ่มพัดลมอีก 0 ตัว")
        assert reply.text == "จำนวนที่เพิ่มหรือลดต้องมากกว่า 0 ครับ รายการเดิมยังอยู่เท่าเดิม"
        assert _lines(client)[0]["qty"] == 2

    async def test_a_size_in_the_name_is_never_the_count(self):
        """The rule the sign fix must not undo: 18 นิ้ว is what the fan is."""
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม 18 นิ้ว", 2, "1000.00")])
        reply = await say(client, "เพิ่มพัดลม 18 นิ้ว อีก 1 ตัว")
        assert "รวมเป็น 3 ×" in reply.text
        assert _lines(client)[0]["qty"] == 3


class TestQuantitiesSaidInWords:
    """"สาม" counts the same as "3" — the clock has read Thai number words
    next to its own units for a while, and a quantity now does too, but
    only where the word is unmistakably a count (review v3, quantity-004
    and 007)."""

    async def test_a_number_word_before_a_counting_word(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 2, "1000.00")])
        reply = await say(client, "เพิ่มพัดลมอีกสามตัว")
        assert "รวมเป็น 5 ×" in reply.text
        assert _lines(client)[0]["qty"] == 5

    async def test_a_number_word_as_the_new_quantity(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 2, "1000.00")])
        reply = await say(client, "เปลี่ยนจำนวนเป็นสาม")
        assert "เป็น 3 ×" in reply.text
        assert _lines(client)[0]["qty"] == 3

    async def test_a_number_word_inside_a_name_is_left_alone(self):
        """"ชุดสามชิ้น" is what a three-piece set is called, not three of
        something — so the fold only applies at the start of a word, after
        a space, or straight after อีก / เป็น / เหลือ / จำนวน."""
        client = _sales()
        await _customer_and_deal(client, lines=[("ชุดสามชิ้น", 2, "1000.00")])
        reply = await say(client, "เพิ่มชุดสามชิ้นอีก 1 ตัว")
        assert "รวมเป็น 3 ×" in reply.text
        assert _lines(client)[0]["qty"] == 3


class TestDeletingALine:
    @pytest.mark.parametrize("message", [
        "D-2026-0001 ลบสินค้าพัดลมออก", "ลบสินค้าพัดลมออก", "เอาพัดลมออก", "ลบพัดลมออกจากดีล", "ตัดพัดลมออก",
        "เอาพัดลมออกจากดีลนี้หน่อย", "ลบสินค้าพัดลม",
    ])
    async def test_the_delete_phrasings_all_delete_the_fan(self, message):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00"), ("ทีวี 40 นิ้ว", 2, "4000.00")])
        reply = await say(client, message)
        assert reply.text == "ลบ พัดลม ออกจากดีล D-2026-0001 แล้ว · ยอดรวมดีล 8,000.00", (message, reply.text)
        assert [l["product_name"] for l in _lines(client)] == ["ทีวี 40 นิ้ว"]

    async def test_a_lead_removal_is_not_a_line_delete(self):
        """"เอาสมชายออกจากรายชื่อ" is about a lead, with or without a deal in play."""
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "2000.00")])
        reply = await say(client, "เอาสมชายออกจากรายชื่อ")
        assert "ไม่พบสินค้า" not in reply.text and len(_lines(client)) == 1


# -------------------------------------------------------------- 5. an abandoned flow, said out loud

class TestAbandonedFlows:
    async def _half_made_customer(self, client):
        reply = await say(client, "ลูกค้าใหม่ สามเสน เขตคิงคิ", ai=_crafted(
            {"action": "create", "entity": "customer", "fields": {"first_name": "สามเสน", "last_name": "เขตคิงคิ"}, "missing": ["phone"]}))
        assert reply.text == "กรุณาระบุเบอร์โทร" and client._pending["missing"] == ["phone"]

    async def test_a_switch_the_model_made_is_announced(self):
        client = _sales()
        await self._half_made_customer(client)
        reply = await say(client, "สร้างดีล", ai=_crafted({"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]}))
        assert reply.text == "เปลี่ยนจากเพิ่มลูกค้าเป็นสร้างดีลแล้วครับ กรุณาระบุชื่อลูกค้า"
        assert client._pending["entity"] == "deal" and client._pending["fields"]["_abandoned"]["fields"]["first_name"] == "สามเสน"

    async def test_the_half_made_customer_is_offered_for_the_deal_and_yes_makes_both(self):
        client = _sales()
        await self._half_made_customer(client)
        await say(client, "สร้างดีล", ai=_crafted({"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]}))
        reply = await say(client, "สามเสน", ai=_crafted({"action": "create", "entity": "deal", "fields": {"target_name": "สามเสน"}, "missing": []}))
        assert reply.text == "ยังสร้างลูกค้า สามเสน เขตคิงคิ ไม่เสร็จ (ขาดเบอร์โทร) ต้องการสร้างลูกค้าโดยไม่มีเบอร์โทรและเปิดดีลให้เลยไหมครับ?"
        assert [send for _l, send in reply.quick_replies] == ["ใช่", "ไม่ใช่"]
        assert not _writes(client, "create_customer") and not _writes(client, "create_deal")
        reply = await say(client, "ใช่")
        assert "เพิ่มลูกค้า สามเสน เขตคิงคิ เรียบร้อยแล้ว" in reply.text and "สร้างดีล D-2026-0001 สำหรับ สามเสน เขตคิงคิ" in reply.text
        created = _writes(client, "create_customer")[0][2]
        assert created["first_name"] == "สามเสน" and "phone" not in created and client._pending is None

    async def test_no_finishes_the_customer_first_and_the_deal_follows_the_phone(self):
        client = _sales()
        await self._half_made_customer(client)
        await say(client, "สร้างดีล", ai=_crafted({"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]}))
        await say(client, "สามเสน", ai=_crafted({"action": "create", "entity": "deal", "fields": {"target_name": "สามเสน"}, "missing": []}))
        reply = await say(client, "ไม่ใช่")
        assert reply.text == "งั้นพิมพ์เบอร์โทรของ สามเสน เขตคิงคิ มาก่อนครับ จะสร้างลูกค้าให้เสร็จแล้วเปิดดีลให้ต่อเลย"
        assert client._pending["entity"] == "customer" and client._pending["missing"] == ["phone"]
        reply = await say(client, "0812345678", ai=_crafted({"action": "create", "entity": "customer", "fields": {"phone": "0812345678"}, "missing": []}))
        assert reply.text == "เพิ่มลูกค้า สามเสน เขตคิงคิ เรียบร้อยแล้ว\nสร้างดีล D-2026-0001 สำหรับ สามเสน เขตคิงคิ เรียบร้อยแล้ว"
        assert _writes(client, "create_customer")[0][2]["phone"] == "0812345678"

    async def test_the_confirmed_switch_still_carries_the_draft(self):
        """The confirm-before-switch (7 Sep) stays; after "ใช่" the typed deal
        command finds the abandoned customer too."""
        client = _sales()
        await self._half_made_customer(client)
        reply = await say(client, "สร้างดีลให้สามเสน")
        assert client._pending["entity"] == "flow_switch" and "กำลังเพิ่มลูกค้า สามเสน เขตคิงคิ" in reply.text
        reply = await say(client, reply.quick_replies[0][1])
        assert reply.text.startswith("ยังสร้างลูกค้า สามเสน เขตคิงคิ ไม่เสร็จ (ขาดเบอร์โทร)")

    async def test_a_different_name_is_still_not_found(self):
        client = _sales()
        await self._half_made_customer(client)
        await say(client, "สร้างดีล", ai=_crafted({"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]}))
        reply = await say(client, "สมปอง", ai=_crafted({"action": "create", "entity": "deal", "fields": {"target_name": "สมปอง"}, "missing": []}))
        assert reply.text == "ไม่พบลูกค้าชื่อ สมปอง ในบริษัทนี้"

    async def test_english_notice(self):
        client = _sales()
        await say(client, "new customer John Smith", ai=_crafted(
            {"action": "create", "entity": "customer", "fields": {"first_name": "John", "last_name": "Smith"}, "missing": ["phone"]}), language="en")
        reply = await say(client, "i'd like to start a deal", ai=_crafted({"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]}), language="en")
        assert reply.text.startswith("Switched from adding a customer to creating a deal. ")


# -------------------------------------------------------------- 6. the words around a product's name

class TestItemParticles:
    @pytest.mark.parametrize("raw,expected", [
        ("ลบสินค้าพัดลมออก", "พัดลม"),
        ("เอาพัดลมออก", "พัดลม"),
        ("ลบพัดลมออกจากดีล", "พัดลม"),
        ("ตัดพัดลมออก", "พัดลม"),
        ("ใส่สินค้า พัดลม 18 นิ้ว อีก", "พัดลม 18 นิ้ว"),
        ("ทีวี 40 นิ้ว ไปอีก", "ทีวี 40 นิ้ว"),
        ("เพิ่มสินค้า แอร์ 1.5 ตัน เข้าดีล D-2026-0001", "แอร์ 1.5 ตัน"),
        ("เพิ่มพัดลมเข้าไปด้วยครับ", "พัดลม"),
        ("เพิ่ม ทีวี 40 นิ้ว ในดีลนี้ให้หน่อย", "ทีวี 40 นิ้ว"),
        ("แอร์ 12000 BTU ราคา", "แอร์ 12000 BTU"),
        ("รบกวนเพิ่มหลอดไฟ 9 วัตต์ นะคะ", "หลอดไฟ 9 วัตต์"),
        ("remove the fan", "fan"),
        ("add 2 more fans", "2 fans"),
        ("เพิ่มอีก", ""),
        ("สินค้า", ""),
    ])
    def test_the_normaliser_table(self, raw, expected):
        assert chat._strip_item_particles(raw) == expected

    @pytest.mark.parametrize("message,op,name,qty", [
        ("เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ", "add", "ทีวี 40 นิ้ว", 2),
        ("เพิ่มพัดลมอีก 3 ตัว", "add", "พัดลม", 3),
        ("เพิ่มอีก 1 ตัว", "add", None, 1),
        ("อีก 1", "add", None, 1),
        ("เอาเพิ่ม 2", "add", None, 2),
        ("เพิ่มพัดลม 18 นิ้ว 2 ตัว", "add", "พัดลม 18 นิ้ว", 2),
        ("ใส่สินค้า พัดลม 18 นิ้ว อีก 3 ตัว", "add", "พัดลม 18 นิ้ว", 3),
        ("ลดพัดลม 1 ตัว", "decrement", "พัดลม", 1),
        ("เอาออก 1 ตัว", "decrement", None, 1),
        ("เอาพัดลมออก 2 ตัว", "decrement", "พัดลม", 2),
        ("ลบสินค้าพัดลมออก", "delete", "พัดลม", None),
        ("เอาพัดลมออก", "delete", "พัดลม", None),
        ("แก้พัดลมเป็น 3 ตัว", "set_qty", "พัดลม", 3),
        ("พัดลม 3 ตัว", "bare_qty", "พัดลม", 3),
        ("add 2 more fans", "add", "fans", 2),
        ("remove the fan", "delete", "fan", None),
        ("remove 1 fan", "decrement", "fan", 1),
    ])
    def test_the_parser_table(self, message, op, name, qty):
        cmd = chat._parse_line_item_command(message)
        assert cmd is not None, message
        assert (cmd["op"], cmd["name"], cmd["qty"]) == (op, name, qty)

    @pytest.mark.parametrize("message", [
        "เพิ่มลูกค้า สมหญิง 0898765432", "เพิ่มช่างใหม่", "ลบ Lead สมชาย", "ลบบันทึก C-2026-0001", "เอาสมชายออกจากรายชื่อ",
        "ปรับราคาเป็น 2000", "ลดราคาพัดลมเหลือ 1200", "เปลี่ยนจำนวนเป็น 3", "ส่วนลด 10%", "เพิ่มสินค้าใหม่", "สร้างดีล พัดลม 1 ตัว",
        "ลูกค้าสนใจอยากได้พัดลม 1 ตัว", "เพิ่มนัดพรุ่งนี้",
    ])
    def test_what_the_parser_leaves_alone(self, message):
        assert chat._parse_line_item_command(message) is None, message

    def test_sizes_are_never_quantities(self):
        cmd = chat._parse_line_item_command("เพิ่มแอร์ 12000 BTU 2 เครื่อง")
        assert (cmd["name"], cmd["qty"]) == ("แอร์ 12000 BTU", 2)
        cmd = chat._parse_line_item_command("เพิ่มพัดลม 18 นิ้ว")
        assert (cmd["name"], cmd["qty"]) == ("พัดลม 18 นิ้ว", 1)


# -------------------------------------------------------------- 7. the latest deal

class TestLatestDeal:
    @pytest.mark.parametrize("message", ["ขอข้อมูลดีลล่าสุด", "ดีลล่าสุด", "ดีลที่เพิ่งสร้าง", "ดีลเมื่อกี้", "ดีลนี้", "ข้อมูลดีลนี้", "สินค้าในดีล", "ดีลนี้มีอะไรบ้าง", "latest deal", "show the last deal"])
    async def test_the_deal_in_play(self, message):
        client = _sales()
        await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00")])
        reply = await say(client, message)
        assert "ไม่พบดีลรหัส" not in reply.text
        assert reply.text.startswith("D-2026-0001 · ใหม่") and "พัดลม × 1 = 1,500.00" in reply.text, (message, reply.text)

    async def test_with_no_deal_in_play_the_list_comes_back(self):
        client = _sales()
        await _customer_and_deal(client)
        client._last_entity_ref = None
        reply = await say(client, "ขอข้อมูลดีลล่าสุด")
        assert reply.text.startswith("ยังไม่มีดีลที่เพิ่งคุยถึงในแชทนี้ นี่คือรายการดีลล่าสุดครับ") and "D-2026-0001" in reply.text

    async def test_the_latest_customer(self):
        client = _sales()
        cust = await client.create_customer("L1", {"first_name": "จรสิงค์", "last_name": "กิ่งปัญญา", "phone": "0576788866"})
        await client.set_last_customer_ref(ME, "sales", customer_id=cust["id"], name="จรสิงค์ กิ่งปัญญา")
        reply = await say(client, "ลูกค้าล่าสุด")
        assert "จรสิงค์ กิ่งปัญญา" in reply.text and "ไม่พบ" not in reply.text


# -------------------------------------------------------------- 8. totals everywhere

class TestTotals:
    async def test_an_empty_deal_shows_zero(self):
        client = _sales()
        await _customer_and_deal(client)
        reply = await say(client, "ขอข้อมูลดีล D-2026-0001")
        assert reply.text == "D-2026-0001 · ใหม่\nยังไม่มีรายการสินค้าในดีลนี้\nรวม: 0.00 บาท"

    async def test_an_empty_deal_in_english(self):
        client = _sales()
        await _customer_and_deal(client)
        reply = await say(client, "deal detail D-2026-0001", language="en")
        assert "Total: 0.00 THB" in reply.text

    async def test_add_edit_delete_all_state_the_deal_total(self):
        client = _sales()
        await _customer_and_deal(client, lines=[("ทีวี 40 นิ้ว", 2, "4000.00")])
        assert "ยอดรวมดีล 9,500.00" in (await say(client, "เพิ่มสินค้า พัดลม")).text
        assert "ยอดรวมดีล 10,000.00" in (await say(client, "ปรับราคาพัดลมเป็น 2000")).text
        assert "ยอดรวมดีล 8,000.00" in (await say(client, "ลบสินค้าพัดลม")).text


# -------------------------------------------------------------- 9/10. the catalogue

class TestProductCardAndSearch:
    async def test_the_card_shows_product_names(self):
        client = _sales(products=[
            {"id": "p1", "product_id": "FAN001", "product_name": "พัดลม", "unit_price": "1500.00", "sku": None},
            {"id": "p2", "product_id": "TV40", "product_name": "ทีวี 40 นิ้ว", "unit_price": "8000.00", "sku": "TV-40"},
        ])
        reply = await say(client, "รายการสินค้า")
        assert [r["title"] for r in reply.list_card["rows"]] == ["พัดลม", "ทีวี 40 นิ้ว"]
        assert reply.list_card["rows"][0]["subtitle"] == "FAN001 · 1,500.00"
        assert "FAN001 · พัดลม · 1,500.00" in reply.text

    @pytest.mark.parametrize("message", ["มีสินค้าอะไรบ้างที่เป็น พัดลม", "ค้นสินค้า พัดลม", "ค้นหาสินค้า พัดลม", "มีพัดลมอะไรบ้าง", "หาสินค้าชื่อพัดลม", "search products fan"])
    async def test_a_search_by_name(self, message):
        client = _sales(products=[
            {"id": "p1", "product_id": "FAN001", "product_name": "พัดลม 16 นิ้ว", "unit_price": "1500.00"},
            {"id": "p2", "product_id": "FAN002", "product_name": "fan 18 inch", "unit_price": "1800.00"},
            {"id": "p3", "product_id": "TV40", "product_name": "ทีวี 40 นิ้ว", "unit_price": "8000.00"},
        ])
        reply = await say(client, message)
        assert "ในแชทยังทำรายการนี้ไม่ได้" not in reply.text and "ทีวี" not in reply.text
        assert "พัดลม 16 นิ้ว" in reply.text or "fan 18 inch" in reply.text, (message, reply.text)

    async def test_nothing_matching_says_so(self):
        client = _sales()
        reply = await say(client, "ค้นหาสินค้า ตู้เย็น")
        assert reply.text == 'ไม่พบสินค้าที่ตรงกับ "ตู้เย็น" ในรายการสินค้า'

    async def test_the_models_view_products_lands_on_the_list(self):
        client = _sales(products=[
            {"id": "p1", "product_id": "FAN001", "product_name": "พัดลม 16 นิ้ว", "unit_price": "1500.00"},
            {"id": "p3", "product_id": "TV40", "product_name": "ทีวี 40 นิ้ว", "unit_price": "8000.00"},
        ])
        reply = await say(client, "ขอดูว่าพัดลมที่ขายมีตัวไหนบ้าง", ai=_crafted(
            {"action": "read", "entity": "product", "fields": {"product_name": "พัดลม"}, "missing": []}))
        assert "ในแชทยังทำรายการนี้ไม่ได้" not in reply.text
        assert "พัดลม 16 นิ้ว" in reply.text and "ทีวี" not in reply.text

    async def test_the_plain_list_and_other_questions_are_untouched(self):
        client = _sales()
        assert "FAN001 · พัดลม" in (await say(client, "มีสินค้าอะไรบ้าง")).text
        reply = await say(client, "มีดีลอะไรบ้าง")
        assert "พัดลม" not in reply.text


# -------------------------------------------------------------- 11. a reply-quoted message

class TestReplyQuotedMessages:
    async def test_only_the_reply_text_is_parsed(self):
        """LINE sends the quoted message as an id, never as text. The reply's
        own words are the command; the quoted deal is the context."""
        client = _sales()
        cust, deal = await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00")])
        client._last_entity_ref = None
        client._mapping = {"entity_type": "deal", "entity_id": deal["id"]}
        reply = await handle_reply(
            client, message_id="line-msg-1", reply_text="ใส่สินค้า พัดลม 18 นิ้ว อีก 3 ตัว", ctx=_ctx(oa="sales"), ai_client=_suggest(),
        )
        assert reply.text.startswith("ยังไม่มี พัดลม 18 นิ้ว ในดีล D-2026-0001")
        assert client._pending["fields"]["name"] == "พัดลม 18 นิ้ว" and client._pending["fields"]["qty"] == 3
        assert (reply.entity_type, reply.entity_id) == ("deal", deal["id"])

    async def test_a_reply_that_adds_more_of_a_line(self):
        client = _sales()
        cust, deal = await _customer_and_deal(client, lines=[("พัดลม", 1, "1500.00")])
        client._last_entity_ref = None
        client._mapping = {"entity_type": "deal", "entity_id": deal["id"]}
        reply = await handle_reply(
            client, message_id="line-msg-1", reply_text="เพิ่มพัดลมอีก 3 ตัว", ctx=_ctx(oa="sales"), ai_client=_suggest(),
        )
        assert "รวมเป็น 4 × 1,500.00" in reply.text


# -------------------------------------------------------------- 12. English

class TestEnglish:
    async def test_add_more_remove_and_latest(self):
        client = _sales(products=[{"id": "p1", "product_id": "FAN001", "product_name": "fan", "unit_price": "1500.00"}])
        await _customer_and_deal(client, name=("John", "Smith"), lines=[("fan", 1, "1500.00"), ("tv 40 inch", 1, "8000.00")])
        reply = await say(client, "add 2 more fans", language="en")
        assert reply.text == "Added 2 more fan. Now 3 × 1,500.00 = 4,500.00 on D-2026-0001 · deal total 12,500.00"
        reply = await say(client, "remove 1 fan", language="en")
        assert reply.text == "Took 1 fan off. Now 2 × 1,500.00 = 3,000.00 on D-2026-0001 · deal total 11,000.00"
        reply = await say(client, "remove the fan", language="en")
        assert reply.text == "Removed fan from D-2026-0001. · deal total 8,000.00"
        reply = await say(client, "latest deal", language="en")
        assert reply.text.startswith("D-2026-0001 · new") and "tv 40 inch × 1 = 8,000.00" in reply.text

    async def test_add_a_new_line_and_answer_the_price(self):
        client = _sales(products=[])
        await _customer_and_deal(client, name=("John", "Smith"))
        reply = await say(client, "add 2 heaters", language="en")
        assert reply.text == '"heaters" is not on D-2026-0001 yet — add it as a new line? Send the unit price, e.g. "1500".'
        reply = await say(client, "2500", language="en")
        assert reply.text == "Added as a new line: heaters × 2 at 2,500.00 on D-2026-0001. Line total 5,000.00 · deal total 5,000.00"
