"""Chat understanding batch (6 Sep 2026): many phrasings reach the same
handler, help comes in layers, the customer OA no longer turns every
unrecognised sentence into a repair job, and the people who should hear
about a job do.

The model is replaced by a probe that records whether it was consulted:
a phrasing that "works" only because the model rescued it is not covered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402
from test_user_review_fixes import ReviewFake  # noqa: E402


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


class Probe:
    def __init__(self):
        self.calls = 0
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        self.calls += 1
        body = json.dumps({"action": "suggest", "entity": None, "fields": {}, "missing": []})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": body}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "x"})


async def say(client, oa, message, role=None):
    probe = Probe()
    ctx = _ctx(oa=oa, primary_role=role or ("technician" if oa == "technician" else "sales"))
    reply = await handle_chat_message(client, message=message, ctx=ctx, ai_client=probe.client)
    return reply, probe.calls


TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read", "warranty.read"]


def _tech():
    c = FakeDataClient(permission_keys=TECH_KEYS, role="technician")
    c._tickets = [
        {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "accept_status": "accepted",
         "assigned_to_ref": "member-1", "customer_name": "สมชาย", "customer_phone": "0812345678",
         "service_address": "99/1", "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08", "scheduled_time": "10:00"},
        {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned", "accept_status": "accepted",
         "assigned_to_ref": "member-9", "customer_name": "คนอื่น", "service_address": "1 ลาดพร้าว", "issue_description": "ตู้เย็น"},
    ]
    return c


# ------------------------------------------------------------------ help

class TestLayeredHelp:
    @pytest.mark.parametrize("phrasing", [
        "ใช้งานยังไง", "ใช้ยังไงครับ", "มันใช้ยังไง", "สอนใช้หน่อย", "ไม่เข้าใจ", "งง", "ช่วยด้วย",
        "มีเมนูอะไรบ้าง", "คุณทำอะไรได้บ้าง", "how do i use this", "??", "ขอคู่มือ", "พิมพ์อะไรได้บ้าง",
    ])
    async def test_every_way_of_asking_gets_the_topic_menu_without_the_model(self, phrasing):
        for oa in ("sales", "technician", "customer"):
            client = FakeDataClient(permission_keys=["customer.read", "ticket.read"])
            reply, calls = await say(client, oa, phrasing)
            assert calls == 0, f"{oa} {phrasing!r} went to the model"
            assert reply.text.startswith("วิธีใช้ LINE"), f"{oa} {phrasing!r}: {reply.text[:60]}"
            assert reply.text.count("\n") <= 14
            assert any(send == "วิธีใช้ 1" for _l, send in reply.quick_replies)
            assert all(len(label) <= 20 for label, _s in reply.quick_replies)

    async def test_a_topic_is_one_message_with_its_picture_and_the_next_button(self, monkeypatch):
        monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
        client = FakeDataClient(permission_keys=["customer.read"])
        reply, calls = await say(client, "customer", "วิธีใช้ 5")
        assert calls == 0 and reply.text.startswith("5. แจ้งซ่อม") and "▸ พิมพ์" in reply.text
        assert reply.images and reply.images[0].startswith("https://app.example.com/api/v1/guide/images/")
        assert any(send == "วิธีใช้ 6" for _l, send in reply.quick_replies)

    async def test_a_bare_digit_after_the_menu_picks_the_topic_and_a_sentence_ends_the_menu(self):
        client = FakeDataClient(permission_keys=["customer.read", "ticket.read"])
        await say(client, "technician", "วิธีใช้")
        reply, calls = await say(client, "technician", "2")
        assert calls == 0 and reply.text.startswith("2. รับงาน")
        # A topic named by its title works too.
        reply, _ = await say(client, "technician", "วิธีใช้ เช็คอิน")
        assert reply.text.startswith("3.")
        # "แก้เบอร์…" while the menu is open is not a topic pick either.
        reply, _ = await say(client, "technician", "งานของฉัน")
        assert "วิธีใช้" not in reply.text.splitlines()[0]

    async def test_the_full_guide_is_still_one_message_away(self):
        client = FakeDataClient(permission_keys=["customer.read"])
        reply, _ = await say(client, "sales", "วิธีใช้ทั้งหมด")
        assert "1. " in reply.text and "8. " in reply.text and "▸ พิมพ์" in reply.text


# ------------------------------------------------------------- staff OAs

class TestStaffPhrasings:
    async def test_polite_particles_and_openers_do_not_hide_a_command(self):
        for phrasing in ("รายชื่อลูกค้าครับ", "ขอดูลูกค้าหน่อย", "ลูกค้า", "ขอรายชื่อลูกค้า", "ลูกค้ามีใครบ้าง", "list customers"):
            client = ReviewFake()
            await FakeDataClient.create_customer(client, "L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
            reply, calls = await say(client, "sales", phrasing)
            assert calls == 0 and "สมชาย" in reply.text, phrasing

    @pytest.mark.parametrize("phrasing", ["ลูกค้าชื่อสมชาย", "เบอร์สมชาย", "สมชาย เบอร์อะไร", "หาลูกค้าชื่อสมชาย", "ค้นหา สมชาย", "ขอเบอร์ลูกค้า สมชาย"])
    async def test_a_name_lookup_in_plain_words_finds_the_customer(self, phrasing):
        client = ReviewFake()
        await FakeDataClient.create_customer(client, "L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        reply, calls = await say(client, "sales", phrasing)
        assert calls == 0 and "0812345678" in reply.text, (phrasing, reply.text)

    async def test_small_talk_is_answered_not_parsed(self):
        for phrasing in ("ขอบคุณครับ", "โอเค", "ok", "👍", "555", "ครับ"):
            client = FakeDataClient(permission_keys=["customer.read"])
            reply, calls = await say(client, "sales", phrasing)
            assert calls == 0 and "ยินดี" in reply.text, phrasing
        client = FakeDataClient(permission_keys=["customer.read"])
        reply, calls = await say(client, "sales", "สวัสดีครับ ขอสอบถามหน่อย")
        assert calls == 0 and "สวัสดี" in reply.text

    async def test_a_single_shop_answers_switch_shop_itself(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        reply, calls = await say(client, "technician", "เปลี่ยนร้าน")
        assert calls == 0 and "บริษัททดสอบ" in reply.text

    async def test_sales_summary_with_a_word_in_front_stays_deterministic(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        reply, calls = await say(client, "sales", "สรุปยอดขาย")
        assert calls == 0 and "entity" not in reply.text

    async def test_discount_ignores_the_quote_code(self):
        client = FakeDataClient(permission_keys=["quote.update"])
        client._quotes = [{"id": "QUOTE-1", "quote_id": "Q-2026-0001", "status": "draft", "deal_id": "d1"}]
        reply, _ = await say(client, "sales", "ส่วนลด Q-2026-0001 500 บาท")
        terms = [r for r in client.recorded if r[0] == "set_quote_terms"]
        assert terms and terms[0][3] == {"discount_amount": "500"}, reply.text
        reply, _ = await say(client, "sales", "ส่วนลด Q-2026-0001 10%")
        assert client.recorded[-1][3] == {"discount_percent": "10"} or any(r[3] == {"discount_percent": "10"} for r in client.recorded if r[0] == "set_quote_terms")


class TestTechnicianPhrasings:
    @pytest.mark.parametrize("phrasing", ["งานของผม", "งาน", "ตารางงาน", "งานที่ต้องไป", "วันนี้มีงานไหม", "งานของฉันครับ"])
    async def test_my_jobs_in_many_words_lists_only_mine(self, phrasing):
        client = _tech()
        reply, calls = await say(client, "technician", phrasing)
        assert calls == 0 and "T-2026-0001" in reply.text and "T-2026-0002" not in reply.text, (phrasing, reply.text)

    async def test_arrival_in_plain_words_checks_in_but_a_sentence_about_arriving_does_not(self):
        client = _tech()
        reply, calls = await say(client, "technician", "ลูกค้าบอกว่าถึงแล้วค่อยโทร")
        assert not [r for r in client.recorded if r[0] == "check_in_ticket"]
        client = _tech()
        reply, calls = await say(client, "technician", "ถึงบ้านลูกค้าแล้วครับ")
        assert calls == 0 and [r for r in client.recorded if r[0] == "check_in_ticket"]
        assert reply.quick_replies == [("ปิดงาน", "ปิดงาน T-2026-0001")]

    async def test_bare_code_and_job_questions_show_the_job(self):
        client = _tech()
        reply, calls = await say(client, "technician", "T-2026-0001")
        assert calls == 0 and "99/1" in reply.text
        reply, calls = await say(client, "technician", "ลูกค้าเบอร์อะไร")
        assert calls == 0 and "0812345678" in reply.text

    async def test_accept_and_decline_words_on_their_own(self):
        client = _tech()
        client._tickets[0].update({"accept_status": "pending"})
        reply, calls = await say(client, "technician", "ไม่รับ")
        assert calls == 0 and [r for r in client.recorded if r[0] == "reject_ticket"]

    async def test_check_out_prefers_the_job_in_progress(self):
        client = _tech()
        client._tickets[1].update({"assigned_to_ref": "member-1", "status": "in_progress"})
        reply, calls = await say(client, "technician", "เสร็จแล้ว")
        assert calls == 0 and "T-2026-0002" in reply.text and "พบปัญหา" in reply.text
        # and the draft can be abandoned
        reply, _ = await say(client, "technician", "ยกเลิก")
        assert "ยกเลิกการปิดงาน T-2026-0002" in reply.text
        assert await client.get_pending_intent("CHN-S-000001", "technician") is None

    async def test_my_profile_on_the_technician_oa(self):
        client = _tech()
        client._profiles = {"CHN-S-000001": {"first_name": "สมศักดิ์", "phone": "0899999999"}}
        reply, calls = await say(client, "technician", "ข้อมูลของฉัน")
        assert calls == 0 and "สมศักดิ์" in reply.text and "0899999999" in reply.text


# ---------------------------------------------------------- customer OA

def _customer():
    c = FakeDataClient(permission_keys=[])
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "customer_chann_uid": "CHN-S-000001", "warranty_end": "2027-01-01"}]
    return c


class TestCustomerCatchAll:
    @pytest.mark.parametrize("phrasing", ["จ่ายเงินยังไง", "ขอใบเสร็จ", "asdfgh"])
    async def test_not_a_fault_opens_no_job_and_offers_the_shop(self, phrasing):
        client = _customer()
        reply, calls = await say(client, "customer", phrasing)
        assert not [r for r in client.recorded if r[0] == "create_ticket"], phrasing
        assert "ยังไม่แน่ใจ" in reply.text
        assert any(send.startswith("คุยกับร้าน ") for _l, send in reply.quick_replies)

    @pytest.mark.parametrize("phrasing", ["ราคาแอร์เท่าไหร่", "มีแอร์รุ่นไหนบ้าง", "อยากซื้อแอร์"])
    async def test_a_product_question_points_at_the_storefront(self, phrasing):
        client = _customer()
        reply, calls = await say(client, "customer", phrasing)
        assert not [r for r in client.recorded if r[0] == "create_ticket"], phrasing
        assert any(send == "สินค้าทั้งหมด" for _l, send in reply.quick_replies)
        assert any(send.startswith("คุยกับร้าน ") for _l, send in reply.quick_replies)

    async def test_help_and_thanks_open_no_job_even_with_an_address_pending(self):
        client = _customer()
        await say(client, "customer", "แอร์ไม่เย็น")          # asks for the address
        assert len([r for r in client.recorded if r[0] == "create_ticket"]) == 1
        reply, _ = await say(client, "customer", "ใช้งานยังไง")
        assert reply.text.startswith("วิธีใช้ LINE")
        reply, _ = await say(client, "customer", "ขอบคุณครับ")
        assert "ยินดี" in reply.text
        assert not [r for r in client.recorded if r[0] == "update_ticket"]
        assert len([r for r in client.recorded if r[0] == "create_ticket"]) == 1

    @pytest.mark.parametrize("phrasing", ["ประตูเลื่อนไม่ได้", "ล้างแอร์", "อยากให้ช่างมาดู", "น้ำไม่ไหล", "ขอนัดช่าง"])
    async def test_faults_and_visit_requests_still_open_a_job(self, phrasing):
        client = _customer()
        reply, calls = await say(client, "customer", phrasing)
        assert [r for r in client.recorded if r[0] == "create_ticket"], (phrasing, reply.text)

    async def test_a_warranty_question_shows_the_registered_products(self):
        client = _customer()
        for phrasing in ("เครื่องผมยังมีประกันไหม", "หมดประกันเมื่อไหร่"):
            reply, _ = await say(client, "customer", phrasing)
            assert "ประกัน" in reply.text and "แอร์" in reply.text, phrasing
        assert not [r for r in client.recorded if r[0] == "create_ticket"]

    async def test_talk_to_the_shop_in_other_words(self):
        client = _customer()
        reply, _ = await say(client, "customer", "ขอคุยกับพนักงาน")
        assert "เปิดการสนทนา" in reply.text or "ต่อ" in reply.text or "ร้าน" in reply.text
        assert not [r for r in client.recorded if r[0] == "create_ticket"]

    async def test_cancelling_a_job_asks_first(self):
        client = _customer()
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "customer_chann_uid": "CHN-S-000001",
                            "scheduled_date": "2026-09-08", "scheduled_time": "10:00"}]
        reply, _ = await say(client, "customer", "ไม่ซ่อมแล้ว")
        assert "ใช่ไหม" in reply.text and not [r for r in client.recorded if r[0] == "set_ticket_status"]
        confirm = next(send for _l, send in reply.quick_replies if send.startswith("ยืนยัน"))
        reply, _ = await say(client, "customer", confirm)
        assert [r for r in client.recorded if r[0] == "set_ticket_status" and r[3] == "cancelled"]

    async def test_the_shop_hears_when_the_job_is_filed_not_when_the_date_arrives(self, monkeypatch):
        client = _customer()
        client._members = [{"id": "cs-1", "chann_uid": "CHN-S-000009", "role": "cs", "status": "active"}]
        client._permission_keys = ["ticket.assign", "ticket.read"]
        client._line_targets = {"CHN-S-000009": "U-cs"}
        pushed = []
        async def _push(oa, to, text, client=None):
            pushed.append((oa, to, text)); return ["m1"]
        monkeypatch.setattr(chat._notify_mod, "push_text", _push)
        await say(client, "customer", "แอร์ไม่เย็น")
        assert any(to == "U-cs" and "รอลูกค้าแจ้ง" in text for _oa, to, text in pushed)


# --------------------------------------------------------- notifications

class TestWhoHears:
    async def test_the_customer_is_told_when_a_technician_accepts_and_arrives(self, monkeypatch):
        client = _tech()
        client._tickets[0].update({"accept_status": "pending", "customer_chann_uid": "CHN-C-1"})
        client._line_targets = {"CHN-C-1": "U-cust"}
        pushed = []
        async def _push(oa, to, text, client=None):
            pushed.append((oa, to, text)); return ["m1"]
        monkeypatch.setattr(chat._notify_mod, "push_text", _push)
        await say(client, "technician", "รับงาน T-2026-0001")
        assert any(oa == "customer" and to == "U-cust" and "รับงาน" in text for oa, to, text in pushed)

    async def test_a_cancellation_reaches_the_technician_on_their_own_oa(self, monkeypatch):
        client = _tech()
        client._members = [{"id": "member-1", "chann_uid": "CHN-T-1", "role": "technician", "status": "active"},
                           {"id": "o-1", "chann_uid": "CHN-O-1", "role": "owner", "status": "active"}]
        client._line_targets = {"CHN-T-1": "U-tech", "CHN-O-1": "U-owner", "CHN-C-1": "U-cust"}
        client._tickets[0]["customer_chann_uid"] = "CHN-C-1"
        pushed = []
        async def _push(oa, to, text, client=None):
            pushed.append((oa, to, text)); return ["m1"]
        monkeypatch.setattr(chat._notify_mod, "push_text", _push)
        await chat._notify_ticket_change(client, "L1", "t1", "งาน T-2026-0001 ถูกยกเลิกโดยร้าน", "th",
                                         customer_text="ร้านยกเลิกงาน T-2026-0001")
        by_target = {to: oa for oa, to, _t in pushed}
        assert by_target["U-tech"] == "technician" and by_target["U-owner"] == "sales" and by_target["U-cust"] == "customer"

    async def test_new_ticket_goes_to_everyone_who_may_dispatch(self, monkeypatch):
        client = FakeDataClient(permission_keys=["ticket.assign"], role="cs")
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open", "customer_name": "ก"}]
        client._members = [{"id": "cs-1", "chann_uid": "CHN-CS", "role": "cs", "status": "active"},
                           {"id": "o-1", "chann_uid": "CHN-O", "role": "owner", "status": "active"}]
        client._line_targets = {"CHN-CS": "U-cs", "CHN-O": "U-owner"}
        pushed = []
        async def _push(oa, to, text, client=None):
            pushed.append((oa, to, text)); return ["m1"]
        monkeypatch.setattr(chat._notify_mod, "push_text", _push)
        await chat._notify_new_ticket(client, "L1", "t1", "th")
        assert {to for _oa, to, _t in pushed} == {"U-cs", "U-owner"}
