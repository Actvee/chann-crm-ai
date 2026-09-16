"""Round 19m — the tester's notes on the customer and technician LINEs.

From the guide's own comment threads (V3Comment.html, 16 ก.ย. 2569), each
one reproduced against the real model before it was touched:

* t-5  "ยังไม่สามารถส่งลิงค์ maps แล้วบันทึกพิกัดได้" — only LINE's own
  location message counted as a position.
* c-5  "ในการ 'จบการสนทนา' ยังใช้คำอื่นไม่ได้ เช่น จบการพูดคุย" — the one
  door out of a live chat accepted one spelling, and the other spelling was
  relayed to the shop as a chat line.
* c-6  "ยังไม่สามารถให้ข้อมูลร้านค้าได้" — three questions in a row about
  the shop's hours, address and phone, each answered "พิมพ์เรื่องที่
  ต้องการติดต่อมาได้เลย". The hours had nowhere to be stored at all.
* c-13 "การตีความคำถามยังผิดพลาดอยู่บางครั้งนำไปเปิดเป็นแจ้งซ่อมแทน" —
  "มาซ่อมต้องจ่ายเงินก่อนไหม" opened a repair job named after the question.
* c-14 the numbered product list answered "กรุณาพิมพ์หมายเลข 1-6" to
  someone saying they would come back later.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services import chat
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import _reads

pytestmark = pytest.mark.asyncio

TECH_KEYS = ["ticket.read", "ticket.update", "service_report.create", "service_report.read"]
CUSTOMER_KEYS = ["customer.read", "ticket.create", "ticket.read", "warranty.read", "warranty.create"]
JOB = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "accept_status": "accepted",
    "assigned_to_ref": "member-1", "owner_member_id": "member-1", "visibility": "public",
    "customer_name": "สมชาย ใจดี", "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
}
READ_TICKET = {"action": "read", "entity": "ticket", "fields": {}, "missing": []}
READ_SHOP = {"action": "read", "entity": "shop", "fields": {}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestAMapLinkIsAPosition:
    """t-5. A technician in someone's soi pastes the pin they already have
    open; LINE's location button is three taps away."""

    def _tech(self):
        client = FakeDataClient(permission_keys=TECH_KEYS, role="technician")
        client._tickets = [dict(JOB)]
        client._member_id = "member-1"
        client._role = "technician"
        return client

    async def test_a_pasted_link_checks_in_with_its_coordinates(self):
        client = self._tech()
        reply = await handle_chat_message(
            client, message="https://maps.google.com/?q=13.7563,100.5018",
            ctx=_ctx(oa="technician", primary_role="technician"), ai_client=_reads(READ_TICKET),
        )
        checked = [w for w in client.recorded if w[0] == "check_in_ticket"]
        assert checked, reply.text
        assert "T-2026-0001" in reply.text and "บันทึกตำแหน่ง" in reply.text, reply.text

    async def test_the_place_pin_wins_over_where_the_map_is_centred(self):
        link = "https://www.google.com/maps/place/X/@13.75,100.50,17z/data=!3m1!4b1!4m5!3d13.736717!4d100.523186"
        assert chat._coordinates_in(link) == (13.736717, 100.523186)

    async def test_a_shortened_link_says_what_to_send_instead(self):
        client = self._tech()
        reply = await handle_chat_message(
            client, message="https://maps.app.goo.gl/abc123",
            ctx=_ctx(oa="technician", primary_role="technician"), ai_client=_reads(READ_TICKET),
        )
        assert not [w for w in client.recorded if w[0] == "check_in_ticket"]
        assert "ลิงก์แบบย่อ" in reply.text, reply.text

    async def test_an_ordinary_sentence_is_not_a_position(self):
        assert chat._coordinates_in("โทร 0812345678") is None
        assert chat._coordinates_in("ถึงแล้วครับ") is None


class TestEndingTheConversation:
    """c-5. The way out cannot be one spelling: during a live chat nothing
    reaches the model, so these words are the only door."""

    @pytest.mark.parametrize("said", [
        "จบการสนทนา", "จบการพูดคุย", "ขอจบการพูดคุยนะครับ", "พอแค่นี้ครับ",
        "เลิกคุยแล้วครับ", "ปิดแชท", "end chat", "หยุดคุยก่อนนะ",
    ])
    async def test_these_all_end_it(self, said):
        assert chat._ends_the_conversation(said), said

    @pytest.mark.parametrize("said", [
        "แอร์ไม่เย็น", "ขอบคุณครับ", "จบงานแล้วครับ", "คุยกับร้าน", "ช่างมากี่โมง",
    ])
    async def test_these_do_not(self, said):
        assert not chat._ends_the_conversation(said), said


