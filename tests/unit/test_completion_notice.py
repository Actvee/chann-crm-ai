"""The customer hears that their job is finished (owner, 10 Sep 2026).

"พองาน ticket เสร็จแล้วไม่มีแจ้งไปหาลูกค้า". The only message that ever said a
job was done was the satisfaction survey, which goes out when the LAST
approval step passes — so a customer waited days while a report sat in a
queue, heard nothing at all when the report was sent back, and heard nothing
ever if the shop never got round to approving.

A ticket becomes `completed` in exactly one place (`phase13.check_out`), and
two surfaces reach it: the technician's chat check-out and the technician
home screen's check-out route. Both are covered here, plus the two rules the
notification system already had — the row is written before the push, and the
message is in the RECIPIENT's language — and the two ways this could go wrong
in the shop's face: a walk-in customer with no LINE account, and the survey
announcing completion a second time days later.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import approval as approval_service  # noqa: E402
from chann_app.services import chat as chat_service  # noqa: E402
from chann_app.services import notify as notify_module  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close",
             "service_report.create", "service_report.read"]
CS_KEYS = ["ticket.read", "ticket.update", "service_report.read",
           "approval.view", "approval.approve", "approval.reject"]

CUSTOMER_UID = "CHN-C-1"


def _shop(*, customer_uid: str = CUSTOMER_UID, language: str = "th",
          permission_keys=None, role: str = "technician") -> FakeDataClient:
    """One in-progress job, one technician on it, one linked customer."""
    c = FakeDataClient(
        permission_keys=list(permission_keys or TECH_KEYS), role=role,
    )
    c._member_id = "tech-1"
    c._tickets = [{
        "id": "t1", "ticket_number": "T-2026-0001", "status": "in_progress",
        "customer_name": "สมหญิง", "customer_chann_uid": customer_uid,
        "assigned_to_ref": "tech-1", "accept_status": "accepted",
        "owner_member_id": "cs-1",
    }]
    c._reports = [{
        "id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1",
        "technician_member_id": "tech-1", "status": "submitted",
        "report_data": {"found_issue": "คอมเพรสเซอร์รั่ว",
                        "work_done": "เปลี่ยนคอมเพรสเซอร์ใหม่"},
    }]
    c._members = [
        {"id": "cs-1", "chann_uid": "CHN-S-000001", "role": "cs", "status": "active"},
        {"id": "tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"},
    ]
    c._line_targets = {"CHN-S-000001": "U-cs", "CHN-T-000001": "U-tech"}
    if customer_uid:
        c._line_targets[customer_uid] = "U-cust"
        c._prefs = {customer_uid: {"language": language}}
    return c


@pytest.fixture
def pushes(monkeypatch):
    """Every LINE push attempted, captured — nothing leaves the process."""
    sent: list[tuple] = []

    async def fake_push_text(oa, to, text, client=None):
        sent.append(("text", oa, to, text))
        return [f"msg-{len(sent)}"]

    async def fake_push_messages(oa, to, messages, client=None):
        sent.append(("messages", oa, to, messages))
        return [f"msg-{len(sent)}"]

    monkeypatch.setattr(notify_module, "push_text", fake_push_text)
    monkeypatch.setattr(chat_service._notify_mod, "push_text", fake_push_text)
    monkeypatch.setattr(approval_service, "push_messages", fake_push_messages)
    return sent


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _completion_rows(client: FakeDataClient) -> list[tuple]:
    return [r for r in client.recorded
            if r[0] == "create_notification" and r[3] == chat_service.TICKET_COMPLETED_TYPE]


def _reopen_rows(client: FakeDataClient) -> list[tuple]:
    return [r for r in client.recorded
            if r[0] == "create_notification" and r[3] == chat_service.TICKET_REOPENED_TYPE]


def _customer_pushes(sent: list[tuple]) -> list[tuple]:
    return [p for p in sent if p[1] == "customer"]


# ------------------------------------------------------------- chat check-out


class TestChatCheckOutTellsTheCustomer:
    @pytest.mark.asyncio
    async def test_the_terse_report_form_finishes_the_job_and_says_so(self, pushes):
        c = _shop()
        reply = await handle_chat_message(
            c,
            message="ปิดงาน T-2026-0001 พบ: คอมเพรสเซอร์รั่ว แก้: เปลี่ยนคอมเพรสเซอร์ใหม่",
            ctx=_ctx(primary_role="technician", oa="technician"),
        )
        assert "SR-2026-0001" in reply.text

        rows = _completion_rows(c)
        assert len(rows) == 1, "one completion, one notification"
        _, license_id, target, _type, message = rows[0]
        assert license_id == LICENSE_ID
        # The recipient is the CUSTOMER, not the technician or the CS.
        assert target == CUSTOMER_UID
        assert "T-2026-0001" in message
        assert "เสร็จแล้ว" in message
        # What was done comes from the report's own "แก้:" answer; no new
        # field was invented to carry it.
        assert "เปลี่ยนคอมเพรสเซอร์ใหม่" in message

    @pytest.mark.asyncio
    async def test_the_row_exists_before_the_push_is_attempted(self, pushes, monkeypatch):
        """notify.py's rule: a LINE outage must not erase the fact that the
        shop said the job was finished."""
        c = _shop()
        order: list[str] = []

        original = c.create_notification

        async def watched(*args, **kwargs):
            order.append("row")
            return await original(*args, **kwargs)

        monkeypatch.setattr(c, "create_notification", watched)

        async def failing_push(oa, to, text, client=None):
            order.append("push")
            from chann_app.line.client import LineReplyError

            raise LineReplyError("LINE is down")

        monkeypatch.setattr(notify_module, "push_text", failing_push)
        reply = await handle_chat_message(
            c, message="ปิดงาน T-2026-0001 พบ: x แก้: y",
            ctx=_ctx(primary_role="technician", oa="technician"),
        )
        assert "SR-2026-0001" in reply.text, "a LINE failure is not a failed check-out"
        assert order[:2] == ["row", "push"]
        assert len(_completion_rows(c)) == 1

    @pytest.mark.asyncio
    async def test_it_goes_out_on_the_customer_oa(self, pushes):
        c = _shop()
        await handle_chat_message(
            c, message="ปิดงาน T-2026-0001 พบ: x แก้: เปลี่ยนคอมเพรสเซอร์ใหม่",
            ctx=_ctx(primary_role="technician", oa="technician"),
        )
        customer = _customer_pushes(pushes)
        assert len(customer) == 1
        # The customer's own OA and the customer's own LINE account — not
        # the technician OA the message was typed on.
        assert customer[0][1] == "customer" and customer[0][2] == "U-cust"

    @pytest.mark.asyncio
    async def test_an_english_customer_is_told_in_english(self, pushes):
        """The RECIPIENT's language, not the technician's (principle 7)."""
        c = _shop(language="en")
        await handle_chat_message(
            c, message="ปิดงาน T-2026-0001 พบ: x แก้: replaced the compressor",
            ctx=_ctx(primary_role="technician", oa="technician"),
        )
        text = _customer_pushes(pushes)[0][3]
        assert "the technician has finished" in text
        assert "What was done: replaced the compressor" in text
        assert "ช่าง" not in text
        # The Thai text is still the one stored, because `message` is the
        # column that is never null.
        assert "เสร็จแล้ว" in _completion_rows(c)[0][4]

    @pytest.mark.asyncio
    async def test_a_walk_in_ticket_finishes_without_raising(self, pushes):
        """No LINE account on the job at all. The shop must still be able to
        close it, and nothing may be written against an identity that is not
        there — `notifications.target_chann_uid` is a foreign key."""
        c = _shop(customer_uid="")
        reply = await handle_chat_message(
            c, message="ปิดงาน T-2026-0001 พบ: x แก้: y",
            ctx=_ctx(primary_role="technician", oa="technician"),
        )
        assert "SR-2026-0001" in reply.text
        assert _completion_rows(c) == []
        assert _customer_pushes(pushes) == []

    @pytest.mark.asyncio
    async def test_a_report_with_no_work_done_still_tells_the_customer(self, pushes):
        """`work_done` is required by the Data Tier's gate, but the notice
        must not depend on it: an empty summary is a missing line, never a
        missing message."""
        c = _shop()
        c._tickets[0]["status"] = "in_progress"
        await chat_service.announce_job_finished(
            c, LICENSE_ID,
            {"id": "sr-1", "ticket_id": "t1", "report_data": {"found_issue": "x"}},
        )
        rows = _completion_rows(c)
        assert len(rows) == 1
        assert "T-2026-0001" in rows[0][4] and "สิ่งที่ทำ" not in rows[0][4]

    @pytest.mark.asyncio
    async def test_the_guided_question_flow_tells_the_customer_too(self, pushes):
        """The three-question form, which is how a technician standing in a
        customer's house actually closes a job."""
        c = _shop()
        await c.set_pending_intent(
            "CHN-S-000001", "technician", action="report", entity="service_report",
            fields={"ticket_id": "t1", "code": "T-2026-0001",
                    "found_issue": "คอมเพรสเซอร์รั่ว", "work_done": "เปลี่ยนคอมเพรสเซอร์ใหม่"},
            missing=["parts_changed"],
        )
        await handle_chat_message(
            c, message="ไม่มี", ctx=_ctx(primary_role="technician", oa="technician"),
        )
        rows = _completion_rows(c)
        assert len(rows) == 1 and rows[0][2] == CUSTOMER_UID
        assert "เปลี่ยนคอมเพรสเซอร์ใหม่" in rows[0][4]

    @pytest.mark.asyncio
    async def test_a_sentence_the_model_reads_as_closing_reaches_the_notice(self, pushes):
        """"ซ่อมเรียบร้อยแล้ว…" — however a technician phrases it, and whether
        the deterministic layer or the model routes it, closing a job is one
        handler (`("close","ticket")` and `("check_out","service_report")` are
        both re-synthesised into "ปิดงาน"). It ends with the report questions
        and then the same completion notice."""
        c = _shop()
        reply = await handle_chat_message(
            c, message="ซ่อมเรียบร้อยแล้วครับ ใบงาน T-2026-0001",
            ctx=_ctx(primary_role="technician", oa="technician"),
            ai_client=httpx.AsyncClient(transport=_ai(
                '{"action": "close", "entity": "ticket", '
                '"fields": {"code": "T-2026-0001"}, "missing": []}'
            )),
        )
        # Nothing is completed until the report is answered — the gate the
        # customer notice hangs off has not opened yet.
        assert _completion_rows(c) == []
        assert "พบ" in reply.text or "ปัญหา" in reply.text
        for answer in ("คอมเพรสเซอร์รั่ว", "เปลี่ยนคอมเพรสเซอร์ใหม่", "ไม่มี"):
            await handle_chat_message(
                c, message=answer, ctx=_ctx(primary_role="technician", oa="technician"),
            )
        assert [r for r in c.recorded if r[0] == "check_out_ticket"]
        rows = _completion_rows(c)
        assert len(rows) == 1 and rows[0][2] == CUSTOMER_UID
        assert "เปลี่ยนคอมเพรสเซอร์ใหม่" in rows[0][4]


