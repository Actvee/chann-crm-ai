"""An appointment can be edited and removed from the dashboard.

Reported by the owner, 9 Sep 2026: "ตอนนี้นัดหมายเหมือนจะแก้ไข หรือลบไม่ได้".
He was right, and it was not a UI oversight — there was no route. The Data
Tier could create a follow-up, list it and set its status, and nothing
else; the Application Tier mirrored exactly that. So the dashboard's
"เลื่อนนัด" button booked a second appointment and cancelled the first,
and chat's `_handle_reminder_move` did the same thing, with a docstring
saying so.

That gave the right answer and the wrong record: every postponement minted
a new id, so the reminder already pushed to LINE named a cancelled row and
the appointment's audit trail restarted from empty.

This file covers the Application Tier seam — the routes, their permission
gate, and the exact shape of what they hand to the Data Tier. The
repository rules and tenant isolation are proved against a real database
in tests/integration/test_appointment_edit_data.py, because a scope that
holds only in a fake proves nothing about the SQL.
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

from chann_app import routers_phase6  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402

ME = "CHN-S-000001"
# The routes type the path parameter as a UUID, so "FU-1" is a 422 before
# any handler runs.
FU = "3f1a6c8e-0f2b-4c7d-9a51-2b8e4d6f1a90"


class _Client(FakeDataClient):
    async def aclose(self):
        pass


def _harness(keys):
    client = _Client(permission_keys=list(keys))
    client._follow_ups = [{
        "id": FU, "entity_type": "customer", "entity_id": "cust-1",
        "due_date": "2026-09-20", "due_time": "09:00:00",
        "notes": "ไปดูหน้างาน", "status": "pending",
    }]

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid=ME, role="sales", is_owner=False,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase6.router)
    app.dependency_overrides[routers_phase6.get_data_client] = override_client
    app.dependency_overrides[routers_phase6.get_tenant_principal] = override_principal
    return TestClient(app), client


@pytest.fixture
def harness():
    return _harness(["followup.read", "followup.create", "followup.update"])


class TestEditingAnAppointment:
    def test_the_route_exists_at_all(self, harness):
        """The whole bug in one assertion: PATCH used to be a 405."""
        http, _ = harness
        response = http.patch(f"/api/v1/follow-ups/{FU}", json={"due_date": "2026-09-25"})
        assert response.status_code == 200, response.text

    def test_the_new_date_and_time_reach_the_data_tier(self, harness):
        http, client = harness
        response = http.patch(
            f"/api/v1/follow-ups/{FU}",
            json={"due_date": "2026-09-25", "due_time": "13:00:00"},
        )
        assert response.status_code == 200, response.text
        sent = [r for r in client.recorded if r[0] == "update_follow_up"]
        assert sent and sent[-1][2] == FU
        assert sent[-1][3] == {"due_date": "2026-09-25", "due_time": "13:00:00"}
        # The actor is the verified principal, never anything from the body.
        assert sent[-1][4] == ME

    def test_only_what_was_sent_is_forwarded(self, harness):
        """`exclude_unset`, not "every field with None for the rest".

        Without it a caller changing the time would blank the note, and
        the field it blanked would be the one nobody was looking at.
        """
        http, client = harness
        http.patch(f"/api/v1/follow-ups/{FU}", json={"due_time": "15:30:00"})
        sent = [r for r in client.recorded if r[0] == "update_follow_up"][-1][3]
        assert sent == {"due_time": "15:30:00"}
        assert client._follow_ups[0]["notes"] == "ไปดูหน้างาน"

    def test_an_explicit_null_clears_the_field(self, harness):
        """Sending notes: null is a different request from not sending it.

        A whole-day reminder is due_time: null, so this is the only way to
        turn an appointment back into one.
        """
        http, client = harness
        http.patch(f"/api/v1/follow-ups/{FU}", json={"due_time": None})
        sent = [r for r in client.recorded if r[0] == "update_follow_up"][-1][3]
        assert sent == {"due_time": None}
        assert client._follow_ups[0]["due_time"] is None

    def test_changing_nothing_is_refused_rather_than_a_silent_ok(self, harness):
        http, client = harness
        response = http.patch(f"/api/v1/follow-ups/{FU}", json={})
        assert response.status_code == 400
        assert not [r for r in client.recorded if r[0] == "update_follow_up"]

    def test_editing_needs_followup_update(self):
        http, client = _harness(["followup.read", "followup.create"])
        response = http.patch(f"/api/v1/follow-ups/{FU}", json={"due_date": "2026-09-25"})
        assert response.status_code == 403
        assert "followup.update" in response.json()["detail"]
        assert not [r for r in client.recorded if r[0] == "update_follow_up"]

    def test_editing_does_not_need_followup_create(self):
        """The gate that made the old dashboard button need both keys.

        Moving an appointment was create-then-cancel, so someone allowed
        to edit but not to create could not postpone one. One PATCH ends
        that.
        """
        http, client = _harness(["followup.read", "followup.update"])
        response = http.patch(f"/api/v1/follow-ups/{FU}", json={"due_date": "2026-09-25"})
        assert response.status_code == 200, response.text
        assert [r for r in client.recorded if r[0] == "update_follow_up"]


class TestRemovingAnAppointment:
    def test_delete_removes_the_row(self, harness):
        http, client = harness
        response = http.delete(f"/api/v1/follow-ups/{FU}")
        assert response.status_code == 204, response.text
        assert client._follow_ups == []
        assert [r for r in client.recorded if r[0] == "delete_follow_up"]

    def test_delete_is_gated_on_followup_update(self):
        """followup.update, not a followup.delete key.

        The catalogue has never had one, and adding a permission key is a
        migration and a role-template change. Deleting is an edit down to
        nothing — the same call note.delete already makes.
        """
        http, client = _harness(["followup.read", "followup.create"])
        response = http.delete(f"/api/v1/follow-ups/{FU}")
        assert response.status_code == 403
        assert "followup.update" in response.json()["detail"]
        assert client._follow_ups, "the row must survive a refused delete"

    def test_the_actor_is_recorded(self, harness):
        http, client = harness
        http.delete(f"/api/v1/follow-ups/{FU}")
        call = [r for r in client.recorded if r[0] == "delete_follow_up"][-1]
        assert call[1] == LICENSE_ID and call[2] == FU and call[3] == ME


class TestChatAndTheDashboardDoTheSameThing:
    def test_both_surfaces_call_the_same_client_method(self):
        """Parity is the owner's rule, and the reason this bug existed on
        both sides at once. `check-parity.py` enforces it mechanically;
        this pins the seam it cannot see."""
        from chann_app.data_client import DataClient
        from chann_app.services import chat

        assert hasattr(DataClient, "update_follow_up")
        assert hasattr(DataClient, "delete_follow_up")
        assert chat.ACTION_PERMISSIONS[("update", "followup")] == "followup.update"
        assert chat.ACTION_PERMISSIONS[("delete", "followup")] == "followup.update"
