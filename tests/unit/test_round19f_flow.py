"""Round 19f — a customer's report waits for the shop (owner, 16 ก.ย. 2569):
technicians do not see it until CS assigns it or opens it to all of them;
CS hears again when the customer completes the report. The model is
stubbed as DEV's model reads the sentences (ask-model.py)."""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.data_client import DataTierError
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "ticket.assign", "ticket.close"]
CUSTOMER = dict(oa="customer", primary_role="customer")
TECH = dict(oa="technician", primary_role="technician")
SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}
RELEASE_READING = {"action": "assign", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": ["target_name"]}
MEMBERS = [
    {"id": "MEMBER-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales", "permission_keys": SHOP_KEYS},
    {"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "display_name": "สมศักดิ์", "channel": "technician"},
    {"id": "m-tech-2", "chann_uid": "CHN-T-000002", "role": "technician", "status": "active", "display_name": "วิชัย", "channel": "technician"},
]
HELD = {
    "id": "t5", "ticket_number": "T-2026-0005", "status": "open", "accept_status": "pending", "visibility": "private",
    "assigned_to_ref": None, "assigned_target_type": None, "customer_chann_uid": "CHN-S-000009",
    "customer_name": "สมชาย ใจดี", "customer_phone": "0812345678", "service_address": "99/1",
    "issue_description": "พัดลมไม่หมุน", "scheduled_date": "2099-01-20", "scheduled_time": "10:00",
}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _shop(tickets=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS])
    client._tickets = [dict(t) for t in (tickets or [])]
    client._members = [dict(m) for m in MEMBERS]
    return client


class TestACustomersReportWaitsForTheShop:
    async def test_the_chat_report_is_private_until_the_shop_acts(self):
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
        await _say(client, "พัดลมไม่หมุน เสียงดัง", {"action": "create", "entity": "ticket", "fields": {"issue_description": "พัดลมไม่หมุน เสียงดัง"}, "missing": []}, ctx=_ctx(**CUSTOMER))
        reply, calls = await _say(client, "ไม่มีหมายเลขเครื่อง", SUGGEST, ctx=_ctx(**CUSTOMER))
        created = next((c for c in calls if c[0] == "create_ticket"), None)
        assert created is not None, reply.text
        assert created[2].get("visibility") == "private", created[2]

    async def test_a_technician_cannot_take_a_held_job(self):
        """A held job is not in the technician's visible list at all, so the
        answer is the deliberate "ไม่พบ" of the privacy rule — the same words
        as a typo, so a technician cannot discover a job by guessing codes
        (test_phase6_chat: claiming an invisible ticket does not confirm it
        exists). Nothing is claimed."""
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.update"])
        client._tickets = [dict(HELD)]
        reply, calls = await _say(client, "รับงาน T-2026-0005", {"action": "claim", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": []}, ctx=_ctx(**TECH))
        assert not [c for c in calls if c[0] == "claim_ticket"], reply.text
        assert "ไม่พบ" in reply.text, reply.text


class TestTheShopOpensAJobToTheTechnicians:
    async def test_the_model_reading_missing_a_name_is_a_release(self):
        client = _shop([HELD])
        reply, calls = await _say(client, "เปิดให้ช่างรับ T-2026-0005", RELEASE_READING)
        assert [c for c in calls if c[0] == "release_ticket"], reply.text
        assert "เปิดงาน T-2026-0005 ให้ช่างรับแล้ว" in reply.text and "แจ้งช่าง 2 คน" in reply.text, reply.text
        # Only the technicians' notice; the other dispatchers get their own
        # "this one is handled" line since round 19p.
        told = [c for c in calls if c[0] == "create_notification" and "ticket_released" in str(c)]
        assert len(told) == 2 and all("รับงาน T-2026-0005" in str(c) for c in told), told
        assert not [c for c in calls if c[0] == "set_pending_intent"], "no 'ให้ใคร' question"

    async def test_the_typed_sentence_works_when_the_model_shrugs(self):
        client = _shop([HELD])
        reply, calls = await _say(client, "ปล่อยงาน T-2026-0005 ให้ช่างรับ", SUGGEST)
        assert [c for c in calls if c[0] == "release_ticket"], reply.text

    async def test_the_dashboard_and_chat_share_the_completeness_gate(self):
        client = _shop([dict(HELD, service_address=None)])
        client._dispatch_error = {"error": "dispatch_blocked", "missing": ["ที่อยู่"]}
        reply, calls = await _say(client, "เปิดให้ช่างรับ T-2026-0005", RELEASE_READING)
        assert "ยังมอบหมายไม่ได้" in reply.text and "ที่อยู่" in reply.text, reply.text
        assert not [c for c in calls if c[0] == "create_notification"]

    async def test_an_accepted_job_is_not_reopened(self):
        client = _shop([dict(HELD, accept_status="accepted", assigned_to_ref="m-tech-1", assigned_to_name="สมศักดิ์", status="assigned")])
        reply, calls = await _say(client, "เปิดให้ช่างรับ T-2026-0005", RELEASE_READING)
        assert "มีช่างรับแล้ว" in reply.text and "สมศักดิ์" in reply.text, reply.text
        assert not [c for c in calls if c[0] == "release_ticket"]

    async def test_naming_a_technician_is_still_an_assignment(self):
        client = _shop([HELD])
        reply, calls = await _say(client, "มอบหมาย T-2026-0005 ให้ สมศักดิ์", {"action": "assign", "entity": "ticket", "fields": {"code": "T-2026-0005", "target_name": "สมศักดิ์"}, "missing": []})
        assert not [c for c in calls if c[0] == "release_ticket"] and "ให้ช่างรับแล้ว" not in reply.text, reply.text

    async def test_without_ticket_assign_the_release_is_refused(self):
        client = FakeDataClient(permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS])
        client._tickets = [dict(HELD)]
        reply, calls = await _say(client, "เปิดให้ช่างรับ T-2026-0005", RELEASE_READING)
        assert not [c for c in calls if c[0] == "release_ticket"], reply.text


class TestTheShopHearsWhenTheReportIsComplete:
    async def test_the_appointment_completes_the_report_and_the_dispatchers_hear(self, monkeypatch):
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
        client._members = [dict(MEMBERS[0], chann_uid="CHN-S-000777"), *[dict(m) for m in MEMBERS[1:]]]
        plain = client.authorization_context

        async def context_of(license_id, chann_uid, channel="sales"):
            if chann_uid == "CHN-S-000777":
                return None  # the owner falls back to role — a dispatcher
            return await plain(license_id, chann_uid, channel)
        monkeypatch.setattr(client, "authorization_context", context_of)
        client._tickets = [dict(HELD, customer_chann_uid="CHN-S-000001", scheduled_date=None, scheduled_time=None, service_address="99/1 ถ.สุขุมวิท")]
        await client.set_pending_intent("CHN-S-000001", "customer", action="report", entity="customer_ticket", fields={"ticket_id": "t5", "code": "T-2026-0005"}, missing=["schedule"], ttl_seconds=3600)
        reply, calls = await _say(client, "พรุ่งนี้ 10 โมง", SUGGEST, ctx=_ctx(**CUSTOMER))
        assert [c for c in calls if c[0] == "update_ticket"], reply.text
        told = [c for c in calls if c[0] == "create_notification" and "ลูกค้าแจ้งข้อมูลครบแล้ว" in str(c)]
        assert told, (reply.text, calls)
        assert "พัดลมไม่หมุน" in str(told[0]) and "เปิดให้ช่างรับ T-2026-0005" in str(told[0]), told[0]
        # only those who dispatch, not the technicians
        assert all("CHN-T-" not in str(c[2]) for c in told), told


class TestTheOwnerCanUnstickAnApproval:
    """Owner, 16 ก.ย. 2569: after the technician closed a job, "อนุมัติ" in the sales
    OA and the dashboard's approve did nothing — the report's step waited on one
    named member. The owner (or approval.manage) now sees and acts on any step."""

    REPORT = {"id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t5", "status": "submitted", "report_data": {"found_issue": "ฟิวส์ขาด", "work_done": "เปลี่ยนฟิวส์"}}
    STEP = {"id": "step-r1-1", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1", "step_order": 1,
            "approver_type": "user", "approver_ref": "member-9", "status": "pending", "acted_by": None, "acted_at": None, "reason": None}
    APPROVE = {"action": "approve", "entity": "approval", "fields": {"code": "SR-2026-0001"}, "missing": []}

    def _client(self, *, owner: bool, keys=None):
        client = FakeDataClient(permission_keys=keys or [*SHOP_KEYS, "approval.view", "approval.approve", "service_report.read"], customers=[dict(c) for c in CUSTOMERS])
        client._tickets = [dict(HELD, status="completed", assigned_to_ref="m-tech-1", accept_status="accepted", owner_member_id="member-9")]
        client._members = [dict(m) for m in MEMBERS] + [{"id": "member-9", "chann_uid": "CHN-S-000009", "role": "cs", "status": "active", "display_name": "พี่ซีเอส", "channel": "sales"}]
        client._reports = [dict(self.REPORT)]
        client._approval_state()
        client._approval_steps = [dict(self.STEP)]
        client._is_owner = owner
        client._role = "owner" if owner else "cs"
        client._member_id = "MEMBER-1"
        return client

    async def test_the_owner_sees_and_approves_someone_elses_step(self):
        client = self._client(owner=True)
        listed, _ = await _say(client, "รายการรออนุมัติ", {"action": "read", "entity": "approval", "fields": {}, "missing": []})
        assert "SR-2026-0001" in listed.text, listed.text
        reply, calls = await _say(client, "อนุมัติ SR-2026-0001", self.APPROVE)
        acted = [c for c in calls if c[0] == "act_on_approval_step"]
        assert acted and acted[0][3] is True, (reply.text, calls)
        assert "อนุมัติ SR-2026-0001 แล้ว" in reply.text and "แทนขั้นของ พี่ซีเอส" in reply.text, reply.text
        assert client._approval_steps[0]["status"] == "approved"

    async def test_a_cs_without_the_key_is_still_told_it_is_not_theirs(self):
        client = self._client(owner=False)
        reply, calls = await _say(client, "อนุมัติ SR-2026-0001", self.APPROVE)
        assert not [c for c in calls if c[0] == "act_on_approval_step"], reply.text
        assert "ไม่มีรายงาน SR-2026-0001 ที่รอคุณอนุมัติ" in reply.text, reply.text

    async def test_approval_manage_is_enough_without_being_the_owner(self):
        client = self._client(owner=False, keys=[*SHOP_KEYS, "approval.view", "approval.approve", "approval.manage", "service_report.read"])
        reply, calls = await _say(client, "อนุมัติ SR-2026-0001", self.APPROVE)
        assert [c for c in calls if c[0] == "act_on_approval_step"], reply.text
