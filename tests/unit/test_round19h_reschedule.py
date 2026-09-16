"""Round 19h (second batch) — the shop confirms the time the customer asked for.

Owner, 16 ก.ย. 2569: the customer moved the visit, the shop typed "ยืนยันเป็น
วันที่ตามนั้นได้" and got "ยังไม่แน่ใจว่าต้องการอะไรครับ" — the model reads
these confirmations as `suggest` (measured), so the rule road answers them, and
only when that job really has an outstanding request.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import _approves_the_customers_time, handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "ticket.assign", "ticket.close"]
SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}
TICKET = {
    "id": "t7", "ticket_number": "T-2026-0007", "status": "assigned", "accept_status": "accepted",
    "assigned_to_ref": "m-tech-1", "assigned_target_type": "technician", "assigned_to_name": "สมศักดิ์",
    "customer_chann_uid": "CHN-S-000009", "customer_name": "สมชาย ใจดี", "customer_phone": "0812345678",
    "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
    "scheduled_date": "2099-01-18", "scheduled_time": "10:00", "visibility": "public",
}
REQUEST = {"id": "n1", "entity_type": "service_ticket", "entity_id": "t7",
           "body": "⚠️ คำขอจากลูกค้า: ลูกค้าขอเลื่อนนัดเป็น 20 ม.ค. 2642 13:00 (ยังไม่ได้เลื่อน รอร้านยืนยัน)"}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _shop(notes=(REQUEST,), tickets=(TICKET,)) -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS])
    client._tickets = [dict(t) for t in tickets]
    client._notes = [dict(n) for n in notes]
    client._line_targets = {"CHN-S-000009": "line-customer", "CHN-S-000001": "line-owner", "CHN-T-000001": "line-tech"}
    client._members = [
        {"id": "MEMBER-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales"},
        {"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "display_name": "สมศักดิ์", "channel": "technician"},
    ]
    return client


class TestTheWords:
    async def test_the_sentences_people_actually_type(self):
        for said in ("ยืนยันเป็นวันที่ตามนั้นได้", "T-2026-0007 เลื่อนได้", "โอเคเลื่อนตามที่ลูกค้าขอได้", "ตกลงตามนั้น"):
            assert _approves_the_customers_time(said), said

    async def test_a_sentence_carrying_its_own_time_is_not_an_approval(self):
        assert not _approves_the_customers_time("เลื่อนนัด T-2026-0007 เป็น 21 ม.ค. 13:00")

    async def test_a_report_about_the_customer_is_not_an_approval(self):
        assert not _approves_the_customers_time("ลูกค้าโทรมาบอกว่าไม่สะดวกวันนั้น")


class TestConfirmingTheCustomersTime:
    async def test_the_visit_moves_to_the_time_they_asked_for(self):
        client = _shop()
        reply, calls = await _say(client, "ยืนยันเป็นวันที่ตามนั้นได้", SUGGEST)
        moved = next((c for c in calls if c[0] == "update_ticket"), None)
        assert moved is not None, (reply.text, calls)
        assert moved[3]["scheduled_date"] == "2099-01-20" and str(moved[3]["scheduled_time"]).startswith("13:00")
        assert "T-2026-0007" in reply.text and "13:00" in reply.text, reply.text
        assert [c for c in calls if c[0] == "create_notification"], "nobody was told"

    async def test_the_customer_is_told(self, monkeypatch):
        """The running commentary of a visit is a LINE push, not a row
        (chat._notify_customer)."""
        import chann_app.services.chat as chat_service
        pushed: list[tuple] = []

        async def fake_push(oa, to, text, client=None, quick_reply=None):
            pushed.append((oa, to, text))
            return ["mid"]
        monkeypatch.setattr(chat_service._notify_mod, "push_text", fake_push)
        client = _shop()
        reply, _ = await _say(client, "ยืนยันเป็นวันที่ตามนั้นได้", SUGGEST)
        to_customer = [p for p in pushed if p[0] == "customer"]
        assert to_customer and "T-2026-0007" in to_customer[0][2] and "13:00" in to_customer[0][2], (reply.text, pushed)

    async def test_the_request_is_marked_handled(self):
        client = _shop()
        await _say(client, "ยืนยันเป็นวันที่ตามนั้นได้", SUGGEST)
        done = [n for n in client._notes if str(n.get("body", "")).startswith("✅")]
        assert done, client._notes

    async def test_with_nothing_outstanding_the_word_keeps_its_own_meaning(self):
        client = _shop(notes=())
        reply, calls = await _say(client, "ยืนยัน", SUGGEST)
        assert not [c for c in calls if c[0] == "update_ticket"], reply.text

    async def test_several_outstanding_requests_ask_which_job(self):
        second = dict(TICKET, id="t8", ticket_number="T-2026-0008")
        client = _shop(
            notes=(REQUEST, dict(REQUEST, id="n2", entity_id="t8")),
            tickets=(TICKET, second),
        )
        reply, calls = await _say(client, "ยืนยันเป็นวันที่ตามนั้นได้", SUGGEST)
        assert not [c for c in calls if c[0] == "update_ticket"], reply.text
        assert "หลายงาน" in reply.text and reply.quick_replies, reply.text

    async def test_the_code_in_the_sentence_picks_the_job(self):
        second = dict(TICKET, id="t8", ticket_number="T-2026-0008")
        client = _shop(
            notes=(REQUEST, dict(REQUEST, id="n2", entity_id="t8")),
            tickets=(TICKET, second),
        )
        reply, calls = await _say(client, "T-2026-0008 ยืนยันตามนั้น", SUGGEST)
        moved = next((c for c in calls if c[0] == "update_ticket"), None)
        assert moved is not None and moved[2] == "t8", (reply.text, calls)
