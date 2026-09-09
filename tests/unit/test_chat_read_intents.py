"""Reads the model understood and the router dropped (owner, 8 Sep 2026).

Typing "ดูลูกค้า สมชาย" on the Sales OA worked. Typing "ขอดูข้อมูลคุณสมหมาย"
— the same request, phrased the way people actually phrase it — was parsed
correctly, passed the permission gate, and came back "เข้าใจแล้วครับ
ต้องการดูลูกค้า แต่ในแชทยังทำรายการนี้ไม่ได้ ลองใช้แดชบอร์ด หรือแจ้งผู้ดูแล
บริษัท". The deterministic path had the view handlers; the AI-intent path
routed create/update/archive and nothing else.

What must hold now:

  1. A read intent on customer/deal/quote reaches the SAME handler the
     typed trigger reaches — one name lookup, one "not found", one
     "several match" with buttons, never a second implementation.
  2. A name with an honorific ("คุณสมหมาย") finds the person; the record
     holds "สมหมาย".
  3. No name means the list; a code or a name means that record.
  4. The other entities registered in ACTION_PERMISSIONS and never routed:
     ticket, service_report, approval, note, warranty, team, member and
     the shop's own settings.
  5. What genuinely has no handler still says so — and now names the
     dashboard page that does it, with the button when the shop has a LIFF
     id, instead of "ลองใช้แดชบอร์ด" and a dead end.
  6. English says all of it in English.
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
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402

SALES_KEYS = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create",
             "service_report.read", "warranty.read"]
ME = "CHN-S-000001"
NOT_IN_CHAT_TH = "ยังทำรายการนี้ไม่ได้"


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "openrouter_model", "m")


def _crafted(body: dict):
    return httpx.AsyncClient(transport=_ai(json.dumps(body, ensure_ascii=False)))


def _read(entity, **fields):
    return _crafted({"action": "read", "entity": entity, "fields": fields, "missing": []})


async def say(client, message, *, ai, language="th", oa="sales", role="sales"):
    return await handle_chat_message(
        client, message=message,
        ctx=_ctx(oa=oa, primary_role="technician" if oa == "technician" else role),
        language=language, ai_client=ai,
    )


async def _sales(*, names=(("สมหมาย", "ใจดี", "0812345678"),)):
    client = FakeDataClient(permission_keys=list(SALES_KEYS), role="sales")
    client._members = [
        {"id": "member-1", "chann_uid": ME, "role": "sales", "status": "active", "first_name": "พนักงาน"},
        {"id": "m-tech", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active",
         "first_name": "สมศักดิ์"},
    ]
    client._customers_created = []
    for first, last, phone in names:
        client._customers_created.append(
            await client.create_customer("L1", {"first_name": first, "last_name": last, "phone": phone})
        )
    return client


# ------------------------------------------------------- 1. the reported bug

class TestCustomerRead:
    @pytest.mark.parametrize("message, target", [
        ("ขอดูข้อมูลคุณสมหมาย", "คุณสมหมาย"),
        ("ดูข้อมูลลูกค้าสมหมาย", "สมหมาย"),
        ("อยากทราบรายละเอียดของสมหมายหน่อยครับ", "สมหมาย"),
    ])
    async def test_a_named_customer_comes_back_in_full(self, message, target):
        client = await _sales()
        reply = await say(client, message, ai=_read("customer", target_name=target))
        assert "C-2026-0001 · สมหมาย ใจดี" in reply.text
        assert "โทร: 0812345678" in reply.text
        assert NOT_IN_CHAT_TH not in reply.text
        assert reply.entity_type == "customer"

    async def test_the_english_sentence_answers_in_english(self):
        client = await _sales()
        reply = await say(client, "show me Sommai's details",
                          ai=_read("customer", target_name="สมหมาย"), language="en")
        assert "C-2026-0001 · สมหมาย ใจดี" in reply.text
        assert "Phone: 0812345678" in reply.text
        assert "not available in chat yet" not in reply.text

    async def test_a_code_in_the_fields_opens_that_record(self):
        client = await _sales()
        reply = await say(client, "ข้อมูลลูกค้า C-2026-0001",
                          ai=_read("customer", customer_id="C-2026-0001"))
        assert "C-2026-0001 · สมหมาย ใจดี" in reply.text

    async def test_the_typed_form_still_works_and_says_the_same_thing(self):
        """The trigger path is the one the AI path now borrows — they must
        not drift, so both are asserted on the same fixture."""
        client = await _sales()
        typed = await say(client, "ข้อมูลลูกค้า C-2026-0001",
                          ai=_crafted({"action": "suggest", "fields": {}, "missing": []}))
        spoken = await say(client, "ขอดูข้อมูลคุณสมหมาย",
                           ai=_read("customer", target_name="คุณสมหมาย"))
        assert typed.text == spoken.text

    async def test_no_name_at_all_is_the_list(self):
        client = await _sales(names=(("สมหมาย", "ใจดี", "0812345678"),
                                     ("สมชาย", "รักดี", "0899999999")))
        reply = await say(client, "อยากเห็นลูกค้าที่มีอยู่ตอนนี้", ai=_read("customer"))
        assert "C-2026-0001 · สมหมาย ใจดี" in reply.text
        assert "C-2026-0002 · สมชาย รักดี" in reply.text

    async def test_a_name_nobody_has_says_so_instead_of_pretending(self):
        client = await _sales()
        reply = await say(client, "ขอดูข้อมูลคุณสมศรี", ai=_read("customer", target_name="สมศรี"))
        assert reply.text == "ไม่พบลูกค้ารหัส สมศรี"
        assert NOT_IN_CHAT_TH not in reply.text

    async def test_a_name_several_people_share_offers_the_choice(self):
        client = await _sales(names=(("สมหมาย", "ใจดี", "0812345678"),
                                     ("สมชาย", "รักดี", "0899999999")))
        reply = await say(client, "ขอดูข้อมูลคุณสม", ai=_read("customer", target_name="สม"))
        assert "มีลูกค้าหลายคนที่ตรงกับ \"สม\"" in reply.text
        assert "C-2026-0001 สมหมาย ใจดี" in reply.text and "C-2026-0002 สมชาย รักดี" in reply.text
        # Pickable, not just listed (principle 3).
        assert [send for _label, send in reply.quick_replies] == [
            "ข้อมูลลูกค้า C-2026-0001", "ข้อมูลลูกค้า C-2026-0002",
        ]

    @pytest.mark.parametrize("action", ["read", "view", "list", "get", "show", "search", "find"])
    async def test_every_word_the_model_uses_for_show_me_lands_here(self, action):
        client = await _sales()
        ai = _crafted({"action": action, "entity": "customer",
                       "fields": {"target_name": "สมหมาย"}, "missing": []})
        reply = await say(client, "อยากทราบข้อมูลของลูกค้ารายนี้หน่อย", ai=ai)
        assert "สมหมาย ใจดี" in reply.text, action
        assert NOT_IN_CHAT_TH not in reply.text, action

    async def test_reading_a_customer_puts_them_in_context_for_the_next_line(self):
        client = await _sales()
        await say(client, "ขอดูข้อมูลคุณสมหมาย", ai=_read("customer", target_name="คุณสมหมาย"))
        ref = await client.get_last_customer_ref(ME, "sales")
        assert ref and ref["name"] == "สมหมาย ใจดี"


# --------------------------------------------------------------- 2. deals

class TestDealRead:
    async def _with_a_deal(self):
        client = await _sales()
        deal = await client.create_deal("L1", {"contact_id": client._customers_created[0]["id"]})
        return client, deal

    async def test_a_deal_code_opens_the_deal(self):
        client, _deal = await self._with_a_deal()
        reply = await say(client, "ขอดูดีล D-2026-0001", ai=_read("deal", deal_code="D-2026-0001"))
        assert reply.text.startswith("D-2026-0001 ·")
        assert reply.entity_type == "deal"

    async def test_open_deal_in_english(self):
        client, _deal = await self._with_a_deal()
        reply = await say(client, "open deal D-2026-0001",
                          ai=_read("deal", deal_code="D-2026-0001"), language="en")
        assert reply.text.startswith("D-2026-0001 ·")
        assert "not available in chat yet" not in reply.text

    async def test_a_code_only_in_the_sentence_is_still_found(self):
        """The model dropped the code from its fields; the message has it."""
        client, _deal = await self._with_a_deal()
        reply = await say(client, "ขอรายละเอียดของ D-2026-0001 ทั้งหมด", ai=_read("deal"))
        assert reply.text.startswith("D-2026-0001 ·")

    async def test_the_latest_deal_is_the_one_in_context(self):
        client, deal = await self._with_a_deal()
        await client.set_last_entity_ref(ME, "sales", entity_type="deal",
                                         entity_id=deal["id"], code=deal["deal_id"])
        reply = await say(client, "ขอข้อมูลดีลล่าสุดหน่อย", ai=_read("deal"))
        assert reply.text.startswith("D-2026-0001 ·")

    async def test_a_named_customer_narrows_the_list_to_theirs(self):
        client, _deal = await self._with_a_deal()
        # Not "ดีลของ…" — that phrase is caught by the trigger path before
        # the model is asked at all, so it would prove nothing here.
        reply = await say(client, "อยากทราบว่าคุณสมหมายมีอะไรค้างอยู่บ้าง",
                          ai=_read("deal", target_name="คุณสมหมาย"))
        assert "D-2026-0001" in reply.text
        assert NOT_IN_CHAT_TH not in reply.text

    async def test_nothing_named_is_the_deal_list(self):
        client, _deal = await self._with_a_deal()
        reply = await say(client, "อยากเห็นดีลที่มีอยู่ในระบบตอนนี้", ai=_read("deal"))
        assert "D-2026-0001" in reply.text

    async def test_a_deal_code_nobody_has_says_so(self):
        client = await _sales()
        reply = await say(client, "ขอดูดีล D-2026-0009", ai=_read("deal", deal_code="D-2026-0009"))
        assert reply.text == "ไม่พบดีลรหัส D-2026-0009"


# --------------------------------------------------------------- 3. quotes

class TestQuoteRead:
    async def _with_a_quote(self):
        client = await _sales()
        deal = await client.create_deal("L1", {"contact_id": client._customers_created[0]["id"]})
        quote = await client.create_quote("L1", {"deal_id": deal["id"]})
        return client, quote

    async def test_the_latest_quote_is_the_one_in_context(self):
        client, quote = await self._with_a_quote()
        await client.set_last_entity_ref(ME, "sales", entity_type="quote",
                                         entity_id=quote["id"], code=quote["quote_id"])
        reply = await say(client, "ขอดูใบเสนอราคาล่าสุด", ai=_read("quote"))
        assert quote["quote_id"] in reply.text
        assert reply.entity_type == "quote"

    async def test_with_nothing_in_context_the_list_is_the_honest_answer(self):
        client, quote = await self._with_a_quote()
        reply = await say(client, "ขอดูใบเสนอราคาล่าสุด", ai=_read("quote"))
        assert quote["quote_id"] in reply.text
        assert NOT_IN_CHAT_TH not in reply.text

    async def test_a_quote_code_opens_that_quote(self):
        client, quote = await self._with_a_quote()
        reply = await say(client, "ขอดูใบเสนอราคา " + quote["quote_id"],
                          ai=_read("quote", quote_code=quote["quote_id"]))
        assert quote["quote_id"] in reply.text and reply.entity_type == "quote"

    async def test_creating_a_quote_remembers_it_so_the_next_line_can_say_this_one(self):
        client = await _sales()
        deal = await client.create_deal("L1", {"contact_id": client._customers_created[0]["id"]})
        await say(client, "สร้างใบเสนอราคาจากดีล D-2026-0001", ai=_crafted(
            {"action": "create", "entity": "quote", "fields": {"deal_code": deal["deal_id"]}, "missing": []}))
        ref = await client.get_last_entity_ref(ME, "sales")
        assert ref and ref["entity_type"] == "quote"


# ------------------------------------------- 4. the entities never dispatched

class TestTheOtherRegisteredReads:
    async def test_an_approval_queue_question_reaches_the_queue(self):
        """entity="approval" was handled inside _handle_ai_understood_intent
        and never dispatched to it."""
        client = await _sales()
        reply = await say(client, "อยากรู้ว่ามีอะไรรอผมตรวจอยู่บ้าง", ai=_read("approval"))
        assert NOT_IN_CHAT_TH not in reply.text
        assert "รออนุมัติ" in reply.text or "ไม่มีรายงานที่รอคุณอนุมัติ" in reply.text

    async def test_a_notes_question_reaches_the_note_list(self):
        client = await _sales()
        customer = client._customers_created[0]
        await client.create_note("L1", {"entity_type": "customer", "entity_id": customer["id"],
                                        "body": "ลูกค้าขอส่วนลด"}, actor_id=ME)
        reply = await say(client, "อยากเห็นบันทึกของลูกค้ารายนี้",
                          ai=_read("note", entity_code=customer["customer_id"]))
        assert "ลูกค้าขอส่วนลด" in reply.text

    async def test_a_warranty_question_without_a_serial_is_the_shops_book(self):
        client = await _sales()
        client._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์",
                               "status": "active", "warranty_end": "2027-01-01"}]
        reply = await say(client, "อยากเห็นเครื่องที่ร้านลงทะเบียนไว้ทั้งหมด", ai=_read("warranty"))
        assert "SN12345678" in reply.text

    async def test_a_shop_details_question_reads_the_company_card(self):
        client = await _sales()
        reply = await say(client, "อยากเห็นข้อมูลบริษัทที่บันทึกไว้", ai=_read("setting"))
        assert NOT_IN_CHAT_TH not in reply.text
        assert "ชื่อนิติบุคคล" in reply.text or "ยังไม่ระบุ" in reply.text

    async def test_a_technician_roster_question_reads_the_roster(self):
        client = await _sales()
        reply = await say(client, "อยากเห็นช่างที่อยู่ในร้านตอนนี้", ai=_read("member"))
        assert reply.text.startswith("ช่างในร้าน (1 คน):")
        assert NOT_IN_CHAT_TH not in reply.text

    async def test_a_team_question_reads_the_teams(self):
        client = await _sales()
        await client.create_technician_team("L1", "ทีมแอร์")
        reply = await say(client, "อยากเห็นทีมที่ตั้งไว้", ai=_read("team"))
        assert "ทีมแอร์" in reply.text

    async def test_a_technician_asking_about_a_job_gets_the_job(self):
        client = FakeDataClient(permission_keys=list(TECH_KEYS), role="technician")
        client._members = [{"id": "member-1", "chann_uid": ME, "role": "technician", "status": "active"}]
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                            "accept_status": "accepted", "assigned_to_ref": "member-1",
                            "customer_name": "สมหมาย", "service_address": "99/1",
                            "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08",
                            "scheduled_time": "10:00"}]
        reply = await say(client, "อยากเห็นรายละเอียดของงาน T-2026-0001",
                          ai=_read("ticket", code="T-2026-0001"), oa="technician")
        assert "T-2026-0001" in reply.text and NOT_IN_CHAT_TH not in reply.text

    async def test_a_technician_asking_for_their_reports_gets_the_list(self):
        client = FakeDataClient(permission_keys=list(TECH_KEYS), role="technician")
        client._members = [{"id": "member-1", "chann_uid": ME, "role": "technician", "status": "active"}]
        client._reports = [{"id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1",
                            "status": "submitted", "technician_member_id": "member-1",
                            "report_data": {"found_issue": "คอมรั่ว"}}]
        reply = await say(client, "อยากเห็นรายงานที่ผมส่งไปแล้ว",
                          ai=_read("service_report"), oa="technician")
        assert "SR-2026-0001" in reply.text


# ------------------------------------------ 5. what genuinely has no handler

class TestTheHonestReplyIsStillHonest:
    async def test_it_says_no_and_names_the_page_that_does_it(self):
        client = await _sales()
        reply = await say(client, "อยากเปลี่ยนบทบาทของสมาชิกคนนี้", ai=_crafted(
            {"action": "update", "entity": "member", "fields": {}, "missing": []}))
        assert NOT_IN_CHAT_TH in reply.text
        assert "หน้า \"สมาชิกและสิทธิ์\" ในแดชบอร์ด" in reply.text
        # No work claimed and no dead end.
        assert "เรียบร้อยแล้ว" not in reply.text

    async def test_in_english_too(self):
        client = await _sales()
        reply = await say(client, "change this member's role", language="en", ai=_crafted(
            {"action": "update", "entity": "member", "fields": {}, "missing": []}))
        assert "not available in chat yet" in reply.text
        assert '"Members and permissions" page in the dashboard' in reply.text

    async def test_the_button_opens_that_page_when_the_shop_has_a_liff_id(self, monkeypatch):
        monkeypatch.setattr(settings, "liff_sales_id", "1234-abcd")
        client = await _sales()
        reply = await say(client, "อยากเปลี่ยนบทบาทของสมาชิกคนนี้", ai=_crafted(
            {"action": "update", "entity": "member", "fields": {}, "missing": []}))
        label, url = reply.quick_reply_url
        assert label == "เปิดหน้าสมาชิกและสิทธิ์"
        assert url == "https://liff.line.me/1234-abcd/members"

    def test_every_entity_the_model_can_emit_has_a_page_to_point_at(self):
        """A pair with no handler and no page is the dead end this fixed."""
        entities = {entity for _action, entity in chat.ACTION_PERMISSIONS}
        missing = sorted(entities - set(chat.ENTITY_DASHBOARD_PAGE))
        assert not missing, f"no dashboard page named for: {missing}"

    def test_every_named_page_is_a_path_the_link_builder_knows(self):
        unknown = sorted(
            section for section, _label in chat.ENTITY_DASHBOARD_PAGE.values()
            if section not in chat.DASHBOARD_PATHS
        )
        assert not unknown, f"ENTITY_DASHBOARD_PAGE names sections DASHBOARD_PATHS has no path for: {unknown}"


# ------------------------------------- 6. a read on the Customer OA is theirs

class TestACustomerOnlyEverReadsTheirOwn:
    """customer.read and ticket.read are both in scope for the Customer OA
    (spec §6), and both mean "mine". A model labelling a customer's question
    a read must never hand them the shop's contact book or job list."""

    async def _customer(self, keys):
        client = FakeDataClient(permission_keys=list(keys), role="customer")
        client._profiles = {ME: {"first_name": "สมหมาย", "last_name": "ใจดี", "phone": "0812345678"}}
        await client.create_customer("L1", {"first_name": "คนอื่น", "last_name": "ไม่เกี่ยว",
                                            "phone": "0899999999"})
        client._tickets = [{"id": "t9", "ticket_number": "T-2026-0009", "status": "open",
                            "customer_name": "คนอื่น", "service_address": "ลับ 1/1",
                            "issue_description": "แอร์ไม่เย็น"}]
        return client

    async def test_a_read_is_their_own_card_not_the_shops_book(self):
        client = await self._customer(["customer.read"])
        reply = await say(client, "ขอดูข้อมูลหน่อย", ai=_read("customer", target_name="คนอื่น"),
                          oa="customer", role="customer")
        assert "คนอื่น" not in reply.text
        assert "0899999999" not in reply.text

    async def test_a_job_read_never_lists_someone_elses_job(self):
        client = await self._customer(["ticket.read"])
        reply = await say(client, "ขอดูงานหน่อย", ai=_read("ticket"), oa="customer", role="customer")
        assert "T-2026-0009" not in reply.text and "ลับ 1/1" not in reply.text


class TestTheModelDoesNotOutrankTheSentence:
    """Real-model corpus, 9 Sep 2026: the model answers check_in to
    "ยังไม่ถึงหน้างาน" about two times in three. The trigger table has
    refused that since 8 Sep; the AI path executed it anyway."""

    def test_a_negation_disclaims_the_job_step(self):
        for text in (
            "ยังไม่ถึงหน้างาน",
            "ยังไม่ถึงครับ",
            "อย่าเช็คอิน T-2026-0001",
            "ห้ามปิดงานนะ",
            "พรุ่งนี้ค่อยเช็คอิน",
            "ลูกค้าบอกว่าถึงแล้ว",
        ):
            assert chat._disclaims_a_job_action(text) is True, text

    def test_a_real_command_is_not_disclaimed(self):
        for text in (
            "เช็คอิน T-2026-0001",
            "ถึงแล้วครับ",
            "ช่วยเช็คอินให้หน่อยได้ไหมครับ",
            "ปิดงาน T-2026-0001",
            "ฮอดแล้วเด้อ",
        ):
            assert chat._disclaims_a_job_action(text) is False, text
