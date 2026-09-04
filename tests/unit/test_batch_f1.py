"""User review batch 2 (4 Sep 2026): phone numbers are digits everywhere,
several customers can be added in one message or by CSV, deals carry their
amount into the list and the reports, and the guide pictures are served.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import csv_import, guides, reports_ai  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.phone import normalise_phone, phone_problem  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402
from test_user_review_fixes import ReviewFake, _model, _seed  # noqa: E402


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


class TestPhoneRule:
    def test_helper(self):
        assert phone_problem("0812345678") is None
        assert phone_problem("081-234-5678") is None
        assert phone_problem("+66 81 234 5678") is None
        assert phone_problem("08x1234567") == "letters"
        assert phone_problem("โทร 0812345678") == "letters"
        assert phone_problem("1234") == "length"
        assert phone_problem("") is None
        assert normalise_phone("+66 81-234-5678") == "0812345678"

    def test_data_schema_refuses_letters(self):
        from pydantic import ValidationError

        from chann_data.schemas import CustomerIn

        with pytest.raises(ValidationError):
            CustomerIn(first_name="สมชาย", phone="08x1234567")
        assert CustomerIn(first_name="สมชาย", phone="081-234-5678").phone == "081-234-5678"

    async def test_chat_create_names_the_problem_and_keeps_the_rest(self):
        client = ReviewFake()
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "08x1234567"}, "missing": []})
        reply = await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใจดี 08x1234567", ctx=_ctx(), ai_client=ai)
        assert "มีตัวอักษร" in reply.text and "08x1234567" in reply.text
        assert not [r for r in client.recorded if r[0] == "create_customer"]
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending["missing"] == ["phone"] and pending["fields"]["first_name"] == "สมชาย"

    async def test_chat_update_refuses_letters(self):
        client = ReviewFake()
        await _seed(client)
        ai = _model({"action": "update", "entity": "customer", "fields": {"target_name": "สมชาย", "phone": "abc"}, "missing": []})
        reply = await handle_chat_message(client, message="แก้เบอร์สมชายเป็น abc", ctx=_ctx(), ai_client=ai)
        assert "มีตัวอักษร" in reply.text
        assert not [r for r in client.recorded if r[0] == "update_customer"]


class TestBulkCustomerAdd:
    async def test_several_lines_become_several_customers(self):
        client = ReviewFake()
        await _seed(client, "เดิม", "อยู่แล้ว", "0899999999")
        message = "เพิ่มลูกค้าหลายคน\nสมชาย ใจดี 0812345678 somchai@example.com\nสมหญิง ดีใจ 081-234-5679\nซ้ำ คนเดิม 0899999999\nคนที่สี่ ไม่มีเบอร์\nคนที่ห้า เบอร์ 08x1234567"
        reply = await handle_chat_message(client, message=message, ctx=_ctx())
        created = [r for r in client.recorded if r[0] == "create_customer" and r[2].get("first_name") in ("สมชาย", "สมหญิง")]
        assert len(created) == 2
        assert created[0][2] == {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "email": "somchai@example.com"}
        assert "เพิ่มลูกค้าแล้ว 2 ราย" in reply.text
        assert "ข้ามเพราะมีอยู่แล้ว 1 ราย" in reply.text and "C-2026-0001" in reply.text
        assert "ไม่สำเร็จ 2 ราย" in reply.text
        assert not [r for r in client.recorded if r[0] == "parse_intent"]

    async def test_single_customer_message_is_untouched(self):
        client = ReviewFake()
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"}, "missing": []})
        reply = await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใจดี 0812345678", ctx=_ctx(), ai_client=ai)
        assert "เพิ่มลูกค้า" in reply.text and len(client._customers) == 1

    async def test_needs_customer_create(self):
        client = ReviewFake(permission_keys=["customer.read"])
        reply = await handle_chat_message(client, message="เพิ่มลูกค้า\nก ข 0812345678\nค ง 0812345679", ctx=_ctx())
        assert "ยังไม่มีสิทธิ์" in reply.text and not client._customers


class TestCustomerCsvImport:
    async def test_rows_are_saved_skipped_or_refused_individually(self):
        client = ReviewFake()
        await _seed(client, "เดิม", "อยู่แล้ว", "0899999999")
        text = "first_name,last_name,phone,email\nสมชาย,ใจดี,0812345678,a@b.com\nซ้ำ,คนเดิม,0899999999,\nผิด,เบอร์,08x1234567,\n,ไม่มีชื่อ,0812345670,\n"
        result = await csv_import.import_customers(client, license_id="L1", text=text, actor_id="me")
        assert result["kind"] == "customers" and result["total"] == 4 and result["saved"] == 1 and result["failed"] == 3
        statuses = {r["row"]: (r["status"], r["message"]) for r in result["rows"]}
        assert statuses[2][0] == "saved"
        assert statuses[3][0] == "error" and "C-2026-0001" in statuses[3][1]
        assert statuses[4][0] == "error" and "phone" in statuses[4][1].lower()
        assert statuses[5][0] == "error"

    def test_missing_columns_are_refused(self):
        with pytest.raises(csv_import.CsvRejected):
            csv_import._rows("fullname,contact\nx,1\n", csv_import.CUSTOMER_COLUMNS, ("first_name", "phone"))


class TestDealAmountInReports:
    def test_sum_of_deal_amount_is_allowed(self):
        spec = reports_ai.validate_query_spec({"entity": "deals", "metric": "sum", "field": "amount", "filter": {"stage": "won"}})
        assert spec["field"] == "amount" and "amount" in reports_ai.NUMERIC_FIELDS["deals"]
        assert "amount" in reports_ai.build_system_prompt()

    def test_data_tier_whitelist_agrees(self):
        from chann_data.repositories.phase17 import NUMERIC_FIELDS as DATA_NUMERIC

        assert set(DATA_NUMERIC["deals"]) == set(reports_ai.NUMERIC_FIELDS["deals"])


class TestGuideImages:
    def test_every_slot_has_a_picture_file(self):
        images = json.loads((ROOT / "application/chann_app/help_images.json").read_text(encoding="utf-8"))["images"]
        web = json.loads((ROOT / "presentation/lib/help-images.json").read_text(encoding="utf-8"))["images"]
        assert images == web
        for slot, path in images.items():
            assert path == f"/api/v1/guide/images/{slot}.png", slot
            assert (ROOT / "application/chann_app/static/help" / f"{slot}.png").exists(), slot

    def test_urls_are_absolute_for_chat_only_when_the_base_is_known(self, monkeypatch):
        monkeypatch.setattr(settings, "public_base_url", "")
        assert guides.help_image_url("customer-link") == ""
        assert guides.help_image_url("customer-link", absolute=False) == "/api/v1/guide/images/customer-link.png"
        monkeypatch.setattr(settings, "public_base_url", "https://app.example.com/")
        assert guides.help_image_url("customer-link") == "https://app.example.com/api/v1/guide/images/customer-link.png"
        assert guides.guide_images("customer")[0].startswith("https://app.example.com/")

    def test_the_route_serves_png_and_refuses_paths(self):
        from chann_app import routers_admin

        app = FastAPI()
        app.include_router(routers_admin.router)
        client = TestClient(app)
        ok = client.get("/api/v1/guide/images/customer-link.png")
        assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and ok.content[:4] == b"\x89PNG"
        assert client.get("/api/v1/guide/images/nope.png").status_code == 404
        assert client.get("/api/v1/guide/images/..%2F..%2Fmain.py").status_code in (404, 422)
