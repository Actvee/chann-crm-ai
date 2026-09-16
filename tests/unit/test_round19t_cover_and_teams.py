"""Round 19t — two more from V4Comment.html, 16 ก.ย. 2569.

* s-crm-9  "ยังแก้ไขใส่ระยะสิ้นสุดประกันไม่ได้ เลยยังไม่ได้เทสดูวันที่สิ้นสุด"
* s-setup-3 "สร้างทีมได้สำเร็จ แต่ยังไม่สามารถทดสอบเพิ่มรายชื่อช่างเข้าทีมได้"
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services import chat
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx
from test_round18e_followups import KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = sorted(set(KEYS) | {"warranty.update", "warranty.read", "warranty.create", "team.manage"})


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _registered() -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, role="sales")
    client._warranties = [{
        "id": "w-1", "warranty_number": "W-2026-0001", "serial_number": "SN12345678",
        "product_name": "แอร์", "product_id": "p1", "status": "active",
        "warranty_start": "2026-01-01", "warranty_end": "2027-01-01", "customer_chann_uid": None,
    }]
    client._products = [{"id": "p1", "product_id": "AC", "product_name": "แอร์", "warranty_months": 12}]
    return client


async def _say(client, message, reading):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(oa="sales", primary_role="sales"), ai_client=_reads(reading),
    )


class TestSettingWhenTheCoverEnds:
    async def test_an_end_date_given_outright_is_kept(self):
        client = _registered()
        reply = await _say(
            client, "ประกัน SN12345678 หมดวันที่ 31/12/2570",
            {"action": "update", "entity": "warranty",
             "fields": {"serial_number": "SN12345678", "warranty_end": "2027-12-31"}, "missing": []},
        )
        assert client._warranties[0]["warranty_end"] == "2027-12-31", client._warranties
        assert "31 ธ.ค. 2570" in reply.text, reply.text

    async def test_a_period_still_recomputes_the_end(self):
        client = _registered()
        await _say(
            client, "แก้ประกัน SN12345678 เป็น 24 เดือน",
            {"action": "update", "entity": "warranty",
             "fields": {"serial_number": "SN12345678", "warranty_months": 24}, "missing": []},
        )
        assert client._warranties[0]["warranty_end"].startswith("2028-01"), client._warranties

    async def test_an_end_before_the_purchase_is_refused(self):
        client = _registered()
        reply = await _say(
            client, "ประกัน SN12345678 หมดวันที่ 1/1/2568",
            {"action": "update", "entity": "warranty",
             "fields": {"serial_number": "SN12345678", "warranty_end": "2025-01-01"}, "missing": []},
        )
        assert client._warranties[0]["warranty_end"] == "2027-01-01", client._warranties
        assert "ต้องไม่ก่อนวันที่ซื้อ" in reply.text, reply.text

    def test_the_prompt_separates_the_three_dates(self):
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT

        assert "warranty_months" in INTENT_SYSTEM_PROMPT
        assert "warranty_end" in INTENT_SYSTEM_PROMPT
        # A Thai year is not a western one — "31/12/2570" became 2070.
        assert "543" in INTENT_SYSTEM_PROMPT


class TestNamingATechnicianWhoHasNoProfileYet:
    """Someone who joined with an invite code has a member row carrying
    LINE's display name and no profile at all. Reading only the profile
    made them unnameable — and the list showed them as a CHN- id."""

    def _shop(self) -> FakeDataClient:
        client = FakeDataClient(permission_keys=SHOP_KEYS, role="sales")
        client._members = [
            {"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician",
             "status": "active", "display_name": "สมศักดิ์"},
        ]
        client._profiles = {}
        client._teams = [{"id": "t-1", "team_name": "แอร์", "scope": "technician"}]
        return client

    async def test_the_line_name_is_enough_to_find_them(self):
        client = self._shop()
        found, _candidates = await chat._technician_named(client, LICENSE_ID, "สมศักดิ์")
        assert found is not None and found["name"] == "สมศักดิ์", found

    async def test_adding_them_to_a_team_works(self):
        client = self._shop()
        reply = await handle_chat_message(
            client, message="เพิ่ม สมศักดิ์ เข้าทีม แอร์",
            ctx=_ctx(oa="sales", primary_role="sales"),
            ai_client=_reads({"action": "update", "entity": "team", "fields": {}, "missing": []}),
        )
        assert [w for w in client.recorded if w[0] == "add_team_member"], reply.text
        assert "สมศักดิ์" in reply.text and "แอร์" in reply.text, reply.text

    async def test_the_profile_name_still_wins_when_there_is_one(self):
        client = self._shop()
        client._profiles = {"CHN-T-000001": {"chann_uid": "CHN-T-000001", "first_name": "สมศักดิ์", "last_name": "ใจดี"}}
        found, _ = await chat._technician_named(client, LICENSE_ID, "สมศักดิ์")
        assert found["name"] == "สมศักดิ์ ใจดี", found

    async def test_the_list_shows_a_name_not_an_id(self):
        client = self._shop()
        reply = await handle_chat_message(
            client, message="รายชื่อช่าง", ctx=_ctx(oa="sales", primary_role="sales"),
            ai_client=_reads({"action": "read", "entity": "member", "fields": {}, "missing": []}),
        )
        assert "สมศักดิ์" in reply.text and "CHN-T-000001" not in reply.text, reply.text
