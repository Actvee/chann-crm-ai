"""4 Sep owner list, part 1 (fixes-3oa-v1): product names in the chat
list, silence as the confirmation inside a live conversation, the diary
shows only what is still ahead, a conversation continues where it left
off, and a one-hour quiet close.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import live_chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.thai_datetime import local_today  # noqa: E402
from test_live_chat import ChatFake  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


class TestProductList:
    async def test_the_list_prints_the_product_name(self):
        client = FakeDataClient(permission_keys=["product.manage"])
        client._products = [{"product_id": "P-1", "product_name": "พัดลมไอเย็น", "sku": "F-1", "unit_price": "3500"}]
        reply = await handle_chat_message(client, message="รายการสินค้า", ctx=_ctx())
        assert "พัดลมไอเย็น" in reply.text
        assert " · - " not in reply.text and not reply.text.rstrip().endswith("-")


class TestLiveChatQuiet:
    async def test_a_line_into_a_conversation_gets_no_echo(self, monkeypatch):
        async def fake_push(oa, to, text, client=None):
            return ["mid"]

        monkeypatch.setattr(live_chat, "push_text", fake_push)
        client = ChatFake(role="customer", permission_keys=[])
        await handle_chat_message(client, message="คุยกับร้าน", ctx=_ctx(primary_role="customer", oa="customer"))
        reply = await handle_chat_message(client, message="ราคาเท่าไหร่", ctx=_ctx(primary_role="customer", oa="customer"))
        assert reply.text == ""
        assert [r for r in client.recorded if r[0] == "add_chat_message" and r[3] == "ราคาเท่าไหร่"]

    async def test_a_failed_line_still_speaks(self, monkeypatch):
        client = ChatFake(role="customer", permission_keys=[])
        await handle_chat_message(client, message="คุยกับร้าน", ctx=_ctx(primary_role="customer", oa="customer"))

        async def broken(*a, **k):
            raise RuntimeError("data tier down")

        monkeypatch.setattr(client, "add_chat_message", broken)
        reply = await handle_chat_message(client, message="ราคาเท่าไหร่", ctx=_ctx(primary_role="customer", oa="customer"))
        assert reply.text

    async def test_the_quiet_close_defaults_to_one_hour(self):
        client = ChatFake()
        assert await live_chat.chat_settings(client, "L1") == (30, 60)


class TestDiaryShowsWhatIsAhead:
    async def test_past_rows_are_kept_but_not_listed(self):
        client = FakeDataClient(permission_keys=["followup.read", "customer.read"])
        customer = await client.create_customer("L1", {
            "first_name": "จิตวิทยา", "last_name": "ลายดอก", "phone": "0879876646",
        })
        today = local_today()
        client._follow_ups = [
            {"id": "f-past", "entity_type": "customer", "entity_id": customer["id"],
             "due_date": (today - timedelta(days=3)).isoformat(), "due_time": None, "notes": "เก่า",
             "status": "pending", "owner_chann_uid": "CHN-S-000001"},
            {"id": "f-next", "entity_type": "customer", "entity_id": customer["id"],
             "due_date": (today + timedelta(days=2)).isoformat(), "due_time": "14:00:00", "notes": "โทรตาม",
             "status": "pending", "owner_chann_uid": "CHN-S-000001"},
        ]
        reply = await handle_chat_message(client, message="ดูนัดหมาย", ctx=_ctx())
        assert "โทรตาม" in reply.text and customer["customer_id"] in reply.text
        assert "เก่า" not in reply.text
        assert "เลยกำหนด" not in reply.text
        assert "1 รายการ" in reply.text

    async def test_nothing_ahead_says_so(self):
        client = FakeDataClient(permission_keys=["followup.read", "customer.read"])
        today = local_today()
        client._follow_ups = [
            {"id": "f-past", "entity_type": "customer", "entity_id": "c-x",
             "due_date": (today - timedelta(days=1)).isoformat(), "due_time": None, "notes": "เก่า",
             "status": "pending", "owner_chann_uid": "CHN-S-000001"},
        ]
        reply = await handle_chat_message(client, message="ดูนัดหมาย", ctx=_ctx())
        assert "ยังไม่มีนัดหมาย" in reply.text
