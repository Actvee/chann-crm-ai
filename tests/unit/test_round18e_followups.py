"""Round 18e — the deferred audit findings and the tester's registration
report (15 ก.ย. 2569), pinned with the model's reading stubbed as DEV's
model returns it (ask-model.py). The model is stubbed: these prove the road.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import DEAL_LOST_ASK_REASON, SURVEY_ALREADY_ANSWERED, handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = [
    "customer.create", "customer.read", "customer.update", "customer.archive", "deal.create", "deal.read",
    "deal.update", "product.manage", "followup.create", "followup.read", "followup.update", "ticket.create",
    "ticket.read", "ticket.update", "team.manage", "warranty.create", "warranty.read",
]
TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read"]
CUSTOMERS = [
    {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"},
    {"id": "c3", "customer_id": "C-2026-0003", "first_name": "สมหญิง", "last_name": "รักดี", "phone": "0811111111", "stage": "lead"},
]
TECH = dict(oa="technician", primary_role="technician")
CUSTOMER = dict(oa="customer", primary_role="customer")


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _reads(reading: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_ai(json.dumps(reading)))


SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    writes = {r[0] for r in client.recorded[since:] if r[0].startswith(("create_", "update_", "transition_", "add_deal", "archive", "register", "set_ticket"))}
    return reply, writes


def _sales(**kw) -> FakeDataClient:
    return FakeDataClient(permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS], **kw)


def _quoting_sales(**kw) -> FakeDataClient:
    return FakeDataClient(permission_keys=[*KEYS, "quote.read", "quote.update"], customers=[dict(c) for c in CUSTOMERS], **kw)


class TestRegisteringAUnitAsksForTheSerialInWords:
    async def test_the_question_names_the_serial(self):
        """Tester: 'ลงทะเบียน พัดลม ให้ เวหา' was answered 'กรุณาระบุรายละเอียดที่เหลือ'."""
        client = _sales()
        reply, writes = await _say(client, "ลงทะเบียน พัดลม ให้ สมหญิง", {"action": "create", "entity": "warranty", "fields": {"product_name": "พัดลม", "target_name": "สมหญิง"}, "missing": ["serial_number"]})
        assert "หมายเลขเครื่อง" in reply.text and "รายละเอียดที่เหลือ" not in reply.text, reply.text
        assert not writes

    async def test_the_serial_typed_next_registers_the_unit_for_that_customer(self):
        """…and the S/N typed next was looked up ('ไม่พบหมายเลข … ในระบบ') instead of registered."""
        client = _sales()
        client._products = [{"id": "p1", "product_id": "FAN16", "product_name": "พัดลม", "unit_price": 1500}]
        await _say(client, "ลงทะเบียน พัดลม ให้ สมหญิง", {"action": "create", "entity": "warranty", "fields": {"product_name": "พัดลม", "target_name": "สมหญิง"}, "missing": ["serial_number"]})
        reply, writes = await _say(client, "SN12345678", SUGGEST)
        assert "register_warranty" in writes, reply.text
        assert "SN12345678" in reply.text and "สมหญิง รักดี" in reply.text and "ไม่พบหมายเลข" not in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_a_bare_serial_with_no_form_is_still_an_enquiry(self):
        client = _sales()
        reply, writes = await _say(client, "SN12345678", {"action": "read", "entity": "warranty", "fields": {"serial_number": "SN12345678"}, "missing": []})
        assert "ไม่พบหมายเลข SN12345678" in reply.text and not writes


class TestALostDealAsksWhy:
    async def test_the_reason_typed_next_is_recorded(self):
        """Audit [7]: 'ดีลนี้แพ้' then 'ราคาแพงกว่าคู่แข่ง' — the reason was 'not sure'."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 250000}])
        first, writes = await _say(client, "ดีลนี้แพ้", {"action": "update", "entity": "deal", "fields": {"deal_code": "D-2026-0001", "status": "lost"}, "missing": []})
        assert "transition_deal_stage" in writes and DEAL_LOST_ASK_REASON["th"] in first.text, first.text
        reply, writes = await _say(client, "ราคาแพงกว่าคู่แข่ง", SUGGEST)
        assert "update_deal" in writes and "ราคาแพงกว่าคู่แข่ง" in reply.text, reply.text
        listed, _w = await _say(client, "ดีลที่แพ้", {"action": "read", "entity": "deal", "fields": {"status": "lost"}, "missing": []})
        assert "ราคาแพงกว่าคู่แข่ง" in listed.text, listed.text

    async def test_a_command_instead_of_a_reason_is_not_a_reason(self):
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 250000}])
        await _say(client, "ดีลนี้แพ้", {"action": "update", "entity": "deal", "fields": {"deal_code": "D-2026-0001", "status": "lost"}, "missing": []})
        reply, writes = await _say(client, "รายชื่อลูกค้า", SUGGEST)
        assert "update_deal" not in writes and "C-2026-0001" in reply.text, reply.text

    async def test_a_reason_in_the_same_sentence_is_not_asked_again(self):
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 250000}])
        reply, _w = await _say(client, "ดีล D-2026-0001 แพ้ เพราะราคาแพง", {"action": "update", "entity": "deal", "fields": {"deal_code": "D-2026-0001", "status": "lost"}, "missing": []})
        assert DEAL_LOST_ASK_REASON["th"] not in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None