class TestTheShopsOwnDetails:
    """c-6. Answer with what the shop has said, and name what it has not."""

    def _customer(self, profile):
        return FakeDataClient(permission_keys=CUSTOMER_KEYS, role="customer", company_profile=profile)

    FILLED = {
        "legal_name": None, "company_name": "TOTO", "tax_id": None,
        "company_address": "12 ถ.เจริญนคร คลองสาน", "company_phone": "021234567",
        "company_email": None, "open_hours": "จ-ส 9:00-18:00", "vat_rate": None,
    }

    async def test_the_hours_are_answered_from_the_shops_profile(self):
        client = self._customer(self.FILLED)
        reply = await handle_chat_message(
            client, message="ร้านเปิดกี่โมงหรอ", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads(READ_SHOP),
        )
        assert "จ-ส 9:00-18:00" in reply.text, reply.text
        assert "021234567" in reply.text, reply.text

    async def test_the_one_thing_asked_for_and_missing_is_named(self):
        client = self._customer(self.FILLED)
        reply = await handle_chat_message(
            client, message="อีเมลร้านคืออะไรครับ", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads(READ_SHOP),
        )
        assert "ยังไม่ได้ลงไว้: อีเมล" in reply.text, reply.text

    async def test_a_card_that_answers_the_question_recites_nothing_else(self):
        # Round 18d made "ร้านอยู่ที่ไหน" answer with the card; naming every
        # empty field underneath would undo that.
        client = self._customer(self.FILLED)
        reply = await handle_chat_message(
            client, message="ร้านอยู่ที่ไหน", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads(READ_SHOP),
        )
        assert "เจริญนคร" in reply.text and "ยังไม่ได้ลงไว้" not in reply.text, reply.text

    async def test_an_empty_profile_says_so_rather_than_only_inviting_a_chat(self):
        client = self._customer({
            "legal_name": None, "company_name": "TOTO", "tax_id": None, "company_address": None,
            "company_phone": None, "company_email": None, "open_hours": None, "vat_rate": None,
        })
        reply = await handle_chat_message(
            client, message="ร้านอยู่ที่ไหน", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads(READ_SHOP),
        )
        assert "ยังไม่ได้ลง" in reply.text, reply.text


class TestAQuestionAboutMoney:
    """c-13. "มาซ่อมต้องจ่ายเงินก่อนไหม" reads as a request for a visit if
    only "มาซ่อม" is weighed."""

    @pytest.mark.parametrize("said", [
        "มาซ่อมต้องจ่ายเงินก่อนไหม", "เก็บเงินปลายทางได้ไหม", "ล้างแอร์ฟรีไหม", "จ่ายเงินยังไงครับ",
    ])
    async def test_money_questions_are_not_faults(self, said):
        assert chat._asks_about_paying(said) or chat._asks_price(said), said

    @pytest.mark.parametrize("said", ["แอร์ไม่เย็น", "มาซ่อมให้หน่อยครับ", "ตู้เย็นมีเสียงดัง"])
    async def test_a_real_report_still_reports(self, said):
        assert not (chat._asks_about_paying(said) or chat._asks_price(said)), said

    async def test_the_question_opens_no_job(self):
        client = FakeDataClient(permission_keys=CUSTOMER_KEYS, role="customer")
        client._warranties = [{
            "id": "w-1", "serial_number": "SN00001", "product_name": "แอร์", "status": "active",
            "customer_chann_uid": _ctx(oa="customer").chann_uid, "warranty_end": "2027-01-01",
        }]
        reply = await handle_chat_message(
            client, message="มาซ่อมต้องจ่ายเงินก่อนไหม", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads(READ_SHOP),
        )
        assert not [w for w in client.recorded if w[0] == "create_ticket"], reply.text


class TestWalkingAwayFromTheProductList:
    """c-14. "ไว้จะแจ้งอีกทีนะครับ" was answered "กรุณาพิมพ์หมายเลข 1-6",
    and so was the next sentence, and the next."""

    @pytest.mark.parametrize("said", ["ไว้จะแจ้งอีกทีนะครับ", "เดี๋ยวแจ้งใหม่ครับ", "ขอบคุณครับ"])
    async def test_walking_away_drops_the_list(self, said):
        assert not chat._tries_to_pick_one(said), said

    @pytest.mark.parametrize("said", ["2", "เอาอันแรก", "ขอตัวที่ 3", "อันที่สองครับ"])
    async def test_reaching_for_one_keeps_it(self, said):
        assert chat._tries_to_pick_one(said), said


