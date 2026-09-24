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
    "company_code": "ABCD2345", "status": "active", "expires_at": None, "created_at": "2026-09-01T00:00:00+00:00",
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

    async def platform_tenants(self, *, q=None, status=None, plan=None):  # plan: DataClient since round 21D
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

    async def update_tenant(self, license_id, changes, actor_id=None):
        self.calls.append(("update_tenant", license_id, changes, actor_id))
        if "status" in changes:
            self.status = changes["status"]
        return {**TENANT, **{k: v for k, v in changes.items() if k in TENANT}, "status": self.status}

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

    # ---- round 18
    async def extend_tenant(self, license_id, days, actor_id=None):
        self.calls.append(("extend_tenant", license_id, days, actor_id))
        if self.status == "suspended":
            self.status = "active"
        return {**TENANT, "status": self.status, "expires_at": "2026-10-31T16:59:59+00:00"}

    async def delete_tenant(self, license_id, *, purge=False, actor_id=None):
        self.calls.append(("delete_tenant", license_id, purge, actor_id))
        if purge:
            return {"id": license_id, "purged": True, "redis_keys_cleared": 3, "rows": {"customers": 12}}
        self.status = "deleted"
        return {**TENANT, "status": "deleted", "deleted_at": "2026-09-14T00:00:00+00:00"}

    async def platform_set_member_role(self, license_id, chann_uid, role_name, actor_id=None):
        self.calls.append(("platform_set_member_role", license_id, chann_uid, role_name, actor_id))
        return {"id": str(uuid.uuid4()), "chann_uid": chann_uid, "role": role_name, "status": "active", "channel": "sales"}

    async def platform_set_member_status(self, license_id, chann_uid, status, actor_id=None):
        self.calls.append(("platform_set_member_status", license_id, chann_uid, status, actor_id))
        return {"id": str(uuid.uuid4()), "chann_uid": chann_uid, "role": "sales", "status": status, "channel": "sales",
                "unassigned_tickets": []}

    async def platform_move_member(self, license_id, chann_uid, *, target_license_id, role_name, actor_id=None):
        self.calls.append(("platform_move_member", license_id, chann_uid, target_license_id, role_name, actor_id))
        return {"source": {"chann_uid": chann_uid, "status": "removed"},
                "target": {"chann_uid": chann_uid, "status": "active", "role": role_name},
                "target_company_name": "ร้านบี", "unassigned_tickets": []}


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
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"status": "archived"}, headers=_login(client))
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
        an owner, manages members and the subscription (round 18) — never
        edits a tenant's customers, deals or tickets. The round-18 routes
        live under /platform/tenants/{id}/(extend|members/...) and the
        DELETE of the tenant itself; none of them touch business
        records, so the business-word check below still holds for them
        without an exemption."""
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


class TestLockoutAndReasons:
    """Review D2/D11 (6 Sep 2026): a locked account says until when, and a
    failed break-glass says why with the Data tier's own status."""

    def test_a_locked_account_is_423_with_locked_until(self, world):
        from chann_app.data_client import DataTierError

        client, fake, _ = world

        async def locked(username, password):
            raise DataTierError(423, "locked", {"error": "locked", "locked_until": "2026-09-06T14:35:00+00:00"})

        fake.authenticate_platform_admin = locked
        res = client.post(f"{API}/platform/login", json={"username": "chai", "password": "nope"})
        assert res.status_code == 423, res.text
        assert res.json()["detail"] == {"error": "locked", "locked_until": "2026-09-06T14:35:00+00:00"}

    def test_a_data_tier_outage_at_login_is_503_not_401(self, world):
        from chann_app.data_client import DataTierError

        client, fake, _ = world

        async def down(username, password):
            raise DataTierError(500, "internal error")

        fake.authenticate_platform_admin = down
        assert client.post(f"{API}/platform/login", json={"username": "chai", "password": "x"}).status_code == 503

    @pytest.mark.parametrize("upstream,expected", [(404, 404), (409, 409), (500, 502)])
    def test_break_glass_passes_the_status_and_the_reason(self, world, upstream, expected):
        from chann_app.data_client import DataTierError

        client, fake, sent = world

        async def refuse(license_id, target_chann_uid, actor_id=None):
            raise DataTierError(upstream, "member is not active", {"error": "inactive_member"})

        fake.force_transfer_owner = refuse
        res = client.post(f"{API}/platform/break-glass/transfer-owner",
                          json={"license_id": TENANT["id"], "target_chann_uid": "CHN-NEW"}, headers=_login(client))
        assert res.status_code == expected, res.text
        assert res.json()["detail"]["reason"] == {"error": "inactive_member"}
        assert not sent


