"""Round 21D — the AI template road refuses on plan BEFORE the drafting
model call (pre-flight S15/M11, ruling R-B).

`TEMPLATE_DESIGN_TRIGGERS` is a bare-substring table that reaches a model
call and writes a template row. Task 5 made the upload route refuse a
Starter shop, but only after `draft_template` had spent the call. Here the
decline comes first: the standard plan refusal, no model call, no row, no
stored object. A word declining — never acting (MODEL_FIRST)."""
from __future__ import annotations

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ctx
from test_template_design import A_QUOTE_DESIGN, SALES_KEYS, _ai, store  # noqa: F401 — the fixture

LOCKED = "🔒 «แบบฟอร์มเอกสารของร้านเอง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")
    monkeypatch.setattr(settings, "chann_sales_contact", "LINE @channcrm|https://line.me/R/ti/p/@channcrm")


def _owner() -> FakeDataClient:
    client = FakeDataClient(permission_keys=SALES_KEYS, role="owner")
    client._is_owner = True
    return client


async def _say(client, message, ai, *, plan):
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload(plan)
    return await handle_chat_message(client, message=message, ctx=ctx, ai_client=ai)


class TestOnStarter:
    async def test_designing_a_quote_is_refused_before_the_model_is_asked(self, store):
        client, ai = _owner(), _ai(A_QUOTE_DESIGN)
        reply = await _say(client, "ออกแบบใบเสนอราคา", ai, plan="starter")
        assert reply.text.startswith(LOCKED)
        assert "อยากเปิดใช้: ติดต่อทีม Chann1 CRM AI LINE @channcrm" in reply.text
        assert ai.calls["n"] == 0
        assert client._templates == [] and client._template_versions == [] and store.puts == []

    async def test_the_ambiguous_request_is_refused_without_asking_which(self, store):
        client, ai = _owner(), _ai(A_QUOTE_DESIGN)
        reply = await _say(client, "ออกแบบเอกสาร", ai, plan="starter")
        assert reply.text.startswith(LOCKED)
        assert ai.calls["n"] == 0
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_answering_the_type_question_after_a_downgrade_is_refused(self, store):
        client, ai = _owner(), _ai(A_QUOTE_DESIGN)
        asked = await _say(client, "ออกแบบเอกสาร", ai, plan="pro")        # the question, on Pro
        assert "ใบรายงานการซ่อม" in asked.text
        reply = await _say(client, "ใบเสนอราคา", ai, plan="starter")    # the answer, on Starter
        assert reply.text.startswith(LOCKED)
        assert ai.calls["n"] == 0 and client._templates == []

    async def test_refining_a_draft_after_a_downgrade_is_refused(self, store):
        client, ai = _owner(), _ai(A_QUOTE_DESIGN)
        await _say(client, "ออกแบบใบเสนอราคา", ai, plan="pro")
        spent = ai.calls["n"]
        await _say(client, "แก้เพิ่ม", ai, plan="pro")
        reply = await _say(client, "เพิ่มช่องเลขที่ผู้เสียภาษี", ai, plan="starter")
        assert reply.text.startswith(LOCKED)
        assert ai.calls["n"] == spent and len(client._template_versions) == 1

    async def test_publishing_a_draft_after_a_downgrade_is_refused(self, store):
        client, ai = _owner(), _ai(A_QUOTE_DESIGN)
        await _say(client, "ออกแบบใบเสนอราคา", ai, plan="pro")
        reply = await _say(client, "ใช้เลย", ai, plan="starter")
        assert reply.text.startswith(LOCKED)
        assert [v["status"] for v in client._template_versions] == ["previewed"]


class TestOnPro:
    async def test_the_same_sentence_drafts(self, store):
        client, ai = _owner(), _ai(A_QUOTE_DESIGN)
        reply = await _say(client, "ออกแบบใบเสนอราคา", ai, plan="pro")
        assert "ฉบับร่าง" in reply.text and ai.calls["n"] == 1