class TestTheWarrantyStartsWhenItIsRegistered:
    """Owner, 16 ก.ย. 2569: "การที่ลูกค้าลงทะเบียนสินค้าในช่อง OA ควรเริ่ม
    วันรับประกันตั้งแต่ตอนที่ลงทะเบียนเลยถ้าไม่มีวันที่".

    Since round 19g a unit may be registered with no purchase date, which
    left a register full of rows that could not answer "ยังอยู่ในประกันไหม".
    """

    async def test_claiming_a_unit_with_no_date_starts_the_cover_today(self):
        from datetime import date

        from chann_app.services.thai_datetime import local_today

        client = FakeDataClient(permission_keys=CUSTOMER_KEYS, role="customer")
        client._warranties = [{
            "id": "w-1", "warranty_number": "W-2026-0001", "serial_number": "SN12345678",
            "product_name": "แอร์", "product_id": "p1", "status": "active",
            "customer_chann_uid": None, "warranty_start": None, "warranty_end": None,
        }]
        reply = await handle_chat_message(
            client, message="SN12345678", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads({"action": "create", "entity": "warranty",
                              "fields": {"serial_number": "SN12345678"}, "missing": []}),
        )
        row = client._warranties[0]
        assert row["warranty_start"] == local_today().isoformat(), row
        assert row["warranty_end"], row
        assert isinstance(date.fromisoformat(row["warranty_end"]), date)
        assert "เริ่มนับประกันตั้งแต่วันนี้" in reply.text, reply.text


class TestFillingInOldPurchaseDates:
    """Owner, 16 ก.ย. 2569: "ใน Sale OA ต้องรองรับการอัพโหลดไฟ csv เข้ามา
    เพื่ออัปเดตวันที่ซื้อกับทะเบียนสินค้าเก่าๆด้วยและคำนวณวันหมดสิ้นสุดมาเลย
    ตามแต่ละสินค้า". A row whose serial was already on file used to come
    back "duplicate serial" and change nothing."""

    def _shop(self):
        client = FakeDataClient(permission_keys=["warranty.create", "warranty.update", "warranty.read"])
        client._products = [{"id": "p1", "product_id": "AC12K", "product_name": "แอร์", "warranty_months": 24}]
        client._warranties = [{
            "id": "w-1", "warranty_number": "W-2026-0001", "serial_number": "SN-AC12K-000123",
            "product_name": "แอร์", "product_id": "p1", "status": "active",
            "customer_chann_uid": None, "warranty_start": None, "warranty_end": None,
        }]
        return client

    async def test_a_known_serial_has_its_purchase_date_filled_in(self):
        from chann_app.services import csv_import

        client = self._shop()
        out = await csv_import.import_warranties(
            client, license_id="L1", actor_id="CHN-S-000001",
            text="serial_number,warranty_start\nSN-AC12K-000123,2025-03-01\n",
        )
        assert out["updated"] == 1 and out["failed"] == 0, out
        row = client._warranties[0]
        assert row["warranty_start"] == "2025-03-01", row
        # 24 months from the product, not the default 12.
        assert row["warranty_end"].startswith("2027-03"), row

    async def test_a_new_serial_is_still_registered(self):
        from chann_app.services import csv_import

        client = self._shop()
        out = await csv_import.import_warranties(
            client, license_id="L1", actor_id="CHN-S-000001",
            text="serial_number,warranty_start\nSN-NEW-000001,2026-01-15\n",
        )
        assert out["saved"] == 1 and out["updated"] == 0, out
        assert len(client._warranties) == 2

    async def test_a_known_serial_with_no_date_changes_nothing(self):
        from chann_app.services import csv_import

        client = self._shop()
        out = await csv_import.import_warranties(
            client, license_id="L1", actor_id="CHN-S-000001",
            text="serial_number,warranty_start\nSN-AC12K-000123,\n",
        )
        assert out["skipped"] == 1 and out["updated"] == 0 and out["failed"] == 0, out
        assert client._warranties[0]["warranty_start"] is None
        assert not [w for w in client.recorded if w[0] == "update_warranty"]