class TestARescheduleWithoutADateHoldsTheJob:
    async def test_the_bare_date_moves_the_job(self):
        """Audit [44]: 'ขอเลื่อนนัด' → 'เลื่อนไปวันไหน' → 'มะรืนนี้ 10 โมง' was 'not sure'."""
        client = FakeDataClient(role="technician", permission_keys=TECH_KEYS, customers=[dict(c) for c in CUSTOMERS])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "contact_id": "c1", "status": "in_progress", "assigned_to_ref": "member-1",
                            "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-16", "scheduled_time": "14:00", "customer_name": "สมชาย ใจดี"}]
        first, _w = await _say(client, "ขอเลื่อนนัด", {"action": "update", "entity": "ticket", "fields": {"situation": "reschedule"}, "missing": []}, ctx=_ctx(**TECH))
        assert "เลื่อนไปวันไหน" in first.text, first.text
        reply, writes = await _say(client, "มะรืนนี้ 10 โมง", SUGGEST, ctx=_ctx(**TECH))
        assert "update_ticket" in writes and "T-2026-0001" in reply.text and "10:00" in reply.text, reply.text


class TestTodaysJobsAfterYesterdaysCode:
    async def test_the_question_is_about_today(self):
        """converse tech-12 t8: the code in the sentence was shown as a card; the question was about today."""
        client = FakeDataClient(role="technician", permission_keys=TECH_KEYS, customers=[dict(c) for c in CUSTOMERS])
        from chann_app.services.chat import local_today
        client._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "contact_id": "c1", "status": "completed", "assigned_to_ref": "member-1", "issue_description": "แอร์", "scheduled_date": "2026-09-14"},
            {"id": "t2", "ticket_number": "T-2026-0002", "contact_id": "c3", "status": "assigned", "assigned_to_ref": "member-1", "issue_description": "ตู้เย็น", "scheduled_date": local_today().isoformat(), "customer_name": "สมหญิง"},
        ]
        reply, _w = await _say(client, "เมื่อวานปิดงาน T-2026-0001 ไปแล้ว วันนี้มีงานอีกไหม", {"action": "read", "entity": "ticket", "fields": {"code": "T-2026-0001"}, "missing": []}, ctx=_ctx(**TECH))
        assert "T-2026-0002" in reply.text and "T-2026-0001" not in reply.text, reply.text


