"""Round 18d audit-verify fixes (15 ก.ย. 2569), items 21–27, held as tests.

Each class pins one item of the audit: the sentence, what the model read,
and what the data tier was asked to do — never merely "no exception". Where
it is cheap, the opposite sentence proves the fix did not over-reach.

  21. Team membership verbs: "เอา … ออกจากทีม" removes, "… เป็นหัวหน้า" adds
      as lead, "ลบทีม" (read as archive/team) deletes the team.
  22. "เปิดดีล D-… ใหม่" comes back action=reopen: the stage change to new
      under deal.reopen, refused without it.
  23. A bare "อนุมัติ" read as read/approval acts on the one report waiting,
      or asks which — it does not list.
  24. A customer's warranty rows carry the serial: "(S/N …)".
  25. "เขามีนัดกี่นัด" after a card is that person's diary, not the shop's
      work list.
  26. "ลูกค้าคนนี้สนใจ GG001" points at the customer even with a deal in
      view: a deal offer, nothing written on the deal.
  27. _reminder_subject strips the new fillers (เดือนนี้, Thai number words,
      pronouns, English weekdays, "remind").
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.thai_datetime import local_today  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx  # noqa: E402
from test_phase14_chat import _shop, _submit, pushes  # noqa: E402, F401

UID = "CHN-S-000001"
CUSTOMER = {
    "id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
    "last_name": "ใจดี", "phone": "0812345678", "stage": "lead",
}
OTHER_CUSTOMER = {
    "id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมหญิง",
    "last_name": "รักดี", "phone": "0898765432", "stage": "lead",
}


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _reading(**intent) -> httpx.AsyncClient:
    """What the model would return, as the transport the router calls."""
    return httpx.AsyncClient(transport=_ai(json.dumps({"missing": [], **intent})))


def _writes(client: FakeDataClient, *names: str) -> list[tuple]:
    return [r for r in client.recorded if r[0] in names]


# --------------------------------------------------------------- item 21


def _team_shop() -> FakeDataClient:
    """An owner holding team.manage, one technician วิชัย, one team แอร์."""
    client = FakeDataClient(permission_keys=["team.manage", "ticket.read"])
    client._is_owner = True
    client._members = [
        {"id": "m-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"},
    ]
    client._profiles = {"CHN-T-000001": {"first_name": "วิชัย", "last_name": "ช่างดี"}}
    client._teams = [{"id": "team-1", "team_name": "แอร์"}]
    client._team_members = []
    return client


class TestTeamMembershipVerbsAreKept:
    @pytest.mark.asyncio
    async def test_out_of_the_team_removes_the_member(self):
        """Item 21: update/team read for "เอา วิชัย ออกจากทีม แอร์" REMOVES, not adds."""
        client = _team_shop()
        client._team_members = [{"id": "m-1", "chann_uid": "CHN-T-000001", "team_id": "team-1",
                                 "member_id": "m-1", "is_lead": False}]
        reply = await handle_chat_message(
            client, message="เอา วิชัย ออกจากทีม แอร์", ctx=_ctx(),
            ai_client=_reading(action="update", entity="team", fields={"name": "แอร์", "members": "วิชัย"}),
        )
        assert ("remove_team_member", LICENSE_ID, "team-1", "m-1") in client.recorded, client.recorded
        assert not _writes(client, "add_team_member"), client.recorded
        assert client._team_members == []
        assert reply.text == "เอา วิชัย ช่างดี ออกจากทีม แอร์ แล้ว", reply.text

    @pytest.mark.asyncio
    async def test_into_the_team_as_lead_adds_the_lead(self):
        """Item 21: the same update/team reading for "เพิ่ม … เข้าทีม … เป็นหัวหน้า" adds as lead."""
        client = _team_shop()
        reply = await handle_chat_message(
            client, message="เพิ่ม วิชัย เข้าทีม แอร์ เป็นหัวหน้า", ctx=_ctx(),
            ai_client=_reading(action="update", entity="team", fields={"name": "แอร์", "members": "วิชัย"}),
        )
        assert ("add_team_member", LICENSE_ID, "team-1", "m-1", True) in client.recorded, client.recorded
        assert not _writes(client, "remove_team_member")
        assert [m["member_id"] for m in client._team_members] == ["m-1"]
        assert client._team_members[0]["is_lead"] is True
        assert reply.text == "เพิ่ม วิชัย ช่างดี เข้าทีม แอร์ เป็นหัวหน้า แล้ว", reply.text

    @pytest.mark.asyncio
    async def test_into_the_team_without_the_lead_word_is_a_plain_add(self):
        """Item 21 (opposite): "เพิ่ม วิชัย เข้าทีม แอร์" adds, and not as lead."""
        client = _team_shop()
        reply = await handle_chat_message(
            client, message="เพิ่ม วิชัย เข้าทีม แอร์", ctx=_ctx(),
            ai_client=_reading(action="update", entity="team", fields={"name": "แอร์", "members": "วิชัย"}),
        )
        assert ("add_team_member", LICENSE_ID, "team-1", "m-1", False) in client.recorded, client.recorded
        assert not _writes(client, "remove_team_member")
        assert reply.text == "เพิ่ม วิชัย ช่างดี เข้าทีม แอร์ แล้ว", reply.text

    @pytest.mark.asyncio
    async def test_archive_team_deletes_the_team(self):
        """Item 21: "ลบทีม แอร์" read as archive/team deletes the team (the gate knows archive)."""
        client = _team_shop()
        reply = await handle_chat_message(
            client, message="ลบทีม แอร์", ctx=_ctx(),
            ai_client=_reading(action="archive", entity="team", fields={"name": "แอร์"}),
        )
        assert ("delete_technician_team", LICENSE_ID, "team-1") in client.recorded, client.recorded
        assert client._teams == []
        assert reply.text == "ลบทีม แอร์ แล้ว", reply.text


# --------------------------------------------------------------- item 22


def _lost_deal_shop(keys: list[str]) -> FakeDataClient:
    return FakeDataClient(
        permission_keys=keys, customers=[CUSTOMER],
        deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "lost", "contact_id": "CUST-1",
                "notes": None, "products": [], "owner_member_id": None}],
    )


class TestReopenIsTheStageChangeToNew:
    @pytest.mark.asyncio
    async def test_reopen_with_the_permission_moves_the_deal_to_new(self):
        """Item 22: action=reopen on a lost deal, with deal.reopen, records the stage change to new."""
        client = _lost_deal_shop(["deal.reopen", "deal.read"])
        reply = await handle_chat_message(
            client, message="เปิดดีล D-2026-0001 ใหม่", ctx=_ctx(),
            ai_client=_reading(action="reopen", entity="deal", fields={"code": "D-2026-0001"}),
        )
        assert _writes(client, "transition_deal_stage") == [
            ("transition_deal_stage", LICENSE_ID, "DEAL-1", "new", True, UID, None),
        ], client.recorded
        assert client._deals[0]["stage"] == "new"
        assert reply.text == "อัปเดตดีล D-2026-0001 เป็นสถานะ ใหม่ เรียบร้อยแล้ว", reply.text

    @pytest.mark.asyncio
    async def test_reopen_without_the_permission_is_refused_and_writes_nothing(self):
        """Item 22 (opposite): without deal.reopen the same reading is refused; the deal stays lost."""
        client = _lost_deal_shop(["deal.read", "deal.update"])
        reply = await handle_chat_message(
            client, message="เปิดดีล D-2026-0001 ใหม่", ctx=_ctx(),
            ai_client=_reading(action="reopen", entity="deal", fields={"code": "D-2026-0001"}),
        )
        assert "คุณยังไม่มีสิทธิ์ทำสิ่งนี้" in reply.text, reply.text
        assert not _writes(client, "transition_deal_stage", "update_deal", "archive_deal"), client.recorded
        assert client._deals[0]["stage"] == "lost"


# --------------------------------------------------------------- item 23


class TestABareApproveAnswersTheNotification:
    def _ai(self) -> httpx.AsyncClient:
        return _reading(action="read", entity="approval", fields={})

    @pytest.mark.asyncio
    async def test_one_report_waiting_is_approved_not_listed(self, pushes):
        """Item 23: "อนุมัติ" read as read/approval with one report waiting records the decision."""
        c = _shop()
        await _submit(c)
        reply = await handle_chat_message(
            c, message="อนุมัติ", ctx=_ctx(primary_role="cs", oa="sales"), ai_client=self._ai(),
        )
        acted = _writes(c, "act_on_approval_step")
        assert acted == [("act_on_approval_step", LICENSE_ID, "step-sr-1-1", True, None)], c.recorded
        assert c._reports[0]["status"] == "approved"
        assert reply.text.startswith("อนุมัติ SR-2026-0001 แล้ว"), reply.text
        assert "รายงานที่รอคุณตรวจ" not in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_two_reports_waiting_asks_which(self, pushes):
        """Item 23: with two reports waiting the bare "อนุมัติ" asks which, with a button per report."""
        c = _shop()
        c._tickets.append({**c._tickets[0], "id": "t2", "ticket_number": "T-2026-0002",
                           "customer_name": "สมศักดิ์", "customer_chann_uid": "CHN-C-2"})
        c._reports.append({**c._reports[0], "id": "sr-2", "report_id": "SR-2026-0002", "ticket_id": "t2"})
        from chann_app.services import approval as approval_service
        await approval_service.on_report_submitted(c, license_id=LICENSE_ID, report=c._reports[0])
        await approval_service.on_report_submitted(c, license_id=LICENSE_ID, report=c._reports[1])
        reply = await handle_chat_message(
            c, message="อนุมัติ", ctx=_ctx(primary_role="cs", oa="sales"), ai_client=self._ai(),
        )
        assert not _writes(c, "act_on_approval_step"), c.recorded
        assert reply.text.startswith("มีหลายรายการรออยู่ เลือกรายงานที่ต้องการครับ"), reply.text
        assert [s for _, s in reply.quick_replies] == ["อนุมัติ SR-2026-0001", "อนุมัติ SR-2026-0002"]
        assert all(r["status"] == "submitted" for r in c._reports)

    @pytest.mark.asyncio
    async def test_a_question_with_the_word_in_it_still_lists(self, pushes):
        """Item 23 (opposite): "มีอะไรรออนุมัติบ้าง" read the same way is the list, not a decision."""
        c = _shop()
        await _submit(c)
        reply = await handle_chat_message(
            c, message="มีอะไรรออนุมัติบ้าง", ctx=_ctx(primary_role="cs", oa="sales"), ai_client=self._ai(),
        )
        assert not _writes(c, "act_on_approval_step"), c.recorded
        assert reply.text.startswith("รายงานที่รอคุณตรวจ:"), reply.text
        assert "SR-2026-0001" in reply.text
        assert c._reports[0]["status"] == "submitted"


# --------------------------------------------------------------- item 24


class TestWarrantyRowsCarryTheSerial:
    @pytest.mark.asyncio
    async def test_product_and_serial_are_both_on_the_row(self):
        """Item 24: on the customer OA each warranty row shows "<product> (S/N <serial>)"."""
        client = FakeDataClient(role="customer", permission_keys=[])
        client._warranties = [
            {"warranty_number": "W-2026-0001", "product_name": "แอร์ 12000 BTU", "serial_number": "SN12345678",
             "status": "active", "customer_chann_uid": UID, "warranty_end": "2027-09-15"},
            {"warranty_number": "W-2026-0002", "product_name": "ตู้เย็น 2 ประตู", "serial_number": "SN22222222",
             "status": "expired", "customer_chann_uid": UID, "warranty_end": None},
            {"warranty_number": "W-2026-0003", "product_name": None, "serial_number": "SN33333333",
             "status": "active", "customer_chann_uid": UID, "warranty_end": None},
        ]
        reply = await handle_chat_message(
            client, message="รายการประกัน", ctx=_ctx(oa="customer", primary_role="customer"),
        )
        lines = reply.text.split("\n")
        assert lines[0] == "สินค้าที่ลงทะเบียนไว้:", reply.text
        assert lines[1].startswith("· W-2026-0001 แอร์ 12000 BTU (S/N SN12345678) — ยังอยู่ในประกัน · ถึง "), reply.text
        assert lines[2] == "· W-2026-0002 ตู้เย็น 2 ประตู (S/N SN22222222) — หมดประกันแล้ว", reply.text
        # No product name: the bare serial, not "(S/N …)" around nothing.
        assert lines[3] == "· W-2026-0003 SN33333333 — ยังอยู่ในประกัน", reply.text
        assert reply.text.count("(S/N") == 2


# --------------------------------------------------------------- item 25


def _diary_shop() -> FakeDataClient:
    client = FakeDataClient(
        permission_keys=["followup.read", "customer.read", "deal.read"],
        customers=[CUSTOMER, OTHER_CUSTOMER],
    )
    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    client._follow_ups = [
        {"id": "FU-1", "entity_type": "customer", "entity_id": "CUST-1", "notes": "โทรนัดติดตั้งแอร์",
         "due_date": tomorrow, "due_time": "10:00", "status": "pending", "owner_chann_uid": UID},
        {"id": "FU-2", "entity_type": "customer", "entity_id": "CUST-2", "notes": "ส่งใบเสนอราคาพัดลม",
         "due_date": tomorrow, "due_time": "14:00", "status": "pending", "owner_chann_uid": UID},
    ]
    return client


class TestTheirAppointmentsAreThatPersonsDiary:
    def _ai(self) -> httpx.AsyncClient:
        return _reading(action="read", entity="report", fields={"type": "agenda"})

    @pytest.mark.asyncio
    async def test_after_a_card_the_pronoun_lists_that_customers_reminders(self):
        """Item 25: "เขามีนัดกี่นัด" with a customer in context is that customer's diary."""
        client = _diary_shop()
        await client.set_last_entity_ref(UID, "sales", license_id=LICENSE_ID, entity_type="customer",
                                         entity_id="CUST-1", code="C-2026-0001")
        reply = await handle_chat_message(client, message="เขามีนัดกี่นัด", ctx=_ctx(), ai_client=self._ai())
        assert reply.text.startswith("นัดหมายของ C-2026-0001 — 1 รายการ"), reply.text
        assert "สมชาย ใจดี (C-2026-0001) — โทรนัดติดตั้งแอร์" in reply.text, reply.text
        assert "ส่งใบเสนอราคาพัดลม" not in reply.text, reply.text
        assert "งานที่ต้องติดตาม" not in reply.text, reply.text
        assert not _writes(client, "due_follow_ups"), client.recorded

    @pytest.mark.asyncio
    async def test_after_viewing_the_customer_the_pronoun_follows_the_card(self):
        """Item 25: "ดูลูกค้า สมชาย" then "เขามีนัดกี่นัด" — the card put สมชาย in context."""
        client = _diary_shop()
        card = await handle_chat_message(
            client, message="ดูลูกค้า สมชาย", ctx=_ctx(),
            ai_client=_reading(action="read", entity="customer", fields={"target_name": "สมชาย"}),
        )
        assert "C-2026-0001" in card.text, card.text
        reply = await handle_chat_message(client, message="เขามีนัดกี่นัด", ctx=_ctx(), ai_client=self._ai())
        assert reply.text.startswith("นัดหมายของ C-2026-0001 — 1 รายการ"), reply.text
        assert "โทรนัดติดตั้งแอร์" in reply.text and "ส่งใบเสนอราคาพัดลม" not in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_without_a_person_the_same_reading_is_the_work_list(self):
        """Item 25 (opposite): "มีนัดกี่นัด" with nobody in context is the shop's work list."""
        client = _diary_shop()
        reply = await handle_chat_message(client, message="มีนัดกี่นัด", ctx=_ctx(), ai_client=self._ai())
        assert ("due_follow_ups", LICENSE_ID, 7) in client.recorded, client.recorded
        assert reply.text.startswith("งานที่ต้องติดตาม:"), reply.text
        assert "นัดหมายของ" not in reply.text, reply.text


