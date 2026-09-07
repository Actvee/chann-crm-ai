"""Review batch C (6 Sep 2026), sales LIFF ↔ API, plus E6/E7 and C4/C8/C9.

What the schema sends back is what the router assigns (C1/C2); a
suspended shop is read-only at the principal (C4); reading the catalogue
is its own key (C8); ticket.assign / ticket.close mean something and the
never-enforced keys are gone (C9); one customer is one fetch (C10); the
API sends reason codes the page translates (C11); technicians are found
by capability (C15); a ticket field can be cleared (C16); notes carry
their author (C18); an ownership transfer reaches the nominee (E6); a
sales group can be filled (E7).
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import authorization  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from chann_data import permissions as P  # noqa: E402
from chann_data import schemas as S  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402

TRANSFER_ID = "33333333-3333-3333-3333-333333333333"


# ------------------------------------------------------------ C1 / C2 schemas


class TestSchemasSendWhatTheRouterAssigns:
    def test_deal_out_carries_value_timing_and_the_lost_reason(self):
        for field in ("expected_close_date", "amount", "currency", "lost_reason"):
            assert field in S.DealOut.model_fields, field

    def test_quote_out_carries_the_terms(self):
        for field in ("valid_until", "discount_percent", "discount_amount"):
            assert field in S.QuoteOut.model_fields, field

    def test_deal_out_round_trips_through_the_router_helper(self):
        from chann_data.routers.internal import _deal_out

        now = datetime.now(timezone.utc)
        deal = SimpleNamespace(
            id=uuid.uuid4(), license_id=uuid.uuid4(), deal_id="D-2026-0001",
            contact_id=uuid.uuid4(), stage="lost", owner_member_id=None, notes=None,
            archived_at=None, created_at=now, updated_at=now,
            expected_close_date=date(2026, 9, 30), amount=Decimal("12500.00"),
            currency="THB", lost_reason="ราคาแพงกว่าคู่แข่ง",
        )
        out = _deal_out(deal, []).model_dump()
        assert out["amount"] == Decimal("12500.00")
        assert out["expected_close_date"] == date(2026, 9, 30)
        assert out["lost_reason"] == "ราคาแพงกว่าคู่แข่ง"

    def test_transfer_out_names_both_people(self):
        for field in ("from_chann_uid", "to_chann_uid"):
            assert field in S.OwnershipTransferOut.model_fields


# ------------------------------------------------------------ C8 / C9 catalogue


class TestCatalogue:
    def test_product_read_exists_and_every_staff_template_has_it(self):
        assert "product.read" in P.PERMISSION_KEYS
        assert "product.read" in P.PERMISSION_DESCRIPTIONS
        for role in ("member", "cs", "technician", "admin"):
            assert "product.read" in P.DEFAULT_ROLE_TEMPLATES[role], role

    def test_keys_nothing_enforced_are_gone(self):
        for key in ("chat_session.claim", "chat_session.transfer", "assignment_rule.manage",
                    "billing.view", "billing.manage"):
            assert key not in P.PERMISSION_KEYS, key
            assert key not in P.PERMISSION_DESCRIPTIONS, key
        for template in P.DEFAULT_ROLE_TEMPLATES.values():
            if template is not None:
                assert not {"billing.view", "chat_session.claim"} & template

    def test_descriptions_match_the_catalogue_exactly(self):
        assert set(P.PERMISSION_DESCRIPTIONS) == P.PERMISSION_KEYS


# ------------------------------------------------------------ C4 suspended principal


class _Identity(FakeDataClient):
    license_status = "active"

    async def resolve_identity(self, line_user_id, primary_role, display_name=None):
        return {"chann_uid": "CHN-S-000001", "primary_role": primary_role}

    async def memberships_of(self, chann_uid, oa=None):
        return [{"license_id": LICENSE_ID, "license_code": "TESTCO",
                 "company_name": "บริษัททดสอบ", "license_status": self.license_status}]


@pytest.fixture
def verified(monkeypatch):
    async def fake_verify(token, audience):
        return {"sub": "U1", "name": "x"}

    monkeypatch.setattr(authorization, "verify_id_token", fake_verify)


class TestSuspendedTenantIsReadOnly:
    async def test_a_get_carries_the_status(self, verified):
        client = _Identity(permission_keys=["customer.read"])
        client.license_status = "suspended"
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method="GET",
        )
        assert principal.is_suspended and principal.license_status == "suspended"

    @pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
    async def test_a_write_is_423_with_a_translatable_body(self, verified, method):
        client = _Identity(permission_keys=["customer.create"])
        client.license_status = "suspended"
        with pytest.raises(HTTPException) as exc:
            await authorization.resolve_tenant_principal(
                client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method=method,
            )
        assert exc.value.status_code == 423
        assert exc.value.detail == {"error": "tenant_suspended"}

    async def test_an_active_shop_writes_as_before(self, verified):
        client = _Identity(permission_keys=["customer.create"])
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method="POST",
        )
        assert not principal.is_suspended

    def test_both_routers_hand_the_method_to_the_principal(self):
        import inspect

        from chann_app import routers_phase6

        for module in (routers_phase2, routers_phase6):
            assert "request" in inspect.signature(module.get_tenant_principal).parameters

    def test_require_any_accepts_either_key(self):
        principal = TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="x", role="r", is_owner=False,
            permission_keys=frozenset({"ticket.update"}),
        )
        principal.require_any("ticket.assign", "ticket.update")
        with pytest.raises(HTTPException):
            principal.require_any("ticket.assign", "ticket.close")


# ------------------------------------------------------------ application routes


class _Client(FakeDataClient):
    async def aclose(self):
        pass

    async def assign_ticket(self, license_id, ticket_id, *, target_type, target_ref, actor_id=None):
        self.recorded.append(("assign_ticket", ticket_id, target_type, target_ref))
        return {"id": ticket_id, "ticket_number": "T-2026-0001", "status": "assigned"}

    async def request_ownership_transfer(self, license_id, from_chann_uid, to_chann_uid):
        self.recorded.append(("request_ownership_transfer", from_chann_uid, to_chann_uid))
        return {"id": TRANSFER_ID, "status": "pending", "from_chann_uid": from_chann_uid,
                "to_chann_uid": to_chann_uid}

    async def list_ownership_transfers(self, license_id, status="pending"):
        return list(getattr(self, "_transfers", []))

    async def accept_ownership_transfer(self, license_id, transfer_id, accepting_chann_uid, actor_id=None):
        raise DataTierError(409, "only the nominated new owner can accept")

    async def list_sales_groups(self, license_id):
        return list(getattr(self, "_groups", []))

    async def create_sales_group(self, license_id, group_name):
        self.recorded.append(("create_sales_group", group_name))
        return {"id": "g-1", "group_name": group_name}

    async def delete_sales_group(self, license_id, group_id):
        self.recorded.append(("delete_sales_group", group_id))

    async def list_sales_group_members(self, license_id, group_id):
        return [{"id": "m-2", "chann_uid": "CHN-S-2", "role": "member", "status": "active", "group_id": group_id}]

    async def add_sales_group_member(self, license_id, group_id, member_id):
        self.recorded.append(("add_sales_group_member", group_id, member_id))
        return {"id": "gm-1", "group_id": group_id, "member_id": member_id}

    async def remove_sales_group_member(self, license_id, group_id, member_id):
        self.recorded.append(("remove_sales_group_member", group_id, member_id))

    async def create_quote(self, license_id, payload, actor_id=None):
        raise DataTierError(409, "this deal has no products yet — add at least one before quoting")

    async def set_quote_status(self, license_id, quote_id, status, actor_id=None):
        raise DataTierError(409, "cannot move a quote from 'draft' to 'accepted'")


def _app(client, *, keys, owner=False, audience="sales", chann_uid="CHN-S-000001"):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid=chann_uid, role="cs", is_owner=owner,
            permission_keys=frozenset(keys), audience=audience,
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


@pytest.fixture(autouse=True)
def _quiet_notifications(monkeypatch):
    """The dispatch routes tell people in LINE; not the point here."""
    import chann_app.services.chat as chat_module

    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_module, "_notify_assigned_ticket", silent)
    monkeypatch.setattr(chat_module, "_notify_ticket_change", silent)


class TestProductsAreReadable:
    def test_product_read_alone_lists_the_catalogue(self):
        client = _Client(permission_keys=["product.read"])
        client._products = [{"id": "p1", "product_id": "FAN001", "product_name": "พัดลม"}]
        assert _app(client, keys=["product.read"]).get(f"/api/v1/licenses/{LICENSE_ID}/products").status_code == 200
        assert _app(client, keys=["product.manage"]).get(f"/api/v1/licenses/{LICENSE_ID}/products").status_code == 200
        assert _app(client, keys=["deal.read"]).get(f"/api/v1/licenses/{LICENSE_ID}/products").status_code == 403


class TestOneCustomer:
    def test_a_customer_is_one_fetch(self):
        client = _Client(customers=[{"id": "c1", "customer_id": "C-2026-0001", "stage": "lead",
                                     "customer_chann_uid": "CHN-C-1"}])
        http = _app(client, keys=["customer.read"])
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/customers/c1").json()["customer_id"] == "C-2026-0001"
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/customers/nope").status_code == 404

    def test_a_linked_customer_reads_only_their_own_record(self):
        client = _Client(customers=[
            {"id": "c1", "customer_id": "C-1", "stage": "contact", "customer_chann_uid": "CHN-S-000001"},
            {"id": "c2", "customer_id": "C-2", "stage": "contact", "customer_chann_uid": "CHN-C-OTHER"},
        ])
        http = _app(client, keys=["customer.read"], audience="customer")
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/customers/c1").status_code == 200
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/customers/c2").status_code == 404


class TestTicketKeysMeanSomething:
    def test_ticket_assign_dispatches_and_ticket_update_still_does(self):
        client = _Client()
        body = {"target_type": "technician", "target_ref": "m-1"}
        for keys, expected in ((["ticket.assign"], 200), (["ticket.update"], 200), (["ticket.read"], 403)):
            http = _app(client, keys=keys)
            assert http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/assign", json=body).status_code == expected, keys

    def test_ticket_close_cancels_and_ticket_update_still_does(self):
        client = _Client()
        for keys, expected in ((["ticket.close"], 200), (["ticket.update"], 200), (["ticket.assign"], 403)):
            http = _app(client, keys=keys)
            response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/status", json={"status": "cancelled"})
            assert response.status_code == expected, (keys, response.text)

    def test_an_empty_value_clears_the_field(self):
        client = _Client()
        http = _app(client, keys=["ticket.update"])
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1",
                              json={"serial_number": "", "customer_phone": "0812345678"})
        assert response.status_code == 200, response.text
        fields = next(r[3] for r in client.recorded if r[0] == "update_ticket")
        assert fields == {"serial_number": None, "customer_phone": "0812345678"}


class TestTechniciansByCapability:
    def test_a_field_role_is_found_by_what_it_can_do(self):
        client = _Client()
        client._members = [
            {"id": "m-1", "chann_uid": "CHN-T-1", "role": "ทีมติดตั้ง", "status": "active"},
            {"id": "m-2", "chann_uid": "CHN-S-2", "role": "cs", "status": "active"},
            {"id": "m-3", "chann_uid": "CHN-T-3", "role": "technician", "status": "active"},
        ]
        client._roles = [
            {"role_name": "ทีมติดตั้ง", "is_owner": False,
             "permission_keys": ["ticket.read", "ticket.update", "service_report.create"]},
            {"role_name": "cs", "is_owner": False,
             "permission_keys": ["ticket.update", "service_report.create", "approval.approve"]},
            {"role_name": "technician", "is_owner": False, "permission_keys": []},
        ]
        rows = _app(client, keys=["ticket.read"]).get(f"/api/v1/licenses/{LICENSE_ID}/technicians").json()
        assert sorted(r["id"] for r in rows) == ["m-1", "m-3"]


class TestReasonCodes:
    def test_a_quote_on_a_bare_deal_says_why_in_a_code(self):
        client = _Client()
        response = _app(client, keys=["quote.create"]).post(
            f"/api/v1/licenses/{LICENSE_ID}/quotes", json={"deal_id": "d1"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "deal_has_no_products"

    def test_an_illegal_quote_move_has_a_code(self):
        client = _Client()
        response = _app(client, keys=["quote.update"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/quotes/q1/status", json={"status": "accepted"},
        )
        assert response.json()["detail"]["reason_code"] == "quote_transition_not_allowed"

    def test_permissions_report_the_license_status(self):
        body = _app(_Client(), keys=["customer.read"]).get(f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert body["license_status"] == "active"


class TestNotesCarryTheirAuthor:
    def test_the_author_is_named(self):
        client = _Client()
        client._notes = [{"id": "n1", "entity_type": "customer", "entity_id": "c1", "body": "โทรแล้ว",
                          "author_chann_uid": "CHN-S-9"}]
        client._profiles = {"CHN-S-9": {"first_name": "สมชาย", "last_name": "ใจดี"}}
        rows = _app(client, keys=["customer.read"]).get(
            f"/api/v1/licenses/{LICENSE_ID}/notes?entity_type=customer&entity_id=c1",
        ).json()
        assert rows[0]["author_display_name"] == "สมชาย ใจดี"


class TestOwnershipTransfer:
    def test_the_nominee_is_told(self):
        client = _Client()
        http = _app(client, keys=[], owner=True)
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/ownership-transfers", json={"to_chann_uid": "CHN-S-2"})
        assert response.status_code == 201, response.text
        assert ("request_ownership_transfer", "CHN-S-000001", "CHN-S-2") in client.recorded
        notice = next(r for r in client.recorded if r[0] == "create_notification")
        assert notice[2] == "CHN-S-2" and notice[3] == "transfer_request"

    def test_only_the_owner_asks_and_only_the_parties_see_it(self):
        client = _Client()
        client._transfers = [{"id": TRANSFER_ID, "status": "pending",
                              "from_chann_uid": "CHN-S-000001", "to_chann_uid": "CHN-S-2"}]
        assert _app(client, keys=["member.manage"]).post(
            f"/api/v1/licenses/{LICENSE_ID}/ownership-transfers", json={"to_chann_uid": "CHN-S-2"},
        ).status_code == 403
        assert len(_app(client, keys=[], owner=True).get(f"/api/v1/licenses/{LICENSE_ID}/ownership-transfers").json()) == 1
        assert len(_app(client, keys=[], chann_uid="CHN-S-2").get(f"/api/v1/licenses/{LICENSE_ID}/ownership-transfers").json()) == 1
        assert _app(client, keys=[], chann_uid="CHN-S-3").get(f"/api/v1/licenses/{LICENSE_ID}/ownership-transfers").json() == []

    def test_a_refused_acceptance_carries_a_code(self):
        response = _app(_Client(), keys=[], chann_uid="CHN-S-3").post(
            f"/api/v1/licenses/{LICENSE_ID}/ownership-transfers/{TRANSFER_ID}/accept",
        )
        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "not_nominee"

    def test_members_with_names_are_for_the_owner_and_people_managers(self):
        client = _Client()
        client._members = [{"id": "m-2", "chann_uid": "CHN-S-2", "role": "member", "status": "active"}]
        client._profiles = {"CHN-S-2": {"first_name": "สมหญิง"}}
        assert _app(client, keys=[], owner=True).get(f"/api/v1/licenses/{LICENSE_ID}/members").json()[0]["display_name"] == "สมหญิง"
        assert _app(client, keys=["team.manage"]).get(f"/api/v1/licenses/{LICENSE_ID}/members").status_code == 200
        assert _app(client, keys=["deal.read"]).get(f"/api/v1/licenses/{LICENSE_ID}/members").status_code == 403


class TestSalesGroups:
    def test_a_group_can_be_made_filled_and_emptied(self):
        client = _Client()
        client._profiles = {"CHN-S-2": {"first_name": "สมหญิง"}}
        http = _app(client, keys=["team.manage"])
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/sales-groups", json={"group_name": "ทีมเหนือ"}).status_code == 201
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/sales-groups/g-1/members", json={"member_id": "m-2"}).status_code == 201
        members = http.get(f"/api/v1/licenses/{LICENSE_ID}/sales-groups/g-1/members").json()
        assert members[0]["display_name"] == "สมหญิง"
        assert http.delete(f"/api/v1/licenses/{LICENSE_ID}/sales-groups/g-1/members/m-2").status_code == 204
        assert http.delete(f"/api/v1/licenses/{LICENSE_ID}/sales-groups/g-1").status_code == 204
        assert ("add_sales_group_member", "g-1", "m-2") in client.recorded
        assert ("remove_sales_group_member", "g-1", "m-2") in client.recorded

    def test_it_takes_team_manage(self):
        http = _app(_Client(), keys=["deal.read"])
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/sales-groups").status_code == 403
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/sales-groups", json={"group_name": "x"}).status_code == 403


class TestDataClientRoutes:
    """The new client calls map onto real Data-tier routes (check-client
    does the same across the whole client; this pins the ones added here)."""

    def test_new_methods_hit_existing_routes(self):
        import re

        from chann_data.main import app as data_app

        routes = [(p, getattr(r, "methods", set())) for r in data_app.routes if (p := getattr(r, "path", ""))]

        def has(method, path):
            probe = re.sub(r"\{[^}]+\}", "X", path)
            return any(
                re.sub(r"\{[^}]+\}", "X", p) == probe and method in methods for p, methods in routes
            )

        assert has("GET", "/internal/v1/licenses/{license_id}/ownership-transfers")
        assert has("GET", "/internal/v1/licenses/{license_id}/sales-groups/{group_id}/members")
        assert has("DELETE", "/internal/v1/licenses/{license_id}/sales-groups/{group_id}/members/{member_id}")
        assert has("GET", "/internal/v1/licenses/{license_id}/customers/{customer_id}")
