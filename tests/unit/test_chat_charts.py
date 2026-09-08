"""Phase 17 "ตาราง/กราฟ" in the chat (owner, 8 Sep 2026: "อยากดู report
ยอดขายเป็นกราฟ").

Four fixed sales pictures answer the phrasings people type, a free-form
report asked for as a chart gets the picture as one more output of the
same answer, and every failure mode still answers with the numbers: no
permission is the ordinary refusal, no document store is the text plus one
sentence saying why there is no picture.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_admin  # noqa: E402
from chann_app.auth.document_link import decode_asset_token  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services import chat, reports_ai, sales_charts  # noqa: E402
from chann_app.services.storage import base as storage_base  # noqa: E402
from chann_app.services.storage.base import StoredDocument, sha256_hex  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx, handle_chat_message  # noqa: E402

SALES_KEYS = ["customer.read", "deal.read", "deal.update", "ticket.read", "view_reports"]


class FakeStore:
    """What the document store does, in memory."""

    def __init__(self, fail: Exception | None = None):
        self.objects: dict[str, bytes] = {}
        self.types: dict[str, str] = {}
        self.fail = fail

    async def put(self, *, key, content, content_type):
        if self.fail:
            raise self.fail
        path = f"gs://bucket/{key}"
        self.objects[path] = content
        self.types[path] = content_type
        return StoredDocument(path=path, sha256=sha256_hex(content), size=len(content))

    async def get(self, *, path):
        if path not in self.objects:
            raise storage_base.DocumentStoreError(f"no stored document at {path}")
        return self.objects[path]


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    fake = FakeStore()
    monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: fake)
    monkeypatch.setattr(storage_base, "get_document_store", lambda *a, **k: fake)
    return fake


@pytest.fixture
def no_store(monkeypatch):
    """A deployment with no bucket — dev, and any environment where GCS is
    not wired up. The report must still answer."""
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    monkeypatch.setattr(
        reports_ai, "get_document_store",
        lambda *a, **k: (_ for _ in ()).throw(storage_base.DocumentStoreNotConfigured("no bucket")),
    )


async def sales_client(permission_keys=None):
    client = FakeDataClient(permission_keys=SALES_KEYS if permission_keys is None else permission_keys)
    client._members = [
        {"id": "member-1", "chann_uid": "CHN-S-000001", "role": "sales", "status": "active", "display_name": "สมชาย ขายเก่ง"},
        {"id": "member-2", "chann_uid": "CHN-S-000002", "role": "sales", "status": "active", "display_name": "สมหญิง"},
    ]
    customer = await client.create_customer("L1", {"first_name": "ลูกค้า", "last_name": "ก", "phone": "0812345678"})
    won = await client.create_deal("L1", {"contact_id": customer["id"]})
    won.update({"stage": "won", "owner_member_id": "member-1",
                "expected_close_date": date.today().isoformat(),
                "products": [{"id": "l1", "product_name": "แอร์ 12000 BTU", "quoted_unit_price": "15900.00", "qty": 2}]})
    won2 = await client.create_deal("L1", {"contact_id": customer["id"]})
    won2.update({"stage": "won", "owner_member_id": "member-2",
                 "expected_close_date": date.today().isoformat(),
                 "products": [{"id": "l2", "product_name": "พัดลมตั้งพื้น 16 นิ้ว", "quoted_unit_price": "1500.00", "qty": 4}]})
    open_deal = await client.create_deal("L1", {"contact_id": customer["id"]})
    open_deal.update({"stage": "proposed", "owner_member_id": "member-1",
                      "products": [{"id": "l3", "product_name": "ตู้เย็น", "quoted_unit_price": "9000.00", "qty": 1}]})
    return client


# ------------------------------------------------------------------ reading the request

class TestWhichChart:
    @pytest.mark.parametrize("message,kind", [
        ("อยากดู report ยอดขายเป็นกราฟ", "pipeline"),
        ("ขอกราฟยอดขาย", "pipeline"),
        ("กราฟดีลแต่ละสถานะ", "pipeline"),
        ("sales report as a chart", "pipeline"),
        ("ยอดขาย 6 เดือนเป็นกราฟ", "monthly"),
        ("กราฟยอดขายรายเดือน", "monthly"),
        ("show me a graph of monthly sales", "monthly"),
        ("สินค้าขายดี 5 อันดับ เป็นกราฟ", "products"),
        ("top products chart", "products"),
        ("ยอดขายรายคนเป็นกราฟ", "owner"),
        ("กราฟยอดขายแต่ละคน", "owner"),
    ])
    def test_every_phrasing_the_owner_listed(self, message, kind):
        request = chat._chart_request(message)
        assert request is not None and request["kind"] == kind

    def test_a_report_that_does_not_say_chart_is_not_one(self):
        assert chat._chart_request("ยอดขาย") is None
        assert chat._chart_request("สรุปยอดขายเดือนนี้") is None
        assert chat._chart_request("") is None

    def test_the_period_and_the_ranking_are_read_from_the_sentence(self):
        assert chat._chart_request("ยอดขาย 3 เดือนเป็นกราฟ")["options"] == {"months": 3}
        assert chat._chart_request("กราฟสินค้าขายดี 3 อันดับ")["options"] == {"top": 3}
        assert chat._chart_request("top 3 products chart")["options"] == {"top": 3}
        assert chat._chart_request("กราฟยอดขายรายเดือน")["options"] == {}


# ------------------------------------------------------------------ the reply

class TestTheChartComesBack:
    @pytest.mark.parametrize("message,heading", [
        ("อยากดู report ยอดขายเป็นกราฟ", "กราฟยอดขายตามสถานะดีล"),
        ("ขอกราฟยอดขาย", "กราฟยอดขายตามสถานะดีล"),
        ("กราฟดีลแต่ละสถานะ", "กราฟยอดขายตามสถานะดีล"),
        ("ยอดขาย 6 เดือนเป็นกราฟ", "กราฟยอดขาย 6 เดือนล่าสุด"),
        ("กราฟยอดขายรายเดือน", "กราฟยอดขาย 6 เดือนล่าสุด"),
        ("สินค้าขายดี 5 อันดับ เป็นกราฟ", "กราฟสินค้าขายดี"),
        ("ยอดขายรายคนเป็นกราฟ", "กราฟยอดขายรายคน"),
        ("sales report as a chart", "กราฟยอดขายตามสถานะดีล"),
        ("show me a graph of monthly sales", "กราฟยอดขาย 6 เดือนล่าสุด"),
        ("top products chart", "กราฟสินค้าขายดี"),
    ])
    async def test_one_picture_and_a_summary(self, store, message, heading):
        client = await sales_client()
        reply = await handle_chat_message(client, message=message, ctx=_ctx(oa="sales"))
        assert len(reply.images) == 1
        assert reply.images[0].startswith("https://app.example/api/v1/assets/")
        assert reply.text.startswith(heading)
        # The text is a complete answer on its own: a notification preview
        # never renders the image.
        assert "ยังส่งรูปกราฟไม่ได้" not in reply.text
        path, content_type, _ = decode_asset_token(reply.images[0].rsplit("/", 1)[-1])
        assert content_type == "image/png"
        assert store.objects[path][:8] == b"\x89PNG\r\n\x1a\n"

    async def test_the_object_is_tenant_scoped_and_the_link_is_short_lived(self, store):
        import jwt

        client = await sales_client()
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        token = reply.images[0].rsplit("/", 1)[-1]
        path, _, _ = decode_asset_token(token)
        assert path.startswith("gs://bucket/reports/11111111-1111-1111-1111-111111111111/charts/")
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        assert claims["exp"] - claims["iat"] == sales_charts.CHART_LINK_TTL_SECONDS == 3600

    async def test_the_numbers_match_the_text_summary_of_the_same_thing(self, store):
        client = await sales_client()
        chart = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        text = await handle_chat_message(client, message="ยอดขาย", ctx=_ctx(oa="sales"))
        # 2 x 15,900 + 4 x 1,500 = 37,800 won; 1 x 9,000 open.
        assert "37,800" in chart.text and "37,800" in text.text
        assert "9,000" in chart.text and "9,000" in text.text

    async def test_the_other_pictures_are_offered_as_buttons(self, store):
        client = await sales_client()
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        labels = [label for label, _ in reply.quick_replies]
        assert "กราฟรายเดือน" in labels and "ดีลแต่ละสถานะ" not in labels
        for _, says in reply.quick_replies:
            assert chat._chart_request(says) is not None

    async def test_a_shop_with_no_deals_is_told_so_rather_than_shown_a_lie(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        assert "ยังไม่มีดีลในระบบ" in reply.text
        assert len(reply.images) == 1  # the picture says "ไม่มีข้อมูล" too


class TestTheTextAnswerOffersTheChart:
    async def test_the_sales_summary_carries_a_view_as_chart_button(self, store):
        client = await sales_client()
        reply = await handle_chat_message(client, message="ยอดขาย", ctx=_ctx(oa="sales"))
        assert not reply.images
        assert ("ดูเป็นกราฟ", "ขอกราฟยอดขาย") in reply.quick_replies
        # And pressing it returns the picture.
        pressed = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        assert len(pressed.images) == 1


class TestPermission:
    async def test_without_deal_read_the_pipeline_chart_is_the_ordinary_refusal(self, store):
        client = await sales_client(permission_keys=["customer.read"])
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        assert reply.text == chat.SUGGEST_NO_PERMISSION_LEAD["th"]
        assert not reply.images and not store.objects

    @pytest.mark.parametrize("message", ["กราฟยอดขายรายเดือน", "กราฟสินค้าขายดี", "กราฟยอดขายรายคน"])
    async def test_without_view_reports_the_report_charts_are_refused(self, store, message):
        client = await sales_client(permission_keys=["customer.read", "deal.read"])
        reply = await handle_chat_message(client, message=message, ctx=_ctx(oa="sales"))
        assert reply.text == chat.SUGGEST_NO_PERMISSION_LEAD["th"]
        assert not reply.images and not store.objects


class TestWithoutStorage:
    async def test_the_answer_survives_with_one_sentence_saying_why(self, no_store):
        client = await sales_client()
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        assert not reply.images
        assert reply.text.startswith("กราฟยอดขายตามสถานะดีล")
        assert "ยังส่งรูปกราฟไม่ได้ตอนนี้" in reply.text

    async def test_a_store_that_fails_mid_write_is_the_same_story(self, monkeypatch):
        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        broken = FakeStore(fail=storage_base.DocumentStoreError("bucket exploded"))
        monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: broken)
        client = await sales_client()
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        assert not reply.images and "ยังส่งรูปกราฟไม่ได้ตอนนี้" in reply.text

    async def test_no_public_base_url_means_no_link_and_the_text_says_so(self, monkeypatch):
        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
        monkeypatch.setattr(settings, "public_base_url", "")
        fake = FakeStore()
        monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: fake)
        client = await sales_client()
        reply = await handle_chat_message(client, message="ขอกราฟยอดขาย", ctx=_ctx(oa="sales"))
        assert not reply.images and "ยังส่งรูปกราฟไม่ได้ตอนนี้" in reply.text


# ------------------------------------------------------------------ the free-form engine

class TestTheAiReportEngineGainsAChart:
    async def test_a_grouped_report_asked_for_as_a_chart_returns_one(self, store):
        client = await sales_client()
        client.run_report_query = _report_query(client, {
            "rows": [{"key": "m1", "label": "สมชาย", "value": 5}, {"key": "m2", "label": "สมหญิง", "value": 2}],
            "total": 7})
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"entity": "deals", "metric": "count", "filter": {"stage": "won"}, "group_by": "owner_member_id"})))
        reply = await handle_chat_message(
            client, message="สรุปดีลปิดสำเร็จแยกตามผู้ดูแล เป็นกราฟ", ctx=_ctx(oa="sales"), ai_client=ai)
        assert len(reply.images) == 1
        assert "• สมชาย: 5" in reply.text
        path, content_type, _ = decode_asset_token(reply.images[0].rsplit("/", 1)[-1])
        assert content_type == "image/png" and store.objects[path][:8] == b"\x89PNG\r\n\x1a\n"

    async def test_a_single_number_says_there_is_nothing_to_plot(self, store):
        client = await sales_client()
        client.run_report_query = _report_query(client, {"rows": [], "total": 3})
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"entity": "deals", "metric": "count", "filter": {"stage": "won"}, "group_by": None})))
        reply = await handle_chat_message(
            client, message="สรุปดีลปิดสำเร็จ เป็นกราฟ", ctx=_ctx(oa="sales"), ai_client=ai)
        assert not reply.images
        assert "รายงานนี้เป็นตัวเลขเดียว" in reply.text and "รวม 3" in reply.text

    async def test_a_plain_report_still_answers_in_text_and_offers_the_chart(self, store):
        client = await sales_client()
        client.run_report_query = _report_query(client, {"rows": [], "total": 3})
        ai = httpx.AsyncClient(transport=_ai(json.dumps({"entity": "deals", "metric": "count"})))
        reply = await handle_chat_message(
            client, message="สรุปดีลปิดสำเร็จเดือนนี้", ctx=_ctx(oa="sales"), ai_client=ai)
        assert not reply.images
        assert ("ดูเป็นกราฟ", "สรุปดีลปิดสำเร็จเดือนนี้ เป็นกราฟ") in reply.quick_replies


def _report_query(client, result):
    async def run(license_id, spec, actor_id=None):
        return {**spec, **result, "generated_at": "2026-09-08T10:00:00+00:00"}
    return run


# ------------------------------------------------------------------ through the route

class TestThroughTheAssetRoute:
    async def test_the_link_in_the_chat_serves_the_picture(self, store):
        client = await sales_client()
        reply = await handle_chat_message(client, message="กราฟดีลแต่ละสถานะ", ctx=_ctx(oa="sales"))
        app = FastAPI()
        app.include_router(routers_admin.router)
        with TestClient(app) as http:
            response = http.get("/api/v1" + reply.images[0].split("/api/v1", 1)[1])
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert 'filename="chart.png"' in response.headers["content-disposition"]

    async def test_an_expired_link_shows_nothing_and_confirms_nothing(self, store, monkeypatch):
        from chann_app.auth import document_link

        client = await sales_client()
        reply = await handle_chat_message(client, message="กราฟดีลแต่ละสถานะ", ctx=_ctx(oa="sales"))
        path, _, _ = decode_asset_token(reply.images[0].rsplit("/", 1)[-1])
        stale = document_link.issue_asset_token(path, "image/png", ttl_seconds=-1, filename="chart.png")
        app = FastAPI()
        app.include_router(routers_admin.router)
        with TestClient(app) as http:
            assert http.get(f"/api/v1/assets/{stale}").status_code == 404


class TestTheDashboardContract:
    """`/licenses/{id}/reports/ai` and `.../run` hand the page the same
    picture the chat sends, as `chart` beside `files` (parity: what the
    chat can do the dashboard can do)."""

    async def test_a_grouped_result_publishes_a_chart_link(self, store):
        spec = reports_ai.validate_query_spec({"entity": "deals", "group_by": "stage"})
        result = {**spec, "rows": [{"key": "won", "label": "สำเร็จ", "value": 5},
                                   {"key": "lost", "label": "ไม่สำเร็จ", "value": 1}], "total": 6}
        url, plottable = await reports_ai.publish_chart_for(spec, result, "th", license_id="L1")
        assert plottable and url and url.startswith("https://app.example/api/v1/assets/")
        path, content_type, _ = decode_asset_token(url.rsplit("/", 1)[-1])
        assert content_type == "image/png" and path.startswith("gs://bucket/reports/L1/charts/")

    async def test_a_single_number_publishes_nothing_and_says_it_is_not_plottable(self, store):
        spec = reports_ai.validate_query_spec({"entity": "deals"})
        url, plottable = await reports_ai.publish_chart_for(spec, {**spec, "rows": [], "total": 3}, "th", license_id="L1")
        assert url is None and plottable is False and not store.objects

    async def test_without_a_store_the_page_simply_gets_no_chart(self, no_store):
        spec = reports_ai.validate_query_spec({"entity": "deals", "group_by": "stage"})
        result = {**spec, "rows": [{"key": "won", "label": "สำเร็จ", "value": 5}], "total": 5}
        assert await reports_ai.publish_chart_for(spec, result, "th", license_id="L1") == (None, True)


class TestTheNumbersBehindThePictures:
    """The four builders read only what the tier already fetches, and read
    it the way the rest of the system does."""

    def test_a_deal_is_worth_its_line_items_then_its_stated_amount(self):
        lines = {"products": [{"product_name": "แอร์", "quoted_unit_price": "15900.00", "qty": 2}], "amount": "1.00"}
        assert sales_charts.deal_value(lines) == 31800
        # 0024: a deal with no lines is worth the amount the salesperson
        # stated — the same fallback DealRepository.pipeline_summary uses.
        assert sales_charts.deal_value({"products": [], "amount": "250000"}) == 250000
        assert sales_charts.deal_value({}) == 0

    def test_the_month_a_deal_counts_in_is_a_bangkok_day(self):
        # 20:30 UTC on 31 Aug is already 03:30 on 1 Sep in the shop.
        assert sales_charts.deal_closed_on({"created_at": "2026-08-31T20:30:00+00:00"}) == date(2026, 9, 1)
        # The close date the shop typed is a plain day and is taken as it is.
        assert sales_charts.deal_closed_on({"expected_close_date": "2026-08-31",
                                            "created_at": "2026-01-01T00:00:00+00:00"}) == date(2026, 8, 31)

    def test_month_labels_end_with_this_month_and_carry_the_year(self):
        labels = sales_charts.month_labels(date(2026, 1, 15), 3, "th")
        assert [text for _, text in labels] == ["พ.ย. 68", "ธ.ค. 68", "ม.ค. 69"]
        assert [key for key, _ in labels] == [(2025, 11), (2025, 12), (2026, 1)]
        assert [text for _, text in sales_charts.month_labels(date(2026, 1, 15), 2, "en")] == ["Dec 25", "Jan 26"]

    async def test_a_won_deal_lands_in_the_month_it_closed(self):
        client = await sales_client()
        for deal in await client.list_deals("L1", stage="won"):
            deal["expected_close_date"] = "2026-07-04"
        answer = await sales_charts.monthly_chart(
            client, license_id="L1", language="th", months=3, today=date(2026, 9, 8))
        assert [label for label, _ in answer.chart.points] == ["ก.ค. 69", "ส.ค. 69", "ก.ย. 69"]
        assert [value for _, value in answer.chart.points] == [37800.0, 0.0, 0.0]
        assert answer.chart.kind == "line" and not answer.empty

    async def test_best_sellers_are_ranked_and_capped_at_what_was_asked_for(self):
        client = await sales_client()
        answer = await sales_charts.product_chart(client, license_id="L1", language="th", top=1)
        assert [label for label, _ in answer.chart.points] == ["แอร์ 12000 BTU"]
        assert answer.chart.kind == "hbar"

    async def test_sales_per_person_uses_the_member_display_name(self):
        client = await sales_client()
        answer = await sales_charts.owner_chart(client, license_id="L1", language="th")
        assert [label for label, _ in answer.chart.points] == ["สมชาย ขายเก่ง", "สมหญิง"]
        assert [value for _, value in answer.chart.points] == [31800.0, 6000.0]

    async def test_an_unknown_chart_is_a_programming_error(self):
        client = await sales_client()
        with pytest.raises(ValueError):
            await sales_charts.build("donut", client, license_id="L1")
