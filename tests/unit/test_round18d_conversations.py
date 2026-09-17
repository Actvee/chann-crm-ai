"""Round 18d — what the real-model conversations found after the audit batch
(converse.py over ~80 scenarios, 15 ก.ย. 2569), pinned with the model's
reading stubbed exactly as DEV's model returned it.

Each test names the conversation it came from. The model is stubbed, so
these prove the road, not the reading (see docs/MODEL_FIRST.md).
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import (
    CHECKIN_PICK_ONE, JOB_ACTION_NOT_REQUESTED, REPORT_WHICH_PRODUCT, TICKET_PICK_ONE, handle_chat_message,
)
from test_phase6_chat import FakeDataClient, _ai, _ctx

# Round 19z: these used to hardcode the day after the day they were
# written, so the suite went red at midnight and stayed red. "พรุ่งนี้"
# has to BE tomorrow, whenever the test runs.
def _tomorrow() -> str:
    from datetime import timedelta

    from chann_app.services.thai_datetime import local_today

    return (local_today() + timedelta(days=1)).isoformat()


pytestmark = pytest.mark.asyncio

TECH_KEYS = ["product.read", "service_report.create", "service_report.read", "service_report.update", "ticket.assign", "ticket.close", "ticket.create", "ticket.read", "ticket.update"]
KEYS = [
    "customer.create", "customer.read", "customer.update", "deal.create", "deal.read", "deal.update", "deal.reopen",
    "product.manage", "followup.create", "followup.read", "followup.update", "ticket.create", "ticket.read",
    "ticket.update", "ticket.assign", "team.manage", "quote.create", "quote.read", "quote.update", "note.create",
]
CUSTOMERS = [
    {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"},
    {"id": "c2", "customer_id": "C-2026-0002", "first_name": "สมชาย", "last_name": "รักดี", "phone": "0898765432", "stage": "lead"},
    {"id": "c3", "customer_id": "C-2026-0003", "first_name": "สมหญิง", "last_name": "รักดี", "phone": "0811111111", "stage": "lead"},
]


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _reads(reading: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_ai(json.dumps(reading)))


def _writes(client: FakeDataClient, since: int = 0) -> set[str]:
    return {r[0] for r in client.recorded[since:] if r[0].startswith(("create_", "update_", "upsert_", "transition_", "assign_", "add_team", "remove_team", "delete_"))}


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, _writes(client, since)


def _sales(**kw) -> FakeDataClient:
    return FakeDataClient(permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS], **kw)


# ------------------------------------------------------------------ deals

class TestReopenOnTheModelRoad:
    async def test_reopen_reaches_the_stage_change(self):
        """deal-create t7: 'เปิดดีล D-2026-0001 ใหม่' read as action=reopen was 'ยังทำรายการนี้ไม่ได้'."""
        client = _sales(deals=[{"id": "deal-1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "lost", "amount": 250000}])
        reply, writes = await _say(client, "เปิดดีล D-2026-0001 ใหม่", {"action": "reopen", "entity": "deal", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        assert "transition_deal_stage" in writes, (reply.text, writes)
        assert "D-2026-0001" in reply.text and "ใหม่" in reply.text, reply.text


class TestThisDealIsTheDealInView:
    async def test_the_card_not_the_list(self):
        """customer-duplicate-name t10: 'ดีลนี้ของใคร' after creating a deal listed deals."""
        client = _sales()
        await _say(client, "สร้างดีลให้ สมหญิง", {"action": "create", "entity": "deal", "fields": {"target_name": "สมหญิง"}, "missing": []})
        reply, writes = await _say(client, "ดีลนี้ของใคร", {"action": "read", "entity": "deal", "fields": {}, "missing": []})
        assert "สมหญิง รักดี" in reply.text and "C-2026-0003" in reply.text, reply.text
        assert not writes


class TestABareDealCommandAfterAnAbandonedCustomerAsksWhom:
    async def test_the_customer_in_view_does_not_get_the_deal(self):
        """flow-switch t10: 'สร้างดีล' while มานพ's form waited created a deal for the customer last in view."""
        client = _sales()
        await _say(client, "สร้างดีลให้ สมหญิง", {"action": "create", "entity": "deal", "fields": {"target_name": "สมหญิง"}, "missing": []})
        await client.set_pending_intent("CHN-S-000001", "sales", action="create", entity="customer", fields={"first_name": "มานพ"}, missing=["last_name", "phone"])
        reply, writes = await _say(client, "สร้างดีล", {"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]})
        assert "create_deal" not in writes, reply.text
        assert "กรุณาระบุชื่อลูกค้า" in reply.text, reply.text


class TestAnUnnamedDraftTakesTheDealsName:
    async def test_the_name_typed_for_the_deal_is_the_customer_being_added(self):
        """R3: 'เพิ่มลูกค้า' → 'สร้างดีล' → 'สามเสน' was 'ไม่พบลูกค้าชื่อ สามเสน'."""
        client = _sales()
        await _say(client, "เพิ่มลูกค้า", {"action": "create", "entity": "customer", "fields": {}, "missing": ["first_name", "last_name", "phone"]})
        await _say(client, "สร้างดีล", {"action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"]})
        reply, writes = await _say(client, "สามเสน", {"action": "create", "entity": "deal", "fields": {"target_name": "สามเสน"}, "missing": []})
        assert "ยังสร้างลูกค้า สามเสน ไม่เสร็จ" in reply.text, reply.text
        assert not writes
        assert [send for _label, send in reply.quick_replies][0] == "ใช่"


# ------------------------------------------------------------------ products

class TestAProductPriceChangeByCode:
    async def test_update_by_code_without_a_name(self):
        """product-catalogue: the model returns update/product with the code and price and no name."""
        client = _sales()
        client._products = [{"id": "p1", "product_id": "TV40", "product_name": "ทีวี 40 นิ้ว", "unit_price": 4000}]
        reply, writes = await _say(client, "เปลี่ยนราคาสินค้า TV40 เป็น 3900", {"action": "update", "entity": "product", "fields": {"product_id": "TV40", "unit_price": 3900}, "missing": []})
        assert "upsert_product" in writes, reply.text
        assert "3,900" in reply.text and "TV40" in reply.text, reply.text

    async def test_the_polite_form_read_as_a_line_item_right_after_saving_the_product(self):
        """product-catalogue t9: 'ช่วยเปลี่ยนราคาสินค้า TV40 เป็น 3,900 ให้หน่อยครับ' → update/line_item; the product just saved is in view."""
        client = _sales()
        await _say(client, "TV40 ทีวี 40 นิ้ว 4000", {"action": "create", "entity": "product", "fields": {"product_id": "TV40", "product_name": "ทีวี 40 นิ้ว", "unit_price": 4000}, "missing": []})
        reply, writes = await _say(client, "ช่วยเปลี่ยนราคาสินค้า TV40 เป็น 3,900 ให้หน่อยครับ", {"action": "update", "entity": "line_item", "fields": {"target_name": "TV40", "quoted_unit_price": 3900}, "missing": []})
        assert "upsert_product" in writes, reply.text
        assert "3,900" in reply.text, reply.text


# ------------------------------------------------------------------ edits say what changed

class TestAnEditSaysWhatChanged:
    async def test_the_new_phone_is_in_the_reply(self):
        """customer-single t5: 'ช่วยแก้เบอร์ สมหญิง ให้เป็น 0811111111 หน่อยครับ' said only 'เรียบร้อยแล้ว'."""
        client = _sales()
        reply, writes = await _say(client, "ช่วยแก้เบอร์ สมหญิง ให้เป็น 0899999999 หน่อยครับ", {"action": "update", "entity": "customer", "fields": {"target_name": "สมหญิง", "phone": "0899999999"}, "missing": []})
        assert "update_customer" in writes, reply.text
        assert "0899999999" in reply.text and "เบอร์โทร" in reply.text, reply.text

    async def test_a_dotted_phone_is_kept_as_digits(self):
        """customer-bulk t5: '081.234.5679' came back with the dots (the Data tier keeps digits)."""
        client = _sales()
        await _say(client, "เพิ่มลูกค้า อารี สุขใจ 081.234.5679", {"action": "create", "entity": "customer", "fields": {"first_name": "อารี", "last_name": "สุขใจ", "phone": "081.234.5679"}, "missing": []})
        reply, _w = await _say(client, "เบอร์ของ อารี", {"action": "read", "entity": "customer", "fields": {"target_name": "อารี"}, "missing": []})
        assert "0812345679" in reply.text, reply.text


# ------------------------------------------------------------------ the diary

class TestADayAloneIsStillAForm:
    async def test_a_reminder_with_a_time_and_a_subject_is_still_a_form(self):
        """"นัดพรุ่งนี้ …" read as create/followup is the form the tester saw (14 ก.ย.), not the diary."""
        client = _sales()
        reply, _w = await _say(client, "นัดพรุ่งนี้ 10 โมง โทรตาม", {"action": "create", "entity": "followup", "fields": {"due_date": _tomorrow(), "due_time": "10:00", "notes": "โทรตาม"}, "missing": ["target_name"]})
        assert "กรุณาระบุชื่อลูกค้า" in reply.text, reply.text


class TestTheSameFlowRestatedReplacesItsForm:
    async def test_no_switch_question_for_the_same_flow(self):
        """A reminder form waiting for its name, then 'นัดพรุ่งนี้ บ่าย 3 โทรตาม': it asked 'จะยกเลิกแล้วตั้งนัดแทนไหม'."""
        client = _sales()
        await client.set_pending_intent("CHN-S-000001", "sales", action="create", entity="followup", fields={"due_date": "2026-09-20"}, missing=["target_name"])
        reply, _w = await _say(client, "นัดพรุ่งนี้ บ่าย 3 โทรตาม", {"action": "create", "entity": "followup", "fields": {"due_date": _tomorrow(), "due_time": "15:00", "notes": "โทรตาม"}, "missing": ["target_name"]})
        assert "แทนไหม" not in reply.text, reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending and pending["entity"] == "followup" and pending["fields"].get("due_time") == "15:00", pending


class TestThisCustomerIsTheCustomerNotTheDeal:
    async def test_the_reminder_goes_on_the_customer(self):
        """R10 t4: 'นัดลูกค้าคนนี้ พรุ่งนี้ 10 โมง' with the deal in view was put on the deal."""
        client = _sales()
        await _say(client, "สร้างดีลให้ สมหญิง", {"action": "create", "entity": "deal", "fields": {"target_name": "สมหญิง"}, "missing": []})
        reply, writes = await _say(client, "นัดลูกค้าคนนี้ พรุ่งนี้ 10 โมง", {"action": "create", "entity": "followup", "fields": {"target_name": "ลูกค้าคนนี้", "due_date": _tomorrow(), "due_time": "10:00"}, "missing": []})
        assert "create_follow_up" in writes, reply.text
        assert "C-2026-0003" in reply.text and "D-2026" not in reply.text, reply.text


class TestChangingTheTimeAfterMovingAnAppointment:
    async def test_the_time_change_moves_the_same_appointment(self):
        """diary-05 t3: 'เปลี่ยนเวลาเป็น 13.00' after 'เลื่อนนัดเป็นวันศุกร์' went looking for a job."""
        client = _sales()
        client._follow_ups = [{"id": "f1", "contact_id": "c3", "entity_type": "customer", "entity_id": "c3", "due_at": "2026-09-17T10:00:00", "status": "pending", "note": "โทรตาม"}]
        await _say(client, "เลื่อนนัด สมหญิง เป็นวันศุกร์", {"action": "update", "entity": "followup", "fields": {"target_name": "สมหญิง", "due_date": "2026-09-18"}, "missing": []})
        reply, writes = await _say(client, "เปลี่ยนเวลาเป็น 13.00", {"action": "update", "entity": "ticket", "fields": {"scheduled_time": "13:00"}, "missing": []})
        assert "update_follow_up" in writes, reply.text
        assert "13:00" in reply.text and "C-2026-0003" in reply.text, reply.text


class TestMovingTheDealsAppointmentIsNotItsCloseDate:
    async def test_เลื่อนนัดดีลนี้_moves_the_reminder(self):
        """diary-13 t3: 'เลื่อนนัดดีลนี้เป็นวันอังคาร' was filed as the expected close date."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c3", "stage": "new", "amount": 1000}])
        client._follow_ups = [{"id": "f1", "entity_type": "deal", "entity_id": "d1", "due_at": "2026-09-21T11:00:00", "status": "pending", "note": ""}]
        await _say(client, "D-2026-0001", {"action": "read", "entity": "deal", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        reply, writes = await _say(client, "เลื่อนนัดดีลนี้เป็นวันอังคาร", {"action": "update", "entity": "deal", "fields": {"expected_close_date": "2026-09-22"}, "missing": []})
        assert "update_follow_up" in writes and "update_deal" not in writes, (reply.text, writes)
        assert "D-2026-0001" in reply.text and "22 ก.ย." in reply.text, reply.text


class TestAFlowSwitchAnsweredWithAnotherCommand:
    async def test_the_typed_command_runs_not_the_held_one(self):
        """diary-03 t4: 'ตั้งนัด สมชาย ใจดี พรุ่งนี้บ่าย 3' typed at 'switch to สร้างดีลให้ สมหญิง?' created the deal."""
        client = _sales()
        await client.set_pending_intent(
            "CHN-S-000001", "sales", action="resolve", entity="flow_switch",
            fields={"original": {"action": "create", "entity": "followup", "fields": {"target_name": "สมชาย ใจดี"}, "missing": ["due_date"]},
                    "command": "สร้างดีลให้ สมหญิง"}, missing=[],
        )
        reply, writes = await _say(client, "ตั้งนัด สมชาย ใจดี พรุ่งนี้บ่าย 3", {"action": "create", "entity": "followup", "fields": {"target_name": "สมชาย ใจดี", "due_date": _tomorrow(), "due_time": "15:00"}, "missing": []})
        assert "create_follow_up" in writes and "create_deal" not in writes, (reply.text, writes)
        assert "C-2026-0001" in reply.text and "15:00" in reply.text, reply.text


# ------------------------------------------------------------------ tickets

class TestThePickerAnswerResumesATicket:
    async def test_the_chosen_person_is_the_tickets_customer(self):
        """ops-02 t2: '2' after the ticket's customer picker was 'C-2026-0002 เป็นรหัสลูกค้า ไม่ใช่งานซ่อม'."""
        client = _sales()
        first, _w = await _say(client, "เปิดงานให้ สมชาย ตู้เย็นไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"target_name": "สมชาย", "issue_description": "ตู้เย็นไม่เย็น"}, "missing": []})
        assert "หลายคน" in first.text, first.text
        reply, writes = await _say(client, "2", {"action": "suggest", "entity": None, "fields": {}, "missing": []})
        assert "create_ticket" in writes, reply.text
        assert "สมชาย รักดี" in reply.text and "T-2026-0001" in reply.text, reply.text


class TestAJobOpenedForACustomerMakesThemTheCustomer:
    async def test_another_job_for_this_customer(self):
        """ops-01 t6: 'ช่วยเปิดงานให้ลูกค้าคนนี้อีกงาน ตู้เย็นไม่เย็น' asked for the name again."""
        client = _sales()
        await _say(client, "เปิดงานให้ สมหญิง แอร์ไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"target_name": "สมหญิง", "issue_description": "แอร์ไม่เย็น"}, "missing": []})
        reply, writes = await _say(client, "ช่วยเปิดงานให้ลูกค้าคนนี้อีกงาน ตู้เย็นไม่เย็น ให้หน่อยครับ", {"action": "create", "entity": "ticket", "fields": {"issue_description": "ตู้เย็นไม่เย็น"}, "missing": []})
        assert "create_ticket" in writes, reply.text
        assert "สมหญิง" in reply.text and "T-2026-0002" in reply.text, reply.text


class TestTheJobCardNamesItsTechnician:
    async def test_the_member_list_supplies_the_name(self):
        """R8 t3: 'ใครรับงาน T-2026-0002' showed the card without the technician."""
        client = _sales()
        client._tickets = [{"id": "t2", "ticket_number": "T-2026-0002", "contact_id": "c3", "status": "assigned", "assigned_to_ref": "m-tech-2",
                            "assigned_target_type": "technician", "issue_description": "ตู้เย็นไม่เย็น", "customer_name": "สมหญิง รักดี"}]
        client._members = [{"id": "m-tech-2", "chann_uid": "CHN-T-000002", "role": "technician", "status": "active", "display_name": "วิชัย", "channel": "technician"}]
        reply, _w = await _say(client, "ใครรับงาน T-2026-0002", {"action": "read", "entity": "ticket", "fields": {"code": "T-2026-0002"}, "missing": []})
        assert "ช่าง: วิชัย" in reply.text, reply.text


class TestUnassignedJobs:
    async def test_only_jobs_with_nobody_on_them(self):
        """R8 t8: 'งานที่ยังไม่ได้มอบหมาย' listed the assigned job too."""
        client = _sales()
        client._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "contact_id": "c1", "status": "open", "issue_description": "แอร์ไม่เย็น", "customer_name": "สมชาย ใจดี"},
            {"id": "t2", "ticket_number": "T-2026-0002", "contact_id": "c3", "status": "assigned", "assigned_to_ref": "m-tech-2", "issue_description": "ตู้เย็นไม่เย็น", "customer_name": "สมหญิง รักดี"},
        ]
        reply, _w = await _say(client, "งานที่ยังไม่ได้มอบหมาย", {"action": "read", "entity": "ticket", "fields": {}, "missing": []})
        assert "T-2026-0001" in reply.text and "T-2026-0002" not in reply.text, reply.text


class TestAFinishedJobIsSaidOnce:
    async def test_no_double_แล้ว(self):
        """tech-09 t2: 'ปิดงาน T-2026-0002' on a finished job said 'เสร็จแล้วแล้วครับ'."""
        client = FakeDataClient(role="technician", permission_keys=TECH_KEYS)
        client._tickets = [{"id": "t2", "ticket_number": "T-2026-0002", "contact_id": "c1", "status": "completed", "assigned_to_ref": "member-1", "issue_description": "แอร์ไม่เย็น"}]
        # DEV's model reads it as update/service_report status=closed (ask-model, 15 ก.ย. 2569).
        reply, writes = await _say(client, "ปิดงาน T-2026-0002", {"action": "update", "entity": "service_report", "fields": {"code": "T-2026-0002", "status": "closed"}, "missing": []}, ctx=_ctx(oa="technician", primary_role="technician"))
        assert "แล้วแล้ว" not in reply.text and "เสร็จแล้ว" in reply.text, reply.text
        assert not writes


class TestCheckOutWithoutACodeListsTheJobsInText:
    async def test_the_codes_are_in_the_text_as_well_as_the_buttons(self):
        """tech-13 t1: the buttons carried the codes; a technician reading text saw none."""
        client = FakeDataClient(role="technician", permission_keys=TECH_KEYS)
        client._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "contact_id": "c1", "status": "in_progress", "assigned_to_ref": "member-1", "issue_description": "แอร์ไม่เย็น", "customer_name": "สมชาย ใจดี"},
            {"id": "t2", "ticket_number": "T-2026-0002", "contact_id": "c3", "status": "in_progress", "assigned_to_ref": "member-1", "issue_description": "ตู้เย็นไม่เย็น", "customer_name": "สมหญิง รักดี"},
        ]
        reply, writes = await _say(client, "ปิดงาน", {"action": "check_out", "entity": "ticket", "fields": {}, "missing": []}, ctx=_ctx(oa="technician", primary_role="technician"))
        assert reply.text.startswith(TICKET_PICK_ONE["th"]), reply.text
        assert "T-2026-0001" in reply.text and "T-2026-0002" in reply.text, reply.text
        assert [send for _l, send in reply.quick_replies] == ["ปิดงาน T-2026-0001", "ปิดงาน T-2026-0002"]
        assert not writes


