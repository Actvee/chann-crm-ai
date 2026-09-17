"""Round 19z — the screen and the chat must describe the same world.

Owner, 17 ก.ย. 2569 (Dev Company One):

  · "Sale OA ในรายงานการซ่อม SR-2026-0003 ขึ้นว่าอนุมัติแล้ว แต่ยังมีรายการ
     ค้างอยู่ใน รอการอนุมัติ"
  · "Tech OA ใน Dashboard ส่วนงานของฉันยังมีงานค้างอยู่ แต่พอพิมพ์งานของฉัน
     ในแชทบอกว่าไม่มีแล้วงานซ่อม"

Both were one thing: a screen deciding for itself what chat decides
through the shared road.

1. The dashboard's approve button PATCHed the report's status straight to
   "approved" and never touched the approval steps, so the step stayed
   `pending` and the report kept its place in "รอการอนุมัติ" — and the
   document, the survey and the notifications the chat road performs were
   all skipped.
2. The technician's "งานของฉัน" on the dashboard had no status test, so
   completed and cancelled jobs stayed on it. Chat's list has excluded
   them since round 6 — chat was right and the screen was wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


class _Client(FakeDataClient):
    async def aclose(self):
        pass

    async def list_service_reports(self, license_id, status=None):
        return [{"id": "sr-3", "report_id": "SR-2026-0003", "ticket_id": "t1",
                 "status": "submitted", "report_data": {}}]

    async def get_service_report(self, license_id, report_id):
        rows = await self.list_service_reports(license_id)
        return next((r for r in rows if str(r.get("id")) == str(report_id)), None)


def _app(client):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="owner", is_owner=True,
            permission_keys=frozenset(["ticket.read", "ticket.update", "approval.view"]),
            audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


@pytest.fixture
def waiting():
    """A report with one step still waiting — the SR-2026-0003 shape."""
    client = _Client(permission_keys=["ticket.read", "ticket.update"])
    client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "completed",
                        "customer_chann_uid": "CHN-C-1", "owner_member_id": "member-1"}]
    client._approval_steps = [{
        "id": "step-1", "entity_type": "service_report", "entity_id": "sr-3",
        "step_order": 1, "status": "pending", "approver_type": "user",
        "approver_ref": "member-1",
    }]
    return _app(client), client


class TestApprovingFromTheScreen:
    def _approve(self, http):
        return http.patch(
            f"/api/v1/licenses/{LICENSE_ID}/service-reports/sr-3/status",
            json={"status": "approved"},
        )

    def test_it_acts_on_the_step_instead_of_writing_the_status(self, waiting):
        http, client = waiting
        assert self._approve(http).status_code == 200
        assert [r for r in client.recorded if r[0] == "act_on_approval_step"], client.recorded

    def test_the_step_stops_being_pending(self, waiting):
        """The whole complaint: approved on one screen, still waiting on
        another."""
        http, client = waiting
        self._approve(http)
        assert [s for s in client._approval_steps if s["status"] == "pending"] == [], (
            client._approval_steps
        )

    def test_rejecting_goes_the_same_way(self, waiting):
        http, client = waiting
        response = http.patch(
            f"/api/v1/licenses/{LICENSE_ID}/service-reports/sr-3/status",
            json={"status": "rejected"},
        )
        assert response.status_code == 200, response.text
        acted = [r for r in client.recorded if r[0] == "act_on_approval_step"]
        assert acted and acted[0][3] is False, client.recorded

    def test_a_report_under_no_rule_is_still_written_directly(self):
        """No step waiting is not an error — it is a report filed under no
        approval rule, and the plain write is the right answer for it."""
        client = _Client(permission_keys=["ticket.read", "ticket.update"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "completed"}]
        client._reports = [{"id": "sr-3", "report_id": "SR-2026-0003", "ticket_id": "t1",
                            "status": "submitted"}]
        client._approval_steps = []
        http = _app(client)
        assert self._approve(http).status_code == 200
        assert [r for r in client.recorded if r[0] == "set_service_report_status"], client.recorded

    def test_a_step_already_acted_on_is_not_acted_on_twice(self):
        client = _Client(permission_keys=["ticket.read", "ticket.update"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "completed"}]
        client._reports = [{"id": "sr-3", "report_id": "SR-2026-0003", "ticket_id": "t1",
                            "status": "submitted"}]
        client._approval_steps = [{
            "id": "step-1", "entity_type": "service_report", "entity_id": "sr-3",
            "step_order": 1, "status": "approved", "approver_type": "user",
            "approver_ref": "member-1",
        }]
        http = _app(client)
        assert self._approve(http).status_code == 200
        assert not [r for r in client.recorded if r[0] == "act_on_approval_step"], client.recorded


class TestTheTechnicianListsAgree:
    """Chat's "งานของฉัน" and the dashboard's must answer the same question.

    The screen is TypeScript, so what is pinned here is the filter itself:
    the dashboard had no status test at all, and that is the whole reason
    the two disagreed. A source check is a poor substitute for running the
    component, but it fails loudly if someone drops the test again.
    """

    SOURCE = ROOT / "presentation/app/liff/technician/TechnicianHome.tsx"

    def test_my_jobs_excludes_finished_work(self):
        text = self.SOURCE.read_text(encoding="utf-8")
        start = text.index("const mine = shown.filter(")
        clause = text[start:text.index(");", start)]
        assert 'x.assigned_to_ref === memberId' in clause, clause
        assert 'x.status !== "completed"' in clause, clause
        assert 'x.status !== "cancelled"' in clause, clause

    def test_chat_excludes_the_same_two(self):
        """The rule this copies: if chat ever changes, this test says so."""
        chat = (ROOT / "application/chann_app/services/chat.py").read_text(encoding="utf-8")
        start = chat.index("# Mine = assigned to me and not finished.")
        assert '("completed", "cancelled")' in chat[start:start + 700]


class TestTheRegisterSaysWhatTheOwnerAsked:
    """Owner: "ทะเบียนสินค้าที่มีการลงทะเบียนลูกค้าแล้วให้ใช้คำว่า มีลูกค้าแล้ว
    แทน ลูกค้าผูก Line แล้ว" — the register's business is whether a unit has
    a customer, not which app that customer uses."""

    def test_both_languages_say_it(self):
        th = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
        en = (ROOT / "presentation/lib/i18n/en.ts").read_text(encoding="utf-8")
        assert 'claimed: "มีลูกค้าแล้ว"' in th, "the Thai register still talks about LINE"
        assert 'claimed: "Has a customer"' in en
        assert 'claimed: "ลูกค้าผูก LINE แล้ว"' not in th
