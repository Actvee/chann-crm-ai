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

    async def get_service_report(self, license_id, report_id):
        rows = await self.list_service_reports(license_id)
        return next((r for r in rows if str(r.get("id")) == str(report_id)), None)

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


# ------------------------------------------------ review D3/D4/D5/D14/D16 (6 Sep 2026)


@pytest.fixture
def technician():
    """A field technician: ticket.* and service_report.* but no
    customer.read — the role template's defining absence."""
    client = _Client(permission_keys=["ticket.read", "ticket.update", "ticket.assign", "service_report.read"])
    client._tickets = [
        {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "assigned_to_ref": "member-1"},
        {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned", "assigned_to_ref": "member-9"},
    ]
    return _app(client, keys=client._permission_keys, audience="technician"), client


class _ReportClient(_Client):
    async def list_service_reports(self, license_id, status=None):
        return [
            {"id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1", "technician_member_id": "member-9"},
            {"id": "sr-2", "report_id": "SR-2026-0002", "ticket_id": "t2", "technician_member_id": "member-1"},
            {"id": "sr-3", "report_id": "SR-2026-0003", "ticket_id": "t3", "technician_member_id": "member-9"},
        ]


class TestFieldScope:
    def test_a_technicians_ticket_list_is_always_scoped_to_them(self, technician):
        http, client = technician
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/tickets").status_code == 200
        assert ("list_tickets", LICENSE_ID, "member-1") in client.recorded
        # Naming somebody else in the query does not widen it.
        http.get(f"/api/v1/licenses/{LICENSE_ID}/tickets?visible_to=member-9")
        assert not [r for r in client.recorded if r[0] == "list_tickets" and r[2] == "member-9"]

    def test_a_dispatcher_keeps_the_whole_queue(self):
        client = _Client(permission_keys=["ticket.read", "customer.read"])
        http = _app(client, keys=client._permission_keys)
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/tickets").status_code == 200
        assert ("list_tickets", LICENSE_ID, None) in client.recorded

    def test_a_technician_reads_only_their_own_reports(self):
        client = _ReportClient(permission_keys=["ticket.read", "service_report.read"])
        client._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "assigned_to_ref": "member-1"},
            {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned", "assigned_to_ref": "member-9"},
            {"id": "t3", "ticket_number": "T-2026-0003", "status": "assigned", "assigned_to_ref": "member-9"},
        ]
        http = _app(client, keys=client._permission_keys, audience="technician")
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/service-reports").json()
        # sr-1: on my job; sr-2: filed by me; sr-3: neither.
        assert [r["report_id"] for r in rows] == ["SR-2026-0001", "SR-2026-0002"]

    def test_cs_reads_every_report(self):
        client = _ReportClient(permission_keys=["ticket.read", "customer.read"])
        http = _app(client, keys=client._permission_keys)
        assert len(http.get(f"/api/v1/licenses/{LICENSE_ID}/service-reports").json()) == 3


class _PdfClient(_Client):
    async def list_service_reports(self, license_id, status=None):
        return list(self._reports)


