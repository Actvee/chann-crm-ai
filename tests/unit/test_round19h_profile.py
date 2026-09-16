"""Round 19h — the profile form, on every channel.

Owner, 16 ก.ย. 2569: on the customer OA "แก้ไขข้อมูลส่วนตัว" then a line with
the name, the address and the phone got no reply and saved nothing — and with
a conversation with the shop open, that line was relayed to the Sales OA,
which should never have seen it. Several fields in one message must work, and
the answer belongs to the question the system asked.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services import live_chat
from chann_app.services.chat import _profile_values_in, handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import _reads

pytestmark = pytest.mark.asyncio

CUSTOMER = dict(oa="customer", primary_role="customer")
TECH = dict(oa="technician", primary_role="technician")
OPEN_FORM = {"action": "update", "entity": "profile", "fields": {}, "missing": ["first_name", "last_name", "phone", "email", "address"]}
SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}
ANSWER = "ชื่อ สมชาย ใจดี ที่อยู่ 99/1 ถ.สุขุมวิท แขวงคลองตัน เบอร์โทร 0891234567"


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


@pytest.fixture
def pushes(monkeypatch):
    sent: list[tuple] = []

    async def fake_push_text(oa, to, text, client=None, quick_reply=None):
        sent.append((oa, to, text))
        return ["mid"]

    async def fake_push_messages(oa, to, messages, client=None):
        sent.append((oa, to, messages))
        return ["mid"]

    monkeypatch.setattr(live_chat, "push_text", fake_push_text)
    monkeypatch.setattr(live_chat, "push_messages", fake_push_messages)
    return sent


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _ctx(**CUSTOMER), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _customer() -> FakeDataClient:
    client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
    client._profiles = {"CHN-S-000001": {"chann_uid": "CHN-S-000001", "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"}}
    return client


class TestTheLabelledParser:
    async def test_three_fields_in_one_line(self):
        assert _profile_values_in(ANSWER) == {
            "first_name": "สมชาย", "last_name": "ใจดี",
            "address": "99/1 ถ.สุขุมวิท แขวงคลองตัน", "phone": "0891234567",
        }

    async def test_one_field_alone(self):
        assert _profile_values_in("เบอร์ 0891234567") == {"phone": "0891234567"}

    async def test_a_sentence_with_no_labels_is_left_to_the_model(self):
        assert _profile_values_in("แอร์ไม่เย็นครับ") == {}

    async def test_a_number_that_is_not_a_phone_is_not_taken(self):
        assert "phone" not in _profile_values_in("เบอร์ 12")


class TestTheCustomerFillsSeveralFieldsAtOnce:
    async def test_the_form_opens_and_the_next_line_is_saved(self):
        client = _customer()
        opened, _ = await _say(client, "แก้ไขข้อมูลส่วนตัว", {"action": "update", "entity": "profile", "fields": {}, "missing": []})
        assert "พิมพ์สิ่งที่จะแก้" in opened.text and "สมชาย ใจดี" in opened.text, opened.text
        reply, calls = await _say(client, ANSWER, SUGGEST)
        saved = next((c for c in calls if c[0] == "update_profile"), None)
        assert saved is not None, (reply.text, calls)
        assert saved[2] == {"first_name": "สมชาย", "last_name": "ใจดี", "address": "99/1 ถ.สุขุมวิท แขวงคลองตัน", "phone": "0891234567"}
        assert "แก้ไขข้อมูลส่วนตัวเรียบร้อย" in reply.text and "0891234567" in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "customer") is None

    async def test_the_model_reading_with_the_form_open_is_used_too(self):
        """Unlabelled values with the form open: the model reads them with the
        form in its prompt (measured 16 ก.ย. 2569)."""
        client = _customer()
        await _say(client, "แก้ไขข้อมูลส่วนตัว", {"action": "update", "entity": "profile", "fields": {}, "missing": []})
        reply, calls = await _say(client, "สมชาย ใจดี 99/1 ถ.สุขุมวิท 0891234567", {"action": "update", "entity": "profile", "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "address": "99/1 ถ.สุขุมวิท", "phone": "0891234567"}, "missing": []})
        assert [c for c in calls if c[0] == "update_profile"], reply.text

    async def test_everything_in_one_message_still_works(self):
        client = _customer()
        reply, calls = await _say(client, "แก้ข้อมูลส่วนตัว " + ANSWER, {"action": "update", "entity": "profile", "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "address": "99/1 ถ.สุขุมวิท แขวงคลองตัน", "phone": "0891234567"}, "missing": []})
        assert [c for c in calls if c[0] == "update_profile"], reply.text

    async def test_cancelling_the_form_writes_nothing(self):
        client = _customer()
        await _say(client, "แก้ไขข้อมูลส่วนตัว", {"action": "update", "entity": "profile", "fields": {}, "missing": []})
        reply, calls = await _say(client, "ยกเลิก", SUGGEST)
        assert not [c for c in calls if c[0] == "update_profile"], reply.text
        assert await client.get_pending_intent("CHN-S-000001", "customer") is None

    async def test_the_technician_form_works_the_same_way(self):
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.update"])
        client._profiles = {"CHN-S-000001": {"chann_uid": "CHN-S-000001", "first_name": "สมศักดิ์", "last_name": "ช่างดี", "phone": "0812345678"}}
        await _say(client, "แก้ไขข้อมูลส่วนตัว", {"action": "update", "entity": "profile", "fields": {}, "missing": []}, ctx=_ctx(**TECH))
        reply, calls = await _say(client, "ชื่อ สมศักดิ์ ช่างเก่ง เบอร์ 0891112222", SUGGEST, ctx=_ctx(**TECH))
        saved = next((c for c in calls if c[0] == "update_profile"), None)
        assert saved is not None and saved[2]["phone"] == "0891112222", (reply.text, calls)


class TestTheFormAnswerNeverReachesTheShop:
    async def test_an_open_form_is_answered_even_while_a_conversation_runs(self, pushes):
        """The form was opened before the conversation: its answer belongs to
        the question this system asked, not to the shop (owner, 16 ก.ย. 2569 —
        it went to the Sales OA and the customer got no reply at all)."""
        client = _customer()
        await _say(client, "แก้ไขข้อมูลส่วนตัว", {"action": "update", "entity": "profile", "fields": {}, "missing": []})
        await _say(client, "คุยกับร้าน อยากถามเรื่องราคา", SUGGEST)
        reply, calls = await _say(client, ANSWER, SUGGEST)
        assert [c for c in calls if c[0] == "update_profile"], reply.text
        assert not [c for c in calls if c[0] == "add_chat_message"], "the shop was sent the answer"
        assert (reply.text or "").strip(), "the customer got no reply"

    async def test_asking_for_the_form_mid_conversation_goes_to_the_shop(self):
        """While the conversation is open the menus stay out of the way."""
        client = _customer()
        await _say(client, "คุยกับร้าน อยากถามเรื่องราคา", SUGGEST)
        reply, calls = await _say(client, "แก้ไขข้อมูลส่วนตัว", {"action": "update", "entity": "profile", "fields": {}, "missing": []})
        assert [c for c in calls if c[0] == "add_chat_message"], reply.text
        assert not [c for c in calls if c[0] == "set_pending_intent"], reply.text

    async def test_an_ordinary_chat_line_still_reaches_the_shop(self, pushes):
        client = _customer()
        await _say(client, "คุยกับร้าน อยากถามเรื่องราคา", SUGGEST)
        reply, calls = await _say(client, "แอร์รุ่นนี้ราคาเท่าไหร่", SUGGEST)
        assert [c for c in calls if c[0] == "add_chat_message"], reply.text


class TestTheShopReadsTheConversationOnTheDashboard:
    """Owner, 16 ก.ย. 2569: only the new conversation is pushed to LINE, with
    the first thing the customer said; the rest is read on the chats page."""

    async def test_the_first_message_is_pushed_and_later_lines_are_not(self, pushes):
        client = _customer()
        client._members = [{"id": "MEMBER-1", "chann_uid": "CHN-S-000777", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales", "permission_keys": ["chat_session.view", "chat_session.reply"]}]
        _opened, opening = await _say(client, "คุยกับร้าน อยากถามเรื่องราคา", SUGGEST)
        first = [c for c in opening if c[0] == "create_notification"]
        assert first and any(c[5] for c in first), "the new conversation was not pushed to LINE"
        _reply, later = await _say(client, "แอร์รุ่นนี้ราคาเท่าไหร่", SUGGEST)
        lines = [c for c in later if c[0] == "create_notification"]
        assert lines, "the dashboard was not told"
        assert not any(c[5] for c in lines), "a chat line was pushed to the shop's LINE"


class TestTheAddressOnFileIsOffered:
    """Owner, 16 ก.ย. 2569: "ถ้าข้อมูลส่วนตัวมีที่อยู่อยู่แล้ว ต้องถามด้วยว่าจะ
    ใช้ที่อยู่ตามข้อมูลส่วนตัวหรือกรอกใหม่"."""

    def _reporting(self) -> FakeDataClient:
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
        client._profiles = {"CHN-S-000001": {"chann_uid": "CHN-S-000001", "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "address": "99/1 ถ.สุขุมวิท"}}
        client._warranties = [{"id": "w-1", "serial_number": "SN1", "product_name": "แอร์", "status": "active", "customer_chann_uid": "CHN-S-000001", "warranty_end": "2099-01-01"}]
        return client

    async def test_the_report_offers_the_address_on_file(self):
        client = self._reporting()
        reply, _ = await _say(client, "แอร์ไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"issue_description": "แอร์ไม่เย็น"}, "missing": []})
        assert "99/1 ถ.สุขุมวิท" in reply.text and "ให้ช่างไปที่นี่ไหม" in reply.text, reply.text
        assert any(send == "ที่อยู่เดิม" for _label, send in reply.quick_replies), reply.quick_replies

    async def test_yes_uses_it_and_moves_on_to_the_appointment(self):
        client = self._reporting()
        await _say(client, "แอร์ไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"issue_description": "แอร์ไม่เย็น"}, "missing": []})
        reply, calls = await _say(client, "ใช่", SUGGEST)
        saved = [c for c in calls if c[0] == "update_ticket"]
        assert saved and saved[0][3].get("service_address") == "99/1 ถ.สุขุมวิท", (reply.text, calls)
        assert "วันไหน" in reply.text, reply.text

    async def test_a_new_address_typed_instead_is_used(self):
        client = self._reporting()
        await _say(client, "แอร์ไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"issue_description": "แอร์ไม่เย็น"}, "missing": []})
        reply, calls = await _say(client, "12/3 ถ.พระราม 4 แขวงคลองเตย", SUGGEST)
        saved = [c for c in calls if c[0] == "update_ticket"]
        assert saved and "12/3" in str(saved[0][3].get("service_address")), (reply.text, calls)

    async def test_with_no_address_on_file_it_just_asks(self):
        client = FakeDataClient(permission_keys=["ticket.read", "ticket.create"])
        client._profiles = {"CHN-S-000001": {"chann_uid": "CHN-S-000001", "first_name": "สมชาย", "phone": "0812345678"}}
        client._warranties = [{"id": "w-1", "serial_number": "SN1", "product_name": "แอร์", "status": "active", "customer_chann_uid": "CHN-S-000001", "warranty_end": "2099-01-01"}]
        reply, _ = await _say(client, "แอร์ไม่เย็น", {"action": "create", "entity": "ticket", "fields": {"issue_description": "แอร์ไม่เย็น"}, "missing": []})
        assert "ขอที่อยู่" in reply.text and "ให้ช่างไปที่นี่ไหม" not in reply.text, reply.text


class TestOnlyTheOpeningLineReachesTheShopsLine:
    """Owner, 16 ก.ย. 2569: "ให้มีแจ้งเตือนแค่ตอนแชทเปิดใหม่เข้ามาพร้อมข้อความ
    ที่ทักมาตอนแรกก็พอ" — and a conversation the customer REOPENS counts as a
    new one."""

    def _shop(self) -> FakeDataClient:
        client = _customer()
        client._members = [{"id": "MEMBER-1", "chann_uid": "CHN-S-000777", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales", "permission_keys": ["chat_session.view", "chat_session.reply"]}]
        return client

    def _pushed(self, calls) -> list:
        return [c for c in calls if c[0] == "create_notification" and c[5]]

    async def test_the_first_line_after_a_bare_open_is_pushed_once(self):
        client = self._shop()
        _opened, opening = await _say(client, "คุยกับร้าน", SUGGEST)
        assert self._pushed(opening), "opening a conversation was not announced"
        _first, first_line = await _say(client, "แอร์รุ่นนี้ราคาเท่าไหร่", SUGGEST)
        assert self._pushed(first_line), "the opening line never reached LINE"
        _second, later = await _say(client, "แล้วมีผ่อนไหม", SUGGEST)
        assert [c for c in later if c[0] == "create_notification"], "the dashboard was not told"
        assert not self._pushed(later), "a later line was pushed to the shop's LINE"

    async def test_reopening_a_closed_conversation_is_announced_again(self):
        client = self._shop()
        await _say(client, "คุยกับร้าน อยากถามเรื่องราคา", SUGGEST)
        await _say(client, "จบการสนทนา", SUGGEST)
        _reopened, again = await _say(client, "คุยกับร้าน กลับมาถามต่อครับ", SUGGEST)
        assert self._pushed(again), "reopening was not announced to the shop"

    async def test_the_first_line_after_a_reopen_is_pushed_too(self):
        client = self._shop()
        await _say(client, "คุยกับร้าน อยากถามเรื่องราคา", SUGGEST)
        await _say(client, "แล้วมีผ่อนไหม", SUGGEST)
        await _say(client, "จบการสนทนา", SUGGEST)
        await _say(client, "คุยกับร้าน", SUGGEST)
        _reply, first_line = await _say(client, "กลับมาถามต่อครับ", SUGGEST)
        assert self._pushed(first_line), "the first line of the reopened conversation never reached LINE"
