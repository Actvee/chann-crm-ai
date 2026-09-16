"""Round 19q — the satisfaction survey reaches the customer however the job ended.

Owner, 16 ก.ย. 2569: "Sale OA ปิดงานแล้วแต่ไม่มีส่งประเมินไปให้ลูกค้า ตรงนี้
ขาดหายหรือไม่ และตอนอนุมัติเช่นกันแก้ให้มีส่งแบบประเมินไปให้ลูกค้าแล้วใช่ไหม"

Both halves answered here. Approval has pushed the survey since round 19j
(and says whether it went); a job the SHOP closed itself never went through
approval, so no survey row was ever created and the customer was asked
nothing at all.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "ticket.close", "ticket.assign", "approval.view", "approval.approve", "service_report.read"]
TICKET = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "in_progress", "accept_status": "accepted",
    "assigned_to_ref": "m-tech-1", "assigned_target_type": "technician", "customer_chann_uid": "CHN-S-000009",
    "customer_name": "สมชาย ใจดี", "issue_description": "แอร์ไม่เย็น", "service_address": "99/1",
    "owner_member_id": "member-1", "visibility": "public",
}
CLOSE = {"action": "close", "entity": "ticket", "fields": {"code": "T-2026-0001"}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _shop(*, customer_has_line: bool = True) -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS])
    client._tickets = [dict(TICKET)]
    client._members = [
        {"id": "member-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active",
         "display_name": "เจ้าของ", "channel": "sales"},
        {"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active",
         "display_name": "สมศักดิ์", "channel": "technician"},
    ]
    client._line_targets = {"CHN-S-000001": "line-owner"}
    if customer_has_line:
        client._line_targets["CHN-S-000009"] = "line-customer"
    client._is_owner = True
    client._role = "owner"
    client._member_id = "member-1"
    return client


async def _close(client, message="ปิดงาน T-2026-0001 สาเหตุ ท่อตัน การแก้ไข ล้างท่อ"):
    since = len(client.recorded)
    reply = await handle_chat_message(
        client, message=message, ctx=_ctx(oa="sales", primary_role="sales"), ai_client=_reads(CLOSE),
    )
    return reply, client.recorded[since:]


class TestTheShopClosesTheJobItself:
    async def test_the_customer_is_asked_how_it_went(self, monkeypatch):
        pushed: list = []

        from chann_app.services import approval as approval_mod

        async def _push(audience, target, messages):
            pushed.append((audience, target, messages))

        monkeypatch.setattr(approval_mod, "push_messages", _push)
        client = _shop()
        reply, wrote = await _close(client)
        assert [w for w in wrote if w[0] == "open_survey_for_ticket"], wrote
        assert pushed and pushed[0][1] == "line-customer", pushed
        assert "ส่งแบบประเมินให้ลูกค้าแล้ว" in reply.text, reply.text

    async def test_a_customer_with_no_line_is_said_so_not_claimed(self, monkeypatch):
        from chann_app.services import approval as approval_mod

        async def _push(audience, target, messages):  # pragma: no cover - must not run
            raise AssertionError("nothing to push to")

        monkeypatch.setattr(approval_mod, "push_messages", _push)
        client = _shop(customer_has_line=False)
        reply, _wrote = await _close(client)
        assert "ยังไม่ได้ผูก LINE" in reply.text, reply.text
        assert "ส่งแบบประเมินให้ลูกค้าแล้ว" not in reply.text, reply.text

    async def test_the_job_is_still_closed_when_the_survey_cannot_go(self, monkeypatch):
        from chann_app.services import approval as approval_mod

        async def _boom(audience, target, messages):
            raise RuntimeError("LINE is down")

        monkeypatch.setattr(approval_mod, "push_messages", _boom)
        client = _shop()
        reply, wrote = await _close(client)
        assert [w for w in wrote if w[0] == "set_ticket_status"], wrote
        assert "ปิดงาน" in reply.text, reply.text

    async def test_one_survey_per_job_however_it_finishes(self):
        client = _shop()
        await _close(client)
        first = list(client._surveys)
        await _close(client)
        assert len(client._surveys) == len(first) == 1, client._surveys


class TestApprovalStillSendsIt:
    """The other half of the owner's question: "ตอนอนุมัติเช่นกันแก้ให้มีส่ง
    แบบประเมินไปให้ลูกค้าแล้วใช่ไหม" — yes, since round 19j, and the reply
    says which of the three things happened."""

    APPROVE = {"action": "approve", "entity": "approval", "fields": {"code": "SR-2026-0001"}, "missing": []}

    def _with_a_report(self) -> FakeDataClient:
        client = _shop()
        client._tickets = [dict(TICKET, status="completed")]
        client._reports = [{
            "id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
            "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"},
            "generated_document_id": None,
        }]
        client._approval_state()
        client._approval_steps = [{
            "id": "step-1", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1",
            "step_order": 1, "approver_type": "user", "approver_ref": "member-1",
            "status": "pending", "acted_by": None, "acted_at": None, "reason": None,
        }]
        return client

    async def test_the_last_step_pushes_the_survey_to_the_customer(self, monkeypatch):
        pushed: list = []

        from chann_app.services import approval as approval_mod

        async def _push(audience, target, messages):
            pushed.append((audience, target, messages))

        monkeypatch.setattr(approval_mod, "push_messages", _push)
        client = self._with_a_report()
        reply = await handle_chat_message(
            client, message="อนุมัติ SR-2026-0001", ctx=_ctx(oa="sales", primary_role="sales"),
            ai_client=_reads(self.APPROVE),
        )
        assert pushed and pushed[0][1] == "line-customer", pushed
        assert "แบบประเมิน" in reply.text, reply.text


class TestAShopWithNoApprovalSteps:
    """A shop that empties its approval rule: there is nobody to wait for,
    so the report is finished when it is filed. It used to sit at
    "submitted" for ever — no paper, no survey, no explanation."""

    async def test_the_report_is_finished_and_the_survey_goes(self, monkeypatch):
        pushed: list = []

        from chann_app.services import approval as approval_mod

        async def _push(audience, target, messages):
            pushed.append((audience, target, messages))

        monkeypatch.setattr(approval_mod, "push_messages", _push)
        client = _shop()
        client._reports = [{
            "id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
            "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"},
        }]
        client._approval_state()
        client._workflow["rules_json"] = {"steps": []}
        client._approval_steps = []
        await approval_mod.on_report_submitted(
            client, license_id="11111111-1111-1111-1111-111111111111",
            report=dict(client._reports[0]), language="th",
        )
        assert client._reports[0]["status"] == "approved", client._reports
        assert [p for p in pushed if p[1] == "line-customer"], pushed


class TestASubmittedReportWithNothingPending:
    """Round 19r: the state the autoflush bug left behind. The sweep finds
    it and finishes it, so reports stuck before the fix shipped heal
    themselves rather than waiting for ever."""

    async def test_the_sweep_finishes_it_and_asks_the_customer(self, monkeypatch):
        pushed: list = []

        from chann_app.services import approval as approval_mod
        from chann_app.services import approval_sla

        async def _push(audience, target, messages):
            pushed.append((audience, target, messages))

        monkeypatch.setattr(approval_mod, "push_messages", _push)
        client = _shop()
        client._reports = [{
            "id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
            "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"},
            "created_at": "2026-09-16T01:00:00+00:00", "updated_at": "2026-09-16T01:00:00+00:00",
        }]
        client._approval_state()
        # Every step acted on, yet the report says submitted.
        client._approval_steps = [{
            "id": "step-1", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1",
            "step_order": 1, "approver_type": "user", "approver_ref": "member-1",
            "status": "approved", "acted_by": "member-1", "acted_at": "2026-09-16T02:00:00+00:00",
            "reason": None, "created_at": "2026-09-16T01:00:00+00:00",
        }]
        await approval_sla.sweep_reports(client, license_ids=["11111111-1111-1111-1111-111111111111"])
        assert client._reports[0]["status"] == "approved", client._reports
        assert [p for p in pushed if p[1] == "line-customer"], pushed

    async def test_a_report_still_waiting_is_left_alone(self):
        from chann_app.services import approval as approval_mod

        client = _shop()
        client._reports = [{
            "id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
            "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"},
        }]
        client._approval_state()
        client._approval_steps = [{
            "id": "step-1", "entity_type": "service_report", "entity_id": "r1", "workflow_id": "wf-1",
            "step_order": 1, "approver_type": "user", "approver_ref": "member-1",
            "status": "pending", "acted_by": None, "acted_at": None, "reason": None,
        }]
        finished = await approval_mod.finish_if_nothing_is_pending(
            client, "11111111-1111-1111-1111-111111111111", dict(client._reports[0]),
        )
        assert finished is False
        assert client._reports[0]["status"] == "submitted"
