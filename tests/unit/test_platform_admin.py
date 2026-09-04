"""Phase 18 — the Platform Admin API (Master Spec 18.5): login, tenant
management, the cross-tenant audit view, break-glass, and isolation.
"""
from __future__ import annotations

import datetime as dt
import sys
import uuid
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_admin  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402

API = "/api/v1"
ADMIN_ID = str(uuid.uuid4())
TENANT = {
    "id": "11111111-1111-1111-1111-111111111111", "license_code": "LIC-1", "company_name": "ร้านเย็นสบาย",
    "company_code": "ABCD2345", "status": "active", "trial_expires_at": None, "created_at": "2026-09-01T00:00:00+00:00",
    "owner_chann_uid": "CHN-OWNER", "owner_name": "สมชาย", "members": 3, "customers": 12, "tickets": 20,
    "open_tickets": 4, "deals": 5, "last_activity_at": None,
}


class _FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.sessions: dict[str, dict] = {}
        self.status = "active"

    async def aclose(self):
        pass

    async def authenticate_platform_admin(self, username, password):
        if (username, password) == ("chai", "correct-horse"):
            return {"admin_id": ADMIN_ID, "username": "chai"}
        return None

    async def create_platform_admin_session(self, session_id, admin_id, ttl):
        self.sessions[session_id] = {"admin_id": admin_id}

    async def get_platform_admin_session(self, session_id):
        return self.sessions.get(session_id)

    async def delete_platform_admin_session(self, session_id):
        self.sessions.pop(session_id, None)

    async def platform_tenants(self, *, q=None, status=None):
        self.calls.append(("platform_tenants", q, status))
        rows = [dict(TENANT, status=self.status)]
        if status and status != self.status:
            return []
        if q and q not in TENANT["company_name"]:
            return []
        return rows

    async def platform_tenant(self, license_id):
        if license_id != TENANT["id"]:
            return None
        return {**TENANT, "status": self.status, "legal_name": None, "company_phone": None, "company_email": None,
                "members_detail": [{"chann_uid": "CHN-OWNER", "role": "owner", "status": "active", "display_name": "สมชาย", "joined_at": None}]}

    async def set_license_status(self, license_id, status, actor_id=None):
        self.calls.append(("set_license_status", license_id, status, actor_id))
        self.status = status
        return {**TENANT, "status": status}

    async def platform_audit(self, **kw):
        self.calls.append(("platform_audit", kw))
        return [{"id": str(uuid.uuid4()), "license_id": TENANT["id"], "entity_type": "license", "entity_id": TENANT["id"],
                 "actor_type": "platform_admin", "actor_id": ADMIN_ID, "action": "update", "field_changes": {"status": ["active", "suspended"]},
                 "ai_reasoning": None, "cross_tenant": True, "created_at": "2026-09-04T00:00:00+00:00"}]

    async def force_transfer_owner(self, license_id, target_chann_uid, actor_id=None):
        self.calls.append(("force_transfer_owner", license_id, target_chann_uid, actor_id))
        return {"id": str(uuid.uuid4()), "chann_uid": target_chann_uid, "role": "owner", "status": "active"}

    async def line_target_of(self, chann_uid):
        return "U-line-" + chann_uid

    async def list_pdpa_requests(self, *, status=None, chann_uid=None):
        return []


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-jwt-secret")
    fake = _FakeClient()

    async def override():
        yield fake

    app = FastAPI()
    app.include_router(routers_admin.router)
    app.dependency_overrides[routers_admin.get_data_client] = override

    sent: list[dict] = []

    async def fake_notify(client, **kw):
        sent.append(kw)
        return {"delivered": True}

    import chann_app.services.notify as notify_module

    monkeypatch.setattr(notify_module, "send_notification", fake_notify)
    return TestClient(app), fake, sent


def _login(client: TestClient) -> dict:
    res = client.post(f"{API}/platform/login", json={"username": "chai", "password": "correct-horse"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _token_without(fake: _FakeClient, permission: str) -> dict:
    session_id = str(uuid.uuid4())
    fake.sessions[session_id] = {"admin_id": ADMIN_ID}
    now = dt.datetime.now(dt.timezone.utc)
    token = jwt.encode({
        "sub": ADMIN_ID, "username": "chai", "scope": "platform.admin.access",
        "permissions": [p for p in ("platform.admin.access", "platform.admin.break_glass") if p != permission],
        "jti": session_id, "iat": now, "exp": now + dt.timedelta(hours=1),
    }, settings.jwt_secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class TestPlatformAdminLogin:
    def test_right_password_gives_a_jwt(self, world):
        client, fake, _ = world
        res = client.post(f"{API}/platform/login", json={"username": "chai", "password": "correct-horse"})
        assert res.status_code == 200 and res.json()["access_token"]
        assert client.get(f"{API}/platform/me", headers=_login(client)).json()["username"] == "chai"

    def test_wrong_password_is_401(self, world):
        client, _, _ = world
        assert client.post(f"{API}/platform/login", json={"username": "chai", "password": "nope"}).status_code == 401

    def test_no_token_is_401_on_every_platform_read(self, world):
        client, _, _ = world
        for path in (f"{API}/platform/tenants", f"{API}/platform/tenants/{TENANT["id"]}", f"{API}/platform/audit"):
            assert client.get(path).status_code == 401, path


class TestTenantManagement:
    def test_lists_every_tenant_with_its_size(self, world):
        client, _, _ = world
        rows = client.get(f"{API}/platform/tenants", headers=_login(client)).json()
        assert rows[0]["company_name"] == "ร้านเย็นสบาย" and rows[0]["open_tickets"] == 4

    def test_search_and_status_pass_through(self, world):
        client, fake, _ = world
        headers = _login(client)
        client.get(f"{API}/platform/tenants?q=เย็น&status_filter=active", headers=headers)
        assert ("platform_tenants", "เย็น", "active") in fake.calls
        assert client.get(f"{API}/platform/tenants?status_filter=weird", headers=headers).status_code == 422

    def test_suspend_then_reopen_records_who_did_it(self, world):
        client, fake, _ = world
        headers = _login(client)
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"status": "suspended"}, headers=headers)
        assert res.status_code == 200 and res.json()["status"] == "suspended"
        assert ("set_license_status", TENANT["id"], "suspended", ADMIN_ID) in fake.calls
        assert client.get(f"{API}/platform/tenants?status_filter=active", headers=headers).json() == []
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"status": "active"}, headers=headers)
        assert res.json()["status"] == "active"
        assert client.get(f"{API}/platform/tenants?status_filter=active", headers=headers).json()[0]["status"] == "active"

    def test_unknown_status_is_refused(self, world):
        client, _, _ = world
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"status": "deleted"}, headers=_login(client))
        assert res.status_code == 422

    def test_unknown_tenant_is_404(self, world):
        client, _, _ = world
        assert client.get(f"{API}/platform/tenants/{uuid.uuid4()}", headers=_login(client)).status_code == 404


