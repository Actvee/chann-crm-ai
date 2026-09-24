"""Round 21D — AI reports on Starter are locked before any model call; the
two service basic reports need the service feature (owner decision Q4);
the five-report buttons never offer what the plan locks."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chann_app import routers_phase2
from chann_app.config import settings
from chann_app.services import authorization, chart_quota, entitlements
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx

# pytest.ini sets asyncio_mode = auto; a `pytestmark = pytest.mark.asyncio`
# here only produces warnings on the sync tests below (task 7's finding).


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class _Counting(httpx.MockTransport):
    def __init__(self, content: str):
        self.calls = 0

        def handler(request):
            self.calls += 1
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                             "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        super().__init__(handler)


async def _say(message, *, plan, ai=None, keys=("view_reports", "deal.read", "setting.manage")):
    client = FakeDataClient(role="owner", permission_keys=list(keys))
    client._is_owner = True
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload(plan)
    transport = _Counting(json.dumps(ai or {"action": "read", "entity": "report", "fields": {}, "missing": []}))
    reply = await handle_chat_message(client, message=message, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=transport))
    return reply, transport.calls, client


class TestTheAiRoadOnStarter:
    async def test_the_typed_prefix_declines_before_the_model(self):
        reply, calls, client = await _say("สร้างรายงานด้วย AI: ยอดขายแยกตามเดือน", plan="starter")
        assert reply.text.startswith("🔒 «ถามรายงานด้วย AI» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter")
        assert "รายงานพื้นฐาน (มูลค่าดีลทั้งหมด · ยอดปิดเดือนนี้ · ยอดค้างชำระ) ยังถามได้ฟรีเสมอ" in reply.text
        assert calls == 0
        assert not [r for r in client.recorded if r[0] == "consume_ai_chart_quota"]

    async def test_a_service_basic_report_on_starter_is_the_service_refusal(self):
        reply, _calls, _client = await _say(
            "งานซ่อมค้างแยกตามช่าง", plan="starter",
            ai={"action": "read", "entity": "report", "fields": {"type": "jobs"}, "missing": [],
                "report": "open_jobs_by_tech"})
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง»")

    async def test_the_free_three_still_answer_and_offer_only_what_the_plan_has(self):
        reply, _calls, _client = await _say(
            "ยอดมูลค่าดีลทั้งหมด", plan="starter",
            ai={"action": "read", "entity": "report", "fields": {"type": "sales"}, "missing": [],
                "report": "pipeline_value"})
        assert "🔒" not in reply.text
        offered = " ".join(text for _label, text in reply.quick_replies)
        assert "open_jobs_by_tech" not in offered and "satisfaction_avg" not in offered
        assert "ดูเป็นรูป" not in " ".join(label for label, _t in reply.quick_replies)


class TestTheFreeReportButtonsOnStarter:
    """Fix round 1 (task 9 review, Critical): the AI-locked guard in
    `_handle_ai_report` used to sit before the free-five short-circuits, so
    a Starter shop's own free-report BUTTON — a "รายงาน: <key>" postback,
    which the pre-model trigger routes into `_handle_ai_report` — dead-
    ended on the AI refusal instead of answering. Owner decision Q4:
    Starter's three free reports, buttons included, must keep working; the
    lock now sits only in front of the true ad-hoc/model road."""

    @pytest.mark.parametrize("key", ["pipeline_value", "won_this_month", "outstanding_invoices"])
    async def test_pressing_a_free_report_button_answers_free(self, key):
        reply, calls, client = await _say(f"รายงาน: {key}", plan="starter")
        assert "🔒" not in reply.text
        assert calls == 0
        assert not [r for r in client.recorded if r[0] == "consume_ai_chart_quota"]

    async def test_pressing_a_service_report_button_is_the_service_refusal(self):
        reply, calls, client = await _say("รายงาน: satisfaction_avg", plan="starter")
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง»")
        assert calls == 0
        assert not [r for r in client.recorded if r[0] == "consume_ai_chart_quota"]

    async def test_the_picture_button_under_a_free_report_is_the_ai_refusal_no_credit(self):
        reply, calls, client = await _say(
            "สร้างรายงานด้วย AI: มูลค่าดีลทั้งหมด เป็นกราฟ", plan="starter")
        assert reply.text.startswith("🔒 «ถามรายงานด้วย AI» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter")
        assert calls == 0
        assert not [r for r in client.recorded if r[0] == "consume_ai_chart_quota"]

    @pytest.mark.parametrize("key", ["pipeline_value", "won_this_month", "outstanding_invoices", "satisfaction_avg"])
    async def test_pro_is_unchanged(self, key):
        reply, _calls, _client = await _say(f"รายงาน: {key}", plan="pro")
        assert "🔒" not in reply.text


def _app(code: str, keys: set[str]) -> TestClient:
    principal = authorization.build_principal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="owner", is_owner=True, role_keys=keys,
        audience="sales", license_status="active", plan_payload=plan_payload(code))

    async def override_client():
        yield FakeDataClient()

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestTheDashboardRoutes:
    def test_the_service_basic_reports_need_service(self):
        http = _app("starter", {"view_reports"})
        out = http.get(f"/api/v1/licenses/{LICENSE_ID}/reports/basic/satisfaction_avg")
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "feature.service"

    def test_running_an_edited_report_on_starter_is_the_ai_refusal(self):
        http = _app("starter", {"view_reports"})
        out = http.post(f"/api/v1/licenses/{LICENSE_ID}/reports/ai/run", json={"spec": {"entity": "deals"}})
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "quota.ai_reports_per_month"


def test_the_fail_open_allowance_is_pros():
    assert chart_quota.FAIL_OPEN_ALLOWANCE == entitlements.UNKNOWN_PLAN["limits"]["ai_reports_per_month"] == 30
    assert not hasattr(chart_quota, "DEFAULT_QUOTA")
