"""Round 21D — the platform admin sets the plan (spec §9; owner decision
Q2, 24 ก.ย. 2569: a downgrade over the limit is refused, in words)."""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chann_app import routers_admin
from chann_app.data_client import DataTierError
from chann_app.services import entitlements
from plan_fixtures import plan_payload

ROOT = Path(__file__).resolve().parents[2]
REFUSAL = {"error": "plan_member_limit", "members": 9, "limit": 5, "inactivate": 4, "plan": "starter",
           "plan_label": "Starter", "message": "ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter"}
#: Pre-flight P9 — the count keys the Data tier's preview sends.
PREVIEW_COUNT_KEYS = {"open_tickets", "technicians", "warranties", "open_chats", "linked_customers",
                      "templates", "custom_roles", "steps", "live_keys"}


class _Admin:
    def __init__(self, *, refuse=None, delete_missing=False, code="pro", quota_fails=False):
        self.recorded: list = []
        self._refuse = refuse
        self._delete_missing = delete_missing
        self._quota_fails = quota_fails
        self.code = code

    async def platform_tenant(self, license_id):
        return {"id": license_id, "company_name": "ร้านแอร์เย็น", "owner_chann_uid": "CHN-OWNER",
                "plan_code": self.code, "plan": plan_payload(self.code), "usage": {"members": 9}}

    async def platform_tenants(self, *, q=None, status=None, plan=None):
        self.recorded.append(("platform_tenants", plan))
        return []

    async def platform_plan_preview(self, license_id):
        return {"current": "pro", "previews": {}}

    async def list_license_settings(self, license_id):
        return []

    async def update_tenant(self, license_id, changes, actor_id=None):
        self.recorded.append(("update_tenant", changes))
        if self._refuse:
            raise DataTierError(self._refuse[0], str(self._refuse[1]), self._refuse[1])
        self.code = changes.get("plan_code", self.code)
        return await self.platform_tenant(license_id)

    async def put_license_setting(self, license_id, key, value, actor_id=None):
        if self._quota_fails:
            raise DataTierError(503, "settings store unavailable")
        self.recorded.append(("put_license_setting", key, value))

    async def delete_license_setting(self, license_id, key, actor_id=None):
        self.recorded.append(("delete_license_setting", key))
        if self._delete_missing:
            # The Data tier's Phase2NotFound for a setting that has no row.
            raise DataTierError(404, "setting not found", {"detail": "setting not found"})

    async def line_target_of(self, chann_uid):
        return "U-owner"

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kw):
        self.recorded.append(("create_notification", kw["target_chann_uid"], kw["type"], kw["message"]))
        return {"id": "n1"}


def _http(client) -> TestClient:
    async def override_client():
        yield client

    async def override_admin():
        return {"sub": "00000000-0000-0000-0000-00000000000a", "username": "root"}

    app = FastAPI()
    app.include_router(routers_admin.router)
    app.dependency_overrides[routers_admin.get_data_client] = override_client
    app.dependency_overrides[routers_admin.require_admin] = override_admin
    return TestClient(app)


def _told(client) -> list:
    return [r for r in client.recorded if r[0] == "create_notification"]


