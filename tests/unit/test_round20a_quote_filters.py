"""Round 20a — look before you issue, and a filtered list must filter.

Owner, 17 ก.ย. 2569:

  · "ใน card ที่แสดงตอนขอดูรายการใบเสนอราคาในแชทควรเป็นปุ่มดูรายละเอียด
     ใบเสนอราคาแทนออกเอกสาร แล้วถ้าจะออกเอกสารค่อยเป็น quick reply
     หลังจากกดดูแล้วแทน"
  · "ตอนนี้พอพิมพ์ว่า ดูใบเสนอราคาที่เปิดอยู่ มีการแสดงตัวที่ปฏิเสธแล้วมาด้วย
     เช็คการดู Query มาแสดงแต่ละอันใหม่ทั้ง ลูกค้า Deal หรือ Quote ว่ารองรับ
     การกรองก่อนนำมาโชว์ในแชทหรือยัง"

Asked before writing anything (ask-model, 17 ก.ย.), the model already
separates them by itself:

    "ดูใบเสนอราคาที่เปิดอยู่"      → read/quote {status: "open"}
    "ใบเสนอราคาที่ยังไม่ตอบรับ"   → read/quote {status: "pending"}

The reading was never missing — `_handle_quote_list` took no filter at
all and dropped it on the floor.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = ["quote.read", "deal.read", "customer.read"]
QUOTES = [
    {"id": "q1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "draft"},
    {"id": "q2", "quote_id": "Q-2026-0002", "deal_id": "d1", "status": "sent"},
    {"id": "q3", "quote_id": "Q-2026-0003", "deal_id": "d2", "status": "rejected"},
    {"id": "q4", "quote_id": "Q-2026-0004", "deal_id": "d2", "status": "accepted"},
    {"id": "q5", "quote_id": "Q-2026-0005", "deal_id": "d2", "status": "expired"},
]


def _shop() -> FakeDataClient:
    client = FakeDataClient(permission_keys=KEYS, role="sales")
    client._quotes = [dict(q) for q in QUOTES]
    return client


async def _say(client, message, ai):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role="sales", oa="sales"),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


def _reads_quotes(status=None):
    fields = {"status": status} if status else {}
    return {"action": "read", "entity": "quote", "fields": fields, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestOpenQuotesAreOnlyOpenOnes:
    async def test_the_rejected_one_is_gone(self):
        reply = await _say(_shop(), "ดูใบเสนอราคาที่เปิดอยู่", _reads_quotes("open"))
        assert "Q-2026-0003" not in reply.text, reply.text

    async def test_so_are_the_accepted_and_the_expired(self):
        reply = await _say(_shop(), "ดูใบเสนอราคาที่เปิดอยู่", _reads_quotes("open"))
        assert "Q-2026-0004" not in reply.text, reply.text
        assert "Q-2026-0005" not in reply.text, reply.text

    async def test_the_live_ones_are_there(self):
        reply = await _say(_shop(), "ดูใบเสนอราคาที่เปิดอยู่", _reads_quotes("open"))
        assert "Q-2026-0001" in reply.text and "Q-2026-0002" in reply.text, reply.text

    async def test_the_answer_says_what_it_left_out(self):
        """A filtered list that does not say it is filtered is a list that
        quietly lies about how many quotes there are."""
        reply = await _say(_shop(), "ดูใบเสนอราคาที่เปิดอยู่", _reads_quotes("open"))
        assert "ไม่รวมที่ตอบรับ/ปฏิเสธ/หมดอายุ" in reply.text, reply.text

    async def test_asking_for_all_of_them_still_shows_all(self):
        reply = await _say(_shop(), "รายการใบเสนอราคา", _reads_quotes())
        for code in ("Q-2026-0001", "Q-2026-0003", "Q-2026-0004"):
            assert code in reply.text, (code, reply.text)

    async def test_one_named_status_lists_only_that_kind(self):
        reply = await _say(_shop(), "ใบเสนอราคาที่ปฏิเสธ", _reads_quotes("rejected"))
        assert "Q-2026-0003" in reply.text, reply.text
        assert "Q-2026-0001" not in reply.text, reply.text

    async def test_none_open_says_so_rather_than_listing_everything(self):
        client = _shop()
        client._quotes = [{"id": "q3", "quote_id": "Q-2026-0003", "deal_id": "d2",
                           "status": "rejected"}]
        reply = await _say(client, "ดูใบเสนอราคาที่เปิดอยู่", _reads_quotes("open"))
        assert "Q-2026-0003" not in reply.text, reply.text
        assert "ไม่มีใบเสนอราคาที่ยังเปิดอยู่" in reply.text, reply.text


class TestTheListRowLooksBeforeItIssues:
    async def test_the_row_opens_the_quote(self):
        reply = await _say(_shop(), "รายการใบเสนอราคา", _reads_quotes())
        rows = (reply.list_card or {}).get("rows") or []
        assert rows, reply.list_card
        assert rows[0]["action_label"] == "ดูรายละเอียด", rows[0]
        assert rows[0]["action_text"].startswith("ดูใบเสนอราคา "), rows[0]

    async def test_no_row_issues_a_document_straight_from_the_list(self):
        reply = await _say(_shop(), "รายการใบเสนอราคา", _reads_quotes())
        rows = (reply.list_card or {}).get("rows") or []
        assert not [r for r in rows if "ออกเอกสาร" in str(r.get("action_text"))], rows

    async def test_issuing_is_offered_once_the_quote_is_open(self):
        """The other half of the owner's sentence: it must still be one tap
        away, on the card where the lines and the discount are visible."""
        client = _shop()
        reply = await _say(
            client, "ดูใบเสนอราคา Q-2026-0002",
            {"action": "read", "entity": "quote",
             "fields": {"code": "Q-2026-0002"}, "missing": []},
        )
        assert [b for b in reply.quick_replies if b[0] == "ออกเอกสาร"], reply.quick_replies


class TestTheOtherListsWereAuditedToo:
    """The owner asked for all three. Deals and customers already filtered;
    these pin that they still do, so the audit is a test and not a claim."""

    async def test_open_deals_leave_out_the_closed_ones(self):
        client = FakeDataClient(permission_keys=KEYS, role="sales")
        client._deals = [
            {"id": "d1", "deal_id": "D-2026-0001", "stage": "new", "contact_id": "c1"},
            {"id": "d2", "deal_id": "D-2026-0002", "stage": "won", "contact_id": "c1"},
            {"id": "d3", "deal_id": "D-2026-0003", "stage": "lost", "contact_id": "c1"},
        ]
        # The shape the deployed model really returns (ask-model, 17 ก.ย.).
        reply = await _say(client, "ดีลที่เปิดอยู่",
                           {"action": "read", "entity": "deal",
                            "fields": {"status": "open"}, "missing": []})
        assert "D-2026-0001" in reply.text, reply.text
        assert "D-2026-0002" not in reply.text and "D-2026-0003" not in reply.text, reply.text

    async def test_a_customer_search_narrows_the_list(self):
        client = FakeDataClient(permission_keys=KEYS, role="sales")
        client._customers = [
            {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
             "last_name": "ใจดี", "phone": "0812345678", "stage": "contact"},
            {"id": "c2", "customer_id": "C-2026-0002", "first_name": "สมหญิง",
             "last_name": "ดีใจ", "phone": "0899999999", "stage": "lead"},
        ]
        reply = await _say(client, "ค้นหาลูกค้า สมชาย",
                           {"action": "read", "entity": "customer",
                            "fields": {"name": "สมชาย"}, "missing": []})
        assert "สมชาย" in reply.text, reply.text
        assert "สมหญิง" not in reply.text, reply.text

    async def test_a_customer_stage_narrows_the_list(self):
        """The one gap the audit found besides quotes: the model returns
        {"status": "lead"} for "ลูกค้าที่เป็น lead" (ask-model, 17 ก.ย.) and
        the list showed everybody."""
        client = FakeDataClient(permission_keys=KEYS, role="sales")
        client._customers = [
            {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
             "last_name": "ใจดี", "phone": "0812345678", "stage": "contact"},
            {"id": "c2", "customer_id": "C-2026-0002", "first_name": "สมหญิง",
             "last_name": "ดีใจ", "phone": "0899999999", "stage": "lead"},
        ]
        reply = await _say(client, "ลูกค้าที่เป็น lead",
                           {"action": "read", "entity": "customer",
                            "fields": {"status": "lead"}, "missing": []})
        assert "C-2026-0002" in reply.text, reply.text
        assert "C-2026-0001" not in reply.text, reply.text
