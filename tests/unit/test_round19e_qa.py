"""Round 19e — the test team's notes of 16 ก.ย. 2569 (exported file), each
pinned with the model's reading stubbed as DEV's model returns it
(ask-model.py). The model is stubbed: these prove the road."""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "quote.read", "quote.update", "ticket.assign", "ticket.close", "service_report.read"]
CUSTOMER = dict(oa="customer", primary_role="customer")
SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}
TICKET = {
    "id": "t5", "ticket_number": "T-2026-0005", "status": "assigned", "accept_status": "accepted",
    "assigned_to_ref": "member-1", "assigned_target_type": "technician", "assigned_to_name": "สมศักดิ์",
    "customer_chann_uid": "CHN-S-000001", "customer_name": "สมชาย ใจดี", "customer_phone": "0812345678",
    "service_address": "99/1", "issue_description": "พัดลมไม่หมุน", "scheduled_date": "2099-01-20", "scheduled_time": "10:00",
}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, [r for r in client.recorded[since:]]


def _shop(tickets=None, **kw) -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS], **kw)
    client._tickets = list(tickets or [])
    return client


def _customer(tickets=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
    client._tickets = list(tickets or [])
    return client


def _with_quote_in_view():
    client = _shop(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 0, "products": []}])
    client._quotes = [{"id": "QUOTE-1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "draft", "generated_document_id": None}]
    client._quote_lines = [
        {"id": "ql-1", "quote_id": "QUOTE-1", "product_name": "พัดลม", "qty": 2, "quoted_unit_price": 1500},
        {"id": "ql-2", "quote_id": "QUOTE-1", "product_name": "Air conditioner", "qty": 1, "quoted_unit_price": 12000},
    ]
    return client


VIEW_QUOTE = {"action": "read", "entity": "quote", "fields": {"code": "Q-2026-0001"}, "missing": []}


class TestTheQuoteDiscountFromTheTestersNote:
    """'ลดราคา 1000 บาท' → which line? → the button dropped the number → 'Air c diti er'."""

    async def test_a_bare_amount_with_the_quote_in_view_is_its_discount(self):
        client = _with_quote_in_view()
        await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", VIEW_QUOTE)
        reply, calls = await _say(client, "ลดราคา 1000 บาท", {"action": "update", "entity": "line_item", "fields": {"quoted_unit_price": 1000}, "missing": []})
        assert [c[0] for c in calls if c[0] == "set_quote_terms"], reply.text
        assert "ส่วนลด 1,000.00" in reply.text and "จะแก้อันไหน" not in reply.text, reply.text

    async def test_naming_the_quote_without_its_code_is_the_discount_too(self):
        client = _with_quote_in_view()
        await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", VIEW_QUOTE)
        reply, calls = await _say(client, "ลดราคาใบเสนอราคา 500 บาท", SUGGEST)
        assert [c for c in calls if c[0] == "set_quote_terms"], reply.text
        assert "ไม่พบสินค้า" not in reply.text, reply.text

    async def test_a_named_line_is_still_a_line_price(self):
        client = _with_quote_in_view()
        await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", VIEW_QUOTE)
        reply, calls = await _say(client, "แก้ราคา Air conditioner เป็น 11000", {"action": "update", "entity": "line_item", "fields": {"target_name": "Air conditioner", "quoted_unit_price": 11000}, "missing": []})
        assert [c for c in calls if c[0] == "update_quote_product"], reply.text
        assert "Air conditioner" in reply.text and "11,000.00" in reply.text and "Air c diti er" not in reply.text, reply.text

    async def test_a_catalogue_reading_of_a_line_in_view_is_the_line(self):
        """The model sometimes reads the reprice as a catalogue change missing a product code."""
        client = _with_quote_in_view()
        await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", VIEW_QUOTE)
        reply, calls = await _say(client, "แก้ราคา Air conditioner เป็น 11000", {"action": "update", "entity": "product", "fields": {"product_name": "Air conditioner", "unit_price": 11000}, "missing": ["product_id"]})
        assert [c for c in calls if c[0] == "update_quote_product"], reply.text
        assert "รหัสสินค้า" not in reply.text, reply.text

    async def test_the_which_line_buttons_carry_the_number(self):
        client = _with_quote_in_view()
        await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", VIEW_QUOTE)
        reply, _ = await _say(client, "แก้ราคาเป็น 1000", {"action": "update", "entity": "line_item", "fields": {"quoted_unit_price": 1000}, "missing": []})
        sends = [send for _label, send in reply.quick_replies]
        assert sends and all(s.endswith("เป็น 1000") for s in sends), reply.quick_replies

    async def test_english_words_inside_a_name_survive(self):
        from chann_app.services.chat import _line_parts_of_clause
        assert _line_parts_of_clause("Air conditioner 1 ตัว")[0] == "Air conditioner"


class TestTheShopClosesAJobFromTheSalesOa:
    """DEV log 16 ก.ย. 10:34: 'ปิดงาน T-2026-0005' became 'ขอเลื่อนนัด' and a date question."""

    def _client(self):
        return _shop(tickets=[dict(TICKET, status="open", assigned_to_ref=None, accept_status=None)])

    async def test_close_read_as_a_ticket_update_with_no_status_still_closes(self):
        client = self._client()
        reply, calls = await _say(client, "ปิดงาน T-2026-0005", {"action": "update", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": []})
        assert [c for c in calls if c[0] == "set_ticket_status" and c[3] == "completed"], reply.text
        assert "ปิดงาน T-2026-0005 แล้ว" in reply.text and "ขอเลื่อนนัด" not in reply.text and "เช็คอิน" not in reply.text, reply.text
        assert not [c for c in calls if c[0] == "create_note" and "ขอเลื่อนนัด" in str(c)], calls

    async def test_the_cause_and_the_fix_are_optional_and_kept(self):
        client = self._client()
        reply, calls = await _say(client, "ปิดงาน T-2026-0005 สาเหตุ ฟิวส์ขาด แก้ไข เปลี่ยนฟิวส์", {"action": "update", "entity": "ticket", "fields": {"code": "T-2026-0005", "status": "closed"}, "missing": []})
        assert "สาเหตุ: ฟิวส์ขาด" in reply.text and "การแก้ไข: เปลี่ยนฟิวส์" in reply.text, reply.text
        note = next(c for c in calls if c[0] == "create_note")
        assert "ปิดงานโดยฝ่ายขาย/CS" in str(note) and "ฟิวส์ขาด" in str(note)

    async def test_the_report_reading_of_the_same_sentence_keeps_both_details(self):
        client = self._client()
        reply, _ = await _say(client, "ปิดงาน T-2026-0005 สาเหตุ ฟิวส์ขาด แก้ไข เปลี่ยนฟิวส์", {"action": "create", "entity": "service_report", "fields": {"code": "T-2026-0005", "found_issue": "ฟิวส์ขาด", "work_done": "เปลี่ยนฟิวส์"}, "missing": []})
        assert "สาเหตุ: ฟิวส์ขาด" in reply.text and "การแก้ไข: เปลี่ยนฟิวส์" in reply.text, reply.text

    async def test_without_ticket_close_the_shop_is_refused(self):
        client = FakeDataClient(permission_keys=[k for k in SHOP_KEYS if k != "ticket.close"], customers=[dict(c) for c in CUSTOMERS])
        client._tickets = [dict(TICKET, status="open")]
        reply, calls = await _say(client, "ปิดงาน T-2026-0005", {"action": "update", "entity": "ticket", "fields": {"code": "T-2026-0005", "status": "closed"}, "missing": []})
        assert not [c for c in calls if c[0] == "set_ticket_status"], reply.text

    async def test_the_technician_oa_still_asks_for_the_report(self):
        client = FakeDataClient(permission_keys=["ticket.update", "ticket.close", "service_report.create"])
        client._tickets = [dict(TICKET, status="in_progress", assigned_to_ref="MEMBER-1")]
        reply, calls = await _say(client, "ปิดงาน T-2026-0005", {"action": "update", "entity": "ticket", "fields": {"code": "T-2026-0005", "status": "closed"}, "missing": []}, ctx=_ctx(primary_role="technician"))
        assert not [c for c in calls if c[0] == "set_ticket_status"], reply.text
        assert "พบปัญหาอะไร" in reply.text, reply.text


class TestTheCustomersRescheduleFromTheTestersNote:
    """'เลื่อนนัดเป็น 9 โมงเช้าแทนได้ไหม' → 'ไม่เข้าใจวันที่'; the shop's 'เลื่อนได้' noted and asked the date; 'ยืนยัน' not understood."""

    async def test_a_time_alone_keeps_the_appointments_day(self):
        client = _customer([dict(TICKET)])
        reply, calls = await _say(client, "เลื่อนนัดเป็น 9 โมงเช้าแทนได้ไหม", {"action": "update", "entity": "ticket", "fields": {"scheduled_time": "09:00"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert "20 ม.ค." in reply.text and "09:00" in reply.text, reply.text
        note = next(c for c in calls if c[0] == "create_note")
        assert "ขอเลื่อนนัดเป็น 20 ม.ค." in str(note) and "09:00" in str(note), note
        assert not [c for c in calls if c[0] == "update_ticket"], "the customer never moves the visit"

    async def test_the_shop_approves_with_one_word(self):
        client = _shop(tickets=[dict(TICKET, customer_chann_uid="CHN-S-000002")])
        client._notes = [{"id": "n1", "entity_type": "service_ticket", "entity_id": "t5", "body": "⚠️ คำขอจากลูกค้า: ลูกค้าขอเลื่อนนัดเป็น 20 ม.ค. 2642 09:00 (ยังไม่ได้เลื่อน รอร้านยืนยัน)"}]
        reply, calls = await _say(client, "T-2026-0005 เลื่อนได้", {"action": "update", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": []})
        moved = next((c for c in calls if c[0] == "update_ticket"), None)
        assert moved is not None and moved[3].get("scheduled_date") == "2099-01-20" and str(moved[3].get("scheduled_time")).startswith("09:00"), (reply.text, calls)
        assert "09:00" in reply.text and "เลื่อนไปวันไหน" not in reply.text, reply.text

    async def test_the_shop_can_still_name_its_own_time(self):
        client = _shop(tickets=[dict(TICKET, customer_chann_uid="CHN-S-000002")])
        client._notes = [{"id": "n1", "entity_type": "service_ticket", "entity_id": "t5", "body": "⚠️ คำขอจากลูกค้า: ลูกค้าขอเลื่อนนัดเป็น 20 ม.ค. 2642 09:00 (ยังไม่ได้เลื่อน รอร้านยืนยัน)"}]
        reply, calls = await _say(client, "เลื่อนนัด T-2026-0005 เป็น 21 ม.ค. 2642 13:00", {"action": "update", "entity": "ticket", "fields": {"code": "T-2026-0005", "scheduled_date": "2099-01-21", "scheduled_time": "13:00"}, "missing": []})
        moved = next((c for c in calls if c[0] == "update_ticket"), None)
        assert moved is not None and moved[3].get("scheduled_date") == "2099-01-21", (reply.text, calls)

    async def test_a_bare_confirmation_with_nothing_open_names_the_appointment(self):
        client = _customer([dict(TICKET)])
        reply, _ = await _say(client, "ยืนยัน", SUGGEST, ctx=_ctx(**CUSTOMER))
        assert "ไม่มีรายการที่รอยืนยัน" in reply.text and "T-2026-0005" in reply.text, reply.text


class TestAChatLineThatLooksLikeThanks:
    """'ได้ครับ' inside the open conversation got 'ยินดีครับ 🙂' from the bot, twice."""

    async def test_small_talk_inside_an_open_conversation_reaches_the_shop(self):
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
        await _say(client, "คุยกับร้าน อยากรู้รุ่นพัดลมทั้งหมด", SUGGEST, ctx=_ctx(**CUSTOMER))
        for line in ("ได้ครับ", "ขอบคุณครับ"):
            reply, calls = await _say(client, line, SUGGEST, ctx=_ctx(**CUSTOMER))
            assert [c for c in calls if c[0] == "add_chat_message"], (line, reply.text)
            assert "ยินดีครับ" not in reply.text, (line, reply.text)

    async def test_small_talk_with_no_conversation_is_still_small_talk(self):
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
        reply, calls = await _say(client, "ขอบคุณครับ", SUGGEST, ctx=_ctx(**CUSTOMER))
        assert "ยินดีครับ" in reply.text and not [c for c in calls if c[0] == "add_chat_message"]
