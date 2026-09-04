"""Phase 17 — ad-hoc AI reports (Master Spec 17.5): the model's JSON
becomes a whitelisted spec or nothing; injection has nowhere to go; the
outputs read well; view_reports gates the door.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import reports_ai  # noqa: E402
from chann_app.services.chat import _is_ai_report_request, handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402


class ReportFake(FakeDataClient):
    def __init__(self, result=None, **kwargs):
        # A salesperson: the member template carries view_reports.
        if kwargs.get("permission_keys") is None:
            kwargs["permission_keys"] = ["customer.read", "deal.read", "ticket.read", "view_reports"]
        super().__init__(**kwargs)
        self.specs: list[dict] = []
        self._result = result

    async def run_report_query(self, license_id, spec, actor_id=None):
        self.specs.append({"license_id": license_id, "actor_id": actor_id, **spec})
        if self._result is not None:
            return {**spec, **self._result, "generated_at": "2026-09-04T10:00:00+00:00"}
        return {**spec, "rows": [], "total": 3, "generated_at": "2026-09-04T10:00:00+00:00"}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    monkeypatch.setattr(settings, "openrouter_model_reasoning", "test-reasoning-model")
    monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: (_ for _ in ()).throw(reports_ai.DocumentStoreNotConfigured("no bucket")))


def _model(payload: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_ai(json.dumps(payload)))


GROUPED = {"rows": [{"key": "m1", "label": "สมชาย", "value": 5}, {"key": "m2", "label": "สมหญิง", "value": 2}], "total": 7}


class TestQuerySpecGeneration:
    async def test_won_deals_last_3_months(self):
        client = ReportFake()
        ai = _model({"entity": "deals", "metric": "count", "filter": {"stage": "won"}, "group_by": None, "date_range": "last_3_months"})
        reply = await handle_chat_message(client, message="ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด", ctx=_ctx(), ai_client=ai)
        assert client.specs and client.specs[0]["entity"] == "deals"
        assert client.specs[0]["filter"] == {"stage": "won"} and client.specs[0]["date_range"] == "last_3_months"
        assert client.specs[0]["date_field"] == "created_at"
        assert "รวม 3" in reply.text and "3 เดือนล่าสุด" in reply.text

    async def test_open_tickets_by_technician(self):
        client = ReportFake(result=GROUPED)
        ai = _model({"entity": "tickets", "metric": "count", "filter": {"status": "open"}, "groupBy": "assigned_to", "dateRange": None})
        reply = await handle_chat_message(client, message="สรุป ticket ค้าง แยกตามช่าง", ctx=_ctx(), ai_client=ai)
        assert client.specs[0]["group_by"] == "assigned_to" and client.specs[0]["filter"] == {"status": "open"}
        assert "• สมชาย: 5" in reply.text and "รวม 7" in reply.text

    async def test_a_vague_request_gets_a_question_back(self):
        client = ReportFake()
        ai = _model({"clarify": "อยากดูรายงานเรื่องอะไรครับ ดีล ลูกค้า หรืองานซ่อม?"})
        reply = await handle_chat_message(client, message="รายงานหน่อย", ctx=_ctx(), ai_client=ai)
        assert "อยากดูรายงานเรื่องอะไร" in reply.text
        assert not client.specs

    def test_the_fixed_report_commands_are_not_hijacked(self):
        assert not _is_ai_report_request("รายงานของฉัน")
        assert not _is_ai_report_request("ออกรายงาน SR-2026-0001")
        assert _is_ai_report_request("รายงานยอดขายเดือนนี้")
        assert _is_ai_report_request("ดูยอดดีล 3 เดือน")
        assert not _is_ai_report_request("สวัสดี")
        assert not _is_ai_report_request("ยอดขาย") and not _is_ai_report_request("สรุปยอด")
        assert _is_ai_report_request("สรุป ticket ค้าง แยกตามช่าง")

    async def test_unreadable_model_output_is_refused_politely(self):
        client = ReportFake()
        ai = httpx.AsyncClient(transport=_ai("I cannot help with that."))
        reply = await handle_chat_message(client, message="รายงานยอดขาย", ctx=_ctx(), ai_client=ai)
        assert "สร้างรายงานนี้ไม่ได้" in reply.text and not client.specs


class TestSqlInjectionPrevention:
    @pytest.mark.parametrize("spec", [
        {"entity": "deals; DROP TABLE deals; --"},
        {"entity": "deals", "filter": {"stage": "won' OR 1=1 --"}},
        {"entity": "customers", "filter": {"stage": "lead; DELETE FROM customers"}},
        {"entity": "tickets", "group_by": "status; --"},
        {"entity": "deals", "date_range": "last_3_months UNION SELECT *"},
        {"entity": "deals", "filter": {"owner_member_id": "1 OR 1=1"}},
        {"entity": "warranties", "filter": {"created_at": "2026-01-01"}},
    ])
    def test_anything_outside_the_whitelist_is_refused(self, spec):
        with pytest.raises(reports_ai.ReportSpecInvalid):
            reports_ai.validate_query_spec(spec)

    async def test_every_query_carries_the_tenant(self):
        client = ReportFake()
        ai = _model({"entity": "customers", "metric": "count"})
        await handle_chat_message(client, message="สรุปลูกค้าทั้งหมด", ctx=_ctx(), ai_client=ai)
        assert client.specs[0]["license_id"] == _ctx().license_id
        assert client.specs[0]["actor_id"] == "CHN-S-000001"

    async def test_the_model_never_gets_a_field_it_invented(self):
        client = ReportFake()
        ai = _model({"entity": "deals", "metric": "sum", "field": "password_hash"})
        reply = await handle_chat_message(client, message="รายงานส่วนลดรวม", ctx=_ctx(), ai_client=ai)
        assert not client.specs and "สร้างรายงานนี้ไม่ได้" in reply.text


class TestWhitelist:
    def test_entity_field_metric(self):
        for bad in ({"entity": "users"}, {"entity": "deals", "filter": {"email": "x"}}, {"entity": "deals", "metric": "median"}):
            with pytest.raises(reports_ai.ReportSpecInvalid):
                reports_ai.validate_query_spec(bad)

    def test_a_good_spec_is_normalised(self):
        spec = reports_ai.validate_query_spec({"entity": "quotes", "metric": "sum", "field": "discount_amount", "filters": {"status": "accepted"}, "dateRange": "this_month"})
        assert spec == {"entity": "quotes", "metric": "sum", "field": "discount_amount", "filter": {"status": "accepted"},
                        "group_by": None, "date_range": "this_month", "date_field": "created_at"}

    def test_the_prompt_is_built_from_the_same_whitelist(self):
        prompt = reports_ai.build_system_prompt()
        for entity in reports_ai.ALLOWED_ENTITIES:
            assert f"- {entity}:" in prompt
        assert "SQL" in prompt and "clarify" in prompt


class TestReportOutput:
    def test_text_summary(self):
        spec = reports_ai.validate_query_spec({"entity": "tickets", "group_by": "status", "date_range": "this_month"})
        text = reports_ai.report_text(spec, {**spec, **GROUPED}, "th")
        assert text.startswith("จำนวนงานซ่อม · เดือนนี้ · แยกตามสถานะ")
        assert "• สมชาย: 5" in text and text.endswith("รวม 7")
        assert "Total 7" in reports_ai.report_text(spec, {**spec, **GROUPED}, "en")

    def test_table_page_has_a_bar_per_row(self):
        spec = reports_ai.validate_query_spec({"entity": "deals", "group_by": "stage"})
        page = reports_ai.report_html(spec, {**spec, **GROUPED}, "th", company_name="ร้านเย็นสบาย")
        assert page.count("class=\"bar\"") == 2 and "width:100%" in page and "width:40%" in page
        assert "ร้านเย็นสบาย" in page and "<script" not in page

    def test_csv_opens_in_excel(self):
        spec = reports_ai.validate_query_spec({"entity": "deals", "group_by": "stage"})
        data = reports_ai.report_csv(spec, {**spec, **GROUPED}, "th")
        assert data.startswith("﻿".encode("utf-8"))
        assert "สมชาย,5" in data.decode("utf-8") and "รวม,7" in data.decode("utf-8")

    async def test_files_are_published_when_storage_exists(self, monkeypatch):
        class _Store:
            def __init__(self):
                self.keys = []

            async def put(self, *, key, content, content_type):
                from chann_app.services.storage.base import StoredDocument, sha256_hex

                self.keys.append(key)
                return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content), size=len(content))

            async def signed_url(self, *, path, expires_seconds):
                return f"https://signed/{path}?ttl={expires_seconds}"

        store = _Store()
        monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: store)
        spec = reports_ai.validate_query_spec({"entity": "deals"})
        files = await reports_ai.publish_files(spec, {**spec, "rows": [], "total": 1}, "th", license_id="L1")
        assert files["csv"].endswith(".csv?ttl=604800") and files["html"].endswith(".html?ttl=604800")
        assert files["pdf"] is None  # renderer not configured in tests
        assert all(k.startswith("reports/L1/") for k in store.keys)


class TestPermission:
    async def test_sales_with_view_reports_can(self):
        client = ReportFake()
        ai = _model({"entity": "deals"})
        reply = await handle_chat_message(client, message="รายงานดีลทั้งหมด", ctx=_ctx(), ai_client=ai)
        assert client.specs and "รวม" in reply.text

    async def test_cs_without_view_reports_cannot(self):
        client = ReportFake(permission_keys=["customer.read", "ticket.read"])
        ai = _model({"entity": "deals"})
        reply = await handle_chat_message(client, message="รายงานดีลทั้งหมด", ctx=_ctx(), ai_client=ai)
        assert not client.specs and "สิทธิ์" in reply.text

    async def test_owner_can_open_it_for_cs(self):
        client = ReportFake(permission_keys=["customer.read", "ticket.read", "view_reports"])
        ai = _model({"entity": "deals"})
        reply = await handle_chat_message(client, message="รายงานดีลทั้งหมด", ctx=_ctx(), ai_client=ai)
        assert client.specs and "รวม" in reply.text

    def test_cs_template_has_no_view_reports_by_default(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))
        from chann_data.permissions import DEFAULT_ROLE_TEMPLATES

        assert "view_reports" not in DEFAULT_ROLE_TEMPLATES["cs"]
        assert "view_reports" in DEFAULT_ROLE_TEMPLATES["member"]
