"""The technician flow as the spec has it (12.4, 13.4), after the owner's
third walk on 3 Sep 2569.

"งานที่ว่าง" got "ไม่มีสิทธิ์"; a job just taken still sat in "งานที่เปิดรับ";
"ปิดงาน" was offered straight after "รับงาน" because claiming set the
ticket to in_progress — check-in never happened. And on the customer OA,
"อยากแจ้งซ่อมพัดลม อีกอัน" was saved as the street address.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402

TECH_KEYS = ["ticket.read", "ticket.update", "service_report.create", "service_report.read"]
SALES_KEYS = ["customer.read", "ticket.read", "ticket.create", "ticket.assign", "team.manage"]


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _tech(**kw):
    return _ctx(primary_role="technician", oa="technician", **kw)


def _shop_with_jobs():
    client = FakeDataClient(role="technician", permission_keys=TECH_KEYS)
    client._tickets = [
        {"id": "t1", "ticket_number": "T-2026-0001", "status": "open", "accept_status": "pending",
         "assigned_to_ref": None, "customer_name": "ก", "service_address": "99/1"},
        {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned", "accept_status": "accepted",
         "assigned_to_ref": "member-9", "customer_name": "ข", "service_address": "1/2"},
        {"id": "t3", "ticket_number": "T-2026-0003", "status": "assigned", "accept_status": "pending",
         "assigned_to_ref": "member-1", "customer_name": "ค", "service_address": "3/4"},
    ]
    return client


class TestOpenJobs:
    @pytest.mark.parametrize("phrase", ["งานที่ว่าง", "งานที่เปิด", "งานที่เปิดรับ", "มีงานไหม"])
    async def test_the_words_technicians_use_all_list_open_jobs(self, phrase):
        client = _shop_with_jobs()
        reply = await handle_chat_message(client, message=phrase, ctx=_tech())
        assert "ไม่มีสิทธิ์" not in reply.text
        assert "T-2026-0001" in reply.text

    async def test_a_job_a_colleague_took_is_not_open(self):
        client = _shop_with_jobs()
        reply = await handle_chat_message(client, message="งานที่เปิดรับ", ctx=_tech())
        assert "T-2026-0002" not in reply.text

    async def test_a_job_i_took_leaves_the_open_list(self):
        client = _shop_with_jobs()
        await handle_chat_message(client, message="รับงาน T-2026-0001", ctx=_tech())
        reply = await handle_chat_message(client, message="งานที่เปิดรับ", ctx=_tech())
        assert "T-2026-0001" not in reply.text


class TestClaimThenCheckInThenCheckOut:
    async def test_claiming_points_at_check_in(self):
        client = _shop_with_jobs()
        reply = await handle_chat_message(client, message="รับงาน T-2026-0001", ctx=_tech())
        assert "เช็คอิน" in reply.text
        assert client._tickets[0]["status"] == "assigned"

    async def test_check_out_before_check_in_is_refused_with_the_next_step(self):
        client = _shop_with_jobs()
        await handle_chat_message(client, message="รับงาน T-2026-0001", ctx=_tech())
        reply = await handle_chat_message(client, message="ปิดงาน T-2026-0001", ctx=_tech())
        assert "ยังไม่ได้เช็คอิน" in reply.text
        assert any("เช็คอิน" in send for _l, send in reply.quick_replies)
        assert not any(r[0] == "check_out_ticket" for r in client.recorded)

    async def test_after_check_in_the_report_questions_start(self):
        client = _shop_with_jobs()
        await handle_chat_message(client, message="รับงาน T-2026-0001", ctx=_tech())
        await handle_chat_message(client, message="เช็คอิน T-2026-0001", ctx=_tech())
        reply = await handle_chat_message(client, message="ปิดงาน T-2026-0001", ctx=_tech())
        assert "ยังไม่ได้เช็คอิน" not in reply.text
        assert "พบ" in reply.text or "ปัญหา" in reply.text


class TestDecline:
    async def test_declining_returns_the_job_to_cs(self):
        client = _shop_with_jobs()
        reply = await handle_chat_message(
            client, message="ปฏิเสธงาน T-2026-0003 ติดงานอื่น", ctx=_tech(),
        )
        rejected = [r for r in client.recorded if r[0] == "reject_ticket"]
        assert rejected and rejected[0][2] == "t3"
        assert "กลับไปที่ CS" in reply.text
        assert not any(r[0] == "claim_ticket" for r in client.recorded), \
            '"ปฏิเสธงาน" contains "รับงาน" and must not be read as a claim'

    async def test_the_only_pending_job_needs_no_code(self):
        client = _shop_with_jobs()
        await handle_chat_message(client, message="ไม่รับงาน", ctx=_tech())
        assert any(r[0] == "reject_ticket" for r in client.recorded)


class TestTeams:
    def _cs(self):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        client._members = [
            {"id": "m-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"},
            {"id": "m-2", "chann_uid": "CHN-T-000002", "role": "technician", "status": "active"},
        ]
        client._profiles = {
            "CHN-T-000001": {"first_name": "สมศักดิ์", "last_name": "ช่างดี"},
            "CHN-T-000002": {"first_name": "สมหญิง", "last_name": "ซ่อมเก่ง"},
        }
        client._teams = []
        return client

    async def test_create_add_lead_list_remove(self):
        client = self._cs()
        reply = await handle_chat_message(client, message="สร้างทีมช่าง แอร์", ctx=_ctx())
        assert "แอร์" in reply.text and client._teams
        reply = await handle_chat_message(client, message="เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า", ctx=_ctx())
        added = [r for r in client.recorded if r[0] == "add_team_member"]
        assert added and added[0][3] == "m-1" and added[0][4] is True
        assert "สมศักดิ์" in reply.text
        await handle_chat_message(client, message="เพิ่ม สมหญิง เข้าทีม แอร์", ctx=_ctx())
        reply = await handle_chat_message(client, message="ทีมช่าง", ctx=_ctx())
        assert "สมศักดิ์" in reply.text and "หัวหน้า" in reply.text and "สมหญิง" in reply.text
        await handle_chat_message(client, message="เอา สมหญิง ออกจากทีม แอร์", ctx=_ctx())
        assert any(r[0] == "remove_team_member" for r in client.recorded)

    async def test_an_unknown_team_is_named_back(self):
        client = self._cs()
        reply = await handle_chat_message(client, message="เพิ่ม สมศักดิ์ เข้าทีม ไฟฟ้า", ctx=_ctx())
        assert "ไฟฟ้า" in reply.text and not any(r[0] == "add_team_member" for r in client.recorded)

    async def test_without_team_manage_nothing_changes(self):
        client = self._cs()
        client._permission_keys = ["ticket.read"]
        reply = await handle_chat_message(
            client, message="สร้างทีมช่าง แอร์",
            ctx=_ctx(),
        )
        # The fake's permission set is what authorization_context returns.
        assert client._teams == [] or "ยังไม่มีสิทธิ์" in reply.text


class TestLanguage:
    async def test_switching_is_stored_against_the_person(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        reply = await handle_chat_message(client, message="เปลี่ยนภาษาเป็นอังกฤษ", ctx=_ctx())
        stored = [r for r in client.recorded if r[0] == "set_display_preferences"]
        assert stored and stored[0][2] == {"language": "en"}
        assert "Switched to English" in reply.text

    async def test_a_customer_can_switch_too(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await handle_chat_message(
            client, message="switch to english", ctx=_ctx(primary_role="customer", oa="customer"),
        )
        assert "English" in reply.text


class TestCustomerAddressStep:
    def _customer(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        client._warranties = [{"id": "w-1", "serial_number": "ONLY00001", "product_name": "พัดลม",
                               "status": "active", "customer_chann_uid": "CHN-S-000001"}]
        return client

    async def test_a_second_report_is_not_saved_as_the_address(self):
        client = self._customer()
        ctx = _ctx(primary_role="customer", oa="customer")
        await handle_chat_message(client, message="พัดลมไม่แรง", ctx=ctx)
        reply = await handle_chat_message(client, message="อยากแจ้งซ่อมพัดลม อีกอัน", ctx=ctx)
        updates = [r for r in client.recorded if r[0] == "update_ticket"]
        assert not any(u[3].get("service_address") for u in updates), "the sentence became the street"
        assert "อาการ" in reply.text or "T-2026-0002" in reply.text

    async def test_report_plus_placeholder_asks_for_symptoms(self):
        client = self._customer()
        ctx = _ctx(primary_role="customer", oa="customer")
        await handle_chat_message(client, message="พัดลมไม่แรง", ctx=ctx)
        reply = await handle_chat_message(client, message="แจ้งซ่อม อันใหม่", ctx=ctx)
        created = [r for r in client.recorded if r[0] == "create_ticket"]
        assert len(created) == 1, 'a ticket called "แจ้งซ่อม อันใหม่" was filed'
        assert "อาการ" in reply.text

    async def test_a_real_address_is_still_saved(self):
        client = self._customer()
        ctx = _ctx(primary_role="customer", oa="customer")
        await handle_chat_message(client, message="พัดลมไม่แรง", ctx=ctx)
        reply = await handle_chat_message(client, message="99/1 ถ.สุขุมวิท", ctx=ctx)
        updates = [r for r in client.recorded if r[0] == "update_ticket"]
        assert updates and updates[-1][3]["service_address"] == "99/1 ถ.สุขุมวิท"
        assert "วันไหน" in reply.text
