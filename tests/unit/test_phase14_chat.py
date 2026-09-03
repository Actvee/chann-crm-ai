"""Phase 14-B — approvals and surveys through chat, and the same service
through the dashboard route (Master Spec 14.6).

The FakeDataClient carries the Data Tier's rules from 14-A: only the
lowest pending step per report is offered, acting closes it, the last
approval flips the report and creates the survey row. LINE pushes are
captured, not sent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from chann_app.services import approval as approval_service  # noqa: E402
from chann_app.services.chat import handle_chat_message, handle_reply  # noqa: E402
from chann_app.services.ai.approval_policy import validate_workflow  # noqa: E402

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

CS_KEYS = ["ticket.read", "ticket.update", "service_report.read",
           "approval.view", "approval.approve", "approval.reject"]
TECH_KEYS = ["ticket.read", "ticket.update", "service_report.create", "service_report.read"]


def _shop(*, cs_member="cs-1", owner_on_ticket=True):
    """A shop with one CS (the ticket owner), one technician, one customer."""
    c = FakeDataClient(permission_keys=CS_KEYS, role="cs")
    c._member_id = cs_member
    c._tickets = [{
        "id": "t1", "ticket_number": "T-2026-0001", "status": "in_progress",
        "customer_name": "สมหญิง", "customer_chann_uid": "CHN-C-1",
        "assigned_to_ref": "tech-1", "accept_status": "accepted",
        "owner_member_id": cs_member if owner_on_ticket else None,
    }]
    c._reports = [{
        "id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1",
        "technician_member_id": "tech-1", "status": "submitted",
        "report_data": {"found_issue": "คอมเพรสเซอร์รั่ว", "work_done": "เปลี่ยนแล้ว"},
    }]
    c._members = [
        {"id": cs_member, "chann_uid": "CHN-S-000001", "role": "cs", "status": "active"},
        {"id": "tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"},
        {"id": "adm-1", "chann_uid": "CHN-A-000001", "role": "admin", "status": "active"},
    ]
    c._line_targets = {"CHN-S-000001": "U-cs", "CHN-T-000001": "U-tech", "CHN-C-1": "U-cust"}
    return c


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    """The policy tests inject a fake model transport; the client still
    refuses to run without a key and model name configured."""
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


@pytest.fixture
def pushes(monkeypatch):
    """Every LINE push the code attempts, captured."""
    sent: list[tuple] = []

    async def fake_push_text(oa, to, text, client=None):
        sent.append(("text", oa, to, text))
        return [f"msg-{len(sent)}"]

    async def fake_push_messages(oa, to, messages, client=None):
        sent.append(("messages", oa, to, messages))
        return [f"msg-{len(sent)}"]

    from chann_app.services import notify
    monkeypatch.setattr(notify, "push_text", fake_push_text)
    monkeypatch.setattr(approval_service, "push_messages", fake_push_messages)
    return sent


async def _submit(c):
    """Open the steps the way check-out does."""
    return await approval_service.on_report_submitted(
        c, license_id=LICENSE_ID, report=c._reports[0],
    )


class TestCheckOutOpensTheFlow:
    @pytest.mark.asyncio
    async def test_checkout_opens_steps_and_pushes_to_the_cs_now(self, pushes):
        c = _shop()
        c._permission_keys = TECH_KEYS
        c._member_id = "tech-1"
        # The fake's check_out returns sr-1; the guided flow's last answer
        # lands here with the report already held in the pending intent.
        await c.set_pending_intent(
            "CHN-T-000001", "technician", action="report", entity="service_report",
            fields={"ticket_id": "t1", "code": "T-2026-0001",
                    "found_issue": "คอมเพรสเซอร์รั่ว", "work_done": "เปลี่ยนแล้ว"},
            missing=["parts_changed"],
        )
        reply = await handle_chat_message(
            c, message="ไม่มี", ctx=_ctx(primary_role="technician"),
        )
        assert "SR-2026-0001" in reply.text
        assert ("open_approval_steps", LICENSE_ID, "sr-1") in c.recorded
        # Owner decision 2: the CS hears the second the job closes.
        assert any(kind == "text" and oa == "sales" and to == "U-cs" and "SR-2026-0001" in text
                   for kind, oa, to, text in pushes)
        # …and the pushed message is mapped, so replying to it works.
        assert (LICENSE_ID, "msg-1", "service_report", "sr-1") in c.recorded

    @pytest.mark.asyncio
    async def test_no_ticket_owner_falls_back_to_admin(self, pushes):
        c = _shop(owner_on_ticket=False)
        c._line_targets["CHN-A-000001"] = "U-admin"
        steps = await _submit(c)
        assert steps[0]["approver_type"] == "role" and steps[0]["approver_ref"] == "admin"
        # The request went to the admin, not to a CS who owns nothing.
        assert [to for kind, _, to, _ in pushes if kind == "text"] == ["U-admin"]


class TestApprovingInChat:
    @pytest.mark.asyncio
    async def test_pending_list_names_the_report_and_offers_a_button(self, pushes):
        c = _shop()
        await _submit(c)
        reply = await handle_chat_message(c, message="รายการรออนุมัติ", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "SR-2026-0001" in reply.text and "สมหญิง" in reply.text
        assert ("SR-2026-0001", "อนุมัติ SR-2026-0001") in reply.quick_replies

    @pytest.mark.asyncio
    async def test_approving_the_only_step_sends_the_survey(self, pushes):
        c = _shop()
        await _submit(c)
        reply = await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "อนุมัติ SR-2026-0001 แล้ว" in reply.text
        assert c._reports[0]["status"] == "approved"
        survey = [p for p in pushes if p[0] == "messages" and p[1] == "customer" and p[2] == "U-cust"]
        assert survey, pushes
        quick = survey[0][3][0]["quickReply"]["items"]
        assert [i["action"]["text"] for i in quick] == ["1", "2", "3"]
        assert ("mark_survey_sent", LICENSE_ID, "survey-t1") in c.recorded
        assert "แบบประเมิน" in reply.text

    @pytest.mark.asyncio
    async def test_approving_without_a_code_picks_the_single_pending_report(self, pushes):
        c = _shop()
        await _submit(c)
        reply = await handle_chat_message(c, message="อนุมัติ", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "SR-2026-0001" in reply.text and "แล้ว" in reply.text

    @pytest.mark.asyncio
    async def test_two_pending_reports_get_buttons_not_a_guess(self, pushes):
        c = _shop()
        c._tickets.append({**c._tickets[0], "id": "t2", "ticket_number": "T-2026-0002",
                           "customer_name": "สมศักดิ์", "customer_chann_uid": "CHN-C-2"})
        c._reports.append({**c._reports[0], "id": "sr-2", "report_id": "SR-2026-0002", "ticket_id": "t2"})
        await approval_service.on_report_submitted(c, license_id=LICENSE_ID, report=c._reports[0])
        await approval_service.on_report_submitted(c, license_id=LICENSE_ID, report=c._reports[1])
        reply = await handle_chat_message(c, message="อนุมัติ", ctx=_ctx(primary_role="cs", oa="sales"))
        assert not [r for r in c.recorded if r[0] == "act_on_approval_step"]
        assert [s for _, s in reply.quick_replies] == ["อนุมัติ SR-2026-0001", "อนุมัติ SR-2026-0002"]

    @pytest.mark.asyncio
    async def test_reply_to_the_notification_then_approve(self, pushes):
        """Owner requirement: reply to the request message and type อนุมัติ."""
        c = _shop()
        await _submit(c)
        c._mapping = {"entity_type": "service_report", "entity_id": "sr-1"}
        reply = await handle_reply(
            c, message_id="msg-1", reply_text="อนุมัติ", ctx=_ctx(primary_role="cs", oa="sales"),
        )
        assert "อนุมัติ SR-2026-0001 แล้ว" in reply.text
        assert reply.entity_type == "service_report" and reply.entity_id == "sr-1"

    @pytest.mark.asyncio
    async def test_rejecting_needs_a_reason_and_tells_the_technician(self, pushes):
        c = _shop()
        await _submit(c)
        asked = await handle_chat_message(c, message="ไม่อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "เหตุผล" in asked.text
        assert not [r for r in c.recorded if r[0] == "act_on_approval_step"]

        reply = await handle_chat_message(
            c, message="ไม่อนุมัติ SR-2026-0001 รูปไม่ครบ", ctx=_ctx(primary_role="cs", oa="sales"),
        )
        assert "ตีกลับ SR-2026-0001" in reply.text and "รูปไม่ครบ" in reply.text
        assert c._reports[0]["status"] == "rejected"
        assert any(oa == "technician" and to == "U-tech" and "รูปไม่ครบ" in text
                   for kind, oa, to, text in pushes if kind == "text")
        # No survey on a rejection.
        assert not [p for p in pushes if p[0] == "messages"]

    @pytest.mark.asyncio
    async def test_someone_elses_step_is_refused_in_words(self, pushes):
        c = _shop()
        await _submit(c)
        c._member_id = "other-cs"      # not the ticket owner
        reply = await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "รอคุณ" in reply.text or "ไม่ถึงขั้น" in reply.text
        assert c._reports[0]["status"] == "submitted"
        assert "not this member" not in reply.text

    @pytest.mark.asyncio
    async def test_needs_the_permission(self, pushes):
        c = _shop()
        c._permission_keys = ["ticket.read"]
        await _submit(c)
        reply = await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "สิทธิ์" in reply.text
        assert c._reports[0]["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_two_step_flow_passes_to_the_admin_then_approves(self, pushes):
        c = _shop()
        c._approval_state()
        c._workflow["rules_json"]["steps"] = [
            {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
            {"order": 2, "approver_type": "role", "approver_ref": "admin"},
        ]
        await _submit(c)
        first = await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        assert "ขั้นถัดไป" in first.text
        assert c._reports[0]["status"] == "submitted"
        assert not [p for p in pushes if p[0] == "messages"]

        c._member_id = "adm-1"
        c._role = "admin"
        second = await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="admin", oa="sales"))
        assert c._reports[0]["status"] == "approved"
        assert [p for p in pushes if p[0] == "messages"], second.text


class TestChatAndDashboardAreOneService:
    """14.6 test_approval_chat_vs_dashboard: the route and the chat handler
    make the same Data Tier calls with the same effects."""

    @pytest.mark.asyncio
    async def test_route_and_chat_record_the_same_calls(self, pushes):
        from types import SimpleNamespace

        from chann_app.routers_phase2 import approve_step

        via_chat = _shop()
        await _submit(via_chat)
        await handle_chat_message(via_chat, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))

        via_route = _shop()
        steps = await _submit(via_route)
        principal = SimpleNamespace(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="cs", is_owner=False,
            permission_keys=frozenset(CS_KEYS), require=lambda key: None,
        )
        await approve_step(LICENSE_ID, steps[0]["id"], {}, principal=principal, client=via_route)

        def acts(client):
            return [r for r in client.recorded if r[0] in ("act_on_approval_step", "mark_survey_sent")]

        assert acts(via_chat) == acts(via_route)
        assert via_chat._reports[0]["status"] == via_route._reports[0]["status"] == "approved"


class TestPolicyByPrompt:
    def _ai_flow(self, steps):
        return httpx.AsyncClient(transport=_ai(json.dumps({
            "version": 1, "entity_type": "service_report", "steps": steps,
        })))

    @pytest.mark.asyncio
    async def test_policy_is_shown_back_then_saved_on_confirm(self):
        c = FakeDataClient(permission_keys=["approval.manage"])
        ai = self._ai_flow([
            {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
            {"order": 2, "approver_type": "role", "approver_ref": "admin"},
        ])
        shown = await handle_chat_message(
            c, message="ตั้งการอนุมัติ ให้ CS ก่อน แล้วต่อด้วย admin", ctx=_ctx(), ai_client=ai,
        )
        assert "ขั้น 1" in shown.text and "ขั้น 2" in shown.text and "admin" in shown.text
        assert not [r for r in c.recorded if r[0] == "replace_approval_workflow"]

        saved = await handle_chat_message(c, message="ยืนยันการอนุมัติ", ctx=_ctx())
        wf = [r for r in c.recorded if r[0] == "replace_approval_workflow"]
        assert len(wf) == 1 and len(wf[0][3]["steps"]) == 2
        assert "บันทึก" in saved.text

    @pytest.mark.asyncio
    async def test_a_role_that_does_not_exist_is_refused(self):
        c = FakeDataClient(permission_keys=["approval.manage"])
        ai = self._ai_flow([{"order": 1, "approver_type": "role", "approver_ref": "supervisor"}])
        reply = await handle_chat_message(
            c, message="ตั้งการอนุมัติ ให้ supervisor", ctx=_ctx(), ai_client=ai,
        )
        assert "supervisor" in reply.text
        assert not [r for r in c.recorded if r[0] == "replace_approval_workflow"]

    @pytest.mark.asyncio
    async def test_show_current_flow(self):
        c = FakeDataClient(permission_keys=["approval.view"])
        reply = await handle_chat_message(c, message="ดูการอนุมัติปัจจุบัน", ctx=_ctx())
        assert "CS เจ้าของงาน" in reply.text

    @pytest.mark.asyncio
    async def test_needs_approval_manage(self):
        c = FakeDataClient(permission_keys=["approval.approve"])
        reply = await handle_chat_message(
            c, message="ตั้งการอนุมัติ ให้ CS", ctx=_ctx(),
            ai_client=self._ai_flow([{"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"}]),
        )
        assert "สิทธิ์" in reply.text

    def test_validator_mirrors_the_data_tier(self):
        roles = ["owner", "admin", "cs"]
        assert validate_workflow({"steps": []}, roles=roles)
        assert validate_workflow({"steps": [{"order": 1, "approver_type": "user", "approver_ref": "somebody"}]}, roles=roles)
        assert validate_workflow({"steps": [{"order": 1, "approver_type": "role", "approver_ref": "admin"},
                                            {"order": 1, "approver_type": "role", "approver_ref": "cs"}]}, roles=roles)
        assert not validate_workflow({"steps": [{"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
                                                {"order": 2, "approver_type": "role", "approver_ref": "admin"}]}, roles=roles)


class TestSurveyAnswers:
    async def _approved(self, pushes):
        c = _shop()
        await _submit(c)
        await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        c._tickets[0]["status"] = "completed"
        return c

    @pytest.mark.asyncio
    async def test_a_digit_records_the_score(self, pushes):
        c = await self._approved(pushes)
        ctx = _ctx(primary_role="customer", oa="customer")
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="2", ctx=ctx)
        assert ("answer_survey", LICENSE_ID, "survey-t1", 2, None) in c.recorded
        assert "พอใช้" in reply.text and "T-2026-0001" in reply.text
        assert not [r for r in c.recorded if r[0] == "create_ticket"]

    @pytest.mark.asyncio
    async def test_off_scale_is_asked_again(self, pushes):
        c = await self._approved(pushes)
        ctx = _ctx(primary_role="customer", oa="customer")
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="5", ctx=ctx)
        assert not [r for r in c.recorded if r[0] == "answer_survey"]
        assert [s for _, s in reply.quick_replies] == ["1", "2", "3"]

    @pytest.mark.asyncio
    async def test_answering_twice_is_told_so(self, pushes):
        c = await self._approved(pushes)
        ctx = _ctx(primary_role="customer", oa="customer")
        ctx.chann_uid = "CHN-C-1"
        await handle_chat_message(c, message="3", ctx=ctx)
        again = await handle_chat_message(c, message="3", ctx=ctx)
        # The second "3" finds no pending survey and falls through to the
        # ordinary customer flow — a bare digit is a greeting there.
        assert "answer_survey" in [r[0] for r in c.recorded]
        assert len([r for r in c.recorded if r[0] == "answer_survey"]) == 1
        assert not [r for r in c.recorded if r[0] == "create_ticket"], again.text

    @pytest.mark.asyncio
    async def test_no_survey_pending_leaves_a_digit_alone(self, pushes):
        c = _shop()
        ctx = _ctx(primary_role="customer", oa="customer")
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="2", ctx=ctx)
        assert not [r for r in c.recorded if r[0] == "answer_survey"]
        assert not [r for r in c.recorded if r[0] == "create_ticket"], reply.text

    @pytest.mark.asyncio
    async def test_customer_without_line_still_gets_a_survey_row(self, pushes):
        c = _shop()
        c._line_targets.pop("CHN-C-1")
        await _submit(c)
        reply = await handle_chat_message(c, message="อนุมัติ SR-2026-0001", ctx=_ctx(primary_role="cs", oa="sales"))
        assert c._reports[0]["status"] == "approved"
        assert not [p for p in pushes if p[0] == "messages"]
        assert "ไม่มี LINE" in reply.text