class TestTheRoutes:
    def test_setting_the_plan_tells_the_owner(self, monkeypatch):
        async def fake_push(*a, **k):
            return ["m"]

        monkeypatch.setattr("chann_app.services.notify.push_text", fake_push)
        client = _Admin()
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "enterprise"})
        assert out.status_code == 200, out.text
        assert out.json()["plan_code"] == "enterprise"
        assert ("update_tenant", {"plan_code": "enterprise"}) in client.recorded
        told = _told(client)
        assert len(told) == 1 and told[0][1] == "CHN-OWNER" and told[0][2] == "plan_changed"
        assert told[0][3].startswith("แพ็กเกจของ ร้านแอร์เย็น เปลี่ยนเป็น Enterprise แล้ว")
        assert "เชื่อมต่อ API ภายนอก" in told[0][3]

    def test_the_notice_goes_through_the_one_owner_notice(self, monkeypatch):
        """R6 — Task 8's shared helper, not a second copy of it."""
        calls = []

        async def fake_owner_notice(client, **kw):
            calls.append(kw)

        monkeypatch.setattr(entitlements, "owner_notice", fake_owner_notice)
        _http(_Admin()).patch("/api/v1/platform/tenants/L1", json={"plan_code": "starter"})
        assert len(calls) == 1
        assert calls[0]["kind"] == "plan_changed" and calls[0]["owner_chann_uid"] == "CHN-OWNER"
        assert calls[0]["message"].split("\n")[0] == "แพ็กเกจของ ร้านแอร์เย็น เปลี่ยนเป็น Starter แล้ว"
        assert calls[0]["message_en"].split("\n")[0] == "ร้านแอร์เย็น is now on the Starter plan"
        helper = inspect.getsource(routers_admin._tell_owner_plan_changed)
        assert "entitlements.owner_notice(" in helper and "send_notification" not in helper

    def test_the_same_plan_again_tells_no_one(self, monkeypatch):
        calls = []

        async def fake_owner_notice(client, **kw):
            calls.append(kw)

        monkeypatch.setattr(entitlements, "owner_notice", fake_owner_notice)
        out = _http(_Admin()).patch("/api/v1/platform/tenants/L1", json={"plan_code": "pro"})
        assert out.status_code == 200 and calls == []

    def test_a_downgrade_over_the_limit_comes_back_in_words(self):
        client = _Admin(refuse=(409, REFUSAL))
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "starter"})
        assert out.status_code == 409
        assert out.json()["detail"] == REFUSAL
        assert _told(client) == []

    def test_a_refused_downgrade_writes_nothing_else_either(self):
        """The top-up change in the same save is not written when the plan is
        refused. (A clear: Ruling 28 already refuses a number on Starter.)"""
        client = _Admin(refuse=(409, REFUSAL))
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "starter", "ai_chart_quota": ""})
        assert out.status_code == 409
        assert not [r for r in client.recorded if r[0] in ("put_license_setting", "delete_license_setting")]

    def test_an_unknown_plan(self):
        out = _http(_Admin(refuse=(422, {"error": "unknown_plan", "message": "unknown plan 'gold'"}))).patch(
            "/api/v1/platform/tenants/L1", json={"plan_code": "gold"})
        assert out.status_code == 422 and out.json()["detail"]["error"] == "unknown_plan"

    def test_other_refusals_keep_their_old_shape(self):
        out = _http(_Admin(refuse=(409, {"error": "something_else"}))).patch(
            "/api/v1/platform/tenants/L1", json={"company_name": "ใหม่"})
        assert out.status_code == 409 and out.json()["detail"]["error"] == "tenant_update_failed"

    def test_an_empty_top_up_clears_the_override(self):
        client = _Admin()
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": ""})
        assert out.status_code == 200, out.text
        assert ("delete_license_setting", "ai_chart_quota") in client.recorded
        assert not [r for r in client.recorded if r[0] == "put_license_setting"]

    def test_clearing_an_override_that_is_not_there_is_done(self):
        """P27 — the Data tier's delete answers 404 when there is no row;
        clearing an already-empty override is a no-op success."""
        client = _Admin(delete_missing=True)
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": None})
        assert out.status_code == 200, out.text
        assert out.json()["id"] == "L1"

    def test_the_list_filters_by_plan(self):
        client = _Admin()
        assert _http(client).get("/api/v1/platform/tenants?plan=starter").status_code == 200
        assert ("platform_tenants", "starter") in client.recorded
        assert _http(client).get("/api/v1/platform/tenants?plan=gold").status_code == 422

    def test_the_tenant_page_carries_the_preview(self):
        out = _http(_Admin()).get("/api/v1/platform/tenants/L1").json()
        assert out["plan_preview"] == {"current": "pro", "previews": {}}

    def test_the_tenant_page_opens_without_the_preview(self):
        client = _Admin()

        async def boom(license_id):
            raise DataTierError(502, "down")

        client.platform_plan_preview = boom
        out = _http(client).get("/api/v1/platform/tenants/L1")
        assert out.status_code == 200 and out.json()["plan_preview"] is None

    def test_the_routes_keep_the_admin_guard(self):
        app = FastAPI()
        app.include_router(routers_admin.router)
        http = TestClient(app)
        assert http.patch("/api/v1/platform/tenants/L1", json={"plan_code": "pro"}).status_code in (401, 403)


def _writes(client) -> list:
    return [r for r in client.recorded if r[0] in ("update_tenant", "put_license_setting", "delete_license_setting")]