class TestAnEarlierJobKnowsTheUnit:
    async def test_a_new_fault_reuses_the_serial_of_the_last_job(self):
        """converse cancel t7: 'แอร์ไม่เย็นอีกแล้ว' after a job with a serial asked to register first."""
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [{"id": "tk1", "ticket_number": "T-2026-0001", "status": "cancelled", "customer_chann_uid": "CHN-S-000001", "serial_number": "SN12345678", "issue_description": "แอร์ไม่เย็น", "created_at": "2026-09-10"}]
        client._warranties = [{"id": "w1", "serial_number": "SN12345678", "product_name": "แอร์", "warranty_end": "2027-01-01", "status": "active", "customer_chann_uid": "CHN-S-000001"}]
        reply, writes = await _say(client, "แอร์ไม่เย็นอีกแล้วค่ะ", {"action": "create", "entity": "ticket", "fields": {"issue_description": "แอร์ไม่เย็นอีกแล้วค่ะ"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert "create_ticket" in writes and "SN12345678" in reply.text, reply.text


class TestASecondRatingIsToldItWasTaken:
    async def test_after_answering_the_survey(self):
        """converse rating t4/t5: '3' again was a greeting, 'ดีเยี่ยม' was 'not sure'."""
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [{"id": "tk1", "ticket_number": "T-2026-0001", "status": "completed", "customer_chann_uid": "CHN-S-000001", "issue_description": "แอร์"}]
        client._approval_steps = []
        client._surveys = [{"id": "survey-tk1", "ticket_id": "tk1", "scale_config_json": {"1": "ไม่ดี", "2": "พอใช้", "3": "ดีเยี่ยม"}, "score": None, "comment": None, "sent_at": "2026-09-15T09:00:00+07:00", "submitted_at": None}]
        first, _w = await _say(client, "3", SUGGEST, ctx=_ctx(**CUSTOMER))
        assert "บันทึกคะแนน" in first.text, first.text
        again, _w = await _say(client, "3", SUGGEST, ctx=_ctx(**CUSTOMER))
        assert again.text == SURVEY_ALREADY_ANSWERED["th"], again.text
        word, _w = await _say(client, "ดีเยี่ยม", SUGGEST, ctx=_ctx(**CUSTOMER))
        assert word.text == SURVEY_ALREADY_ANSWERED["th"], word.text


class TestTheProductPickerTakesANumber:
    async def test_1_adds_the_first_product(self):
        """Audit [10]: '1' after 'มีสินค้าหลายรายการที่ตรงกับ พัดลม' went to the model."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 0}])
        client._products = [{"id": "p1", "product_id": "FAN16", "product_name": "พัดลม 16 นิ้ว", "unit_price": 1500}, {"id": "p2", "product_id": "FAN18", "product_name": "พัดลม 18 นิ้ว", "unit_price": 1900}]
        await _say(client, "D-2026-0001", {"action": "read", "entity": "deal", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        picker, _w = await _say(client, "ใส่สินค้า พัดลม อีก 2 ตัว", {"action": "create", "entity": "line_item", "fields": {"target_name": "พัดลม", "qty": 2}, "missing": []})
        assert "1. พัดลม 16 นิ้ว" in picker.text and "2. พัดลม 18 นิ้ว" in picker.text, picker.text
        reply, writes = await _say(client, "1", SUGGEST)
        assert "add_deal_product" in writes and "พัดลม 16 นิ้ว × 2" in reply.text, reply.text


class TestARefusalNamesTheCustomerInTheSentence:
    async def test_the_named_customers_code_not_the_placeholder(self):
        """Audit [20]: 'ยกเลิกนัดสมหญิงไม่ได้ใช่ไหม' answered with the table's C-2026-0001."""
        client = _sales()
        client._follow_ups = [{"id": "f1", "contact_id": "c3", "entity_type": "customer", "entity_id": "c3", "due_at": "2026-09-17T10:00:00", "status": "pending", "note": "โทร"}]
        reply, writes = await _say(client, "ยกเลิกนัดสมหญิงไม่ได้ใช่ไหม", SUGGEST)
        assert "C-2026-0003" in reply.text and "C-2026-0001" not in reply.text, reply.text
        assert not writes


class TestViewingAJobMakesItsCustomerTheCustomer:
    async def test_another_job_for_this_customer_after_the_card(self):
        """Audit [32]: 'เปิดงานให้ลูกค้าคนนี้อีกงาน' after 'ข้อมูลงาน T-…' asked for a name."""
        client = _sales()
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "contact_id": "c3", "status": "open", "issue_description": "แอร์", "customer_name": "สมหญิง รักดี"}]
        await _say(client, "ข้อมูลงาน T-2026-0001", {"action": "read", "entity": "ticket", "fields": {"code": "T-2026-0001"}, "missing": []})
        reply, writes = await _say(client, "เปิดงานให้ลูกค้าคนนี้อีกงาน ตู้เย็นไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"issue_description": "ตู้เย็นไม่เย็น"}, "missing": []})
        assert "create_ticket" in writes and "สมหญิง รักดี" in reply.text, reply.text


class TestLeadDeletionQuestionsAndRefusals:
    async def test_a_how_to_asks_and_does_not_archive(self):
        """Audit [69]: 'ลบ Lead สมชาย ได้ไหม' went straight to the archive confirmation."""
        client = _sales()
        reply, writes = await _say(client, "ลบ Lead สมชาย ได้ไหม", SUGGEST)
        assert not writes and "ยืนยันลบ" not in reply.text and "ใช่ไหม" in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_a_refusal_is_acknowledged(self):
        """Audit [70]: 'อย่าเพิ่งลบ Lead สมชาย' was 'not sure'."""
        client = _sales()
        reply, writes = await _say(client, "อย่าเพิ่งลบ Lead สมชาย", SUGGEST)
        assert not writes and "ยังไม่ได้" in reply.text, reply.text


class TestNegatedCreatesDoNotCreate:
    async def test_a_refused_customer(self):
        """Audit [70]: 'ไม่ต้องสร้างลูกค้า สมหญิง รักดี 0898765432 แล้วนะ' — the model still read a create."""
        client = _sales()
        reply, writes = await _say(client, "ไม่ต้องสร้างลูกค้า สมหญิง รักดี 0898765432 แล้วนะ", {"action": "create", "entity": "customer", "fields": {"first_name": "สมหญิง", "last_name": "รักดี", "phone": "0898765432"}, "missing": []})
        assert "create_customer" not in writes and "ยังไม่ได้เพิ่มลูกค้า" in reply.text, reply.text

    async def test_a_refused_team(self):
        """Audit [70]: 'อย่าเพิ่งสร้างทีมช่าง ไฟฟ้า' was 'not sure'."""
        client = _sales()
        reply, writes = await _say(client, "อย่าเพิ่งสร้างทีมช่าง ไฟฟ้า", SUGGEST)
        assert "create_technician_team" not in {r[0] for r in client.recorded} and "ยังไม่ได้" in reply.text, reply.text


class TestTheLatestCustomer:
    async def test_ลูกค้าล่าสุด_is_one_customer(self):
        """converse customer-single t4: 'ลูกค้าล่าสุด' listed everyone."""
        client = _sales()
        reply, _w = await _say(client, "ลูกค้าล่าสุด", {"action": "read", "entity": "customer", "fields": {}, "missing": []})
        assert "C-2026-0003" in reply.text and "C-2026-0001" not in reply.text, reply.text


class TestAHalfMadeCustomerSurvivesTheDealDetour:
    """R3 (real model, 15 ก.ย. 2569): 'ลูกค้าใหม่ สามเสน' → 'สร้างดีล' → 'สร้างดีลเลย' → 'สามเสน' lost the draft."""

    async def _to_the_offer(self, client):
        await _say(client, "ลูกค้าใหม่ สามเสน", {"action": "create", "entity": "customer", "fields": {}, "missing": ["first_name", "last_name", "phone"]})
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending["fields"].get("first_name") == "สามเสน", pending  # named before the missing gate asked
        await _say(client, "สร้างดีล", {"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]})
        await _say(client, "สร้างดีลเลย", {"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]})
        reply, writes = await _say(client, "สามเสน", {"action": "create", "entity": "deal", "fields": {"target_name": "สามเสน"}, "missing": []})
        assert "ยังสร้างลูกค้า สามเสน ไม่เสร็จ" in reply.text and not writes, reply.text

    async def test_the_restated_deal_form_keeps_the_draft(self):
        client = _sales()
        await self._to_the_offer(client)

    async def test_the_buttons_label_is_the_no_answer(self):
        client = _sales()
        await self._to_the_offer(client)
        reply, writes = await _say(client, "ใส่เบอร์ก่อน", SUGGEST)
        assert "มาก่อนครับ" in reply.text and not writes, reply.text

    async def test_the_phone_typed_at_the_offer_fills_the_form(self):
        client = _sales()
        await self._to_the_offer(client)
        reply, writes = await _say(client, "0855555555", SUGGEST)
        assert "นามสกุล" in reply.text and not writes, reply.text
        reply, writes = await _say(client, "แซ่ลี้", {"action": "create", "entity": "customer", "fields": {"last_name": "แซ่ลี้"}, "missing": []})
        assert {"create_customer", "create_deal"} <= writes, (reply.text, writes)
        assert "สามเสน แซ่ลี้ (C-2026-0004)" in reply.text or "สามเสน แซ่ลี้" in reply.text, reply.text


class TestAPhoneEditOnTheSalesOaIsACustomerEdit:
    """Owner, 16 ก.ย. 2569: 'แก้เบอร์' on the sales OA — the model read the member's own profile;
    staff edit customers there (their own details live on the dashboard)."""

    async def test_with_a_customer_in_view_the_edit_lands_on_them(self):
        client = _sales()
        await _say(client, "ข้อมูลลูกค้า สมชาย", {"action": "read", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        reply, writes = await _say(client, "แก้เบอร์ 0891234567", {"action": "update", "entity": "profile", "fields": {"phone": "0891234567"}, "missing": []})
        assert "update_customer" in writes and "สมชาย ใจดี" in reply.text and "0891234567" in reply.text, reply.text
        assert "update_profile" not in writes

    async def test_with_nobody_in_view_the_name_is_asked_and_the_edit_waits(self):
        client = _sales()
        reply, writes = await _say(client, "แก้เบอร์ 0891234567", {"action": "update", "entity": "profile", "fields": {"phone": "0891234567"}, "missing": []})
        assert not writes and "ชื่อลูกค้า" in reply.text and "เบอร์โทร" not in reply.text, reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending and pending["entity"] == "customer" and pending["missing"] == ["target_name"], pending
        reply, writes = await _say(client, "สมชาย", {"action": "update", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        assert "update_customer" in writes and "0891234567" in reply.text, reply.text

    async def test_a_bare_แก้เบอร์_asks_for_the_customer_not_the_members_phone(self):
        client = _sales()
        reply, writes = await _say(client, "แก้เบอร์", {"action": "update", "entity": "profile", "fields": {}, "missing": ["phone"]})
        assert not writes and "ชื่อลูกค้า" in reply.text, reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending and pending["entity"] == "customer" and "target_name" in pending["missing"], pending

    async def test_saying_my_own_still_means_the_member(self):
        client = _sales()
        reply, writes = await _say(client, "แก้เบอร์ของฉัน 0891234567", {"action": "update", "entity": "profile", "fields": {"phone": "0891234567"}, "missing": []})
        assert "update_customer" not in writes and "Dashboard" in reply.text, reply.text


class TestADealSentenceWithAProductLine:
    """Owner, 16 ก.ย. 2569: 'สร้างดีล พัดลม 50 ตัว ปิดสิ้นเดือนนี้' made the deal with its close
    date and no line items — DEV's model keeps the customer and the date and drops the product."""

    def test_the_product_clause_is_cut_out_of_the_sentence(self):
        from chann_app.services.chat import _product_clause_of_deal_sentence as clause
        assert clause("สร้างดีลให้ สมชาย พัดลม 50 ตัว ปิดสิ้นเดือนนี้", "สมชาย") == "เพิ่มสินค้า พัดลม 50 ตัว"
        assert clause("สร้างดีล พัดลม 50 ตัว ปิดสิ้นเดือนนี้", None) == "เพิ่มสินค้า พัดลม 50 ตัว"
        assert clause("สร้างดีลให้ สมชาย มูลค่า 250,000 ปิดสิ้นเดือนนี้", "สมชาย") is None
        assert clause("สร้างดีลให้ สมชาย", "สมชาย") is None

    async def test_the_line_is_added_to_the_deal_just_made(self):
        client = _sales()
        client._products = [{"id": "p1", "product_id": "FAN16", "product_name": "พัดลม", "unit_price": 1500}]
        reply, writes = await _say(client, "สร้างดีลให้ สมชาย ใจดี พัดลม 50 ตัว ปิดสิ้นเดือนนี้", {"action": "create", "entity": "deal", "fields": {"target_name": "สมชาย ใจดี", "expected_close_date": "2026-09-30"}, "missing": []})
        assert {"create_deal", "add_deal_product"} <= writes, (reply.text, writes)
        assert "พัดลม × 50" in reply.text and "30 ก.ย." in reply.text, reply.text

    async def test_with_the_customer_in_view(self):
        client = _sales()
        client._products = [{"id": "p1", "product_id": "FAN16", "product_name": "พัดลม", "unit_price": 1500}]
        await _say(client, "ข้อมูลลูกค้า สมหญิง", {"action": "read", "entity": "customer", "fields": {"target_name": "สมหญิง"}, "missing": []})
        reply, writes = await _say(client, "สร้างดีล พัดลม 50 ตัว ปิดสิ้นเดือนนี้", {"action": "create", "entity": "deal", "fields": {"amount": 50, "expected_close_date": "2026-09-30"}, "missing": ["target_name"]})
        assert {"create_deal", "add_deal_product"} <= writes, (reply.text, writes)
        assert "สมหญิง รักดี" in reply.text and "พัดลม × 50" in reply.text and "มูลค่า 50 " not in reply.text, reply.text


def _writes(client, since: int, *names: str) -> list:
    return [r for r in client.recorded[since:] if r[0] in names]


class TestSeveralProductsInOneSentence:
    """Owner, 16 ก.ย. 2569: 'เพิ่มพัดลม 1 ตัว และ แอร์ 1 ตัว' looked for a product called
    "พัดลม และ แอร์" — on the deal, on the quote and in the catalogue only one product
    at a time could be added. DEV's model answers such a sentence with one JSON object per
    product (raw replies read 16 ก.ย.); the parser used to reject that as "Extra data"."""

    DEAL = {"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 0, "products": []}
    PRODUCTS = [
        {"id": "p1", "product_id": "FAN16", "product_name": "พัดลม", "unit_price": 1500},
        {"id": "p2", "product_id": "AIR12", "product_name": "แอร์", "unit_price": 12000},
    ]
    TWO_LINES = {
        "action": "create", "entity": "line_item", "fields": {"target_name": "พัดลม", "qty": 2}, "missing": [],
        "and_then": [{"action": "create", "entity": "line_item", "fields": {"target_name": "แอร์", "qty": 1}, "missing": []}],
    }

    def _with_deal_in_view(self):
        client = _quoting_sales(deals=[dict(self.DEAL, products=[])])
        client._products = [dict(p) for p in self.PRODUCTS]
        return client

    async def test_two_readings_put_two_lines_on_the_deal(self):
        client = self._with_deal_in_view()
        await _say(client, "ข้อมูลดีล D-2026-0001", {"action": "read", "entity": "deal", "fields": {"code": "D-2026-0001"}, "missing": []})
        since = len(client.recorded)
        reply, _ = await _say(client, "เพิ่มพัดลม 2 ตัว และ แอร์ 1 ตัว", self.TWO_LINES)
        added = [w[3]["product_name"] for w in _writes(client, since, "add_deal_product")]
        assert added == ["พัดลม", "แอร์"], (reply.text, added)
        assert "พัดลม × 2" in reply.text and "แอร์ × 1" in reply.text, reply.text
        assert reply.entity_type == "deal" and reply.entity_id == "d1"

    async def test_one_reading_that_dropped_the_second_product_still_adds_both(self):
        """The model sometimes returns only the first product for this sentence — the
        sentence itself says there are two."""
        client = self._with_deal_in_view()
        await _say(client, "ข้อมูลดีล D-2026-0001", {"action": "read", "entity": "deal", "fields": {"code": "D-2026-0001"}, "missing": []})
        since = len(client.recorded)
        reply, _ = await _say(client, "เพิ่มพัดลม 1 ตัว และ แอร์ 1 ตัว", {"action": "create", "entity": "line_item", "fields": {"target_name": "พัดลม", "qty": 1}, "missing": []})
        added = [w[3]["product_name"] for w in _writes(client, since, "add_deal_product")]
        assert added == ["พัดลม", "แอร์"], (reply.text, added)

    async def test_two_readings_put_two_lines_on_the_quote(self):
        client = self._with_deal_in_view()
        client._quotes = [{"id": "QUOTE-1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "draft", "generated_document_id": None}]
        await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", {"action": "read", "entity": "quote", "fields": {"code": "Q-2026-0001"}, "missing": []})
        since = len(client.recorded)
        reply, _ = await _say(client, "เพิ่มสินค้าในใบเสนอราคา พัดลม 2 ตัว และ แอร์ 1 ตัว", self.TWO_LINES)
        added = [w[3]["product_name"] for w in _writes(client, since, "add_quote_product")]
        assert added == ["พัดลม", "แอร์"], (reply.text, added)
        assert "เข้าใบเสนอราคา Q-2026-0001" in reply.text and "เข้าดีล Q-" not in reply.text, reply.text

    async def test_a_deal_sentence_with_two_products_gets_both_lines(self):
        client = _sales()
        client._products = [dict(p) for p in self.PRODUCTS]
        since = len(client.recorded)
        reply, writes = await _say(client, "สร้างดีลให้ สมชาย ใจดี พัดลม 50 ตัว และ แอร์ 2 ตัว ปิดสิ้นเดือนนี้", {"action": "create", "entity": "deal", "fields": {"target_name": "สมชาย ใจดี", "expected_close_date": "2026-09-30"}, "missing": []})
        assert "create_deal" in writes, reply.text
        added = [w[3]["product_name"] for w in _writes(client, since, "add_deal_product")]
        assert added == ["พัดลม", "แอร์"], (reply.text, added)

    async def test_two_catalogue_products_in_one_sentence(self):
        client = _sales()
        since = len(client.recorded)
        reply, _ = await _say(client, "เพิ่มสินค้าเข้าคลัง พัดลม รหัส FAN01 ราคา 1500 กับ แอร์ รหัส AIR01 ราคา 12000", {
            "action": "create", "entity": "product", "fields": {"product_id": "FAN01", "product_name": "พัดลม", "unit_price": 1500}, "missing": [],
            "and_then": [{"action": "create", "entity": "product", "fields": {"product_id": "AIR01", "product_name": "แอร์", "unit_price": 12000}, "missing": []}],
        })
        saved = [w[2] for w in _writes(client, since, "upsert_product")]
        assert saved == ["FAN01", "AIR01"], (reply.text, saved)
        assert "FAN01" in reply.text and "AIR01" in reply.text, reply.text

    async def test_the_first_item_missing_a_detail_holds_it_and_says_the_rest_wait(self):
        client = _sales()
        reply, _ = await _say(client, "เพิ่มสินค้าใหม่ พัดลม ราคา 1500 บาท และ แอร์ ราคา 12000 บาท", {
            "action": "create", "entity": "product", "fields": {"product_name": "พัดลม", "unit_price": 1500}, "missing": ["product_id"],
            "and_then": [{"action": "create", "entity": "product", "fields": {"product_name": "แอร์", "unit_price": 12000}, "missing": ["product_id"]}],
        })
        assert not _writes(client, 0, "upsert_product"), reply.text
        assert "แอร์" in reply.text and "รหัส" in reply.text, reply.text
        held = await client.get_pending_intent("CHN-S-000001", "sales")
        assert held and held["entity"] == "product" and held["fields"]["product_name"] == "พัดลม"

    async def test_extra_readings_of_another_kind_are_not_run(self):
        """Two readings are run together only when they are more of the same kind of
        item; a note read alongside a customer edit is not a second command."""
        from chann_app.services.chat import _items_read_together
        assert _items_read_together({"action": "update", "entity": "customer", "fields": {}, "and_then": [{"action": "create", "entity": "note", "fields": {}}]}) == []
        assert len(_items_read_together(self.TWO_LINES)) == 2
        assert _items_read_together({"action": "create", "entity": "line_item", "fields": {}}) == []


class TestTheQuoteCardListsItsLines:
    """Owner, 16 ก.ย. 2569: 'ตอนขอข้อมูลใบเสนอราคา ก็ไม่ละเอียด ไม่มีรายการสินค้าเหมือนดีล'."""

    async def test_lines_and_total_are_on_the_card(self):
        client = _quoting_sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 0, "products": []}])
        client._quotes = [{"id": "QUOTE-1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "draft", "generated_document_id": None}]
        client._quote_lines = [
            {"id": "ql-1", "quote_id": "QUOTE-1", "product_name": "พัดลม", "qty": 2, "quoted_unit_price": 1500},
            {"id": "ql-2", "quote_id": "QUOTE-1", "product_name": "แอร์", "qty": 1, "quoted_unit_price": 12000},
        ]
        reply, _ = await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", {"action": "read", "entity": "quote", "fields": {"code": "Q-2026-0001"}, "missing": []})
        assert "1. พัดลม × 2 = 3,000.00" in reply.text and "2. แอร์ × 1 = 12,000.00" in reply.text, reply.text
        assert "รวม: 15,000.00 บาท" in reply.text and "ดีล: D-2026-0001" in reply.text, reply.text

    async def test_an_empty_quote_says_so(self):
        client = _quoting_sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "proposed", "amount": 0, "products": []}])
        client._quotes = [{"id": "QUOTE-1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "draft", "generated_document_id": None}]
        reply, _ = await _say(client, "ข้อมูลใบเสนอราคา Q-2026-0001", {"action": "read", "entity": "quote", "fields": {"code": "Q-2026-0001"}, "missing": []})
        assert "ยังไม่มีรายการสินค้า" in reply.text, reply.text
