"""Round 21D — the three OAs on a plan without the feature (spec §7).

A technician of a downgraded shop is told once, per message, that the
shop switched service off — profile and language still work; a customer
of a shop without the Customer LINE link hears how to reach the shop; the
storefront (platform-wide) still works; an invite code made before a
downgrade is refused and not spent."""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.data_client import DataTierError
from chann_app.services import registration
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, *, oa, plan="starter", ai=None, role=None):
    ctx = _ctx(primary_role=role or oa, oa=oa)
    ctx.memberships[0]["plan"] = plan_payload(plan)
    transport = _ai(json.dumps(ai or {"action": "suggest", "entity": None, "fields": {}, "missing": []}))
    return await handle_chat_message(client, message=message, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=transport))


class TestTheTechnicianOA:
    async def test_anything_about_the_shop_gets_the_notice(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        reply = await _say(client, "งานของฉัน", oa="technician")
        assert reply.text == ("ร้าน บริษัททดสอบ ปิดงานบริการและทีมช่างไว้ชั่วคราว (แพ็กเกจ Starter) — "
                              "งานและรายงานเดิมยังอยู่ครบ แจ้งเจ้าของร้านถ้าต้องการเปิดใช้")

    async def test_switching_language_still_works(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read"])
        reply = await _say(client, "english please", oa="technician")
        assert "ปิดงานบริการ" not in reply.text

    async def test_on_pro_nothing_changes(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read"])
        reply = await _say(client, "งานของฉัน", oa="technician", plan="pro")
        assert "ปิดงานบริการ" not in reply.text


class TestTheCustomerOA:
    async def test_a_linked_customer_hears_how_to_reach_the_shop(self):
        client = FakeDataClient(role="customer", permission_keys=[],
                                company_profile={"company_name": "บริษัททดสอบ", "company_phone": "021234567"})
        reply = await _say(client, "แอร์ไม่เย็น", oa="customer")
        assert reply.text == "ร้าน บริษัททดสอบ ยังไม่เปิดให้บริการลูกค้าทาง LINE — ติดต่อร้านได้ที่ 021234567"

    async def test_without_a_phone_the_line_is_left_out(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await _say(client, "แอร์ไม่เย็น", oa="customer")
        assert reply.text == "ร้าน บริษัททดสอบ ยังไม่เปิดให้บริการลูกค้าทาง LINE"

    async def test_the_storefront_still_works(self):
        client = FakeDataClient(role="customer", permission_keys=[],
                                storefront_results=[{"product_name": "แอร์ 12000 BTU", "company_name": "ร้านอื่น",
                                                     "company_code": "ABCD2345", "unit_price": "15000"}])
        reply = await _say(client, "สินค้าทั้งหมด", oa="customer")
        assert "ยังไม่เปิดให้บริการลูกค้าทาง LINE" not in reply.text


class TestInvites:
    async def test_starter_owner_asking_for_a_technician_code_hears_the_plan(self):
        client = FakeDataClient(role="owner", permission_keys=["member.manage", "setting.manage"])
        client._is_owner = True
        client._plan = plan_payload("starter")
        reply = await _say(client, "ขอรหัสเชิญช่าง", oa="sales", role="owner")
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป")
        assert not [r for r in client.recorded if r[0] == "create_invite"]

    async def test_at_the_limit_no_code_is_made(self):
        client = FakeDataClient(role="owner", permission_keys=["member.manage"])
        client._plan = plan_payload("starter")
        client._members_count = 5
        reply = await _say(client, "ขอรหัสเชิญทีมขาย", oa="sales", role="owner")
        assert reply.text == ("ร้านมีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว — "
                              "เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม")


class _Redeeming(FakeDataClient):
    def __init__(self, body, status, **kw):
        super().__init__(**kw)
        self._body, self._status = body, status

    async def redeem_invite(self, *, invite_code, chann_uid, display_name=None, oa=None):
        raise DataTierError(self._status, str(self._body), self._body)


class TestRedeemingACode:
    async def test_a_technician_code_on_a_downgraded_shop(self):
        client = _Redeeming({"error": "plan_required", "feature": "feature.service", "plan": "starter",
                             "min_plan": "pro", "company_name": "ร้านแอร์เย็น"}, 403)
        ctx = _ctx(primary_role="technician", oa="technician")
        text = await registration._redeem_invite_reply(client, "ABCDEFGHJK", ctx, "th", oa="technician")
        assert text == ("รหัสนี้ใช้ไม่ได้ตอนนี้ — ร้าน ร้านแอร์เย็น ใช้แพ็กเกจ Starter "
                        "ซึ่งยังไม่มีงานบริการและทีมช่าง แจ้งเจ้าของร้านได้เลย")

    async def test_a_full_shop_refuses_the_joiner_and_tells_the_owner(self):
        client = _Redeeming({"error": "member_limit_reached", "limit": 5, "plan": "starter",
                             "license_id": "11111111-1111-1111-1111-111111111111",
                             "company_name": "ร้านแอร์เย็น", "owner_chann_uid": "CHN-OWNER"}, 409)
        client._line_targets = {"CHN-OWNER": "U-owner-line"}
        ctx = _ctx(primary_role="sales", oa="sales")
        text = await registration._redeem_invite_reply(client, "ABCDEFGHJK", ctx, "th", oa="sales")
        assert text == ("ร้าน ร้านแอร์เย็น มีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว ยังเข้าร่วมไม่ได้ — "
                        "แจ้งเจ้าของร้านได้เลย")
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert told and told[0][2] == "CHN-OWNER" and told[0][3] == "member_limit_reached"

    async def test_the_code_is_not_consumed_by_the_refusal(self):
        """Spec §7.2: a code made before a downgrade still works after the
        upgrade — the redeem attempt that got the plan refusal is the ONLY
        call made; nothing else marks the code used."""
        client = _Redeeming({"error": "plan_required", "feature": "feature.service", "plan": "starter",
                             "min_plan": "pro", "company_name": "ร้านแอร์เย็น"}, 403)
        ctx = _ctx(primary_role="technician", oa="technician")
        await registration._redeem_invite_reply(client, "ABCDEFGHJK", ctx, "th", oa="technician")
        assert client.recorded == []
