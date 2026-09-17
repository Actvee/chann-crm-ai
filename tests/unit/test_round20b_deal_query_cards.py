"""Round 20b — every list answer looks like a list answer.

Owner, 17 ก.ย. 2569: "พิมพ์ รายการดีลที่ปิดแล้ว แต่ระบบตอบกลับมาเป็นตัวอักษร
ไม่เป็น card เหมือนแบบอื่น".

"รายการดีล" is answered by `_handle_deal_list`, which sends a card. Nine
other deal questions — closed, lost, closing this month or week, overdue,
undated, biggest, over a value — are answered by `_handle_deal_query`,
which sent bare text. Same records, same kind of question, and the answer
looked like a lesser feature depending on the words used.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = ["deal.read", "customer.read", "view_reports"]
DEALS = [
    {"id": "d1", "deal_id": "D-2026-0001", "stage": "new", "contact_id": "c1",
     "amount": "5000", "customer_name": "สมชาย"},
    {"id": "d2", "deal_id": "D-2026-0002", "stage": "won", "contact_id": "c1",
     "amount": "30000", "customer_name": "สมชาย"},
    {"id": "d3", "deal_id": "D-2026-0003", "stage": "lost", "contact_id": "c1",
     "amount": "9000", "customer_name": "สมหญิง", "lost_reason": "ราคาสูงไป"},
]


def _shop() -> FakeDataClient:
    client = FakeDataClient(permission_keys=KEYS, role="sales")
    client._deals = [dict(d) for d in DEALS]
    return client


async def _say(client, message, ai):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role="sales", oa="sales"),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


def _reads_deals(status):
    return {"action": "read", "entity": "deal", "fields": {"status": status}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestClosedDealsLookLikeEveryOtherList:
    async def test_the_owners_sentence_comes_back_as_a_card(self):
        """The shape the deployed model really returns for it (ask-model,
        17 ก.ย.): read/deal {"status": "won"}."""
        reply = await _say(_shop(), "รายการดีลที่ปิดแล้ว", _reads_deals("won"))
        assert reply.list_card, reply.text
        assert reply.list_card["rows"], reply.list_card

    async def test_the_card_holds_the_deals_it_listed(self):
        reply = await _say(_shop(), "รายการดีลที่ปิดแล้ว", _reads_deals("won"))
        titles = [r["title"] for r in reply.list_card["rows"]]
        assert titles == ["D-2026-0002"], titles

    async def test_each_row_opens_that_deal(self):
        reply = await _say(_shop(), "รายการดีลที่ปิดแล้ว", _reads_deals("won"))
        row = reply.list_card["rows"][0]
        assert row["action_label"] == "ดู", row
        assert row["action_text"] == "ข้อมูลดีล D-2026-0002", row

    async def test_the_text_still_says_the_same_thing(self):
        """The card is an addition. A LINE preview shows no card at all, so
        the sentence has to keep standing on its own."""
        reply = await _say(_shop(), "รายการดีลที่ปิดแล้ว", _reads_deals("won"))
        assert "ดีลที่ปิดสำเร็จ 1 ดีล" in reply.text, reply.text
        assert "D-2026-0002" in reply.text, reply.text

    async def test_the_lost_ones_too(self):
        reply = await _say(_shop(), "ดีลที่ไม่สำเร็จ", _reads_deals("lost"))
        assert reply.list_card, reply.text
        assert [r["title"] for r in reply.list_card["rows"]] == ["D-2026-0003"]

    async def test_an_empty_answer_is_still_plain(self):
        """Nothing to put in a card is not a card with nothing in it."""
        client = _shop()
        client._deals = [dict(DEALS[0])]
        reply = await _say(client, "รายการดีลที่ปิดแล้ว", _reads_deals("won"))
        assert reply.list_card is None, reply.list_card
        assert "ไม่มี" in reply.text, reply.text

    async def test_the_plain_list_kept_its_card(self):
        reply = await _say(
            _shop(), "รายการดีล",
            {"action": "read", "entity": "deal", "fields": {}, "missing": []},
        )
        assert reply.list_card, reply.text
        assert len(reply.list_card["rows"]) == 3, reply.list_card
