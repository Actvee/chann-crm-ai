"""Round 19y — say back what was saved, and show what a customer has.

Owner, 16 ก.ย. 2569:

    "ตอนนี้พอบันทึกนัดหมายหรือบันทึกโน๊ต ต้องการให้ระบบตอบกลับมาใส่เนื้อหา
     ด้วยว่าบันทึกหรือตั้งนัดว่าอะไร เพื่อให้ผู้ใช้เห็นและ recheck ได้
     กับเวลาขอดูข้อมูลลูกค้าคนไหนแล้วควรเห็นข้อมูลเช่น มีบันทึกว่าอะไร ,
     นัดหมายที่จะถึง , มีดีลอะไรบ้างที่เปิดอยู่ , ใบเสนอราคา …
     และไม่แน่ใจว่ามีบอก Email รึยัง"

Email was already on the card whenever the record carried one; the other
four were not, although every one of them was a single read away. The
reminder already echoed its date for exactly the reason the owner gives —
a reading of free text is only checkable if it is shown — and the note
and the appointment's subject did not.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = [
    "customer.read", "customer.manage", "deal.read", "deal.manage", "quote.read",
    "note.create", "note.read", "followup.create", "followup.read",
]
CUSTOMER = {
    "id": "c-1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี",
    "phone": "0812345678", "email": "somchai@example.com", "stage": "contact",
    "address": "99/1 ถนนสุขุมวิท",
}


def _shop(**extra) -> FakeDataClient:
    client = FakeDataClient(permission_keys=KEYS, role="sales")
    client._customers = [dict(CUSTOMER)]
    for key, value in extra.items():
        setattr(client, key, value)
    return client


async def _say(client, message, ai):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role="sales", oa="sales"),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


def _view_customer():
    return {"action": "read", "entity": "customer",
            "fields": {"customer_id": "C-2026-0001"}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestSavingSaysWhatItSaved:
    async def test_a_note_comes_back_with_its_words(self):
        client = _shop()
        reply = await _say(
            client, "บันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด 10% ก่อนตัดสินใจ",
            # The shape the deployed model really returns (ask-model, 16 ก.ย.).
            {"action": "create", "entity": "note",
             "fields": {"body": "ลูกค้าขอส่วนลด 10% ก่อนตัดสินใจ",
                        "entity_code": "C-2026-0001"}, "missing": []},
        )
        assert [w for w in client.recorded if w[0] == "create_note"], client.recorded
        assert "ลูกค้าขอส่วนลด 10%" in reply.text, reply.text

    async def test_an_appointment_comes_back_with_what_it_is_about(self):
        client = _shop()
        reply = await _say(
            client, "เตือน C-2026-0001 พรุ่งนี้ ตามเรื่องใบเสนอราคา",
            {"action": "create", "entity": "followup",
             "fields": {"entity_code": "C-2026-0001", "due_date": "2026-09-17",
                        "notes": "ตามเรื่องใบเสนอราคา"}, "missing": []},
        )
        saved = [w for w in client.recorded if w[0] == "create_follow_up"]
        assert saved, client.recorded
        assert "ตามเรื่องใบเสนอราคา" in reply.text, reply.text
        # The date was already echoed and must stay echoed.
        assert "ตั้งเตือน" in reply.text, reply.text


class TestTheCustomerCardShowsTheirActivity:
    def _busy(self) -> FakeDataClient:
        client = _shop()
        client._notes = [
            {"id": "n-1", "entity_type": "customer", "entity_id": "c-1",
             "body": "ลูกค้าขอส่วนลด 10%", "created_at": "2026-09-15T09:00:00+00:00"},
            {"id": "n-2", "entity_type": "customer", "entity_id": "c-1",
             "body": "โทรตามแล้ว ยังไม่ตัดสินใจ", "created_at": "2026-09-16T09:00:00+00:00"},
        ]
        client._follow_ups = [
            {"id": "f-1", "entity_type": "customer", "entity_id": "c-1",
             "due_date": "2026-09-18", "due_time": "14:00", "status": "pending",
             "notes": "ตามเรื่องใบเสนอราคา"},
        ]
        client._deals = [
            {"id": "d-1", "deal_id": "D-2026-0003", "contact_id": "c-1", "stage": "proposed",
             "amount": "30000", "created_at": "2026-09-10T00:00:00+00:00"},
            {"id": "d-2", "deal_id": "D-2026-0004", "contact_id": "c-1", "stage": "won",
             "amount": "9000", "created_at": "2026-09-01T00:00:00+00:00"},
        ]
        client._quotes = [
            {"id": "q-1", "quote_id": "Q-2026-0002", "deal_id": "d-1", "status": "sent",
             "created_at": "2026-09-12T00:00:00+00:00"},
        ]
        return client

    async def test_every_section_the_owner_named_is_there(self):
        reply = await _say(self._busy(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        for expected in ("บันทึก (2)", "นัดหมายที่จะถึง (1)", "ดีลที่เปิดอยู่ (1)", "ใบเสนอราคา (1)"):
            assert expected in reply.text, (expected, reply.text)

    async def test_the_email_is_on_the_card(self):
        reply = await _say(self._busy(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "somchai@example.com" in reply.text, reply.text

    async def test_the_newest_note_is_the_one_shown(self):
        reply = await _say(self._busy(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "โทรตามแล้ว" in reply.text, reply.text

    async def test_a_closed_deal_is_not_an_open_one(self):
        reply = await _say(self._busy(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "D-2026-0003" in reply.text, reply.text
        assert "D-2026-0004" not in reply.text, "a won deal is not open"

    async def test_the_appointment_says_when_and_what(self):
        reply = await _say(self._busy(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "14:00" in reply.text and "ตามเรื่องใบเสนอราคา" in reply.text, reply.text

    async def test_the_card_still_fits_in_a_line_bubble(self):
        reply = await _say(self._busy(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert len(reply.text.split("\n")) <= 15, reply.text

    async def test_a_long_list_says_how_many_were_left_out(self):
        client = self._busy()
        client._notes = [
            {"id": f"n-{i}", "entity_type": "customer", "entity_id": "c-1",
             "body": f"บันทึกที่ {i}", "created_at": f"2026-09-{i:02d}T09:00:00+00:00"}
            for i in range(1, 8)
        ]
        reply = await _say(client, "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "บันทึก (7)" in reply.text, reply.text
        assert "อีก 5 รายการ" in reply.text, reply.text

    async def test_a_customer_with_nothing_is_told_so(self):
        reply = await _say(_shop(), "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "ยังไม่มีบันทึก" in reply.text, reply.text

    async def test_one_failing_read_does_not_lose_the_card(self):
        """A quotes hiccup must not cost the customer their phone number."""
        client = self._busy()

        async def _boom(*_a, **_k):
            raise RuntimeError("data tier said 503")

        client.list_quotes = _boom
        reply = await _say(client, "ข้อมูลลูกค้า C-2026-0001", _view_customer())
        assert "0812345678" in reply.text, reply.text
        assert "ดีลที่เปิดอยู่ (1)" in reply.text, reply.text
