"""Integrity batch (6 Sep 2026), application routes: a ticket action is
taken as the caller, never as a member named in the body; a linked
customer sees only their own reports and none of the shop's people;
records carry their creator as owner; ownership moves only with
reassign_records; the dashboard status route can only cancel.
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

    async def set_customer_owner(self, license_id, customer_id, owner_member_id, actor_id=None):
        self.recorded.append(("set_customer_owner", customer_id, owner_member_id))
        return {"id": customer_id, "owner_member_id": owner_member_id}

    async def list_service_reports(self, license_id, status=None):
        return [
            {"id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1"},
            {"id": "sr-2", "report_id": "SR-2026-0002", "ticket_id": "t2"},
        ]


def _app(client, *, keys, audience="sales"):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="cs", is_owner=False,
            permission_keys=frozenset(keys), audience=audience,
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


@pytest.fixture
def staff():
    client = _Client(permission_keys=["ticket.read", "ticket.update", "customer.create", "deal.create", "ticket.create"])
    client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "customer_chann_uid": "CHN-S-000001"},
                       {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned", "customer_chann_uid": "CHN-C-OTHER"}]
    return _app(client, keys=client._permission_keys), client


@pytest.fixture
def customer():
    client = _Client(permission_keys=["ticket.read", "ticket.create", "customer.read"])
    client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "customer_chann_uid": "CHN-S-000001"},
                       {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned", "customer_chann_uid": "CHN-C-OTHER"}]
    client._members = [{"id": "m-1", "chann_uid": "CHN-T-1", "role": "technician", "status": "active"}]
    return _app(client, keys=client._permission_keys, audience="customer"), client


class TestActingAsYourself:
    @pytest.mark.parametrize("path,record", [
        ("claim", "claim_ticket"), ("reject", "reject_ticket"), ("check-in", "check_in_ticket"),
    ])
    def test_the_body_cannot_name_another_member(self, staff, path, record):
        http, client = staff
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/{path}", json={"member_id": "somebody-else"})
        assert response.status_code == 200, response.text
        call = next(r for r in client.recorded if r[0] == record)
        assert "somebody-else" not in call and "member-1" in call  # the fake's own member id

    def test_check_out_files_the_report_as_the_caller(self, staff):
        http, client = staff
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/check-out",
                             json={"member_id": "somebody-else", "report_data": {"found_issue": "x", "work_done": "y"}})
        assert response.status_code == 200, response.text
        assert [r for r in client.recorded if r[0] == "check_out_ticket"]

    def test_a_customer_may_not_take_ticket_actions(self, customer):
        http, _ = customer
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/claim", json={}).status_code == 403


class TestCustomerScope:
    def test_a_customer_sees_only_reports_of_their_own_jobs(self, customer):
        http, _ = customer
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/service-reports").json()
        assert [r["report_id"] for r in rows] == ["SR-2026-0001"]

    @pytest.mark.parametrize("path", ["technicians", "technician-teams", "tickets/t1/dispatch-check"])
    def test_a_customer_cannot_list_the_shops_people_or_dispatch_state(self, customer, path):
        http, _ = customer
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/{path}").status_code == 403


class TestOwnership:
    def test_a_created_customer_and_deal_carry_their_creator(self, staff):
        http, client = staff
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/customers",
                             json={"first_name": "ก", "last_name": "ข", "phone": "0812345678"})
        assert response.status_code == 201, response.text
        payload = next(r[2] for r in client.recorded if r[0] == "create_customer")
        assert payload["owner_member_id"] == "member-1"
        deal = http.post(f"/api/v1/licenses/{LICENSE_ID}/deals", json={"contact_id": response.json()["id"]})
        assert deal.status_code == 201, deal.text
        assert next(r[2] for r in client.recorded if r[0] == "create_deal")["owner_member_id"] == "member-1"

    def test_a_staff_filed_ticket_is_owned_by_the_cs_who_took_it(self, staff):
        http, client = staff
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets", json={"issue_description": "แอร์ไม่เย็น"})
        assert response.status_code == 201, response.text
        assert next(r[2] for r in client.recorded if r[0] == "create_ticket")["owner_member_id"] == "member-1"

    def test_reassigning_needs_the_permission(self):
        client = _Client(permission_keys=["customer.update"])
        http = _app(client, keys=["customer.update"])
        assert http.patch(f"/api/v1/licenses/{LICENSE_ID}/customers/c1/owner", json={"owner_member_id": "m-2"}).status_code == 403
        http = _app(client, keys=["reassign_records"])
        assert http.patch(f"/api/v1/licenses/{LICENSE_ID}/customers/c1/owner", json={"owner_member_id": "m-2"}).status_code == 200
        assert ("set_customer_owner", "c1", "m-2") in client.recorded


class TestStatusRoute:
    def test_only_cancel_is_possible_from_the_dashboard(self, staff):
        http, client = staff
        for status in ("completed", "open"):
            assert http.patch(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/status", json={"status": status}).status_code == 422
        assert not [r for r in client.recorded if r[0] == "set_ticket_status"]
