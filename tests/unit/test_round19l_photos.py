"""Round 19l — the pictures on a job: what is attached, and taking one off.

Owner, 16 ก.ย. 2569: "ในหน้า ticket ของ tech oa พอกดแนบรูปแล้วน่าจะมีขึ้นโชว์
เป็นรายการชื่อให้หน่อยว่าแนบรูปอะไรไปบ้าง … เพื่อลบออกเพิ่มใหม่หรือแก้ไข
รายละเอียดในหน้า Dashboard และเช็คเรื่องการทำงานแบบนี้ผ่านแชทด้วยเพื่อยังไม่ได้
เพิ่มการลบรูปที่แนบออก".

Attaching has worked since 13.1. Listing and removing had no words at all —
in chat or on the screen — so five pictures produced five identical
confirmations and no way back.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx
from test_round18e_followups import KEYS, _reads

pytestmark = pytest.mark.asyncio

TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read"]
TICKET = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "in_progress", "accept_status": "accepted",
    "assigned_to_ref": "member-1", "assigned_target_type": "technician", "owner_member_id": "member-9",
    "customer_name": "สมชาย ใจดี", "issue_description": "แอร์ไม่เย็น", "service_address": "99/1",
    "visibility": "public",
}
LIST = {"action": "read", "entity": "photo", "fields": {}, "missing": []}
DELETE_2 = {"action": "delete", "entity": "photo", "fields": {"index": 2}, "missing": []}
DELETE_NOTHING = {"action": "delete", "entity": "photo", "fields": {}, "missing": []}
NAME_1 = {"action": "update", "entity": "photo", "fields": {"index": 1, "caption": "ก่อนซ่อม"}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _tech_ctx():
    return _ctx(oa="technician", primary_role="technician")


async def _say(client, message, reading, ctx=None):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=ctx or _tech_ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


async def _on_site(captions=("หน้าแอร์ก่อนซ่อม.jpg", None), keys=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=list(keys if keys is not None else TECH_KEYS), role="technician")
    client._tickets = [dict(TICKET)]
    client._members = [
        {"id": "member-1", "chann_uid": _tech_ctx().chann_uid, "role": "technician",
         "status": "active", "display_name": "สมศักดิ์", "channel": "technician"},
    ]
    client._member_id = "member-1"
    client._role = "technician"
    for i, caption in enumerate(captions, start=1):
        await client.add_ticket_photo(LICENSE_ID, "t1", {
            "photo_url": f"documents/l1/tickets/t1/photos/{i}.jpg",
            "photo_type": "evidence" if i == 1 else "checkin",
            "caption": caption,
        })
    return client


class TestSeeingWhatIsAttached:
    async def test_the_list_names_every_picture(self):
        client = await _on_site()
        reply, _ = await _say(client, "รูปที่แนบไปมีอะไรบ้าง", LIST)
        assert "T-2026-0001 (2 รูป)" in reply.text, reply.text
        # The one that came from a phone keeps the file's own name; the one
        # sent in chat has none and is called by its position.
        assert "1. หน้าแอร์ก่อนซ่อม.jpg" in reply.text, reply.text
        assert "2. รูปที่ 2" in reply.text, reply.text

    async def test_a_job_with_no_pictures_says_how_to_send_one(self):
        client = await _on_site(captions=())
        reply, _ = await _say(client, "ขอดูรูปที่แนบ", LIST)
        assert "ยังไม่มีรูปแนบ" in reply.text, reply.text
        assert not reply.quick_replies, reply.quick_replies

    async def test_every_picture_gets_a_button_that_removes_it(self):
        client = await _on_site()
        reply, _ = await _say(client, "ขอดูรูปที่แนบ", LIST)
        assert ("ลบรูปที่ 2", "ลบรูปที่ 2 ของงาน T-2026-0001") in reply.quick_replies, reply.quick_replies


class TestTakingOneOff:
    async def test_the_named_picture_is_removed(self):
        client = await _on_site()
        reply, wrote = await _say(client, "ลบรูปที่ 2", DELETE_2)
        assert [w for w in wrote if w[0] == "delete_ticket_photo"], wrote
        assert "เหลือ 1 รูป" in reply.text, reply.text
        assert len(await client.list_ticket_photos(LICENSE_ID, "t1")) == 1

    async def test_without_a_number_it_asks_which_and_writes_nothing(self):
        client = await _on_site()
        reply, wrote = await _say(client, "ลบรูปออกหน่อย", DELETE_NOTHING)
        assert not [w for w in wrote if w[0] == "delete_ticket_photo"], wrote
        assert "รูปไหน" in reply.text, reply.text
        assert len(await client.list_ticket_photos(LICENSE_ID, "t1")) == 2

    async def test_a_number_past_the_end_removes_nothing(self):
        client = await _on_site()
        reply, wrote = await _say(client, "ลบรูปที่ 9", {"action": "delete", "entity": "photo", "fields": {"index": 9}, "missing": []})
        assert not [w for w in wrote if w[0] == "delete_ticket_photo"], wrote
        assert "ไม่มีรูปที่ 9" in reply.text, reply.text

    async def test_a_technician_without_the_permission_removes_nothing(self):
        client = await _on_site(keys=["ticket.read"])
        reply, wrote = await _say(client, "ลบรูปที่ 2", DELETE_2)
        assert not [w for w in wrote if w[0] == "delete_ticket_photo"], wrote
        assert len(await client.list_ticket_photos(LICENSE_ID, "t1")) == 2


class TestNamingOne:
    async def test_a_picture_can_be_called_something(self):
        client = await _on_site()
        reply, wrote = await _say(client, "ตั้งชื่อรูปที่ 1 ว่า ก่อนซ่อม", NAME_1)
        assert [w for w in wrote if w[0] == "name_ticket_photo"], wrote
        assert "ก่อนซ่อม" in reply.text, reply.text
        rows = await client.list_ticket_photos(LICENSE_ID, "t1")
        assert rows[0]["caption"] == "ก่อนซ่อม"

    async def test_the_new_name_shows_up_in_the_list(self):
        client = await _on_site()
        await _say(client, "ตั้งชื่อรูปที่ 1 ว่า ก่อนซ่อม", NAME_1)
        reply, _ = await _say(client, "ขอดูรูปที่แนบ", LIST)
        assert "1. ก่อนซ่อม" in reply.text, reply.text


class TestTheJobItBelongsTo:
    """The model says missing=["code"] for "เอารูปแรกออก" — the technician
    standing on site did not name the job because they are on it. The
    handler finds it the same three ways every other job command does."""

    async def test_the_job_in_hand_is_the_one_it_means(self):
        client = await _on_site()
        reply, wrote = await _say(
            client, "เอารูปแรกออก",
            {"action": "delete", "entity": "photo", "fields": {"index": 1}, "missing": ["code"]},
        )
        assert [w for w in wrote if w[0] == "delete_ticket_photo"], reply.text
        assert "เหลือ 1 รูป" in reply.text, reply.text
