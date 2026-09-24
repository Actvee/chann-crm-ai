"""Round 21D — "ร้านใช้แพ็กเกจอะไร" in chat, the twin of the dashboard's plan
card (spec §6.1, owner rule 2). The model reads the entity (measured before
and after the prompt block, plan-readings-{before,after}.txt); nothing here
acts on a word."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chat as C
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

ROOT = Path(__file__).resolve().parents[2]
READ_PLAN = {"action": "read", "entity": "plan", "fields": {}, "missing": []}
CONTACT = "LINE @channcrm|https://line.me/R/ti/p/@channcrm"


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setattr(settings, "chann_sales_contact", "")


async def _ask(code: str, *, keys=("setting.manage",), members=3, message="ร้านใช้แพ็กเกจอะไร",
               language="th", client=None, ai_override=None):
    client = client or FakeDataClient(role="owner", permission_keys=list(keys))
    client._is_owner = True
    client._plan = plan_payload(code, ai_override=ai_override)
    client._members_count = members
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = client._plan
    return await handle_chat_message(client, message=message, ctx=ctx, language=language,
                                      ai_client=httpx.AsyncClient(transport=_ai(json.dumps(READ_PLAN))))


def test_it_is_registered_the_way_every_capability_is():
    assert C.ACTION_PERMISSIONS[("read", "plan")] == "setting.manage"
    assert C.ENTITY_DASHBOARD_PAGE["plan"][0] == "company"
    prompt = (ROOT / "application/chann_app/services/ai/intent.py").read_text(encoding="utf-8")
    assert 'entity="plan"' in prompt


async def test_starter():
    reply = await _ask("starter", members=3)
    lines = reply.text.split("\n")
    assert lines[0] == "แพ็กเกจของร้าน: Starter"
    assert "ผู้ใช้ 3/5 คน" in reply.text
    assert "ถามรายงานด้วย AI: มีในแพ็กเกจ Pro ขึ้นไป" in reply.text
    assert "ยังไม่มีในแพ็กเกจนี้:" in reply.text
    assert "มีในแพ็กเกจ:" not in reply.text          # Starter includes none of the features
    assert len(lines) <= 6
    assert reply.intent == {"action": "read", "entity": "plan"}


async def test_pro_names_what_is_included_and_the_credits():
    reply = await _ask("pro", members=4)
    lines = reply.text.split("\n")
    assert lines[0] == "แพ็กเกจของร้าน: Pro"
    assert lines[1].startswith("มีในแพ็กเกจ: ")
    assert "ผู้ใช้ 4/15 คน" in reply.text
    assert "เครดิตรายงาน AI เดือนนี้ 0/30" in reply.text
    assert "ยังไม่มีในแพ็กเกจนี้: ขั้นตอนอนุมัติหลายระดับ (Enterprise+) · เชื่อมต่อ API ภายนอก (Enterprise+)" in reply.text


async def test_enterprise_plus_has_no_cap():
    reply = await _ask("enterprise_plus", members=120)
    assert "ผู้ใช้ 120 คน · ไม่จำกัด" in reply.text
    assert "ยังไม่มีในแพ็กเกจนี้" not in reply.text


@pytest.mark.parametrize("code", ["starter", "pro", "enterprise", "enterprise_plus"])
async def test_every_plan_fits_the_chat_bubble(code, monkeypatch):
    monkeypatch.setattr(settings, "chann_sales_contact", CONTACT)
    for language in ("th", "en"):
        reply = await _ask(code, language=language)
        assert len(reply.text.split("\n")) <= 15 and len(reply.text) <= 700, reply.text


async def test_the_upgrade_contact_only_when_it_is_configured(monkeypatch):
    reply = await _ask("starter")
    assert "ติดต่อทีม Chann1" not in reply.text
    assert reply.quick_reply_url is None or "line.me" not in reply.quick_reply_url[1]

    monkeypatch.setattr(settings, "chann_sales_contact", CONTACT)
    reply = await _ask("starter")
    assert "อยากเปิดใช้: ติดต่อทีม Chann1 CRM AI LINE @channcrm" in reply.text
    assert reply.quick_reply_url == ("ติดต่อทีม Chann1", "https://line.me/R/ti/p/@channcrm")

    monkeypatch.setattr(settings, "chann_sales_contact", "โทร 02-123-4567")   # a label, no url
    reply = await _ask("starter")
    assert "อยากเปิดใช้: ติดต่อทีม Chann1 CRM AI โทร 02-123-4567" in reply.text
    assert reply.quick_reply_url is None or reply.quick_reply_url[0] != "ติดต่อทีม Chann1"


async def test_the_top_plan_offers_no_upgrade(monkeypatch):
    monkeypatch.setattr(settings, "chann_sales_contact", CONTACT)
    reply = await _ask("enterprise_plus")
    assert "อยากเปิดใช้" not in reply.text


async def test_english():
    reply = await _ask("pro", language="en", message="what plan are we on")
    assert reply.text.split("\n")[0] == "The shop's plan: Pro"
    assert "AI report credits this month 0/30" in reply.text


async def test_it_needs_setting_manage():
    reply = await _ask("pro", keys=("customer.read",))
    # the central gate's refusal: the permission lead naming setting.manage
    assert reply.text.split("\n")[0] == "⛔ คุณยังไม่มีสิทธิ์ทำสิ่งนี้ — ต้องมีสิทธิ์ «จัดการการตั้งค่าบริษัท»"
    assert "🔒" not in reply.text and "แพ็กเกจของร้าน" not in reply.text


async def test_the_handler_itself_refuses_without_setting_manage():
    client = FakeDataClient(role="sales", permission_keys=["customer.read"])
    ctx = _ctx(primary_role="sales", oa="sales")
    reply = await C._handle_plan_read(client, ctx=ctx, license_id="L1", permission_keys=["customer.read"],
                                      language="th")
    assert reply.text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")


async def test_used_credits_and_a_top_up_come_from_the_payload():
    """Pro with the admin top-up to 100 and 7 spent: both numbers are the
    Data tier's, end to end."""
    client = FakeDataClient(role="owner", permission_keys=["setting.manage"])
    real = client.license_plan

    async def with_spend(license_id):
        out = await real(license_id)
        out["usage"]["ai_reports_used"] = 7
        return out
    client.license_plan = with_spend
    reply = await _ask("pro", client=client, ai_override=100)
    assert "เครดิตรายงาน AI เดือนนี้ 7/100" in reply.text


class _Unreadable(FakeDataClient):
    async def license_plan(self, license_id):
        raise RuntimeError("Data tier down")


async def test_a_failed_read_is_never_stated_as_the_plan():
    """plan_for fails open to Pro for GATING (spec §5.1); a read whose whole
    job is to say the plan must not report that fallback as fact."""
    client = _Unreadable(role="owner", permission_keys=["setting.manage"])
    reply = await _ask("starter", client=client)
    assert reply.text == C._t(C.PLAN_READ_UNAVAILABLE, "th")
    assert not any(ch.isdigit() for ch in reply.text)
    assert "Pro" not in reply.text


async def test_an_unknown_plan_payload_is_not_stated_either():
    client = FakeDataClient(role="owner", permission_keys=["setting.manage"])

    async def old_image(license_id):
        return {"plan": None, "usage": {}}
    client.license_plan = old_image
    reply = await C._handle_plan_read(client, ctx=_ctx(primary_role="owner", oa="sales"), license_id="L1",
                                      permission_keys=["setting.manage"], language="en")
    assert reply.text == C._t(C.PLAN_READ_UNAVAILABLE, "en")