class TestTenantEdit:
    """7 Sep 2026: the console could only suspend/reopen — a trial's
    deadline and the shop's own details were read-only."""

    def test_status_only_keeps_the_original_route(self, world):
        client, fake, _ = world
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"status": "trial"}, headers=_login(client))
        assert res.status_code == 200
        assert ("set_license_status", TENANT["id"], "trial", ADMIN_ID) in fake.calls
        assert not [c for c in fake.calls if c[0] == "update_tenant"]

    def test_details_and_trial_day_go_through_one_audited_patch(self, world):
        client, fake, _ = world
        res = client.patch(
            f"{API}/platform/tenants/{TENANT['id']}",
            json={"company_name": " ร้านเย็นสบาย 2 ", "company_phone": "021234567", "expires_at": "2026-09-30", "status": "active"},
            headers=_login(client),
        )
        assert res.status_code == 200, res.text
        call = [c for c in fake.calls if c[0] == "update_tenant"][0]
        assert call[1] == TENANT["id"] and call[3] == ADMIN_ID
        changes = call[2]
        assert changes["company_name"] == "ร้านเย็นสบาย 2" and changes["company_phone"] == "021234567"
        assert changes["status"] == "active"
        # A bare day is the END of that Bangkok day, not its first second.
        assert changes["expires_at"] == "2026-09-30T23:59:59+07:00"

    def test_an_empty_trial_day_clears_the_deadline(self, world):
        client, fake, _ = world
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"expires_at": ""}, headers=_login(client))
        assert res.status_code == 200
        changes = [c for c in fake.calls if c[0] == "update_tenant"][0][2]
        assert changes == {"clear_expires_at": True}

    def test_refusals_are_explained(self, world):
        client, _, _ = world
        headers = _login(client)
        assert client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"company_name": "  "}, headers=headers).status_code == 422
        assert client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"expires_at": "30/09/2026"}, headers=headers).status_code == 422
        assert client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"company_email": "not-an-email"}, headers=headers).status_code == 422
        assert client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={}, headers=headers).status_code == 422