class TestTeamsNameTheirMembers:
    async def test_a_member_without_a_profile_is_named_from_the_member_row(self):
        """R8 t7: 'ทีมช่าง' listed '· แอร์: (หัวหน้า)' with no name."""
        client = _sales()
        client._teams = [{"id": "team-1", "team_name": "แอร์"}]
        client._team_members = [{"team_id": "team-1", "member_id": "m-tech-2", "chann_uid": "CHN-T-000002", "is_lead": True, "display_name": "วิชัย"}]
        reply, _w = await _say(client, "ทีมช่าง", {"action": "read", "entity": "team", "fields": {}, "missing": []})
        assert "วิชัย" in reply.text and "หัวหน้า" in reply.text, reply.text


# ------------------------------------------------------------------ the customer OA

def _customer_with_two_machines() -> FakeDataClient:
    client = FakeDataClient(role="customer", permission_keys=[])
    client._warranties = [
        {"id": "w-1", "serial_number": "ONLY00001", "product_name": "แอร์ Daikin", "product_id": "prod-air-1", "warranty_start": "2026-01-01", "warranty_end": "2027-01-01", "status": "active", "customer_chann_uid": "CHN-S-000001"},
        {"id": "w-2", "serial_number": "ONLY00002", "product_name": "ตู้เย็น", "product_id": "prod-fridge-1", "warranty_start": "2026-01-01", "warranty_end": "2027-01-01", "status": "active", "customer_chann_uid": "CHN-S-000001"},
    ]
    return client


