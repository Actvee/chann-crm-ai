"""Round 19h — the shop's queue shows the jobs only the shop can move.

Owner, 16 ก.ย. 2569: "พิม 'งานซ่อม' ใน Sale OA แต่บอกว่าไม่มีงานเปิดอยู่ ทั้งๆ
ที่มีลูกค้าแจ้งเข้ามาแล้ว". Round 19f made a customer's own report private so
technicians cannot take it before CS dispatches it — and every ticket lookup
in chat passed the TECHNICIAN's visibility filter, on every channel, so the
shop's own queue went blank. The dashboard, which never filtered, showed the
job all along.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "ticket.assign", "ticket.close"]
TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create"]
TECH = dict(oa="technician", primary_role="technician")
# What the customer's report looks like after round 19f: nobody assigned,
# private, waiting for the shop.
HELD = {
    "id": "t5", "ticket_number": "T-2026-0005", "status": "open", "accept_status": "pending",
    "visibility": "private", "assigned_to_ref": None, "assigned_target_type": None,
    "customer_chann_uid": "CHN-S-000009", "customer_name": "สมชาย ใจดี", "customer_phone": "0812345678",
    "service_address": "99/1 ถ.สุขุมวิท", "issue_description": "พัดลมไม่หมุน",
    "scheduled_date": "2099-01-20", "scheduled_time": "10:00",
}
# The model's own reading of "งานซ่อม" on the sales OA (ask-model, 16 ก.ย. 2569).
JOBS_REPORT = {"action": "read", "entity": "report", "fields": {"type": "jobs"}, "missing": []}
SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _with(tickets, keys=None, **ctx_kw) -> FakeDataClient:
    client = FakeDataClient(permission_keys=keys or SHOP_KEYS, customers=[dict(c) for c in CUSTOMERS])
    client._tickets = [dict(t) for t in tickets]
    return client


class TestTheShopSeesWhatTheCustomerReported:
    async def test_the_jobs_list_shows_the_held_report(self):
        client = _with([HELD])
        reply, _ = await _say(client, "งานซ่อม", JOBS_REPORT)
        assert "T-2026-0005" in reply.text, reply.text
        assert "ไม่มีงานเปิดรับ" not in reply.text and "ไม่มีงานที่รอมอบหมาย" not in reply.text, reply.text
        assert "ช่างยังไม่เห็น" in reply.text, reply.text

    async def test_the_card_offers_to_open_it_to_the_technicians(self):
        client = _with([HELD])
        reply, _ = await _say(client, "งานซ่อม", JOBS_REPORT)
        rows = (reply.list_card or {}).get("rows") or []
        assert rows and rows[0]["action_text"] == "เปิดให้ช่างรับ T-2026-0005", rows

    async def test_a_job_already_open_to_the_technicians_is_not_marked(self):
        """Opened to the pool: in the queue until somebody takes it, but the
        shop has already done its part, so no "รอมอบหมาย" beside it."""
        client = _with([dict(HELD, visibility="public")])
        reply, _ = await _say(client, "งานซ่อม", JOBS_REPORT)
        assert "T-2026-0005" in reply.text and "ช่างยังไม่เห็น" not in reply.text, reply.text

    async def test_a_job_a_technician_accepted_has_left_the_queue(self):
        client = _with([dict(HELD, visibility="public", assigned_to_ref="m-tech-1", accept_status="accepted", status="assigned")])
        reply, _ = await _say(client, "งานซ่อม", JOBS_REPORT)
        assert "T-2026-0005" not in reply.text and "ไม่มีงานที่รอมอบหมาย" in reply.text, reply.text

    async def test_the_queue_says_so_when_nothing_waits(self):
        client = _with([])
        reply, _ = await _say(client, "งานซ่อม", JOBS_REPORT)
        assert "ไม่มีงานที่รอมอบหมาย" in reply.text and "รายการงาน" in reply.text, reply.text

    async def test_the_detail_card_opens_a_held_job(self):
        client = _with([HELD])
        reply, _ = await _say(client, "ข้อมูลงาน T-2026-0005", {"action": "read", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": []})
        assert "T-2026-0005" in reply.text and "สมชาย ใจดี" in reply.text, reply.text
        assert "ไม่พบ" not in reply.text, reply.text

    async def test_cs_can_take_the_case_itself(self):
        """The owner's rule of 16 ก.ย.: CS takes the case first and only then
        involves a technician."""
        client = _with([HELD])
        reply, calls = await _say(client, "รับงาน T-2026-0005", {"action": "claim", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": []})
        assert [c for c in calls if c[0] == "claim_ticket"], reply.text


class TestTheTechnicianStillCannotSeeIt:
    async def test_the_open_list_hides_a_held_job(self):
        client = _with([HELD], keys=TECH_KEYS)
        reply, _ = await _say(client, "งานที่เปิดรับ", SUGGEST, ctx=_ctx(**TECH))
        assert "T-2026-0005" not in reply.text and "ไม่มีงานเปิดรับ" in reply.text, reply.text

    async def test_once_the_shop_opens_it_the_technician_sees_it(self):
        client = _with([dict(HELD, visibility="public")], keys=TECH_KEYS)
        reply, _ = await _say(client, "งานที่เปิดรับ", SUGGEST, ctx=_ctx(**TECH))
        assert "T-2026-0005" in reply.text, reply.text

    async def test_a_colleagues_private_job_stays_hidden(self):
        client = _with([dict(HELD, visibility="private", assigned_to_ref="someone-else", accept_status="accepted", status="assigned")], keys=TECH_KEYS)
        reply, _ = await _say(client, "งานที่เปิดรับ", SUGGEST, ctx=_ctx(**TECH))
        assert "T-2026-0005" not in reply.text, reply.text


class TestTheFakeMirrorsTheRealFilter:
    """The fake ignored visible_to entirely, so every test passed while DEV
    answered "ไม่มีงานเปิดรับ". A fake more generous than the real tier hides
    exactly this bug (CLAUDE.md, paid for twice)."""

    async def test_visible_to_filters_private_jobs(self):
        client = _with([HELD, dict(HELD, id="t6", ticket_number="T-2026-0006", visibility="public")])
        everything = await client.list_tickets("L1")
        filtered = await client.list_tickets("L1", visible_to="member-1")
        assert {t["ticket_number"] for t in everything} == {"T-2026-0005", "T-2026-0006"}
        assert {t["ticket_number"] for t in filtered} == {"T-2026-0006"}

    async def test_a_job_assigned_to_me_is_visible(self):
        client = _with([dict(HELD, assigned_to_ref="member-1")])
        filtered = await client.list_tickets("L1", visible_to="member-1")
        assert [t["ticket_number"] for t in filtered] == ["T-2026-0005"]
