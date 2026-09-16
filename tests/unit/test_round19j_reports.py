"""Round 19j — what the shop sees after a service report is approved.

Owner, 16 ก.ย. 2569: "ถ้าออกเอกสารแล้วเปลี่ยนเป็นคำว่าดูเอกสารแทน ตอนนี้เป็น
ออก PDF อย่างเดียว" and "หลังจากอนุมัติแล้วมีแค่ให้กดออก PDF แต่ไม่เห็นมีแบบ
ประเมินส่งไปให้ลูกค้าเลย". The DEV log showed why the survey never went: the
shop's workflow had a second step whose role nobody holds, so the report stayed
`submitted` — approved, in the person's mind, but not to the system.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "approval.view", "approval.approve", "approval.reject", "service_report.read", "ticket.assign"]
TICKET = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "completed", "accept_status": "accepted",
    "assigned_to_ref": "m-tech-1", "assigned_target_type": "technician", "customer_chann_uid": "CHN-S-000009",
    "customer_name": "สมชาย ใจดี", "issue_description": "แอร์ไม่เย็น", "service_address": "99/1",
    "owner_member_id": "member-9", "visibility": "public",
}
REPORT = {"id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
          "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"}, "generated_document_id": None}
READ_REPORT = {"action": "read", "entity": "service_report", "fields": {"code": "SR-2026-0001"}, "missing": []}
APPROVE = {"action": "approve", "entity": "approval", "fields": {"code": "SR-2026-0001"}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _shop(report=REPORT, steps=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS])
    client._tickets = [dict(TICKET)]
    client._reports = [dict(report)]
    client._members = [
        {"id": "MEMBER-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales"},
        {"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "display_name": "สมศักดิ์", "channel": "technician"},
    ]
    client._line_targets = {"CHN-S-000009": "line-customer", "CHN-S-000001": "line-owner"}
    client._is_owner = True
    client._role = "owner"
    client._member_id = "MEMBER-1"
    client._approval_state()
    client._approval_steps = list(steps if steps is not None else [
        {"id": "step-1", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1", "step_order": 1,
         "approver_type": "user", "approver_ref": "member-9", "status": "pending", "acted_by": None, "acted_at": None, "reason": None},
    ])
    return client


class TestTheDocumentButtonSaysWhatItDoes:
    async def test_before_it_is_issued_the_button_offers_to_issue(self):
        client = _shop(report=dict(REPORT, status="approved"))
        reply, _ = await _say(client, "ข้อมูลรายงาน SR-2026-0001", READ_REPORT)
        assert [label for label, _send in reply.quick_replies] == ["ออกเอกสาร"], reply.quick_replies

    async def test_once_issued_the_button_says_view(self):
        client = _shop(report=dict(REPORT, status="approved", generated_document_id="doc-1"))
        reply, _ = await _say(client, "ข้อมูลรายงาน SR-2026-0001", READ_REPORT)
        assert [label for label, _send in reply.quick_replies] == ["ดูเอกสาร"], reply.quick_replies

    async def test_a_report_still_waiting_offers_the_approval_buttons(self):
        client = _shop()
        reply, _ = await _say(client, "ข้อมูลรายงาน SR-2026-0001", READ_REPORT)
        assert [label for label, _send in reply.quick_replies] == ["อนุมัติ", "ไม่อนุมัติ"], reply.quick_replies


class TestApprovingTellsYouWhatIsLeft:
    async def test_a_step_that_remains_is_offered_as_the_next_tap(self):
        """Two steps: approving the first leaves the report submitted, and the
        owner may approve the second (round 19f) — so offer it."""
        client = _shop(steps=[
            {"id": "step-1", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1", "step_order": 1,
             "approver_type": "user", "approver_ref": "member-9", "status": "pending", "acted_by": None, "acted_at": None, "reason": None},
            {"id": "step-2", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1", "step_order": 2,
             "approver_type": "role", "approver_ref": "manager", "status": "pending", "acted_by": None, "acted_at": None, "reason": None},
        ])
        reply, calls = await _say(client, "อนุมัติ SR-2026-0001", APPROVE)
        assert [c for c in calls if c[0] == "act_on_approval_step"], reply.text
        assert ("อนุมัติขั้นถัดไป", "อนุมัติ SR-2026-0001") in reply.quick_replies, reply.quick_replies
        assert client._reports[0]["status"] == "submitted"

    async def test_the_last_step_sends_the_survey_and_offers_the_document(self, monkeypatch):
        import chann_app.services.approval as approval_service
        pushed: list[tuple] = []

        async def fake_push_messages(oa, to, messages, client=None):
            pushed.append((oa, to, messages))
            return ["mid"]
        monkeypatch.setattr(approval_service, "push_messages", fake_push_messages)
        client = _shop()
        reply, calls = await _say(client, "อนุมัติ SR-2026-0001", APPROVE)
        assert client._reports[0]["status"] == "approved", (reply.text, calls)
        assert [p for p in pushed if p[0] == "customer"], "the customer was not sent the survey"
        assert "แบบประเมิน" in reply.text, reply.text
        assert ("ดูเอกสาร", "ออกรายงาน SR-2026-0001") in reply.quick_replies, reply.quick_replies

    async def test_a_customer_with_no_line_is_named_not_silently_skipped(self):
        client = _shop()
        client._line_targets = {}
        reply, _ = await _say(client, "อนุมัติ SR-2026-0001", APPROVE)
        assert "ไม่มี LINE" in reply.text, reply.text