class TestOneHookForEverySurface:
    def test_nothing_closes_a_job_without_going_through_after_check_out(self):
        """The drift guard. Two surfaces already reach `check_out`, and the
        customer notice hangs off ONE hook; a third surface wired to the old
        `on_report_submitted` would open approvals and tell nobody outside
        the shop — which is exactly the bug that was reported.
        """
        import re

        calls: list[tuple[str, int]] = []
        hooks: dict[str, list[tuple[str, int]]] = {
            "after_check_out": [], "_after_report_submitted": [],
            "on_report_submitted": [],
        }
        for path in sorted((ROOT / "application").rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r"\bcheck_out_ticket\(", code) and "def " not in code:
                    calls.append((path.name, number))
                for hook in hooks:
                    if re.search(rf"\b{hook}\(", code) and "def " not in code:
                        hooks[hook].append((path.name, number))

        # Every caller of the Data Tier's check-out, and every caller of a
        # post-check-out hook, lives in one of these two files.
        assert {name for name, _ in calls} == {"chat.py", "routers_phase2.py"}
        assert len(calls) == 3, calls  # two in chat (guided + terse), one route
        assert len(hooks["after_check_out"]) == len(calls), hooks
        # The inner hooks are reached only through it.
        assert [n for n, _ in hooks["_after_report_submitted"]] == ["chat.py"]
        assert len(hooks["_after_report_submitted"]) == 1
        assert [n for n, _ in hooks["on_report_submitted"]] == ["chat.py"]
        assert len(hooks["on_report_submitted"]) == 1