CUSTOMER = dict(oa="customer", primary_role="customer")


class TestAFaultWaitingForItsMachine:
    async def test_a_new_fault_shows_the_picker_again(self):
        """two registered machines t3: a restated fault while the picker waited was told to register first."""
        client = _customer_with_two_machines()
        first, _w = await _say(client, "ไม่เย็นเลย", {"action": "create", "entity": "ticket", "fields": {"issue_description": "ไม่เย็นเลย"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert first.text == REPORT_WHICH_PRODUCT["th"], first.text
        reply, writes = await _say(client, "เสียงดังมากด้วย", {"action": "create", "entity": "ticket", "fields": {"issue_description": "เสียงดังมากด้วย"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert "create_ticket" not in writes
        assert "ก่อนแจ้งซ่อม ขอลงทะเบียน" not in reply.text, reply.text
        assert any("ONLY00001" in send for _l, send in reply.quick_replies), (reply.text, reply.quick_replies)

    async def test_a_question_is_answered_and_the_fault_still_waits(self):
        """two registered machines t2: 'ขอถามก่อน ประกันยังไม่หมดใช่ไหม' mid-pick was re-asked for the S/N."""
        client = _customer_with_two_machines()
        await _say(client, "ไม่เย็นเลย", {"action": "create", "entity": "ticket", "fields": {"issue_description": "ไม่เย็นเลย"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        reply, writes = await _say(client, "ขอถามก่อน ประกันยังไม่หมดใช่ไหม", {"action": "read", "entity": "warranty", "fields": {}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert "create_ticket" not in writes
        assert "ก่อนแจ้งซ่อม ขอลงทะเบียน" not in reply.text, reply.text
        assert "ONLY00001" in reply.text, reply.text
        assert "ไม่เย็นเลย" in reply.text and "ยังรอ" in reply.text, reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "customer")
        assert pending and pending["entity"] == "pending_customer_message", pending


class TestAProfileEditSaysWhatChanged:
    async def test_the_new_phone_is_in_the_reply(self):
        """customer help t3: 'เบอร์ใหม่ 0891234567' said only 'แก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว'."""
        client = FakeDataClient(role="customer", permission_keys=[])
        reply, writes = await _say(client, "เบอร์ใหม่ 0891234567", {"action": "update", "entity": "profile", "fields": {"phone": "0891234567"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert "update_profile" in writes, reply.text
        assert "0891234567" in reply.text, reply.text


# ------------------------------------------------------------------ second real-model run

class TestABareIssueIsTheQuoteInView:
    async def test_ออกเอกสาร_after_making_the_quote(self):
        """quote-context t4: 'ออกเอกสาร' right after 'สร้างใบเสนอราคา' was answered with the search hint."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 0}])
        await _say(client, "สร้างใบเสนอราคา D-2026-0001", {"action": "create", "entity": "quote", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        reply, writes = await _say(client, "ออกเอกสาร", {"action": "suggest", "entity": None, "fields": {}, "missing": []})
        assert "ค้นหา" not in reply.text, reply.text
        assert "Q-2026-0001" in reply.text or "ไม่ครบ" in reply.text or "ยังไม่พร้อม" in reply.text, reply.text

    async def test_with_nothing_in_view_it_asks_which(self):
        client = _sales()
        reply, _w = await _say(client, "ออกเอกสาร", {"action": "suggest", "entity": None, "fields": {}, "missing": []})
        assert "ใบเสนอราคาไหน" in reply.text, reply.text


class TestThisQuoteIsTheQuoteInView:
    async def test_which_deal_is_this_quote_for(self):
        """quote-context t8: 'ใบเสนอราคานี้ของดีลไหน' (read/quote missing code) filed a form."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 0}])
        await _say(client, "สร้างใบเสนอราคา D-2026-0001", {"action": "create", "entity": "quote", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        reply, _w = await _say(client, "ใบเสนอราคานี้ของดีลไหน", {"action": "read", "entity": "quote", "fields": {}, "missing": ["code"]})
        assert "D-2026-0001" in reply.text and "Q-2026-0001" in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None


class TestTheQuoteCardShowsItsDiscount:
    async def test_the_discount_is_on_the_card(self):
        """quote-lifecycle t4: 'Q-2026-0001' after 'ลดราคา Q-2026-0001 500 บาท' did not show the 500."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 0}])
        await _say(client, "สร้างใบเสนอราคา D-2026-0001", {"action": "create", "entity": "quote", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        client._quotes[0]["discount_amount"] = "500.00"
        reply, _w = await _say(client, "Q-2026-0001", {"action": "read", "entity": "quote", "fields": {"code": "Q-2026-0001"}, "missing": []})
        assert "ส่วนลด: 500.00" in reply.text, reply.text


class TestADealWithAValueSaysIt:
    async def test_the_card_shows_the_stated_value(self):
        """R1 t5: 'ยอดดีล D-2026-0002 เท่าไหร่' on a 10,000 deal with no lines said 'รวม: 0.00'."""
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 10000}])
        reply, _w = await _say(client, "ยอดดีล D-2026-0001 เท่าไหร่", {"action": "read", "entity": "deal", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        assert "10,000" in reply.text and "0.00" not in reply.text, reply.text


class TestOverMeansMoreThan:
    async def test_เกิน_excludes_the_exact_amount(self):
        """R4 t5: 'ดีลเกิน 1 หมื่น' listed the 10,000 deal."""
        client = _sales(deals=[
            {"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 10000},
            {"id": "d2", "deal_id": "D-2026-0002", "contact_id": "c3", "stage": "new", "amount": 15000},
        ])
        reply, _w = await _say(client, "ดีลเกิน 1 หมื่น", {"action": "read", "entity": "deal", "fields": {"amount_greater_than": 10000}, "missing": []})
        assert "D-2026-0002" in reply.text and "D-2026-0001" not in reply.text, reply.text

    async def test_ตั้งแต่_includes_it(self):
        client = _sales(deals=[
            {"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new", "amount": 10000},
        ])
        reply, _w = await _say(client, "ดีลตั้งแต่ 1 หมื่น", {"action": "read", "entity": "deal", "fields": {"min_amount": 10000}, "missing": []})
        assert "D-2026-0001" in reply.text, reply.text


class TestTheTechnicianTeamsQuestion:
    async def test_ทีมช่างมีใครบ้าง_lists_the_teams(self):
        """ops-05 t4: 'ทีมช่างมีใครบ้าง' listed the technicians, not the teams and their leads."""
        client = _sales()
        client._teams = [{"id": "team-1", "team_name": "แอร์"}]
        client._team_members = [{"team_id": "team-1", "member_id": "m-tech-2", "chann_uid": "CHN-T-000002", "is_lead": True, "display_name": "วิชัย"}]
        reply, _w = await _say(client, "ทีมช่างมีใครบ้าง", {"action": "read", "entity": "team", "fields": {"scope": "technician"}, "missing": []})
        assert "แอร์" in reply.text and "หัวหน้า" in reply.text, reply.text


class TestTheWarrantyCardNamesTheSerial:
    async def test_เช็คประกัน_shows_the_serial(self):
        """customer 'เช็คประกัน ONLY00001' answered without the serial."""
        client = FakeDataClient(role="customer", permission_keys=[])
        client._warranties = [{"id": "w-1", "warranty_number": "W-2026-0001", "serial_number": "ONLY00001", "product_name": "แอร์ Daikin", "warranty_start": "2026-01-01", "warranty_end": "2027-01-01", "status": "active", "customer_chann_uid": "CHN-S-000001"}]
        reply, _w = await _say(client, "เช็คประกัน ONLY00001", {"action": "read", "entity": "warranty", "fields": {"serial_number": "ONLY00001"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        assert "ONLY00001" in reply.text and "แอร์ Daikin" in reply.text, reply.text