class TestCustomerReportPdf:
    def _client(self, generated_document_id="doc-1"):
        client = _PdfClient(permission_keys=["ticket.read", "ticket.create", "customer.read"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "completed", "customer_chann_uid": "CHN-S-000001"},
                           {"id": "t2", "ticket_number": "T-2026-0002", "status": "completed", "customer_chann_uid": "CHN-C-OTHER"}]
        client._reports = [
            {"id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "approved", "generated_document_id": generated_document_id},
            {"id": "sr-2", "report_id": "SR-2026-0002", "ticket_id": "t2", "status": "approved", "generated_document_id": "doc-2"},
        ]
        return client

    def test_a_customer_opens_the_paper_for_their_own_job(self, monkeypatch):
        from chann_app.config import settings

        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        monkeypatch.setattr(settings, "jwt_secret", "unit-test-jwt-secret")
        client = self._client()
        http = _app(client, keys=client._permission_keys, audience="customer")
        res = http.post(f"/api/v1/licenses/{LICENSE_ID}/service-reports/sr-1/document", json={})
        assert res.status_code == 200, res.text
        assert res.json()["document_id"] == "doc-1" and res.json()["url"].startswith("https://app.example/api/v1/documents/")

    def test_but_not_somebody_elses_and_never_issues_one(self):
        client = self._client(generated_document_id=None)
        http = _app(client, keys=client._permission_keys, audience="customer")
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/service-reports/sr-2/document", json={}).status_code == 404
        res = http.post(f"/api/v1/licenses/{LICENSE_ID}/service-reports/sr-1/document", json={"reissue": True})
        assert res.status_code == 409 and res.json()["detail"]["error"] == "not_issued"


class TestDocumentFilename:
    @pytest.mark.parametrize("document,expected", [
        ({"document_type": "quote", "data_snapshot": {"quote": {"quote_id": "Q-2026-0007"}}}, "quote-Q-2026-0007.pdf"),
        ({"document_type": "service_report", "data_snapshot": {"report": {"report_id": "SR-2026-0003"}}}, "report-SR-2026-0003.pdf"),
        ({"document_type": "quote", "data_snapshot": {"quote": {"quote_id": "../x y"}}}, "quote-x-y.pdf"),
        ({"document_type": "quote"}, "quote.pdf"),
    ])
    def test_each_document_downloads_under_its_own_code(self, document, expected):
        assert routers_phase2._document_filename(document) == expected

    def test_the_header_names_the_document(self, staff, monkeypatch):
        from chann_app.services.storage import base as storage_base

        http, client = staff

        class Store:
            async def get(self, *, path):
                return b"%PDF-1.4"

        monkeypatch.setattr(storage_base, "get_document_store", lambda *a, **k: Store())

        async def document(license_id, document_id):
            return {"id": document_id, "sha256": "abc", "output_path": "documents/x.pdf",
                    "document_type": "service_report", "data_snapshot": {"report": {"report_id": "SR-2026-0003"}}}

        client.get_generated_document = document
        http = _app(client, keys=["quote.read"])
        res = http.get(f"/api/v1/licenses/{LICENSE_ID}/documents/doc-1")
        assert res.status_code == 200, res.text
        assert res.headers["content-disposition"] == 'inline; filename="report-SR-2026-0003.pdf"'


class TestUnlinkedCustomer:
    async def test_a_customer_without_a_shop_still_gets_a_principal(self, monkeypatch):
        from chann_app.services import authorization

        class Client(_Client):
            async def resolve_identity(self, line_user_id, primary_role, display_name=None):
                return {"chann_uid": "CHN-C-000009", "primary_role": primary_role}

            async def memberships_of(self, chann_uid, oa=None):
                return []

            async def get_active_tenant(self, chann_uid, oa):
                return None

        async def fake_verify(token, audience):
            return {"sub": "U9", "name": "x"}

        monkeypatch.setattr(authorization, "verify_id_token", fake_verify)
        principal = await authorization.resolve_tenant_principal(
            Client(permission_keys=[]), x_liff_id_token="t", x_liff_audience="customer", x_license_id="",
        )
        assert principal.is_customer and principal.license_id == ""
        # Staff with no membership are still refused.
        with pytest.raises(Exception) as refused:
            await authorization.resolve_tenant_principal(
                Client(permission_keys=[]), x_liff_id_token="t", x_liff_audience="sales", x_license_id="",
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_the_storefront_is_open_to_them_and_tenant_routes_are_not(self):
        from test_customer_home import PRODUCTS

        client = _Client(permission_keys=[], storefront_results=PRODUCTS)
        client._members = [{"id": "m-1", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"}]

        async def override_principal():
            return TenantPrincipal(license_id="", chann_uid="CHN-C-000009", role="customer", is_owner=False,
                                   permission_keys=frozenset({"ticket.read", "ticket.create", "customer.read"}),
                                   audience="customer")

        async def override_client():
            yield client

        app = FastAPI()
        app.include_router(routers_phase2.router)
        app.dependency_overrides[routers_phase2.get_data_client] = override_client
        app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
        http = TestClient(app)
        assert len(http.get("/api/v1/storefront/products").json()) == 2
        res = http.post("/api/v1/storefront/interest", json={"license_id": LICENSE_ID, "product_name": "พัดลมไอเย็น"})
        assert res.status_code == 201, res.text
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/tickets").status_code == 403


class TestNewTicketNotice:
    def test_each_dispatcher_hears_in_their_own_language(self, monkeypatch):
        """Review D16: the route passes "th", and it does not matter — the
        notice carries both texts and the recipient's preference decides."""
        import chann_app.services.notify as notify_module

        # The fake's authorization_context answers with these keys for every
        # member, so both members count as dispatchers (ticket.assign).
        client = _Client(permission_keys=["ticket.read", "ticket.create", "ticket.assign", "customer.read"])
        client._members = [
            {"id": "m-th", "chann_uid": "CHN-TH", "role": "owner", "status": "active"},
            {"id": "m-en", "chann_uid": "CHN-EN", "role": "admin", "status": "active"},
        ]
        client._prefs = {"CHN-EN": {"language": "en"}}
        client._line_targets = {"CHN-TH": "U-th", "CHN-EN": "U-en"}
        pushed: list[tuple[str, str]] = []

        async def fake_push(oa, to, text):
            pushed.append((to, text))
            return ["mid"]

        monkeypatch.setattr(notify_module, "push_text", fake_push)
        http = _app(client, keys=client._permission_keys, audience="customer")
        res = http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets", json={"issue_description": "แอร์ไม่เย็น"})
        assert res.status_code == 201, res.text
        by_target = dict(pushed)
        assert by_target["U-th"].startswith("แจ้งซ่อมใหม่")
        assert by_target["U-en"].startswith("New repair request")
