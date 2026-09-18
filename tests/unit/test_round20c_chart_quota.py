"""Round 20c — charts made to order, capped per company per month.

Owner, 17 ก.ย. 2569: "เรื่องกราฟอยากให้มีอิสระสามารถ generate กราฟจากระบบได้
เลย แต่ทำเป็นระบบ quota ก็ได้ว่าจะใช้งานกราฟแบบสร้างเองได้กี่ครั้งของบริษัท
นั้นๆ" — then chose 30 a month, set by the Chann administrator, with the
four ready-made charts and every report in words staying free.

The engine that draws a made-to-order chart already existed
(`reports_ai.answer(..., with_chart=True)`). What stood in its way was the
door: `_is_ai_report_request` only accepted a sentence STARTING with
"รายงาน"/"สรุป"/…, so "กราฟยอดขายแยกตามพื้นที่" was not a report request at
all and fell through to the fixed pipeline picture.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chart_quota
from chann_app.services.chat import (
    _chart_request, _is_ai_report_request, _wants_a_made_to_order_chart,
)
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = ["view_reports", "deal.read", "customer.read"]


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


@pytest.fixture(autouse=True)
def _a_shop_that_can_receive_pictures(monkeypatch):
    """A document store, because the allowance now follows the picture.

    Round 20c spent the quota BEFORE drawing, so these scenarios ran fine
    with no store at all: nothing was ever delivered and the counter moved
    anyway. That is the bug the owner reported on 18 ก.ย. 2569 — "สร้าง
    รายงานด้วย AI" took one of the month's charts and answered with a page
    of text. The count now moves only when a picture reaches the person,
    so a shop that cannot be sent one is never charged, and these tests
    have to describe a shop that can.
    """
    from chann_app.services import reports_ai
    from chann_app.services.storage.base import StoredDocument, sha256_hex

    class _Store:
        async def put(self, *, key, content, content_type):
            return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content), size=len(content))

    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: _Store())


class TestWhichRoadAPictureTakes:
    """The four ready-made charts must stay free and deterministic; anything
    beyond them has to reach the engine that can draw it."""

    @pytest.mark.parametrize("asked", [
        "ขอกราฟยอดขาย", "กราฟยอดขาย", "ขอกราฟ", "กราฟดีล",
        # the monthly picture takes a period itself, so it stays free
        "กราฟยอดขาย 6 เดือนล่าสุด", "กราฟสินค้าขายดี 5 อันดับ",
    ])
    def test_a_ready_made_request_stays_ready_made(self, asked):
        assert _chart_request(asked) is not None, asked
        assert not _wants_a_made_to_order_chart(asked), asked

    @pytest.mark.parametrize("asked", [
        "กราฟยอดขายแยกตามพื้นที่ 6 เดือนล่าสุด",
        "ขอกราฟเปรียบเทียบยอดขายรายคนของไตรมาสนี้",
    ])
    def test_a_made_to_order_request_goes_to_the_engine(self, asked):
        assert _wants_a_made_to_order_chart(asked), asked
        assert _is_ai_report_request(asked), asked

    def test_the_fixed_sales_summary_is_still_not_an_ai_report(self):
        assert not _is_ai_report_request("สรุปยอดขาย")


class TestTheQuotaItself:
    def _shop(self, **extra) -> FakeDataClient:
        client = FakeDataClient(permission_keys=KEYS, role="sales")
        for key, value in extra.items():
            setattr(client, key, value)
        return client

    async def test_the_first_chart_of_the_month_is_allowed(self):
        client = self._shop()
        out = await chart_quota.spend_one(client, license_id="lic-1")
        assert out["allowed"] is True, out
        assert out["used"] == 1 and out["allowance"] == 30, out

    async def test_it_stops_at_the_allowance(self):
        client = self._shop(_ai_chart_quota=3)
        seen = [(await chart_quota.spend_one(client, license_id="lic-1"))["allowed"]
                for _ in range(4)]
        assert seen == [True, True, True, False], seen

    async def test_a_company_sets_its_own_number(self):
        client = self._shop(_ai_chart_quota=1)
        assert (await chart_quota.spend_one(client, license_id="lic-1"))["allowed"] is True
        refused = await chart_quota.spend_one(client, license_id="lic-1")
        assert refused["allowed"] is False and refused["allowance"] == 1, refused

    async def test_a_new_month_starts_over(self):
        client = self._shop(_ai_chart_quota=1)
        await chart_quota.spend_one(client, license_id="lic-1")
        client._ai_chart_usage = {"month": "2026-08", "used": 99}
        assert (await chart_quota.spend_one(client, license_id="lic-1"))["allowed"] is True

    async def test_a_data_tier_outage_does_not_cost_the_shop_its_chart(self):
        """The quota caps a cost. Failing closed on an outage would make an
        unrelated incident look like a billing wall."""
        client = self._shop()

        async def _boom(*_a, **_k):
            raise RuntimeError("data tier said 503")

        client.consume_ai_chart_quota = _boom
        out = await chart_quota.spend_one(client, license_id="lic-1")
        assert out["allowed"] is True and out.get("unknown") is True, out


def _ai_router_then_spec(spec: dict):
    """The router call, then the report engine's own spec call — two model
    calls in one turn, which one canned answer cannot serve."""
    replies = [
        json.dumps({"action": "read", "entity": "report",
                    "fields": {"type": "chart", "period": "6 months"}, "missing": []}),
        json.dumps(spec),
    ]
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        content = replies[min(seen["n"], len(replies) - 1)]
        seen["n"] += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "provider": "fireworks",
        })

    return httpx.MockTransport(handler)


SPEC = {"entity": "deals", "metric": "count", "group_by": "stage", "filters": {}}


class TestWhatTheShopIsToldWhenItRunsOut:
    async def _ask_for_a_chart(self, client):
        from chann_app.services.chat import handle_chat_message

        return await handle_chat_message(
            client, message="ขอกราฟยอดขายแยกตามพื้นที่ 6 เดือนล่าสุด",
            ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=_ai_router_then_spec(SPEC)),
        )

    async def test_the_answer_says_the_number_and_when_it_resets(self):
        client = FakeDataClient(permission_keys=KEYS, role="sales")
        client._ai_chart_quota = 0
        reply = await self._ask_for_a_chart(client)
        assert "ครบ 0 ครั้งของเดือนนี้แล้ว" in reply.text, reply.text
        assert "เริ่มนับใหม่" in reply.text, reply.text

    async def test_it_points_at_what_is_still_free(self):
        client = FakeDataClient(permission_keys=KEYS, role="sales")
        client._ai_chart_quota = 0
        reply = await self._ask_for_a_chart(client)
        assert "ยังใช้ได้ไม่จำกัด" in reply.text, reply.text
        assert [b for b in reply.quick_replies if b[0] == "กราฟยอดขาย"], reply.quick_replies

    async def test_a_ready_made_chart_never_spends_the_quota(self):
        """The whole point of keeping them deterministic."""
        from chann_app.services.chat import handle_chat_message

        client = FakeDataClient(permission_keys=KEYS + ["deal.read"], role="sales")
        await handle_chat_message(
            client, message="ขอกราฟยอดขาย", ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "read", "entity": "report",
                 "fields": {"type": "chart"}, "missing": []}
            ))),
        )
        assert not [w for w in client.recorded if w[0] == "consume_ai_chart_quota"], client.recorded


class TestOnlyTheChannAdminSetsTheNumber:
    """Owner's choice: the allowance is ours to set, not the shop's."""

    def _app(self, client):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from chann_app import routers_admin

        async def override_client():
            yield client

        async def override_admin():
            return {"sub": "admin-1", "role": "owner"}

        app = FastAPI()
        app.include_router(routers_admin.router)
        app.dependency_overrides[routers_admin.get_data_client] = override_client
        app.dependency_overrides[routers_admin.require_admin] = override_admin
        return TestClient(app)

    def _client(self):
        class _C(FakeDataClient):
            async def aclose(self):
                pass

            async def put_license_setting(self, license_id, key, value, actor_id=None):
                self.recorded.append(("put_license_setting", license_id, key, value))
                return {"setting_key": key, "setting_value": value}

            async def platform_tenant(self, license_id):
                return {"id": license_id, "company_name": "ร้านทดสอบ"}

        return _C(permission_keys=[], role="sales")

    def test_the_admin_can_raise_it(self):
        client = self._client()
        response = self._app(client).patch(
            "/api/v1/platform/tenants/lic-1", json={"ai_chart_quota": 100},
        )
        assert response.status_code == 200, response.text
        written = [r for r in client.recorded if r[0] == "put_license_setting"]
        assert written and written[0][2] == "ai_chart_quota" and written[0][3] == 100, written

    def test_a_number_that_is_not_one_is_refused(self):
        client = self._client()
        response = self._app(client).patch(
            "/api/v1/platform/tenants/lic-1", json={"ai_chart_quota": "เยอะๆ"},
        )
        assert response.status_code == 422, response.text
        assert not [r for r in client.recorded if r[0] == "put_license_setting"], client.recorded

    def test_zero_turns_made_to_order_charts_off_for_that_shop(self):
        client = self._client()
        response = self._app(client).patch(
            "/api/v1/platform/tenants/lic-1", json={"ai_chart_quota": 0},
        )
        assert response.status_code == 200, response.text
        assert [r for r in client.recorded if r[0] == "put_license_setting"][0][3] == 0


class TestTheExplicitCommand:
    """Owner: "การใช้ AI ทำเป็นคำสั่งนำหน้าไว้ก็ได้ เช่น สร้างรายงานด้วย AI :
    ชื่อกราฟที่ต้องการ เพื่อให้ง่ายต่อการตรวจสอบ"."""

    @pytest.mark.parametrize("typed,wanted", [
        ("สร้างรายงานด้วย AI: ยอดขายแยกตามพื้นที่", "ยอดขายแยกตามพื้นที่"),
        ("สร้างรายงานด้วย AI : ยอดขายแยกตามพื้นที่", "ยอดขายแยกตามพื้นที่"),
        ("รายงานด้วย AI ยอดขายรายไตรมาส", "ยอดขายรายไตรมาส"),
        ("AI report: sales by region", "sales by region"),
    ])
    def test_the_prefix_is_stripped_to_what_was_asked_for(self, typed, wanted):
        from chann_app.services.chat import ai_report_asked_outright

        assert ai_report_asked_outright(typed) == wanted, typed

    def test_an_ordinary_sentence_is_not_an_explicit_command(self):
        from chann_app.services.chat import ai_report_asked_outright

        assert ai_report_asked_outright("ขอกราฟยอดขาย") == ""
        assert ai_report_asked_outright("สรุปยอดขายเดือนนี้") == ""

    def test_it_always_wins_over_the_ready_made_pictures(self):
        """Even when the words after it look like a ready-made chart: the
        person said outright which road they wanted."""
        from chann_app.services.chat import _wants_a_made_to_order_chart

        assert _wants_a_made_to_order_chart("สร้างรายงานด้วย AI: กราฟยอดขาย")
        assert _is_ai_report_request("สร้างรายงานด้วย AI: กราฟยอดขาย")

    async def test_it_spends_the_quota_like_any_other_made_to_order_chart(self):
        from chann_app.services.chat import handle_chat_message

        client = FakeDataClient(permission_keys=KEYS, role="sales")
        await handle_chat_message(
            client, message="สร้างรายงานด้วย AI: ยอดดีลแยกตามสถานะ",
            ctx=_ctx(primary_role="sales", oa="sales"),
            # An explicit command skips the router, so the FIRST model call
            # is the spec call — the same transport its sibling test uses.
            # With the router transport the spec call read the router's
            # answer, no report came out, and this test still passed:
            # proof of the bug it now guards against, since the old code
            # spent the allowance before knowing there was a picture.
            ai_client=httpx.AsyncClient(transport=_ai(json.dumps(SPEC))),
        )
        assert [w for w in client.recorded if w[0] == "consume_ai_chart_quota"], client.recorded

    async def test_the_engine_reads_the_request_not_the_wrapper(self):
        """The ROUTER sees the whole sentence — it has to, to recognise the
        command. The report engine's own spec call must not: asking it to
        make sense of the words "สร้างรายงานด้วย AI:" is asking it to parse
        the instruction instead of the request."""
        from chann_app.services.chat import handle_chat_message

        client = FakeDataClient(permission_keys=KEYS, role="sales")
        prompts: list[str] = []
        replies = [
            json.dumps({"action": "read", "entity": "report",
                        "fields": {"type": "chart"}, "missing": []}),
            json.dumps(SPEC),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            prompts.append(json.dumps(body["messages"], ensure_ascii=False))
            content = replies[min(len(prompts) - 1, len(replies) - 1)]
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "x",
            })

        await handle_chat_message(
            client, message="สร้างรายงานด้วย AI: ยอดดีลแยกตามสถานะ",
            ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        # The explicit command is honoured before the router, so the router's
        # model call does not happen at all — one call, not two. That is the
        # improvement, not a gap: the person already said which road.
        assert prompts, "the spec call never happened"
        spec_prompt = prompts[-1]
        assert "ยอดดีลแยกตามสถานะ" in spec_prompt, spec_prompt[-400:]
        assert "สร้างรายงานด้วย" not in spec_prompt, spec_prompt[-400:]


@pytest.mark.asyncio
class TestTheHintIsWhereChartsAreTalkedAbout:
    """Owner: "อย่าลืมใส่คำแนะนำการสร้างกราฟด้วย AI ลงไปเวลามีการถามถึงกราฟ
    ด้วยนะ ผู้ใช้จะได้รู้" — the moment someone asks for a picture is the
    moment they would want a different one."""

    async def _ready_made_chart(self):
        from chann_app.services.chat import handle_chat_message

        client = FakeDataClient(permission_keys=KEYS + ["deal.read"], role="sales")
        return await handle_chat_message(
            client, message="ขอกราฟยอดขาย", ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "read", "entity": "report", "fields": {"type": "chart"},
                 "missing": []}
            ))),
        )

    async def test_a_ready_made_chart_says_how_to_ask_for_another_kind(self):
        reply = await self._ready_made_chart()
        assert "สร้างรายงานด้วย AI:" in reply.text, reply.text

    async def test_it_shows_an_example_not_just_the_syntax(self):
        reply = await self._ready_made_chart()
        assert "เช่น" in reply.text, reply.text

    async def test_there_is_a_button_for_it(self):
        reply = await self._ready_made_chart()
        assert [b for b in reply.quick_replies if b[0] == "กราฟด้วย AI"], reply.quick_replies

    async def test_the_button_prefills_the_command(self):
        reply = await self._ready_made_chart()
        button = [b for b in reply.quick_replies if b[0] == "กราฟด้วย AI"][0]
        assert button[1].startswith("สร้างรายงานด้วย AI:"), button

    async def test_the_reply_still_fits_a_line_bubble(self):
        reply = await self._ready_made_chart()
        assert len(reply.text.split("\n")) <= 15, reply.text

    async def test_an_ai_chart_says_which_one_it_was(self):
        from chann_app.services.chat import handle_chat_message

        client = FakeDataClient(permission_keys=KEYS, role="sales")
        client._ai_chart_quota = 30
        reply = await handle_chat_message(
            client, message="สร้างรายงานด้วย AI: ยอดดีลแยกตามสถานะ",
            ctx=_ctx(primary_role="sales", oa="sales"),
            # An explicit command skips the router, so the FIRST model call
            # is the spec call.
            ai_client=httpx.AsyncClient(transport=_ai(json.dumps(SPEC))),
        )
        assert "1/30" in reply.text, reply.text
        assert "สร้างด้วย AI" in reply.text, reply.text

    async def test_the_sales_summary_offers_it_too(self):
        from chann_app.services.chat import handle_chat_message

        client = FakeDataClient(permission_keys=KEYS + ["deal.read"], role="sales")
        reply = await handle_chat_message(
            client, message="สรุปการขาย", ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "read", "entity": "report", "fields": {"type": "sales"},
                 "missing": []}
            ))),
        )
        assert [b for b in reply.quick_replies if b[0] == "กราฟด้วย AI"], reply.quick_replies