# --------------------------------------------------------------- item 26


def _deal_in_view_shop() -> FakeDataClient:
    client = FakeDataClient(
        permission_keys=["deal.create", "deal.update", "deal.read", "customer.read"],
        customers=[CUSTOMER],
        deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "new", "contact_id": "CUST-1",
                "notes": None, "products": []}],
    )
    client._products = [
        {"id": "P-1", "product_id": "GG001", "product_name": "GG001 พัดลมตั้งพื้น 16 นิ้ว",
         "unit_price": "1500", "archived_at": None},
    ]
    return client


class TestThisCustomerIsInterestedPointsAtTheCustomer:
    def _ai(self) -> httpx.AsyncClient:
        return _reading(action="create", entity="line_item", fields={"target_name": "GG001", "qty": 1})

    async def _in_context(self, client: FakeDataClient) -> None:
        await client.set_last_customer_ref(UID, "sales", license_id=LICENSE_ID, customer_id="CUST-1", name="สมชาย ใจดี")
        await client.set_last_entity_ref(UID, "sales", license_id=LICENSE_ID, entity_type="deal",
                                         entity_id="DEAL-1", code="D-2026-0001")

    @pytest.mark.asyncio
    async def test_the_deal_in_view_gets_nothing_and_a_deal_is_offered(self):
        """Item 26: "ลูกค้าคนนี้สนใจ GG001" with a deal in view offers a deal for the customer."""
        client = _deal_in_view_shop()
        await self._in_context(client)
        reply = await handle_chat_message(client, message="ลูกค้าคนนี้สนใจ GG001", ctx=_ctx(), ai_client=self._ai())
        assert reply.text == "ต้องการสร้างดีลสำหรับ สมชาย ใจดี และเพิ่มGG001ใช่ไหมครับ?", reply.text
        assert [s for _, s in reply.quick_replies] == ["ใช่", "ไม่ใช่"], reply.quick_replies
        assert not _writes(client, "add_deal_product", "update_deal", "create_deal"), client.recorded
        assert client._deals[0]["products"] == []
        pending = await client.get_pending_intent(UID, "sales")
        assert pending and pending["entity"] == "deal_item_confirm", pending
        assert pending["fields"]["contact"]["id"] == "CUST-1" and pending["fields"]["item"] == "GG001", pending

    @pytest.mark.asyncio
    async def test_a_plain_add_still_lands_on_the_deal_in_view(self):
        """Item 26 (opposite): "เพิ่ม GG001 1 ตัว" with the same context goes on the deal, no offer."""
        client = _deal_in_view_shop()
        await self._in_context(client)
        reply = await handle_chat_message(client, message="เพิ่ม GG001 1 ตัว", ctx=_ctx(), ai_client=self._ai())
        added = _writes(client, "add_deal_product")
        assert added and added[0][2] == "DEAL-1", (client.recorded, reply.text)
        assert "ต้องการสร้างดีลสำหรับ" not in reply.text, reply.text
        pending = await client.get_pending_intent(UID, "sales")
        assert not (pending and pending.get("entity") == "deal_item_confirm"), pending


