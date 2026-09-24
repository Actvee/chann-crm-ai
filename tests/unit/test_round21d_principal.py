"""Round 21D — the principal: locked keys subtracted in one place, `require`
says WHY (plan, not permission), `require_feature` for the named checks,
and the dashboard routes that share a key with always-on work."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from chann_app import routers_phase2
from chann_app.data_client import DataTierError
from chann_app.services import authorization
from chann_app.services.authorization import TenantPrincipal
from plan_fixtures import plan_payload, plan_view
from test_phase6_chat import LICENSE_ID, FakeDataClient


def _principal(code: str, keys: set[str], *, is_owner=False) -> TenantPrincipal:
    return authorization.build_principal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="admin", is_owner=is_owner,
        role_keys=keys, audience="sales", license_status="active", plan_payload=plan_payload(code),
    )


class TestRequire:
    def test_a_locked_key_is_refused_for_the_plan_not_the_permission(self):
        principal = _principal("starter", {"ticket.read", "customer.read"})
        assert "ticket.read" not in principal.permission_keys
        with pytest.raises(HTTPException) as caught:
            principal.require("ticket.read")
        assert caught.value.status_code == 403 and caught.value.detail["error"] == "plan_required"
        assert caught.value.detail["feature"] == "feature.service"
        principal.require("customer.read")

    def test_a_key_the_role_never_had_is_still_a_permission_refusal(self):
        principal = _principal("pro", {"customer.read"})
        with pytest.raises(HTTPException) as caught:
            principal.require("deal.read")
        assert caught.value.detail == "permission required: deal.read"

    def test_require_any_names_the_plan_when_the_only_holder_is_locked(self):
        principal = _principal("starter", {"ticket.update"})
        with pytest.raises(HTTPException) as caught:
            principal.require_any("ticket.assign", "ticket.update")
        assert caught.value.detail["error"] == "plan_required"

    def test_require_feature(self):
        _principal("enterprise", set()).require_feature("feature.external_api")
        with pytest.raises(HTTPException) as caught:
            _principal("pro", {"setting.manage"}).require_feature("feature.external_api")
        assert caught.value.detail["min_plan"] == "enterprise"

    def test_a_principal_built_without_a_plan_is_pro(self):
        principal = TenantPrincipal(license_id="L", chann_uid="U", role="r", is_owner=False,
                                    permission_keys=frozenset({"ticket.read"}))
        assert principal.plan.code == "pro" and principal.plan.known is False
        principal.require("ticket.read")


class _Identity(FakeDataClient):
    plan = None

    async def resolve_identity(self, line_user_id, primary_role, display_name=None):
        return {"chann_uid": "CHN-S-000001", "primary_role": primary_role}

    async def memberships_of(self, chann_uid, oa=None):
        return [{"license_id": LICENSE_ID, "license_code": "TESTCO", "company_name": "บริษัททดสอบ",
                 "license_status": "active", "plan": self.plan}]


@pytest.fixture
def verified(monkeypatch):
    async def fake_verify(token, audience):
        return {"sub": "U1", "name": "x"}

    monkeypatch.setattr(authorization, "verify_id_token", fake_verify)


class TestTheLiffPrincipal:
    async def test_staff_on_starter(self, verified):
        client = _Identity(permission_keys=["ticket.read", "customer.read"])
        client.plan = plan_payload("starter")
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method="GET")
        assert principal.plan.code == "starter"
        assert principal.permission_keys == {"customer.read"} and principal.plan_locked_keys == {"ticket.read"}

    async def test_a_customer_of_a_starter_shop_can_do_nothing_there_and_is_told_why(self, verified):
        client = _Identity(permission_keys=[])
        client.plan = plan_payload("starter")
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="customer", x_license_id="", method="GET")
        assert principal.permission_keys == frozenset()
        with pytest.raises(HTTPException) as caught:
            principal.require("invoice.read")
        assert caught.value.detail["feature"] == "feature.customer_line_link"

    async def test_an_old_data_tier_with_no_plan_is_pro(self, verified):
        client = _Identity(permission_keys=["ticket.read"])
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method="GET")
        assert principal.plan.code == "pro" and "ticket.read" in principal.permission_keys


class _Routes(FakeDataClient):
    async def license_plan(self, license_id):
        return {"plan": plan_payload("starter"), "usage": {"members": 3, "members_limit": 5}}

    async def create_api_key(self, license_id, payload, actor_id=None):
        return {"id": "k1", "name": payload.get("name"), "key": "chann_live_x"}

    async def create_role(self, license_id, payload, actor_id=None):
        return {"role_name": payload["role_name"]}


def _app(principal: TenantPrincipal, client=None) -> TestClient:
    client = client or _Routes()

    async def override_client():
        yield client

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestTheDashboardRoutes:
    def test_me_carries_the_plan_and_the_contact_for_whoever_can_act_on_it(self, monkeypatch):
        monkeypatch.setattr(routers_phase2.entitlements.settings, "chann_sales_contact", "LINE @chann|https://line.me/x")
        owner = _app(_principal("starter", {"setting.manage"}, is_owner=True)).get(
            f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert owner["plan"]["code"] == "starter" and owner["sales_contact"]["url"] == "https://line.me/x"
        clerk = _app(_principal("starter", {"ticket.read", "customer.read"})).get(
            f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert clerk["sales_contact"] is None and clerk["plan_locked_keys"] == ["ticket.read"]

    def test_the_plan_route(self):
        out = _app(_principal("starter", {"setting.manage"})).get(f"/api/v1/licenses/{LICENSE_ID}/plan")
        assert out.status_code == 200 and out.json()["usage"]["members"] == 3
        denied = _app(_principal("starter", {"customer.read"})).get(f"/api/v1/licenses/{LICENSE_ID}/plan")
        assert denied.status_code == 403

    @pytest.mark.parametrize("method,path,body,feature", [
        ("post", "/roles", {"role_name": "ช่างอาวุโส", "permission_keys": ["customer.read"]}, "feature.custom_roles"),
        ("post", "/api-keys", {"name": "ERP"}, "feature.external_api"),
        ("get", "/technician-teams", None, "feature.service"),
        ("get", "/surveys/summary", None, "feature.service"),
    ])
    def test_a_named_check_refuses_on_plan(self, method, path, body, feature):
        keys = {"role.manage", "setting.manage", "team.manage", "view_reports", "ticket.read"}
        call = getattr(_app(_principal("starter", keys, is_owner=True)), method)
        out = call(f"/api/v1/licenses/{LICENSE_ID}{path}", **({"json": body} if body else {}))
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required" and out.json()["detail"]["feature"] == feature

    def test_compile_policy_is_custom_roles(self):
        """Final fix (Task 5): the role-policy compiler is the custom-roles
        feature — refused on Starter, reachable on Pro."""
        path = f"/api/v1/licenses/{LICENSE_ID}/roles/compile-policy"
        body = {"policy_prompt": "customer.read deal.read"}
        out = _app(_principal("starter", {"role.manage"})).post(path, json=body)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.custom_roles"
        ok = _app(_principal("pro", {"role.manage"})).post(path, json=body)
        assert ok.status_code == 200, ok.text
        no_role = _app(_principal("pro", {"customer.read"})).post(path, json=body)
        assert no_role.status_code == 403 and no_role.json()["detail"] == "permission required: role.manage"

    @pytest.mark.parametrize("key", ["chat_sla_minutes", "chat_timeout_minutes"])
    def test_the_chat_minutes_are_live_chat(self, key):
        """Ruling 29 (final fix round 1): PUT settings/{key} for the two
        live-chat minutes is feature.live_chat; any other key is not."""
        path = f"/api/v1/licenses/{LICENSE_ID}/settings/{key}"
        out = _app(_principal("starter", {"setting.manage"})).put(path, json={"setting_value": 15})
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.live_chat"
        ok = _app(_principal("pro", {"setting.manage"})).put(path, json={"setting_value": 15})
        assert ok.status_code == 200, ok.text
        other = _app(_principal("starter", {"setting.manage"})).put(
            f"/api/v1/licenses/{LICENSE_ID}/settings/job_sla_unassigned_minutes", json={"setting_value": 60})
        assert other.status_code == 200, other.text

    @pytest.mark.parametrize("code", ["starter", "pro", "enterprise"])
    def test_a_non_owner_making_a_key_hears_owner_only_on_every_plan(self, code):
        """Final fix (Task 5): owner-only before the plan — a non-owner is
        never pointed at an upgrade they could not act on."""
        out = _app(_principal(code, {"setting.manage"}, is_owner=False)).post(
            f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "owner_only"

    def test_the_team_reads_refuse_on_plan_through_their_key(self):
        """Final fix (Task 5): the GET team routes carry no separate plan
        check — ticket.read IS feature.service, so a holder on Starter
        hears the plan, and a non-holder hears the permission."""
        for path in ("/technician-teams", "/technician-teams/t1/members"):
            held = _app(_principal("starter", {"ticket.read"})).get(f"/api/v1/licenses/{LICENSE_ID}{path}")
            assert held.status_code == 403 and held.json()["detail"]["feature"] == "feature.service"
            not_held = _app(_principal("starter", {"customer.read"})).get(f"/api/v1/licenses/{LICENSE_ID}{path}")
            assert not_held.status_code == 403
            assert not_held.json()["detail"] == "permission required: ticket.read"

    def test_enterprise_makes_a_key(self):
        out = _app(_principal("enterprise", {"setting.manage"}, is_owner=True)).post(
            f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert out.status_code in (200, 201), out.text

    def test_a_data_tier_plan_refusal_keeps_its_403(self):
        exc = DataTierError(403, "x", {"error": "plan_required", "feature": "feature.service"})
        assert routers_phase2._propagate(exc).status_code == 403
        assert routers_phase2._propagate(DataTierError(403, "cross tenant")).status_code == 502


# --- Controller rulings on top of the brief (pre-flight R-B, R-C, R-D, C5) ---

_LOCKED_EVERYWHERE = {"role.manage", "setting.manage", "team.manage", "view_reports", "ticket.read",
                      "invoice.update", "quote.update"}


class TestTheRulings:
    def test_me_tells_no_permission_from_plan_locked(self):
        """R-C: a non-owner holder of setting.manage must be able to show a
        plan-locked nav entry — `held_keys` is what the role grants,
        `permission_keys` what the plan lets them use, `plan_locked_keys`
        the difference."""
        body = _app(_principal("starter", {"setting.manage", "ticket.read", "warranty.read", "customer.read"})).get(
            f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert body["permission_keys"] == ["customer.read", "setting.manage"]
        assert body["plan_locked_keys"] == ["ticket.read", "warranty.read"]
        assert body["held_keys"] == ["customer.read", "setting.manage", "ticket.read", "warranty.read"]
        assert body["is_owner"] is False and body["plan"]["locked"]["feature.service"] == "pro"

    def test_me_on_pro_locks_nothing_the_role_holds(self):
        body = _app(_principal("pro", {"ticket.read"})).get(f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert body["plan_locked_keys"] == [] and body["held_keys"] == body["permission_keys"] == ["ticket.read"]

    def test_api_keys_can_still_be_listed_and_revoked_on_every_plan(self):
        """R-D: a downgraded owner must be able to see and revoke what an
        outside system holds; only MAKING a key is the feature."""
        client = FakeDataClient()
        # A key made while the shop was on Enterprise, before the downgrade.
        client._api_keys = [{"id": "k1", "license_id": LICENSE_ID, "name": "ERP", "revoked_at": None}]
        app = _app(_principal("starter", {"setting.manage"}, is_owner=True), client)
        listed = app.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys")
        assert listed.status_code == 200 and [k["id"] for k in listed.json()["keys"]] == ["k1"]
        revoked = app.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys/k1/revoke")
        assert revoked.status_code == 200 and revoked.json()["revoked_at"]
        made = app.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP 2"})
        assert made.status_code == 403 and made.json()["detail"]["feature"] == "feature.external_api"

    @pytest.mark.parametrize("method,path,body", [
        ("get", "/technician-teams", None),
        ("post", "/technician-teams", {"team_name": "ทีม A"}),
        ("delete", "/technician-teams/t1", None),
        ("get", "/technician-teams/t1/members", None),
        ("post", "/technician-teams/t1/members", {"member_id": "m1"}),
        ("delete", "/technician-teams/t1/members/m1", None),
        ("post", "/document-templates/upload", {"template_name": "x", "html": "<p>x</p>"}),
        ("post", "/document-templates/t1/versions/v1/publish", None),
        ("post", "/document-templates/t1/active", {"is_active": True}),
        ("post", "/invoices/i1/send", {"kind": "invoice"}),
        ("post", "/quotes/q1/send", {}),
        ("patch", "/roles/sales", {"role_name": "sales", "permission_keys": ["customer.read"]}),
    ])
    def test_every_named_route_refuses_on_starter(self, method, path, body):
        """C5: all six technician-team routes, not five — and the rest of
        the named checks the brief lists."""
        call = getattr(_app(_principal("starter", _LOCKED_EVERYWHERE, is_owner=True)), method)
        kwargs = {"json": body} if body is not None else {}
        out = call(f"/api/v1/licenses/{LICENSE_ID}{path}", **kwargs)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"

    def test_the_technician_team_writes_are_named_checks_not_only_key_locks(self):
        """team.manage is always-on — without the named check a Starter
        owner could still make a technician team."""
        out = _app(_principal("starter", {"team.manage"}, is_owner=True)).post(
            f"/api/v1/licenses/{LICENSE_ID}/technician-teams", json={"team_name": "ทีม A"})
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "feature.service"


class TestTheTemplatePrincipal:
    """R-B: chat's AI-drafted template goes through the template routes
    with a principal of its own; it must carry the licence's plan, or a
    Starter shop could draft and publish through chat what the dashboard
    refuses."""

    def _ctx(self, plan):
        from chann_app.services.identity import ResolvedContext, TenantResolution

        return ResolvedContext(
            chann_uid="CHN-S-000001", primary_role="sales", display_name=None,
            resolution=TenantResolution.SINGLE, oa="sales",
            memberships=[{"license_id": LICENSE_ID, "license_code": "TESTCO", "plan": plan}],
        )

    @pytest.mark.parametrize("code", ["starter", "pro", "enterprise"])
    def test_its_plan_is_the_licences(self, code):
        from chann_app.services import chat

        principal = chat._template_principal(self._ctx(plan_payload(code)), LICENSE_ID, ["setting.manage"])
        assert principal.plan == plan_view(code)
        assert principal.plan.has("feature.custom_documents") is (code != "starter")

    def test_starter_cannot_publish_through_it(self):
        from chann_app.services import chat

        principal = chat._template_principal(self._ctx(plan_payload("starter")), LICENSE_ID, ["setting.manage"])
        with pytest.raises(HTTPException) as caught:
            principal.require_feature("feature.custom_documents")
        assert caught.value.detail["error"] == "plan_required"

    def test_a_membership_with_no_plan_is_pro(self):
        from chann_app.services import chat

        principal = chat._template_principal(self._ctx(None), LICENSE_ID, ["setting.manage"])
        assert principal.plan.code == "pro" and principal.plan.known is False