class TestTheTopUpFollowsThePlan:
    """Ruling 28 — the API, not only the console, refuses a top-up on a
    plan without one (Starter): a stored override would come back to life
    after a later upgrade."""

    def test_a_top_up_on_starter_is_refused_and_nothing_is_stored(self):
        client = _Admin(code="starter")
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": 50})
        assert out.status_code == 422, out.text
        detail = out.json()["detail"]
        assert detail["error"] == "ai_quota_not_on_plan" and detail["plan"] == "starter"
        assert detail["message"] == "แพ็กเกจ Starter ไม่มีการเพิ่มโควต้ารายงาน AI — ไม่ได้บันทึก"
        assert _writes(client) == []

    def test_zero_is_a_top_up_too(self):
        client = _Admin(code="starter")
        assert _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": 0}).status_code == 422
        assert _writes(client) == []

    def test_clearing_on_starter_is_allowed(self):
        """Removing an inert override is what Ruling 28 wants, not refuses."""
        client = _Admin(code="starter")
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": ""})
        assert out.status_code == 200, out.text
        assert ("delete_license_setting", "ai_chart_quota") in client.recorded

    def test_a_top_up_on_pro_is_stored(self):
        client = _Admin(code="pro")
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": 50})
        assert out.status_code == 200, out.text
        assert ("put_license_setting", "ai_chart_quota", 50) in client.recorded

    def test_a_move_to_starter_with_a_top_up_is_refused_whole(self, monkeypatch):
        calls = []

        async def fake_owner_notice(client, **kw):
            calls.append(kw)

        monkeypatch.setattr(entitlements, "owner_notice", fake_owner_notice)
        client = _Admin(code="pro")
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "starter", "ai_chart_quota": 50})
        assert out.status_code == 422, out.text
        assert out.json()["detail"]["message"] == (
            "แพ็กเกจ Starter ไม่มีการเพิ่มโควต้ารายงาน AI — ไม่ได้บันทึกอะไร แพ็กเกจยังเป็นแบบเดิม")
        assert _writes(client) == [] and client.code == "pro" and calls == []

    def test_a_move_up_from_starter_with_a_top_up_is_stored(self):
        client = _Admin(code="starter")
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "pro", "ai_chart_quota": 50})
        assert out.status_code == 200, out.text
        assert ("update_tenant", {"plan_code": "pro"}) in client.recorded
        assert ("put_license_setting", "ai_chart_quota", 50) in client.recorded

    def test_the_top_up_plans_are_the_data_tiers(self):
        from chann_data.plans import PLANS

        assert entitlements.QUOTA_TOP_UP_PLANS == frozenset(c for c, p in PLANS.items() if p.quota_top_up)


class TestAHalfDoneSaveSaysSo:
    """Review item 2 — the plan is written first; a top-up that then fails
    must not read as "nothing changed"."""

    def test_the_plan_changed_and_the_top_up_did_not(self, monkeypatch):
        calls = []

        async def fake_owner_notice(client, **kw):
            calls.append(kw)

        monkeypatch.setattr(entitlements, "owner_notice", fake_owner_notice)
        client = _Admin(code="pro", quota_fails=True)
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "enterprise", "ai_chart_quota": 80})
        assert out.status_code == 200, out.text
        body = out.json()
        assert body["plan_code"] == "enterprise" and body["plan_changed"] is True
        assert body["quota_error"] == "settings store unavailable"
        assert len(calls) == 1  # the plan did change, so the owner hears it

    def test_other_details_saved_and_the_top_up_did_not(self):
        client = _Admin(code="pro", quota_fails=True)
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"company_name": "ใหม่", "ai_chart_quota": 80})
        assert out.status_code == 200, out.text
        assert out.json()["plan_changed"] is False and out.json()["quota_error"]

    def test_a_top_up_alone_that_fails_is_a_failure(self):
        """Nothing else was written, so an error is the honest answer."""
        client = _Admin(code="pro", quota_fails=True)
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": 80})
        assert out.status_code == 502 and out.json()["detail"]["error"] == "tenant_update_failed"

    def test_a_clean_save_carries_no_quota_error(self):
        out = _http(_Admin()).patch("/api/v1/platform/tenants/L1", json={"plan_code": "enterprise", "ai_chart_quota": 80})
        assert out.status_code == 200 and "quota_error" not in out.json()


class TestTheNotice:
    def test_gained_and_locked_are_named(self):
        text = entitlements.plan_change_text(plan_payload("pro"), plan_payload("starter"), "ร้านแอร์เย็น", "th")
        lines = text.split("\n")
        assert lines[0] == "แพ็กเกจของ ร้านแอร์เย็น เปลี่ยนเป็น Starter แล้ว"
        assert lines[1].startswith("ล็อกไว้ (ข้อมูลเดิมยังอยู่ครบ): ")
        assert "งานบริการ / งานซ่อม และทีมช่าง" in lines[1]
        assert len(lines) == 2

    def test_an_upgrade_names_what_it_opens(self):
        text = entitlements.plan_change_text(plan_payload("pro"), plan_payload("enterprise"), "ร้าน", "en")
        assert text.split("\n") == [
            "ร้าน is now on the Enterprise plan",
            "Now included: Multi-level approval · External API",
        ]


