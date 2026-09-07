"""Backend review fixes (6 Sep 2026, report section E).

E1  a notification whose entity_id is not a UUID is stored without it
    rather than lost to a 422; a linked customer now points at the row.
E8  a LINE-linked person whose phone the shop already keyed in is attached
    to that row instead of the duplicate being counted as success.
E14 the approved-report notice carries its own type.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import approval, notify, onboarding  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402

ROW_ID = str(uuid.uuid4())


class _Client(FakeDataClient):
    """The chat fake, plus what these paths need: the identity-attach route
    and a create_customer that hands back a UUID like the real one."""

    def __init__(self, *, existing_by_phone=None, link_raises=None, **kw):
        super().__init__(**kw)
        self._existing_by_phone = existing_by_phone
        self._link_raises = link_raises
        self.notifications: list[dict] = []

    async def link_customer_identity(self, license_id, *, phone, customer_chann_uid, actor_id=None):
        self.recorded.append(("link_customer_identity", license_id, phone, customer_chann_uid))
        if self._link_raises:
            raise self._link_raises
        row = self._existing_by_phone
        if row is None:
            return None
        return {**row, "customer_chann_uid": customer_chann_uid}

    async def create_customer(self, license_id, payload, actor_id=None):
        row = await super().create_customer(license_id, payload, actor_id)
        row["id"] = str(uuid.uuid4())
        row["customer_chann_uid"] = payload.get("customer_chann_uid")
        return row

    async def create_notification(self, license_id, **kw):
        self.notifications.append(kw)
        return await super().create_notification(license_id, **kw)


def _shop(client):
    client._members = [
        {"id": "m-1", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"},
        {"id": "m-2", "chann_uid": "CHN-TECH", "role": "technician", "status": "active"},
    ]
    client._profiles = {"CHN-C-1": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"}}


class TestNotificationEntityId:
    async def test_a_business_code_is_dropped_not_fatal(self, caplog):
        client = _Client()
        row = await notify.send_notification(
            client, license_id=LICENSE_ID, target_chann_uid="CHN-OWNER", target_line_user_id=None,
            type="chat_session_new", message="x", entity_type="customer_link", entity_id="CHN-C-000001",
        )
        assert row["id"]
        assert client.notifications[0]["entity_id"] is None
        assert "non-UUID entity_id" in caplog.text

    async def test_a_uuid_passes_through(self):
        client = _Client()
        await notify.send_notification(
            client, license_id=LICENSE_ID, target_chann_uid="CHN-OWNER", target_line_user_id=None,
            type="chat_session_new", message="x", entity_type="customer", entity_id=ROW_ID,
        )
        assert client.notifications[0]["entity_id"] == ROW_ID

    async def test_none_stays_none(self):
        client = _Client()
        await notify.send_notification(
            client, license_id=LICENSE_ID, target_chann_uid="CHN-OWNER", target_line_user_id=None,
            type="chat_session_new", message="x",
        )
        assert client.notifications[0]["entity_id"] is None

    def test_the_new_types_route_to_an_oa(self):
        assert notify.TYPE_TO_OA["approval_approved"] == "technician"
        assert notify.TYPE_TO_OA["trial_expiring"] == "sales"


class TestCustomerLinkOnboarding:
    async def test_a_phone_match_attaches_the_identity_instead_of_creating(self):
        client = _Client(existing_by_phone={"id": ROW_ID, "customer_id": "C-2026-0007", "phone": "0812345678"})
        _shop(client)
        client._settings = [{"setting_key": "auto_accept_new_customers", "setting_value": True}]
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name="Somchai",
        )
        assert result["linked"] is True and result["created"] is True
        assert result["customer_id"] == ROW_ID
        assert not [r for r in client.recorded if r[0] == "create_customer"]
        link = [r for r in client.recorded if r[0] == "link_customer_identity"]
        assert link and link[0][2] == "0812345678" and link[0][3] == "CHN-C-1"
        note = client.notifications[-1]
        assert note["entity_type"] == "customer" and note["entity_id"] == ROW_ID
        assert "C-2026-0007" in note["message"]

    async def test_the_match_is_attached_even_with_auto_accept_off(self):
        client = _Client(existing_by_phone={"id": ROW_ID, "customer_id": "C-2026-0007"})
        _shop(client)
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name=None,
        )
        assert result["linked"] is True
        assert client.notifications[-1]["entity_id"] == ROW_ID

    async def test_no_match_and_auto_accept_creates_and_points_at_the_new_row(self):
        client = _Client()
        _shop(client)
        client._settings = [{"setting_key": "auto_accept_new_customers", "setting_value": True}]
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name=None,
        )
        assert result["created"] is True and result["linked"] is False
        created = [r for r in client.recorded if r[0] == "create_customer"]
        assert created and created[0][2]["customer_chann_uid"] == "CHN-C-1"
        note = client.notifications[-1]
        assert note["entity_type"] == "customer"
        assert note["entity_id"] == result["customer_id"]
        uuid.UUID(note["entity_id"])  # a real UUID, not the CHN- code

    async def test_no_match_and_auto_accept_off_asks_the_shop_with_no_entity(self):
        client = _Client()
        _shop(client)
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name=None,
        )
        assert result["created"] is False and result["notified"] == 1
        note = client.notifications[-1]
        assert note["entity_id"] is None and note["entity_type"] is None
        assert "สร้างลูกค้า สมชาย ใจดี 0812345678" in note["message"]

    async def test_a_number_held_by_another_identity_is_not_taken(self):
        client = _Client(link_raises=DataTierError(409, {"error": "duplicate", "existing_id": ROW_ID}))
        _shop(client)
        client._settings = [{"setting_key": "auto_accept_new_customers", "setting_value": True}]
        # The create then also refuses (same phone) — nothing is claimed.
        async def refuse(license_id, payload, actor_id=None):
            raise DataTierError(409, {"error": "duplicate", "existing_id": ROW_ID})
        client.create_customer = refuse
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name=None,
        )
        assert result["created"] is False and result["linked"] is False
        assert client.notifications[-1]["entity_id"] is None


class TestApprovedReportType:
    async def test_the_outcome_has_its_own_type_on_the_technician_oa(self, monkeypatch):
        client = _Client()
        client._members = [{"id": "m-9", "chann_uid": "CHN-TECH", "role": "technician", "status": "active"}]
        client._line_targets = {"CHN-TECH": "Uline"}
        pushed = []

        async def fake_push(oa, to, text, client=None):
            pushed.append((oa, to, text))
            return ["mid-1"]

        monkeypatch.setattr(notify, "push_text", fake_push)
        report = {"id": ROW_ID, "report_id": "SR-2026-0001", "technician_member_id": "m-9"}
        await approval._notify_submitter_of_document(client, LICENSE_ID, report, "https://x/pdf", "th")
        assert client.notifications[-1]["type"] == "approval_approved"
        assert pushed and pushed[0][0] == "technician"


# ------------------------------------------------ routes that had no caller

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from chann_app import routers_admin, routers_phase2  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402


class _RouteClient(_Client):
    async def aclose(self):
        pass

    async def list_audit_log(self, license_id, *, entity_type=None, actor_type=None, limit=100):
        self.recorded.append(("list_audit_log", license_id, entity_type, actor_type, limit))
        return [{"id": "a-1", "entity_type": entity_type or "customer", "action": "create"}]

    async def get_pdpa_request(self, request_id):
        return {"id": request_id, "request_type": "export", "status": "pending"} if request_id == "r-1" else None


def _tenant_app(client, keys):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="admin", is_owner=False,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestWiredRoutes:
    def test_the_shops_audit_trail_needs_audit_log_view(self):
        client = _RouteClient()
        ok = _tenant_app(client, ["audit_log.view"]).get(f"/api/v1/licenses/{LICENSE_ID}/audit-log?entity_type=customer&limit=9999")
        assert ok.status_code == 200 and ok.json()[0]["entity_type"] == "customer"
        assert client.recorded[-1] == ("list_audit_log", LICENSE_ID, "customer", None, 500)
        denied = _tenant_app(_RouteClient(), ["customer.read"]).get(f"/api/v1/licenses/{LICENSE_ID}/audit-log")
        assert denied.status_code == 403
        other = _tenant_app(_RouteClient(), ["audit_log.view"]).get("/api/v1/licenses/someone-else/audit-log")
        assert other.status_code == 403

    def test_a_platform_admin_can_open_one_pdpa_request(self):
        client = _RouteClient()

        async def override_client():
            yield client

        async def override_admin():
            return {"sub": "admin-1", "username": "root"}

        app = FastAPI()
        app.include_router(routers_admin.router)
        app.dependency_overrides[routers_admin.get_data_client] = override_client
        app.dependency_overrides[routers_admin.require_admin] = override_admin
        assert TestClient(app).get("/api/v1/platform/pdpa/requests/r-1").json()["request_type"] == "export"
        assert TestClient(app).get("/api/v1/platform/pdpa/requests/r-9").status_code == 404

    async def test_issue_for_report_reads_one_report_not_the_list(self, monkeypatch):
        from chann_app.services import report_issue

        class _Fake(_Client):
            async def get_service_report(self, license_id, report_id):
                self.recorded.append(("get_service_report", license_id, report_id))
                return None

            async def list_service_reports(self, license_id, status=None):
                raise AssertionError("the list must not be read for one report")

        client = _Fake()
        with pytest.raises(LookupError):
            await report_issue.issue_for_report(client, license_id=LICENSE_ID, report_id="sr-404")
        assert ("get_service_report", LICENSE_ID, "sr-404") in client.recorded
