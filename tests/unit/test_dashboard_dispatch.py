"""Plan B1 (3 Sep) — the Sales tickets page dispatches, edits and cancels
through the same routes chat's handlers call, and the assignee hears
about it in LINE either way.

The dashboard assign route used to dispatch silently; the edit and
status routes did not exist above the Data Tier.
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

    async def assign_ticket(self, license_id, ticket_id, *, target_type, target_ref, actor_id=None):
        self.recorded.append(("assign_ticket", ticket_id, target_type, target_ref))
        return {"id": ticket_id, "ticket_number": "T-2026-0001", "assigned_target_type": target_type,
                "assigned_to_ref": target_ref, "accept_status": "pending", "status": "assigned"}


@pytest.fixture
def harness(monkeypatch):
    client = _Client(permission_keys=["ticket.read", "ticket.update"])
    notices: list[tuple] = []

    async def fake_assigned(c, license_id, ticket, label, language):
        notices.append(("assigned", ticket.get("ticket_number"), label))

    async def fake_change(c, license_id, ticket_id, text, language, text_en=None, **_customer):
        notices.append(("change", ticket_id, text))

    import chann_app.services.chat as chat_module

    monkeypatch.setattr(chat_module, "_notify_assigned_ticket", fake_assigned)
    monkeypatch.setattr(chat_module, "_notify_ticket_change", fake_change)

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="cs", is_owner=False,
            permission_keys=frozenset({"ticket.read", "ticket.update"}), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client, notices


class TestDispatchFromTheQueue:
    def test_assigning_to_a_team_tells_the_team(self, harness):
        http, client, notices = harness
        client._teams = [{"id": "team-1", "team_name": "แอร์"}]
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/assign",
            json={"target_type": "technician_team", "target_ref": "team-1"},
        )
        assert response.status_code == 200, response.text
        assert ("assign_ticket", "t1", "technician_team", "team-1") in client.recorded
        assert notices == [("assigned", "T-2026-0001", "แอร์")]

    def test_editing_keeps_only_the_gates_fields(self, harness):
        http, client, _ = harness
        response = http.patch(
            f"/api/v1/licenses/{LICENSE_ID}/tickets/t1",
            json={"customer_phone": "0812345678", "status": "completed", "service_address": ""},
        )
        assert response.status_code == 200, response.text
        updates = [r for r in client.recorded if r[0] == "update_ticket"]
        assert updates and updates[-1][3] == {"customer_phone": "0812345678"}

    def test_editing_nothing_is_a_422_not_a_silent_ok(self, harness):
        http, _, _ = harness
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1", json={"status": "x"})
        assert response.status_code == 422

    def test_cancelling_tells_the_technician(self, harness):
        http, client, notices = harness
        response = http.patch(
            f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/status", json={"status": "cancelled"},
        )
        assert response.status_code == 200, response.text
        assert any(r[0] == "set_ticket_status" for r in client.recorded)
        assert notices and notices[0][0] == "change" and "ยกเลิก" in notices[0][2]

    def test_an_unknown_status_is_refused(self, harness):
        http, _, _ = harness
        response = http.patch(
            f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/status", json={"status": "in_progress"},
        )
        assert response.status_code == 422