# --------------------------------------------------------------- item 27


class TestReminderSubjectFillers:
    def test_this_month_is_not_the_subject(self):
        """Item 27: "เดือนนี้" (and the old "ลูกค้า" filler) leave only the verb."""
        assert chat._reminder_subject("เตือนเดือนนี้ โทรหาลูกค้า", "") == "โทรหา"

    def test_english_weekday_and_remind_are_not_the_subject(self):
        """Item 27: "remind me monday …" keeps the errand, drops the day and the verb."""
        subject = chat._reminder_subject("remind me monday call the customer", "")
        assert subject == "call the customer", subject
        assert "monday" not in subject and "remind" not in subject

    def test_thai_number_words_pronouns_and_this_deal_are_fillers(self):
        """Item 27: "สอง", "เขา", "ดีลนี้", "next week" are stripped; the errand survives."""
        assert chat._reminder_subject("เตือนสองทุ่ม โทรหาลูกค้า", "") == "โทรหา"
        assert chat._reminder_subject("เตือนเขาพรุ่งนี้ นัดติดตั้ง", "") == "ติดตั้ง"
        assert chat._reminder_subject("เตือน ดีลนี้ พรุ่งนี้ ส่งใบเสนอราคา", "") == "ส่งใบเสนอราคา"
        assert chat._reminder_subject("เตือน next week send the quote", "") == "send the quote"

    def test_a_real_subject_is_kept_whole(self):
        """Item 27: a subject with no filler comes back untouched; a clock suffix ("10am") is not part of it."""
        assert chat._reminder_subject("เตือนพรุ่งนี้ ส่งใบเสนอราคา", "") == "ส่งใบเสนอราคา"
        assert chat._reminder_subject("remind me tomorrow 10am send the quote", "") == "send the quote"