def _ts_record(source: str, name: str) -> dict[str, str]:
    """The `name: { key: "value", ... }` object literal in a .ts file."""
    block = re.search(rf"\b{name}: \{{(.*?)\}}", source, re.S)
    assert block, name
    return dict(re.findall(r'"?([\w.]+)"?: "([^"]*)"', block.group(1)))


class TestTheConsole:
    EDIT = (ROOT / "presentation/app/admin/tenants/[id]/TenantEdit.tsx").read_text(encoding="utf-8")
    MEMBERS = (ROOT / "presentation/app/admin/tenants/[id]/TenantMembers.tsx").read_text(encoding="utf-8")
    LIST = (ROOT / "presentation/app/admin/page.tsx").read_text(encoding="utf-8")
    COPY = (ROOT / "presentation/lib/admin-copy.ts").read_text(encoding="utf-8")

    def test_the_plan_names_are_the_plans(self):
        """C14/R3: exact codes and exact labels, not a substring test."""
        assert _ts_record(self.COPY, "planNames") == entitlements.PLAN_LABELS
        assert 'const PLAN_CODES = ["starter", "pro", "enterprise", "enterprise_plus"] as const;' in self.EDIT
        assert "<option key={code} value={code}>" in self.EDIT
        assert 'const PLANS = ["", "starter", "pro", "enterprise", "enterprise_plus"] as const;' in self.LIST

    def test_the_preview_words_are_the_features_and_counts(self):
        names = _ts_record(self.COPY, "featureNames")
        for key, th in names.items():
            assert entitlements.FEATURE_LABELS[key]["th"] == th, key
        assert set(names) == {k for k in entitlements.FEATURE_LABELS if k.startswith("feature.")}
        words = _ts_record(self.COPY, "countWords")
        assert set(words) == PREVIEW_COUNT_KEYS
        # Ruling 25: technicians are counted under service, not sales groups.
        assert words["technicians"] == "ช่าง"
        assert "plan_preview" in self.EDIT and "preview.locks" in self.EDIT

    def test_a_refused_downgrade_offers_no_confirm(self):
        assert "refused" in self.EDIT and "planRefused" in self.EDIT
        assert "disabled={busy || Boolean(refused)}" in self.EDIT
        assert not re.search(r"force|anyway|ยืนยันต่อ", self.EDIT, re.I)
        assert 'planRefused: (n: number, plan: string) => `ต้องปิดใช้งาน (inactivate) สมาชิก ${n} คนก่อนลดแพ็กเกจเป็น ${plan}`' in self.COPY

    def test_the_refusal_sits_by_the_plan_and_links_the_members(self):
        assert 'id="plan-error"' in self.EDIT and 'aria-describedby' in self.EDIT
        assert 'role="alert"' in self.EDIT
        assert 'href="#tenant-members"' in self.EDIT and 'id="tenant-members"' in self.MEMBERS

    def test_usage_against_limits_and_the_relabelled_top_up(self):
        assert "tenant.usage" in self.EDIT
        assert 'aiChartQuota: "เพิ่มโควต้ารายงาน AI"' in self.COPY
        hint = re.search(r'aiChartQuotaHint: "([^"]*)"', self.COPY).group(1)
        assert "0 = ปิดกราฟสร้างเอง" in hint and "เว้นว่าง = กลับไปใช้ตัวเลขของแพ็กเกจ" in hint
        assert 'disabled={busy || form.plan_code === "starter"}' in self.EDIT and "aiChartStarter" in self.EDIT

    def test_the_console_shows_both_answers_by_the_field(self):
        assert 'id="ai-chart-error"' in self.EDIT and "setQuotaError(res.reason)" in self.EDIT
        assert "quota_error" in self.EDIT and "planSavedQuotaFailed" in self.EDIT
        assert 'planSavedQuotaFailed: "แพ็กเกจเปลี่ยนแล้ว แต่ตั้งโควต้าไม่สำเร็จ"' in self.COPY

    def test_the_list_has_a_plan_column_and_filter(self):
        assert "plan_code" in self.LIST and 'name="plan"' in self.LIST
        assert "columns.plan" in self.LIST and "colSpan={10}" in self.LIST