class TestCrossTenantAuditView:
    def test_cross_tenant_default_and_filters(self, world):
        client, fake, _ = world
        headers = _login(client)
        rows = client.get(f"{API}/platform/audit?cross_tenant=true", headers=headers).json()
        assert rows and rows[0]["cross_tenant"] is True
        client.get(f"{API}/platform/audit?license_id={TENANT['id']}&actor_type=platform_admin", headers=headers)
        kw = [c[1] for c in fake.calls if c[0] == "platform_audit"][-1]
        assert kw["license_id"] == TENANT["id"] and kw["actor_type"] == "platform_admin"


class TestBreakGlass:
    def test_force_transfer_succeeds_and_tells_the_new_owner(self, world):
        client, fake, sent = world
        res = client.post(f"{API}/platform/break-glass/transfer-owner",
                          json={"license_id": TENANT["id"], "target_chann_uid": "CHN-NEW"}, headers=_login(client))
        assert res.status_code == 200 and res.json()["role"] == "owner"
        assert ("force_transfer_owner", TENANT["id"], "CHN-NEW", ADMIN_ID) in fake.calls
        assert sent and sent[0]["target_chann_uid"] == "CHN-NEW" and sent[0]["type"] == "ownership_transferred"
        assert sent[0]["target_line_user_id"] == "U-line-CHN-NEW"

    def test_needs_the_break_glass_permission(self, world):
        client, fake, _ = world
        res = client.post(f"{API}/platform/break-glass/transfer-owner",
                          json={"license_id": TENANT["id"], "target_chann_uid": "CHN-NEW"},
                          headers=_token_without(fake, "platform.admin.break_glass"))
        assert res.status_code == 403
        assert not [c for c in fake.calls if c[0] == "force_transfer_owner"]

    def test_both_fields_are_required(self, world):
        client, _, _ = world
        res = client.post(f"{API}/platform/break-glass/transfer-owner", json={"license_id": TENANT["id"]}, headers=_login(client))
        assert res.status_code == 422


class TestIsolation:
    def test_an_admin_token_cannot_use_the_liff_path(self, world):
        client, _, _ = world
        res = client.get(f"{API}/liff/customer/me", headers=_login(client))
        assert res.status_code == 401

    def test_the_admin_api_has_no_business_writes(self):
        """The operator reads tenants and audit, flips a status, transfers
        an owner — never edits a tenant's customers, deals or tickets."""
        import ast

        source = (ROOT / "application/chann_app/routers_admin.py").read_text(encoding="utf-8")
        writes = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ("post", "patch", "put", "delete") and d.args:
                    path = d.args[0].value
                    operator_route = "require_admin" in ast.dump(node)  # scheduler sweeps use their own secret
                    if operator_route and path.startswith("/platform/") and any(w in path for w in ("customers", "deals", "tickets", "quotes")):
                        writes.append(path)
        assert not writes, writes


class TestSuspendedTenantInChat:
    async def test_a_member_of_a_suspended_shop_is_told_and_stopped(self):
        client = FakeDataClient()
        ctx = _ctx(primary_role="sales", oa="sales")
        ctx.memberships[0]["license_status"] = "suspended"
        reply = await handle_chat_message(client, message="ลูกค้าใหม่ สมชาย 0812345678", ctx=ctx)
        assert "ระงับ" in reply.text
        assert not [r for r in client.recorded if r[0] in ("create_customer", "create_ticket")]

    async def test_pdpa_rights_still_work_while_suspended(self, monkeypatch):
        from chann_app.services import pdpa

        class _NoStore:
            async def put(self, **kw):
                raise pdpa.DocumentStoreNotConfigured("no bucket")

        monkeypatch.setattr(pdpa, "get_document_store", lambda *a, **k: _NoStore())
        client = FakeDataClient()
        ctx = _ctx(primary_role="sales", oa="sales")
        ctx.memberships[0]["license_status"] = "suspended"
        reply = await handle_chat_message(client, message="ขอข้อมูลของฉัน", ctx=ctx)
        assert "สำเนาข้อมูล" in reply.text
