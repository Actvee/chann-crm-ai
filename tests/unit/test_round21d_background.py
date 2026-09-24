"""Round 21D — the platform's own work honours the plan (spec §5.9), plus
controller Rulings 26 and 27 from the task 11 preflight scan."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from chann_app.data_client import DataTierError
from chann_app.services import approval, approval_sla, entitlements, job_sla, notify
from chann_app.services.authorization import TenantPrincipal
from chann_app.services.chat import handle_chat_message
from chann_app.services.documents import selection
from chann_app import routers_phase2
from plan_fixtures import plan_payload

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

pytestmark = pytest.mark.asyncio


class _Shops:
    """Three shops: one without service, one with, one from an older Data
    tier that sends no plan (→ Pro, so it keeps being swept)."""

    def __init__(self):
        self.asked: list[str] = []
        self.pushed: list = []
        self.rows: list = []

    async def platform_tenants(self, **_kw):
        return [{"id": "L-starter", "status": "active", "plan": plan_payload("starter")},
                {"id": "L-pro", "status": "active", "plan": plan_payload("pro")},
                {"id": "L-old", "status": "active"}]

    async def list_tickets(self, license_id, *a, **k):
        self.asked.append(license_id)
        return []

    async def list_service_reports(self, license_id, status=None):
        self.asked.append(license_id)
        return []

    async def list_license_settings(self, license_id):
        return []

    async def license_plan(self, license_id):
        return {"plan": plan_payload("starter" if license_id == "L-starter" else "pro"), "usage": {}}

    async def list_document_templates(self, license_id, document_type=None):
        self.asked.append(license_id)
        return []

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kw):
        self.rows.append((license_id, kw))
        return {"id": "n1", **kw}


async def test_the_job_sweep_skips_a_shop_without_service():
    shops = _Shops()
    await job_sla.sweep_jobs(shops)
    assert shops.asked == ["L-pro", "L-old"]


async def test_the_approval_sweep_skips_a_shop_without_service(monkeypatch):
    async def four_hours(_client, _license_id):
        return {"approval": 4}

    monkeypatch.setattr(approval_sla, "sla_settings", four_hours)
    shops = _Shops()
    await approval_sla.sweep_reports(shops)
    assert shops.asked == ["L-pro", "L-old"]


async def test_a_customer_push_on_a_shop_without_the_link_is_recorded_not_pushed(monkeypatch):
    pushed = []

    async def fake_push(*args, **kwargs):
        pushed.append(args)
        return ["m1"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    shops = _Shops()
    await notify.send_notification(shops, license_id="L-starter", target_chann_uid="CHN-C", target_line_user_id="U-c",
                                   type="document_sent", message="ใบเสนอราคา")
    assert pushed == []
    assert shops.rows and shops.rows[0][1]["delivery_line"] is False
    await notify.send_notification(shops, license_id="L-pro", target_chann_uid="CHN-C", target_line_user_id="U-c",
                                   type="document_sent", message="ใบเสนอราคา")
    assert len(pushed) == 1


async def test_the_strict_road_names_the_plan_not_a_missing_target(monkeypatch):
    """Final fix (Task 11): on a shop without the Customer LINE link the
    strict road refuses as `plan_locked`, not `no_line_target`, and writes
    no row; document_send says it in its own words."""
    from chann_app.services import document_send

    async def must_not_push(*a, **k):
        raise AssertionError("pushed")

    monkeypatch.setattr(notify, "push_text", must_not_push)
    shops = _Shops()
    with pytest.raises(notify.NotificationNotDelivered) as caught:
        await notify.send_notification(shops, license_id="L-starter", target_chann_uid="CHN-C",
                                       target_line_user_id="U-c", type="document_sent", message="ใบเสนอราคา",
                                       raise_on_failure=True)
    assert caught.value.reason == "plan_locked" and shops.rows == []
    assert document_send.DocumentSendFailed("plan_locked").reason == "plan_locked"
    assert "plan_locked" in document_send.SEND_FAILURE_WORDS
    # A customer with no LINE target on a shop WITH the link keeps its reason.
    with pytest.raises(notify.NotificationNotDelivered) as missing:
        await notify.send_notification(shops, license_id="L-pro", target_chann_uid="CHN-C",
                                       target_line_user_id=None, type="document_sent", message="ใบเสนอราคา",
                                       raise_on_failure=True)
    assert missing.value.reason == "no_line_target"


async def test_a_staff_push_is_not_the_customer_link(monkeypatch):
    pushed = []

    async def fake_push(*args, **kwargs):
        pushed.append(args)
        return ["m1"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    await notify.send_notification(_Shops(), license_id="L-starter", target_chann_uid="CHN-O", target_line_user_id="U-o",
                                   type="followup_due", message="นัดวันนี้")
    assert len(pushed) == 1


async def test_the_survey_is_not_pushed(monkeypatch):
    async def must_not_push(*a, **k):
        raise AssertionError("pushed")

    monkeypatch.setattr(approval, "push_messages", must_not_push)
    out = await approval.send_survey(_Shops(), license_id="L-starter", survey={"id": "s1", "ticket_id": "t1"})
    assert out == "plan_locked"


async def test_a_new_document_uses_the_system_template():
    shops = _Shops()
    assert await selection.resolve_tenant_template(shops, "L-starter", "quote") == (None, None)
    assert "L-starter" not in shops.asked


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    """The two chat-driven rulings below hold a policy for confirmation
    through a stubbed model call; the client still refuses to run
    without a key and model name configured."""
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


# ------------------------------------------------------- controller Ruling 26
#
# "assignment rules with technician scope belong to feature.service — add
# them to the feature map ... the rule save/apply road refuses on a plan
# without service; the sweep skips them."

TECHNICIAN_RULE_JSON = json.dumps({
    "version": 1, "scope": "technician",
    "match_criteria": [{"field": "product.category", "operator": "equals",
                        "value": "AIR_CONDITIONER", "assign_to_team": "AC Team"}],
    "selection_strategy": "least_load",
})
SALES_RULE_JSON = json.dumps({
    "version": 1, "scope": "sales",
    "match_criteria": [{"field": "customer.stage", "operator": "equals",
                        "value": "lead", "assign_to_team": "ขาย A"}],
    "selection_strategy": "round_robin",
})


def _owner_client(**kw) -> FakeDataClient:
    client = FakeDataClient(permission_keys=["setting.manage"], role="owner", **kw)
    client._is_owner = True
    client._teams = [{"id": "team-1", "team_name": "AC Team"}]
    client._sales_groups = [{"id": "sg-1", "group_name": "ขาย A"}]
    return client


async def test_a_technician_scope_rule_is_refused_at_confirm_on_starter():
    client = _owner_client()
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload("starter")
    ai = httpx.AsyncClient(transport=_ai(TECHNICIAN_RULE_JSON))
    preview = await handle_chat_message(
        client, message="ตั้งกฎมอบหมาย ช่างแอร์ให้ทีม AC Team", ctx=ctx, ai_client=ai,
    )
    assert "ยืนยันกฎ" in [q[0] for q in preview.quick_replies]
    confirmed = await handle_chat_message(client, message="ยืนยันกฎ", ctx=ctx)
    assert "🔒" in confirmed.text
    assert not [r for r in client.recorded if r[0] == "upsert_assignment_rule"]


async def test_a_sales_scope_rule_still_saves_on_starter():
    client = _owner_client()
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload("starter")
    ai = httpx.AsyncClient(transport=_ai(SALES_RULE_JSON))
    await handle_chat_message(
        client, message="ตั้งกฎมอบหมาย ลูกค้าใหม่ให้กลุ่มขาย A", ctx=ctx, ai_client=ai,
    )
    confirmed = await handle_chat_message(client, message="ยืนยันกฎ", ctx=ctx)
    assert [r for r in client.recorded if r[0] == "upsert_assignment_rule"]
    assert "🔒" not in confirmed.text


async def test_the_dashboard_route_refuses_a_technician_rule_on_starter():
    client = _owner_client()
    principal = TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-O", role="owner", is_owner=True,
        permission_keys=frozenset({"setting.manage"}),
        plan=entitlements.PlanView.from_payload(plan_payload("starter")),
    )
    with pytest.raises(Exception) as exc:
        await routers_phase2.put_assignment_rule(
            license_id=LICENSE_ID,
            payload={"rules_json": {
                "version": 1, "scope": "technician",
                "match_criteria": [{"field": "product.category", "operator": "equals",
                                    "value": "AIR_CONDITIONER", "assign_to_team": "AC Team"}],
                "selection_strategy": "least_load",
            }},
            principal=principal, client=client,
        )
    assert entitlements.is_plan_refusal(exc.value)
    assert not [r for r in client.recorded if r[0] == "upsert_assignment_rule"]


async def test_the_dashboard_route_saves_a_sales_rule_on_starter():
    client = _owner_client()
    principal = TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-O", role="owner", is_owner=True,
        permission_keys=frozenset({"setting.manage"}),
        plan=entitlements.PlanView.from_payload(plan_payload("starter")),
    )
    saved = await routers_phase2.put_assignment_rule(
        license_id=LICENSE_ID,
        payload={"rules_json": {
            "version": 1, "scope": "sales",
            "match_criteria": [{"field": "customer.stage", "operator": "equals",
                                "value": "lead", "assign_to_team": "ขาย A"}],
            "selection_strategy": "round_robin",
        }},
        principal=principal, client=client,
    )
    assert saved is not None
    assert [r for r in client.recorded if r[0] == "upsert_assignment_rule"]


# ------------------------------------------------------- controller Ruling 27
#
# "A Data-tier plan refusal on a multi-step approval chain (403
# plan_required) must surface in chat as the plan refusal via
# `_plan_reply_from` ... test on Pro (a 2-step chain → the Enterprise
# refusal naming the feature)."

TWO_STEP_APPROVAL_JSON = json.dumps({
    "version": 1, "entity_type": "service_report",
    "steps": [
        {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
        {"order": 2, "approver_type": "role", "approver_ref": "admin"},
    ],
})


async def test_a_two_step_chain_on_pro_surfaces_the_enterprise_plan_refusal():
    client = FakeDataClient(permission_keys=["approval.manage"], role="owner")
    client._is_owner = True

    async def refuse_multi_level(license_id, entity_type, rules_json, *, updated_by=None, actor_id=None):
        raise DataTierError(403, "plan_required", structured={
            "error": "plan_required", "feature": "feature.multi_level_approval",
            "plan": "pro", "min_plan": "enterprise",
        })

    client.replace_approval_workflow = refuse_multi_level
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload("pro")
    ai = httpx.AsyncClient(transport=_ai(TWO_STEP_APPROVAL_JSON))
    preview = await handle_chat_message(
        client, message="ตั้งการอนุมัติ ให้ CS ก่อน แล้วต่อด้วย admin", ctx=ctx, ai_client=ai,
    )
    assert "ยืนยันการอนุมัติ" in preview.text or preview.quick_replies
    confirmed = await handle_chat_message(client, message="ยืนยันการอนุมัติ", ctx=ctx)
    assert "🔒" in confirmed.text
    assert entitlements.feature_label("feature.multi_level_approval", "th") in confirmed.text
    assert "ล้มเหลว" not in confirmed.text