class TestSubscriptionRound18:
    """Round 18: the product is a subscription — expiry for every status,
    renewal, admin notes, soft delete / purge, and member management
    from the console."""

    def test_expires_at_is_the_name_and_the_old_one_still_works(self, world):
        client, fake, _ = world
        headers = _login(client)
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"expires_at": "2026-12-31"}, headers=headers)
        assert res.status_code == 200, res.text
        changes = [c for c in fake.calls if c[0] == "update_tenant"][-1][2]
        assert changes == {"expires_at": "2026-12-31T23:59:59+07:00"}
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"trial_expires_at": "2026-12-31"}, headers=headers)
        assert res.status_code == 200
        assert [c for c in fake.calls if c[0] == "update_tenant"][-1][2] == {"expires_at": "2026-12-31T23:59:59+07:00"}
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"expires_at": None}, headers=headers)
        assert [c for c in fake.calls if c[0] == "update_tenant"][-1][2] == {"clear_expires_at": True}

    def test_admin_notes_go_through_the_patch(self, world):
        client, fake, _ = world
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"admin_notes": " ชำระแล้วถึงสิ้นปี "}, headers=_login(client))
        assert res.status_code == 200
        assert [c for c in fake.calls if c[0] == "update_tenant"][-1][2] == {"admin_notes": "ชำระแล้วถึงสิ้นปี"}

    def test_extend_renews_and_reopens_a_suspended_tenant(self, world):
        client, fake, _ = world
        headers = _login(client)
        fake.status = "suspended"
        res = client.post(f"{API}/platform/tenants/{TENANT['id']}/extend", json={"days": 30}, headers=headers)
        assert res.status_code == 200, res.text
        assert ("extend_tenant", TENANT["id"], 30, ADMIN_ID) in fake.calls
        assert res.json()["status"] == "active"
        for bad in ({"days": 0}, {"days": 5000}, {"days": "many"}, {}):
            assert client.post(f"{API}/platform/tenants/{TENANT['id']}/extend", json=bad, headers=headers).status_code == 422, bad
        assert client.post(f"{API}/platform/tenants/{TENANT['id']}/extend", json={"days": 30}).status_code == 401

    def test_extend_passes_the_data_tiers_refusal(self, world):
        from chann_app.data_client import DataTierError

        client, fake, _ = world

        async def refuse(license_id, days, actor_id=None):
            raise DataTierError(409, "deleted", {"error": "conflict", "message": "a deleted company cannot be extended"})

        fake.extend_tenant = refuse
        res = client.post(f"{API}/platform/tenants/{TENANT['id']}/extend", json={"days": 30}, headers=_login(client))
        assert res.status_code == 409 and res.json()["detail"]["reason"]["error"] == "conflict"

    def test_soft_delete_by_default_and_deleted_is_a_listable_status(self, world):
        client, fake, _ = world
        headers = _login(client)
        res = client.delete(f"{API}/platform/tenants/{TENANT['id']}", headers=headers)
        assert res.status_code == 200, res.text
        assert ("delete_tenant", TENANT["id"], False, ADMIN_ID) in fake.calls
        assert res.json()["status"] == "deleted"
        assert client.get(f"{API}/platform/tenants?status_filter=deleted", headers=headers).json()[0]["status"] == "deleted"
        # Restoring goes through the ordinary PATCH.
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}", json={"status": "active"}, headers=headers)
        assert res.status_code == 200 and ("set_license_status", TENANT["id"], "active", ADMIN_ID) in fake.calls

    def test_purge_needs_break_glass_and_is_forwarded(self, world):
        client, fake, _ = world
        res = client.delete(f"{API}/platform/tenants/{TENANT['id']}?purge=true",
                            headers=_token_without(fake, "platform.admin.break_glass"))
        assert res.status_code == 403
        assert not [c for c in fake.calls if c[0] == "delete_tenant"]
        res = client.delete(f"{API}/platform/tenants/{TENANT['id']}?purge=true", headers=_login(client))
        assert res.status_code == 200 and res.json()["purged"] is True
        assert ("delete_tenant", TENANT["id"], True, ADMIN_ID) in fake.calls
        assert client.delete(f"{API}/platform/tenants/{TENANT['id']}").status_code == 401

    def test_member_role_from_the_console_never_owner(self, world):
        client, fake, _ = world
        headers = _login(client)
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/role", json={"role": "admin"}, headers=headers)
        assert res.status_code == 200 and res.json()["role"] == "admin"
        assert ("platform_set_member_role", TENANT["id"], "CHN-STAFF", "admin", ADMIN_ID) in fake.calls
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/role", json={"role": "owner"}, headers=headers)
        assert res.status_code == 409 and res.json()["detail"]["error"] == "owner_via_break_glass"
        assert client.patch(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/role", json={}, headers=headers).status_code == 422

    def test_member_status_from_the_console(self, world):
        from chann_app.data_client import DataTierError

        client, fake, _ = world
        headers = _login(client)
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/status", json={"status": "removed"}, headers=headers)
        assert res.status_code == 200 and res.json()["status"] == "removed"
        assert ("platform_set_member_status", TENANT["id"], "CHN-STAFF", "removed", ADMIN_ID) in fake.calls
        assert client.patch(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/status", json={"status": "gone"}, headers=headers).status_code == 422

        async def owner(license_id, chann_uid, status, actor_id=None):
            raise DataTierError(409, "owner", {"error": "conflict", "message": "the owner cannot be removed or demoted"})

        fake.platform_set_member_status = owner
        res = client.patch(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-OWNER/status", json={"status": "removed"}, headers=headers)
        assert res.status_code == 409 and "owner" in res.json()["detail"]["reason"]["message"]

    def test_member_move_to_another_company(self, world):
        client, fake, _ = world
        headers = _login(client)
        target = str(uuid.uuid4())
        res = client.post(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/move",
                          json={"target_license_id": target, "role": "sales"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["target"]["role"] == "sales" and res.json()["target_company_name"] == "ร้านบี"
        assert ("platform_move_member", TENANT["id"], "CHN-STAFF", target, "sales", ADMIN_ID) in fake.calls
        assert client.post(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/move",
                           json={"target_license_id": TENANT["id"], "role": "sales"}, headers=headers).status_code == 422
        assert client.post(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/move",
                           json={"target_license_id": target, "role": "owner"}, headers=headers).status_code == 409
        assert client.post(f"{API}/platform/tenants/{TENANT['id']}/members/CHN-STAFF/move",
                           json={"role": "sales"}, headers=headers).status_code == 422

    async def test_a_member_of_a_deleted_shop_is_stopped_like_a_suspended_one(self):
        client = FakeDataClient()
        ctx = _ctx(primary_role="sales", oa="sales")
        ctx.memberships[0]["license_status"] = "deleted"
        reply = await handle_chat_message(client, message="ลูกค้าใหม่ สมชาย 0812345678", ctx=ctx)
        assert "ระงับ" in reply.text
        assert not [r for r in client.recorded if r[0] in ("create_customer", "create_ticket")]

    def test_the_principal_treats_deleted_as_read_only(self):
        from fastapi import HTTPException

        from chann_app.services.authorization import refuse_if_suspended

        refuse_if_suspended("deleted", "GET")
        with pytest.raises(HTTPException) as exc:
            refuse_if_suspended("deleted", "POST")
        assert exc.value.status_code == 423