# ------------------------------------------- the technician home screen's route


def _route_app(client, *, keys, audience="technician"):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-T-000001", role="technician",
            is_owner=False, permission_keys=frozenset(keys), audience=audience,
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class _RouteClient(FakeDataClient):
    async def aclose(self):
        pass


class TestTheCheckOutRouteTellsTheCustomer:
    def test_the_dashboard_check_out_sends_the_same_notice(self, pushes):
        """Parity: what chat does the screen does. The route used to call
        `on_report_submitted` on its own, so anything added to chat's
        after-check-out work would have missed it."""
        c = _RouteClient(permission_keys=TECH_KEYS, role="technician")
        c._member_id = "tech-1"
        c._tickets = [{
            "id": "t1", "ticket_number": "T-2026-0001", "status": "in_progress",
            "customer_chann_uid": CUSTOMER_UID, "assigned_to_ref": "tech-1",
            "owner_member_id": "cs-1",
        }]
        c._members = [
            {"id": "cs-1", "chann_uid": "CHN-S-000001", "role": "cs", "status": "active"},
        ]
        c._line_targets = {CUSTOMER_UID: "U-cust", "CHN-S-000001": "U-cs"}

        http = _route_app(c, keys=TECH_KEYS)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/check-out",
            json={"report_data": {"found_issue": "คอมเพรสเซอร์รั่ว",
                                  "work_done": "เปลี่ยนคอมเพรสเซอร์ใหม่"}},
        )
        assert response.status_code == 200, response.text
        rows = _completion_rows(c)
        assert len(rows) == 1 and rows[0][2] == CUSTOMER_UID
        assert "เปลี่ยนคอมเพรสเซอร์ใหม่" in rows[0][4]
        assert _customer_pushes(pushes)[0][1] == "customer"

    def test_a_walk_in_job_still_closes_from_the_screen(self, pushes):
        c = _RouteClient(permission_keys=TECH_KEYS, role="technician")
        c._member_id = "tech-1"
        c._tickets = [{"id": "t1", "ticket_number": "T-2026-0001",
                       "status": "in_progress", "customer_chann_uid": "",
                       "assigned_to_ref": "tech-1"}]
        c._members = []
        http = _route_app(c, keys=TECH_KEYS)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/check-out",
            json={"report_data": {"found_issue": "x", "work_done": "y"}},
        )
        assert response.status_code == 200, response.text
        assert _completion_rows(c) == []

    def test_the_status_route_still_cannot_complete_a_ticket(self):
        """The one other way a ticket could reach `completed` from outside.

        It is refused (review, 6 Sep 2026: closing from here skipped
        check-in, the report and approval), and it has to STAY refused —
        the moment it stops being, a job could be finished with nobody
        telling the customer, which is the bug this file is about.
        """
        c = _RouteClient(permission_keys=["ticket.update"], role="cs")
        c._tickets = [{"id": "t1", "ticket_number": "T-2026-0001",
                       "status": "in_progress", "customer_chann_uid": CUSTOMER_UID}]
        http = _route_app(c, keys=["ticket.close", "ticket.update"], audience="sales")
        for status in ("completed", "open", "in_progress"):
            response = http.patch(
                f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/status", json={"status": status},
            )
            assert response.status_code == 422, f"{status}: {response.text}"
        assert not [r for r in c.recorded if r[0] == "set_ticket_status"]


