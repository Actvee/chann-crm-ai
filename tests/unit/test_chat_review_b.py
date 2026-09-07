"""Chat fixes from the owner's live testing, 7 Sep 2026 (round B extras).

1. Interrupting a create flow asks before switching: a different command
   while a lead / deal / appointment waits for an answer is confirmed,
   naming both; a list or a tile is still answered in place with the flow
   kept; the flow's own answer still lands.
2. Many leads at once: pasted lines without a trigger word, one line with
   commas / "และ", rows without a phone asked for one at a time, and a
   summary that names what was created, skipped and asked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402

SALES_KEYS = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
ME = "CHN-S-000001"


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "openrouter_model", "m")


def _suggest():
    return httpx.AsyncClient(transport=_ai(json.dumps(
        {"action": "suggest", "entity": None, "fields": {}, "missing": []}, ensure_ascii=False,
    )))


def _crafted(body: dict):
    return httpx.AsyncClient(transport=_ai(json.dumps(body, ensure_ascii=False)))


async def say(client, message, *, ai=None, language="th"):
    return await handle_chat_message(client, message=message, ctx=_ctx(oa="sales"), language=language, ai_client=ai or _suggest())


def _sales():
    c = FakeDataClient(permission_keys=list(SALES_KEYS), role="sales")
    c._members = [{"id": "member-1", "chann_uid": ME, "role": "member", "status": "active"}]
    return c


def _writes(client, name):
    return [r for r in client.recorded if r[0] == name]


async def _lead_awaiting_phone(client):
    await client.set_pending_intent(
        ME, "sales", action="create", entity="customer", fields={"first_name": "สมชาย"}, missing=["phone"],
    )


# -------------------------------------------------------------- 1. interrupting a create flow

class TestInterruptingACreateFlowAsksFirst:
    async def test_lead_then_deal_is_confirmed_not_created(self):
        client = _sales()
        await client.create_customer("L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        await _lead_awaiting_phone(client)
        reply = await say(client, "สร้างดีลให้สมชาย")
        assert not _writes(client, "create_deal")
        assert "กำลังเพิ่มลูกค้า สมชาย" in reply.text and "เบอร์โทร" in reply.text and "สร้างดีล" in reply.text
        sends = [send for _label, send in reply.quick_replies]
        assert sends == ["สร้างดีลให้สมชาย", chat.FLOW_SWITCH_KEEP_TEXT]
        assert reply.quick_replies[0][0] == "สร้างดีลเลย" and reply.quick_replies[1][0] == "เพิ่มลูกค้าต่อ"
        assert client._pending["entity"] == "flow_switch"

    async def test_tapping_the_new_command_runs_exactly_what_was_typed(self):
        client = _sales()
        await client.create_customer("L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        await _lead_awaiting_phone(client)
        first = await say(client, "สร้างดีลให้สมชาย")
        reply = await say(client, first.quick_replies[0][1])
        assert _writes(client, "create_deal") and "D-2026-" in reply.text
        assert client._pending is None or client._pending.get("entity") != "flow_switch"

    async def test_keeping_the_flow_restores_the_original_question(self):
        client = _sales()
        await _lead_awaiting_phone(client)
        await say(client, "สร้างดีลให้สมชาย")
        reply = await say(client, chat.FLOW_SWITCH_KEEP_TEXT)
        assert "เบอร์โทร" in reply.text and not _writes(client, "create_deal")
        assert client._pending["entity"] == "customer" and client._pending["missing"] == ["phone"]

    async def test_lead_then_appointment_is_confirmed(self):
        client = _sales()
        await _lead_awaiting_phone(client)
        reply = await say(client, "เตือน C-2026-0001 พรุ่งนี้ 10 โมง")
        assert not _writes(client, "create_follow_up")
        assert "กำลังเพิ่มลูกค้า" in reply.text and "ตั้งนัด" in reply.text

    async def test_deal_then_lead_is_confirmed(self):
        client = _sales()
        await client.set_pending_intent(
            ME, "sales", action="create", entity="deal", fields={"target_name": "สมชาย"}, missing=["amount"],
        )
        reply = await say(client, "เพิ่มลูกค้า สมหญิง 0898765432")
        assert not _writes(client, "create_customer")
        assert "กำลังสร้างดีล สมชาย" in reply.text and "มูลค่าดีล" in reply.text and "เพิ่มลูกค้า" in reply.text

    async def test_answering_the_pending_question_still_works(self):
        client = _sales()
        await _lead_awaiting_phone(client)
        ai = _crafted({"action": "create", "entity": "customer", "fields": {"phone": "0812345678"}, "missing": []})
        reply = await say(client, "0812345678", ai=ai)
        assert "จะยกเลิกแล้ว" not in reply.text
        assert _writes(client, "create_customer") or "นามสกุล" in reply.text

    async def test_a_list_is_answered_in_place_and_the_flow_kept(self):
        client = _sales()
        await _lead_awaiting_phone(client)
        reply = await say(client, "รายชื่อลูกค้า")
        assert "จะยกเลิกแล้ว" not in reply.text
        assert client._pending["entity"] == "customer" and client._pending["missing"] == ["phone"]

    async def test_cancel_still_drops_the_flow(self):
        client = _sales()
        await _lead_awaiting_phone(client)
        reply = await say(client, "ยกเลิก")
        assert "ยกเลิกแล้ว" in reply.text and client._pending is None

    async def test_english_reader_gets_english(self):
        client = _sales()
        await _lead_awaiting_phone(client)
        reply = await say(client, "สร้างดีลให้สมชาย", language="en")
        assert "adding a customer" in reply.text and "phone number" in reply.text and "create the deal" in reply.text
        assert reply.quick_replies[1][0] == "Keep the customer" and reply.quick_replies[0][0] == "Do the new one"


# -------------------------------------------------------------- 2. many leads at once

class _DuplicateAware(FakeDataClient):
    """The real Data tier refuses a second customer with the same phone."""

    async def create_customer(self, license_id, payload, actor_id=None):
        for c in self._customers:
            if c.get("phone") and c.get("phone") == payload.get("phone"):
                raise DataTierError(409, "duplicate", {"error": "duplicate", "existing_code": c["customer_id"]})
        return await super().create_customer(license_id, payload, actor_id)


def _dup_sales():
    c = _DuplicateAware(permission_keys=list(SALES_KEYS), role="sales")
    c._members = [{"id": "member-1", "chann_uid": ME, "role": "member", "status": "active"}]
    return c


class TestManyLeadsAtOnce:
    async def test_lines_without_a_trigger_word_are_a_list(self):
        client = _sales()
        reply = await say(client, "สมชาย ใจดี 0812345678\nสมหญิง ดีใจ 0898765432")
        created = _writes(client, "create_customer")
        assert [r[2]["first_name"] for r in created] == ["สมชาย", "สมหญิง"]
        assert "เพิ่มลูกค้าแล้ว 2 ราย" in reply.text and "C-2026-0001" in reply.text and "C-2026-0002" in reply.text

    async def test_one_line_separated_by_commas_or_lae(self):
        client = _sales()
        await say(client, "เพิ่มลูกค้า สมชาย 0812345678, สมหญิง 0898765432")
        assert [r[2]["first_name"] for r in _writes(client, "create_customer")] == ["สมชาย", "สมหญิง"]
        client = _sales()
        await say(client, "เพิ่มลูกค้า สมชาย 0812345678 และ สมหญิง 0898765432")
        assert [r[2]["first_name"] for r in _writes(client, "create_customer")] == ["สมชาย", "สมหญิง"]

    async def test_a_row_without_a_phone_is_asked_for_after_the_complete_ones(self):
        client = _sales()
        reply = await say(client, "เพิ่มลูกค้า สมชาย 0812345678\nสมหญิง ดีใจ\nสมศักดิ์ 0866666666")
        assert [r[2]["first_name"] for r in _writes(client, "create_customer")] == ["สมชาย", "สมศักดิ์"]
        assert "บรรทัด 2 (สมหญิง ดีใจ) ยังไม่มีเบอร์" in reply.text and "ข้าม" in reply.text
        assert client._pending["entity"] == "bulk_customer_phone"
        reply = await say(client, "0898765432")
        assert [r[2]["first_name"] for r in _writes(client, "create_customer")] == ["สมชาย", "สมศักดิ์", "สมหญิง"]
        assert "เพิ่มลูกค้าแล้ว 3 ราย" in reply.text and client._pending is None

    async def test_skip_leaves_the_row_out_and_says_so(self):
        client = _sales()
        await say(client, "เพิ่มลูกค้า สมชาย 0812345678\nสมหญิง ดีใจ")
        reply = await say(client, "ข้าม")
        assert len(_writes(client, "create_customer")) == 1
        assert "ข้ามตามที่บอก 1 ราย: สมหญิง ดีใจ" in reply.text and client._pending is None

    async def test_something_else_while_a_phone_is_asked_is_not_a_phone(self):
        client = _sales()
        await say(client, "เพิ่มลูกค้า สมชาย 0812345678\nสมหญิง ดีใจ")
        reply = await say(client, "รายชื่อลูกค้า")
        assert "สมชาย" in reply.text and client._pending is None

    async def test_summary_names_created_codes_skipped_duplicates_and_what_was_asked(self):
        client = _dup_sales()
        await client.create_customer("L1", {"first_name": "สมชาย", "last_name": "เดิม", "phone": "0812345678"})
        reply = await say(client, "เพิ่มลูกค้า สมชาย ใจดี 0812345678\nสมหญิง ดีใจ 0898765432\nสมศักดิ์ มีสุข")
        assert "เพิ่มลูกค้าแล้ว 1 ราย" in reply.text
        assert "ข้ามเพราะมีอยู่แล้ว 1 ราย: สมชาย ใจดี → C-2026-0001" in reply.text
        assert "สมหญิง ดีใจ (C-2026-0002)" in reply.text
        assert "ยังไม่มีเบอร์ 1 ราย" in reply.text and "บรรทัด 3 (สมศักดิ์ มีสุข)" in reply.text

    async def test_a_single_customer_still_goes_through_the_model(self):
        client = _sales()
        ai = _crafted({"action": "create", "entity": "customer",
                       "fields": {"first_name": "สมหญิง", "last_name": "ดีใจ", "phone": "0898765432"}, "missing": []})
        reply = await say(client, "เพิ่มลูกค้า สมหญิง ดีใจ 0898765432", ai=ai)
        assert "เพิ่มลูกค้าแล้ว" not in reply.text
        assert [r[2]["first_name"] for r in _writes(client, "create_customer")] == ["สมหญิง"]

    async def test_two_unrelated_lines_are_not_a_list(self):
        client = _sales()
        reply = await say(client, "บันทึกว่า C-2026-0001 โทรแล้ว 0812345678\nเตือนพรุ่งนี้ 0898765432")
        assert not _writes(client, "create_customer")