# ------------------------------------------------- withdrawn: the job reopened


class TestTheJobReopensAfterARejectedReport:
    """The case to think hardest about: the customer was told "finished",
    then the shop sent the report back and the Data Tier put the ticket
    back to `in_progress`. Saying nothing leaves a promise standing that
    the shop has withdrawn — and the customer may need to be home again."""

    async def _reject(self, client):
        await approval_service.on_report_submitted(
            client, license_id=LICENSE_ID, report=client._reports[0],
        )
        steps = await client.approval_steps_for_entity(
            LICENSE_ID, "service_report", "sr-1",
        )
        step = min(
            (s for s in steps if s.get("status") == "pending"),
            key=lambda s: int(s.get("step_order") or 0),
        )
        return await approval_service.act(
            client, license_id=LICENSE_ID, step_id=str(step["id"]), approve=False,
            actor_chann_uid="CHN-S-000001", reason="รูปหน้างานไม่ครบ",
        )

    @pytest.mark.asyncio
    async def test_the_customer_hears_that_it_is_not_finished_after_all(self, pushes):
        c = _shop(permission_keys=CS_KEYS, role="cs")
        c._member_id = "cs-1"
        result = await self._reject(c)
        assert result["report_status"] == "rejected"

        rows = _reopen_rows(c)
        assert len(rows) == 1
        assert rows[0][2] == CUSTOMER_UID
        assert "T-2026-0001" in rows[0][4]
        assert _customer_pushes(pushes)[0][1] == "customer"

    @pytest.mark.asyncio
    async def test_the_approvers_reason_is_not_repeated_to_the_customer(self, pushes):
        """What the shop rejected is between the shop and its technician."""
        c = _shop(permission_keys=CS_KEYS, role="cs")
        c._member_id = "cs-1"
        await self._reject(c)
        assert "รูปหน้างานไม่ครบ" not in _reopen_rows(c)[0][4]
        assert not any("รูปหน้างานไม่ครบ" in str(p[3]) for p in _customer_pushes(pushes))
        # The technician does hear the reason, on their own OA.
        tech = [p for p in pushes if p[1] == "technician"]
        assert any("รูปหน้างานไม่ครบ" in str(p[3]) for p in tech)

    @pytest.mark.asyncio
    async def test_a_walk_in_job_can_still_be_sent_back(self, pushes):
        c = _shop(customer_uid="", permission_keys=CS_KEYS, role="cs")
        c._member_id = "cs-1"
        result = await self._reject(c)
        assert result["report_status"] == "rejected"
        assert _reopen_rows(c) == []


# ----------------------------------------------------------- no second telling


class TestTheSurveyNoLongerAnnouncesCompletion:
    async def _approve_to_the_end(self, client):
        await approval_service.on_report_submitted(
            client, license_id=LICENSE_ID, report=client._reports[0],
        )
        result = {}
        for _ in range(5):
            steps = await client.approval_steps_for_entity(
                LICENSE_ID, "service_report", "sr-1",
            )
            pending = [s for s in steps if s.get("status") == "pending"]
            if not pending:
                break
            step = min(pending, key=lambda s: int(s.get("step_order") or 0))
            result = await approval_service.act(
                client, license_id=LICENSE_ID, step_id=str(step["id"]), approve=True,
                actor_chann_uid="CHN-S-000001",
            )
            if result.get("report_status") == "approved":
                break
        return result

    def test_the_prompt_asks_for_a_rating_and_claims_nothing_else(self):
        for language in ("th", "en"):
            text = approval_service.SURVEY_PROMPT[language].format(ticket="T-2026-0001")
            assert "T-2026-0001" in text
            # It must no longer be an announcement — the completion notice
            # said this days earlier, at check-out.
            assert "เสร็จเรียบร้อยแล้ว" not in text
            assert "is complete" not in text
            assert "เสร็จแล้ว" not in text
        assert "ให้คะแนน" in approval_service.SURVEY_PROMPT["th"]
        assert "rate" in approval_service.SURVEY_PROMPT["en"].lower()

    @pytest.mark.asyncio
    async def test_the_customer_is_never_told_twice_that_the_job_is_done(self, pushes):
        """One job, closed then fully approved: exactly one message says the
        work is finished, and the later one only asks for a rating."""
        c = _shop(permission_keys=CS_KEYS, role="cs")
        c._member_id = "cs-1"
        # The check-out notice.
        await chat_service.announce_job_finished(c, LICENSE_ID, c._reports[0])
        result = await self._approve_to_the_end(c)
        assert result.get("report_status") == "approved"
        assert result.get("survey_sent") is True

        said_finished = [
            p for p in _customer_pushes(pushes)
            if "เสร็จแล้ว" in str(p[3]) or "has finished" in str(p[3])
        ]
        assert len(said_finished) == 1, _customer_pushes(pushes)

        survey = [p for p in _customer_pushes(pushes) if p[0] == "messages"]
        assert len(survey) == 1
        assert "เสร็จเรียบร้อยแล้ว" not in survey[0][3][0]["text"]
        assert "ให้คะแนน" in survey[0][3][0]["text"]

    def test_the_flow_description_still_matches_what_the_customer_gets(self):
        """`describe_workflow` is read aloud to whoever configures approvals;
        it must not promise the survey announces completion."""
        rules = {"steps": [{"order": 1, "approver_type": "role", "approver_ref": "admin"}]}
        summary = approval_service.describe_workflow(rules, "th")
        assert "แบบประเมิน" in summary
