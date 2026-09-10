"""A sentence that mentions an action must not perform it — everywhere.

The intent guard was added on 9-10 ก.ย. 2569 and wired to twelve call
sites plus the whole AI road. That left roughly twenty DETERMINISTIC
branches writing to the database with no shape check at all, which is the
larger half of the product: measured on the real handlers, only ~5-10% of
messages ever reach the model, so most writes happen on the rule road.

Measured before the fix, with the records present:

    ไม่ต้องยกเลิกใบเสนอราคา Q-2026-0001        -> the quotation was rejected
    ลูกค้ายังไม่ตอบรับใบเสนอราคา Q-2026-0001   -> the quotation was ACCEPTED
    ยังไม่ต้องปิดดีล D-2026-0001 สำเร็จ         -> the deal was closed won
    ลูกค้าบอกว่าปิดดีล D-... สำเร็จแล้วเหรอ     -> the deal was closed won
    ไม่ต้องสร้างดีลให้ สมชาย ใจดี               -> the deal was created
    ไม่ต้องเชิญช่างแล้ว                          -> an invite code was issued
    เพิ่มช่างยังไง                               -> an invite code was issued
    ลูกค้าถามว่าเพิ่มช่างยังไง                   -> an invite code was issued
    สร้างทีมช่างยังไง                            -> a team named "ยังไง" was created
    ไม่ต้องลงทะเบียน SN12345678                  -> the warranty was registered

The second line is the clearest statement of the problem: the sentence
says the customer has NOT accepted, and the record was set to accepted.

Every test below is a PAIR. The negated/questioning form must write
nothing, and the plain command must still write — because the standing
risk in this codebase is the opposite mistake. Twice now a fix for
over-acting has refused real commands ("แอร์ไม่ได้ซ่อมมานาน…" was refused
for containing ไม่; "ช่วยเช็คอินให้หน่อยได้ไหมครับ" got the guide), so a
guard with no positive half is not finished.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402

KEYS = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
# Everything that is not a lookup. Deliberately a deny-list of reads
# rather than an allow-list of writes: a write named something this file
# never anticipated must still show up.
READS = (
    "get_", "list_", "search", "permission_catalog", "authorization_context",
    "resolve", "storefront_browse", "storefront_search", "code_for",
    "line_target_of", "set_last_", "clear_", "save_",
)

# `customer_id` is the C- code, not a UUID — that is what the Data tier
# returns (schemas.py) and what _resolve_entity matches on.
CUSTOMERS = [{
    "id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
    "last_name": "ใจดี", "phone": "0812345678", "stage": "lead",
}]
DEALS = [{
    "id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
    "contact_id": "CUST-1", "notes": None, "products": [],
}]
QUOTES = [{
    "id": "QUOTE-1", "quote_id": "Q-2026-0001", "status": "sent",
    "deal_id": "DEAL-1", "contact_id": "CUST-1", "items": [], "total": "1000.00",
}]


async def _say(message, *, oa="sales"):
    """One message through the whole engine. Returns (reply, writes)."""
    client = FakeDataClient(
        role="sales", permission_keys=KEYS,
        customers=[dict(c) for c in CUSTOMERS],
        deals=[dict(d) for d in DEALS],
        quotes=[dict(q) for q in QUOTES],
    )
    reply = await chat.handle_chat_message(
        client, ctx=_ctx(primary_role="sales", oa=oa), message=message, language="th",
    )
    writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
    return (reply.text or ""), writes, client


# (a sentence that must NOT write, the plain command that must)
PAIRS = [
    ("ไม่ต้องยกเลิกใบเสนอราคา Q-2026-0001", "ยกเลิกใบเสนอราคา Q-2026-0001"),
    ("ลูกค้ายังไม่ตอบรับใบเสนอราคา Q-2026-0001", "ลูกค้าตอบรับใบเสนอราคา Q-2026-0001"),
    ("ยังไม่ต้องให้ส่วนลด Q-2026-0001", "ใบเสนอราคา Q-2026-0001 ส่วนลด 10%"),
    ("ยังไม่ต้องปิดดีล D-2026-0001 สำเร็จ", "ปิดดีล D-2026-0001 สำเร็จ"),
    ("ลูกค้าบอกว่าปิดดีล D-2026-0001 สำเร็จแล้วเหรอ", "ปิดดีล D-2026-0001 สำเร็จ"),
    ("ไม่ต้องสร้างดีลให้ สมชาย ใจดี", "สร้างดีลให้ สมชาย ใจดี"),
    ("ไม่ต้องเชิญช่างแล้ว", "ขอรหัสเชิญช่าง"),
    ("เพิ่มช่างยังไง", "เพิ่มช่าง"),
    ("ลูกค้าถามว่าเพิ่มช่างยังไง", "ขอรหัสเชิญช่าง"),
    ("สร้างทีมช่างยังไง", "สร้างทีมช่าง แอร์"),
    ("ไม่ต้องลงทะเบียน SN12345678", "ลงทะเบียน SN12345678"),
]


class TestASentenceThatDeclinesWritesNothing:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("declined,_command", PAIRS, ids=[p[0] for p in PAIRS])
    async def test_nothing_reaches_the_data_tier(self, declined, _command):
        text, writes, _ = await _say(declined)
        assert writes == [], f"{declined!r} wrote {writes}"
        # And it says what it did not do, rather than going quiet or
        # shrugging — the reply has to teach the command that WOULD work.
        assert text.strip(), "a refusal with no words is worse than the write"
        assert "ยังไม่แน่ใจว่าต้องการอะไร" not in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("_declined,command", PAIRS, ids=[p[1] for p in PAIRS])
    async def test_the_plain_command_still_works(self, _declined, command):
        text, writes, _ = await _say(command)
        assert writes, f"{command!r} went inert — the guard is over-blocking"
        assert "ยังไม่แน่ใจว่าต้องการอะไร" not in text


class TestTheOppositeMistake:
    """The regressions this codebase has actually shipped: a fix for
    over-acting that refused a real command. Each of these contains a word
    the guard looks at and is nonetheless an order."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        # ไม่ inside the stage name, not a negation of the command.
        "ปิดดีล D-2026-0001 ไม่สำเร็จ",
        # A polite request is still a request.
        "ช่วยสร้างดีลให้ สมชาย ใจดี หน่อยครับ",
        "ขอรหัสเชิญช่างหน่อยครับ",
        "รบกวนยกเลิกใบเสนอราคา Q-2026-0001 ให้หน่อย",
    ])
    async def test_it_still_acts(self, message):
        text, writes, _ = await _say(message)
        assert writes, f"{message!r} was refused — over-blocking"


class TestTheRecordIsUntouched:
    """Not just "no call was made" — the row itself must be unchanged,
    which is what the owner actually cares about."""

    @pytest.mark.asyncio
    async def test_a_declined_quote_keeps_its_status(self):
        _, _, client = await _say("ลูกค้ายังไม่ตอบรับใบเสนอราคา Q-2026-0001")
        assert client._quotes[0]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_a_declined_deal_keeps_its_stage(self):
        _, _, client = await _say("ยังไม่ต้องปิดดีล D-2026-0001 สำเร็จ")
        assert client._deals[0]["stage"] == "proposed"

    @pytest.mark.asyncio
    async def test_the_command_does_change_it(self):
        _, _, client = await _say("ปิดดีล D-2026-0001 สำเร็จ")
        assert client._deals[0]["stage"] == "won"


class TestTheJobRoad:
    """Claim, reject, assign and the technician-situation branch. These sit
    on the technician's road, where `_disclaims_a_job_action` covered only
    the check-in/check-out vocabulary and only from `_command_like` —
    `_action_command`, which claim/reject/assign use, never consulted it."""

    TICKET = {
        "id": "t1", "ticket_number": "T-2026-0001", "status": "open",
        "accept_status": "pending", "assigned_to_ref": "member-1",
        "customer_name": "สมชาย", "customer_phone": "0812345678",
        "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
        "scheduled_date": "2026-09-11", "scheduled_time": "10:00",
    }

    @classmethod
    async def _say(cls, message, *, oa="technician", accepted=False):
        from chann_data.permissions import DEFAULT_ROLE_TEMPLATES as T

        keys = sorted(T["technician"] if oa == "technician" else T["admin"])
        client = FakeDataClient(
            permission_keys=keys, role="technician" if oa == "technician" else "sales",
        )
        ticket = dict(cls.TICKET)
        if accepted:
            ticket.update(status="assigned", accept_status="accepted")
        client._tickets = [ticket]
        reply = await chat.handle_chat_message(
            client,
            ctx=_ctx(primary_role="technician" if oa == "technician" else "sales", oa=oa),
            message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,oa", [
        ("ไม่ต้องรับงาน T-2026-0001", "technician"),
        ("ลูกค้าบอกว่าช่างรับงาน T-2026-0001 แล้ว", "sales"),
        ("ไม่ต้องปฏิเสธงาน T-2026-0001", "technician"),
        ("ถ้าฝนตกจะปฏิเสธงาน T-2026-0001", "technician"),
        ("ไม่ต้องมอบหมาย T-2026-0001 ให้ทีม AC Team", "sales"),
    ])
    async def test_a_declining_sentence_does_not_move_the_job(self, message, oa):
        text, writes = await self._say(message, oa=oa)
        assert writes == [], f"{message!r} wrote {writes}"
        assert "ยังไม่ได้" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,oa,call", [
        ("รับงาน T-2026-0001", "technician", "claim_ticket"),
        ("ปฏิเสธงาน T-2026-0001 ติดงานอื่น", "technician", "reject_ticket"),
        ("มอบหมาย T-2026-0001 ให้ทีม AC Team", "sales", "assign_ticket"),
    ])
    async def test_the_real_decision_still_lands(self, message, oa, call):
        _, writes = await self._say(message, oa=oa)
        assert call in writes

    @pytest.mark.asyncio
    async def test_a_decline_phrased_as_a_negation_is_still_a_decline(self):
        """"รับงานไม่ได้" IS the command — the trigger table says so, and
        reading its ไม่ as a refusal would refuse the refusal."""
        text, _ = await self._say("รับงานไม่ได้ครับ ติดงานอื่น")
        assert "ยังไม่ได้ปฏิเสธ" not in text

    @pytest.mark.asyncio
    async def test_what_the_technician_reports_is_filed_as_said(self):
        """The situation branch writes a note AND pushes it to dispatch, so
        a misread is read by other people. "ยังไม่ต้องสั่งอะไหล่" was filed
        as "ต้องสั่งอะไหล่" — the opposite."""
        _, wrote = await self._say("ต้องสั่งอะไหล่", accepted=True)
        assert "create_note" in wrote
        text, held = await self._say("ยังไม่ต้องสั่งอะไหล่", accepted=True)
        assert held == []
        # And a report that genuinely contains ไม่ still files.
        _, still = await self._say("ลูกค้าไม่อยู่ครับ", accepted=True)
        assert "create_note" in still

    @pytest.mark.asyncio
    async def test_asking_how_to_reschedule_does_not_reschedule(self):
        text, writes = await self._say("ขอเลื่อนนัดยังไง", accepted=True)
        assert writes == []
        _, moved = await self._say("ขอเลื่อนนัด T-2026-0001 พรุ่งนี้ 10 โมง", accepted=True)
        assert "update_ticket" in moved


class TestNotes:
    """All three note branches — create, edit, delete — wrote on sentences
    that declined or asked how. The delete one resolved its target from
    context, so "อย่าเพิ่งลบบันทึกนั้น" destroyed the latest note of
    whichever customer was last discussed."""

    @staticmethod
    async def _say(message):
        client = FakeDataClient(
            role="sales", permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS],
        )
        client._notes = [{
            "id": "NOTE-1", "entity_type": "customer", "entity_id": "CUST-1", "body": "เดิม",
        }]
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องบันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด",
        "อย่าบันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด",
        "แก้บันทึกยังไง",
        "อย่าเพิ่งลบบันทึกนั้น",
        "เช่น บันทึกว่า C-2026-0001 สนใจ",
    ])
    async def test_nothing_is_written(self, message):
        _, writes = await self._say(message)
        assert writes == [], f"{message!r} wrote {writes}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,call", [
        ("บันทึกว่า C-2026-0001 ลูกค้าขอส่วนลด", "create_note"),
        ("ลบบันทึก C-2026-0001", "delete_note"),
    ])
    async def test_the_command_still_works(self, message, call):
        _, writes = await self._say(message)
        assert call in writes

    @pytest.mark.asyncio
    async def test_reading_notes_is_not_a_write_and_is_not_guarded(self):
        text, writes = await self._say("ดูบันทึกของ C-2026-0001")
        assert writes == []
        assert "ยังไม่ได้บันทึก" not in text


class TestLinesOnADeal:
    """The three trigger-table line branches. Only the parser path was
    guarded; remove, edit and product-add were not."""

    DEAL = {
        "id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
        "contact_id": "CUST-1", "notes": None,
        "products": [{"id": "dp-4", "product_name": "พัดลม", "qty": 2, "quoted_unit_price": "1500"}],
    }

    @classmethod
    async def _say(cls, message):
        client = FakeDataClient(role="sales", permission_keys=KEYS, deals=[dict(cls.DEAL)])
        client._last_entity_ref = {
            "entity_type": "deal", "entity_id": "DEAL-1", "code": "D-2026-0001", "extra": None,
        }
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if "deal_product" in c[0]]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องลบสินค้าพัดลม",
        "เดี๋ยวค่อยลบสินค้าพัดลม",
        "ไม่ต้องแก้ราคาพัดลมเหลือ 1400",
        "ไม่ต้องเพิ่มสินค้า ทีวี 40 นิ้ว ราคา 4000",
        "เมื่อวานเพิ่มสินค้า ทีวี 40 นิ้ว ราคา 4000 ไปแล้ว",
        "เช่น ลบสินค้าพัดลม",
    ])
    async def test_the_lines_are_untouched(self, message):
        _, writes = await self._say(message)
        assert writes == [], f"{message!r} wrote {writes}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,call", [
        ("ลบสินค้าพัดลม", "remove_deal_product"),
        ("แก้ราคาพัดลมเหลือ 1400", "update_deal_product"),
        ("เพิ่มสินค้า ทีวี 40 นิ้ว ราคา 4000", "add_deal_product"),
    ])
    async def test_the_command_still_edits_the_deal(self, message, call):
        _, writes = await self._say(message)
        assert call in writes

    @pytest.mark.asyncio
    async def test_a_mid_sentence_example_word_is_not_an_example(self):
        """"เช่น" only means "for example" at the FRONT. "เพิ่มสินค้า เช่น
        พัดลม ราคา 1200" is an order."""
        _, writes = await self._say("เพิ่มสินค้า เช่น พัดลม ราคา 1200")
        assert writes, "a mid-sentence เช่น refused a real order"


class TestAStatusQuestionIsAnswered:
    """Owner: "สร้างใบเสนอราคาไปหรือยัง" must CHECK the status, not create.

    Not creating it was already right. But the answer was rendered from
    the sentence alone — "ยังไม่ได้สร้างใบเสนอราคา" with no lookup of any
    kind — so a shop whose quotation had already gone out was told the
    opposite of the truth. A confident wrong answer is worse than the
    write it replaced.
    """

    QUOTE = [{
        "id": "QUOTE-1", "quote_id": "Q-2026-0001", "status": "sent",
        "deal_id": "DEAL-1", "contact_id": "CUST-1", "items": [], "total": "1000.00",
    }]

    @staticmethod
    async def _ask(message, quotes):
        client = FakeDataClient(
            role="sales", permission_keys=KEYS,
            deals=[dict(d) for d in DEALS], quotes=[dict(q) for q in quotes],
        )
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"),
            message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "สร้างใบเสนอราคา D-2026-0001 ไปหรือยัง",
        "ออกใบเสนอราคา Q-2026-0001 ไปหรือยัง",
    ])
    async def test_it_says_yes_when_the_quotation_exists(self, message):
        text, writes = await self._ask(message, self.QUOTE)
        assert writes == []
        assert "Q-2026-0001" in text
        assert "ยังไม่ได้" not in text, "asserted 'not yet' about a quotation that exists"

    @pytest.mark.asyncio
    async def test_it_says_not_yet_when_there_is_none(self):
        text, writes = await self._ask("สร้างใบเสนอราคา D-2026-0001 ไปหรือยัง", [])
        assert writes == []
        assert "ยังไม่ได้" in text
        # And it teaches the command that would do it.
        assert "สร้างใบเสนอราคา" in text

    @pytest.mark.asyncio
    async def test_the_command_itself_still_creates(self):
        text, writes = await self._ask("สร้างใบเสนอราคา D-2026-0001", [])
        assert "create_quote" in writes


class TestTheShopAndTheCustomersOwnScreen:
    """Company details, the bulk paste, shop settings, the customer's own
    profile, and opening a live chat. The company and bulk entries had
    their guard VOCABULARY written when the guard was built and their call
    sites were never added — the table said they were covered and grep for
    the action name returned nothing."""

    @staticmethod
    async def _sales(message):
        client = FakeDataClient(role="sales", permission_keys=KEYS)
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @staticmethod
    async def _customer(message):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="customer", oa="customer"),
            message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ยังไม่ต้องตั้งเลขผู้เสียภาษี 0105551234567",
        "ไม่ต้องแก้ข้อมูลบริษัท ชื่อนิติบุคคล บริษัท ก จำกัด",
        "ตั้งที่อยู่บริษัทยังไง",
    ])
    async def test_company_details_are_not_changed(self, message):
        _, writes = await self._sales(message)
        assert "update_company_profile" not in writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ตั้งเลขผู้เสียภาษี 0105551234567",
        "ตั้งที่อยู่บริษัท 99/1 ถนนสุขุมวิท",
    ])
    async def test_the_company_command_still_saves(self, message):
        _, writes = await self._sales(message)
        assert "update_company_profile" in writes

    @pytest.mark.asyncio
    async def test_a_declined_bulk_paste_creates_nobody(self):
        paste = "เพิ่มลูกค้า สมชาย ใจดี 0812345678; สมหญิง รักดี 0898765432"
        _, wrote = await self._sales(paste)
        assert wrote.count("create_customer") == 2
        _, held = await self._sales("ไม่ต้อง" + paste)
        assert held == []

    @pytest.mark.asyncio
    async def test_asking_how_a_setting_works_does_not_set_it(self):
        """A shop-wide switch, flipped by a question. The number was pulled
        with a bare re.search that ignored the trailing "ได้ยังไงครับ"."""
        _, wrote = await self._sales("ตั้งค่าลบ lead อัตโนมัติ 90 วัน")
        assert "put_license_setting" in wrote
        _, held = await self._sales("ตั้งค่าลบ lead อัตโนมัติ 90 วัน ได้ยังไงครับ")
        assert "put_license_setting" not in held

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["ไม่ต้องคุยกับร้าน", "คุยกับร้านยังไง"])
    async def test_no_conversation_is_opened_behind_the_customer(self, message):
        """Opening one pushes a notification to every agent at the shop."""
        _, writes = await self._customer(message)
        assert "open_chat_session" not in writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "คุยกับร้าน",
        "คุยกับร้าน แอร์เสียครับ",
        "ขอคุยกับพนักงาน",
        # An unhappy customer reaches a person whatever shape the sentence
        # takes. This branch is deliberately generous and stays that way.
        "บริการแย่มาก",
        "แอดมินอยู่ไหม",
        "ขอคุยกับคนจริงๆ",
    ])
    async def test_someone_who_wants_a_person_still_gets_one(self, message):
        _, writes = await self._customer(message)
        assert "open_chat_session" in writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "เบอร์ 0812345678 ใช่ไหม",
        "ชื่อ สมชาย ใจดี หรือเปล่า",
    ])
    async def test_a_question_about_your_details_does_not_set_them(self, message):
        """It stored the phone as "0812345678 ใช่ไหม" — the question
        particle written into the record."""
        text, writes = await self._sales(message)
        assert "update_profile" not in writes
        assert "ใช่ไหม" not in text.replace("ใช่ไหมครับ", "")


class TestTheGuardsOwnBugs:
    """Two faults inside intent_guard itself, exposed by wiring it to more
    branches. Both would have mis-answered the branches it ALREADY guarded;
    they were simply never sent a sentence of the right shape."""

    def test_a_report_containing_thai_do_not_is_not_a_why_question(self):
        """"ทำไม" is "why", and it is also the first four characters of
        "ทำไม่จบ" — "did not finish". A technician's status report was read
        as a how-to question and nothing was filed."""
        from chann_app.services.intent_guard import intent_to_act

        situation = tuple(w for _kind, words in chat._SITUATION_WORDS for w in words)
        for message in ("ต้องสั่งอะไหล่ครับ วันนี้ทำไม่จบ", "วันนี้ทำไม่เสร็จ ต้องมาต่อพรุ่งนี้"):
            assert intent_to_act(message, action="job_situation", triggers=situation).acts
        # And a real "why" is still a question.
        assert not intent_to_act(
            "ทำไมถึงต้องสั่งอะไหล่", action="job_situation", triggers=situation,
        ).acts

    def test_an_abandon_word_that_is_the_command_is_the_command(self):
        """A customer calls off a visit by typing "ยกเลิกค่ะ". "ยกเลิก" is
        both this handler's trigger and a word for "never mind", and the
        abandon rule ran before the imperative exemption — so the one
        sentence that means cancel was read as "forget it"."""
        from chann_app.services.intent_guard import intent_to_act

        triggers = chat.CUSTOMER_CANCEL_TRIGGERS + chat.CUSTOMER_CANCEL_PHRASES
        assert intent_to_act("ยกเลิกค่ะ", action="ticket_cancel", triggers=triggers).acts
        # The negation of it is still a negation.
        assert not intent_to_act(
            "ไม่ต้องยกเลิกนัด", action="ticket_cancel", triggers=triggers,
        ).acts
        # And a genuine abandonment, with no trigger in it, still holds.
        assert not intent_to_act("หยุดก่อน", action="ticket_cancel", triggers=triggers).acts

    def test_an_unknown_action_is_loud_rather_than_open(self):
        """The guard used to return ACT for an action it had no words for,
        so a call site could look protected and do nothing. That is how
        twenty-odd writing branches were unguarded while a table implied
        otherwise."""
        from chann_app.services.intent_guard import intent_to_act

        with pytest.raises(KeyError):
            intent_to_act("ไม่ต้องทำอะไร", action="an_action_nobody_declared")
        # Passing the handler's own triggers is the supported way to guard
        # a branch whose vocabulary lives with the handler.
        assert not intent_to_act(
            "ไม่ต้องทำอะไรเลย", action="an_action_nobody_declared", triggers=("ทำอะไร",),
        ).acts


class TestApprovingAReport:
    """The most dangerous pair in the product. Approving flips the report,
    issues the customer's PDF and fires the satisfaction survey — and the
    two triggers are near-anagrams: APPROVAL_REJECT_TRIGGERS holds
    "ไม่อนุมัติ", which is NOT a substring of "ไม่ต้องอนุมัติ". So the reject
    test missed, the approve test's bare "อนุมัติ" hit, and a refusal to
    approve approved the report."""

    @staticmethod
    async def _say(message):
        client = FakeDataClient(role="sales", permission_keys=KEYS)
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องอนุมัติ SR-2026-0001",
        "ยังไม่ต้องอนุมัติรายงาน SR-2026-0001",
        "ยังไม่ต้องตีกลับ SR-2026-0001 รูปไม่ครบ",
        "เช่น พิมพ์ว่า อนุมัติ SR-2026-0001",
        "อนุมัติยังไง",
    ])
    async def test_the_report_is_not_acted_on(self, message):
        _, writes = await self._say(message)
        assert "act_on_approval_step" not in writes

    def test_both_real_decisions_survive_the_guard(self):
        """The reject command is itself spelled with ไม่ — refusing IT would
        be the other mistake."""
        from chann_app.services.intent_guard import intent_to_act

        triggers = chat.APPROVAL_REJECT_TRIGGERS + chat.APPROVAL_APPROVE_TRIGGERS
        for message in ("อนุมัติ SR-2026-0001", "ไม่อนุมัติ SR-2026-0001 รูปไม่ครบ",
                        "ตีกลับ SR-2026-0001 รูปไม่ครบ"):
            assert intent_to_act(message, action="approval_act", triggers=triggers).acts


class TestIssuingDocuments:
    """Issuing builds a PDF and sends it to the customer; publishing a
    template changes every document the shop issues from then on."""

    @staticmethod
    async def _say(message):
        client = FakeDataClient(role="sales", permission_keys=KEYS)
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องออกเอกสาร Q-2026-0001",
        "เช่น พิมพ์ว่า ออกเอกสาร Q-2026-0001",
        "ไม่ต้องออกรายงาน SR-2026-0001",
        # With no code named, the handler picks the person's only approved
        # report and issues it — so a bare how-do-I sent a customer a PDF.
        "ออกรายงานยังไง",
    ])
    async def test_no_document_is_built(self, message):
        _, writes = await self._say(message)
        assert not [w for w in writes if "document" in w]

    def test_publishing_a_template_needs_more_than_the_word(self):
        """_template_draft_decision matches by CONTAINMENT, and its drop
        list only knows refusals spelled ไม่เอา/ไม่ใช้/ยกเลิก/ทิ้ง — which
        "ไม่ต้องใช้" is not, so it fell through to "ใช้แบบนี้" and
        published."""
        from chann_app.services.intent_guard import intent_to_act

        triggers = tuple(chat._TEMPLATE_YES_CONTAINS) + tuple(chat._TEMPLATE_YES_WORDS)
        for message in ("ยังไม่ต้องเผยแพร่", "อย่าเพิ่งเผยแพร่", "เผยแพร่ยังไง",
                        "ถ้าลูกค้าโอเคค่อยใช้แบบนี้", "ลูกค้าบอกว่าจะใช้แบบนี้"):
            assert not intent_to_act(
                message, action="template_publish", triggers=triggers,
            ).acts, message
        for message in ("ใช้เลย", "เผยแพร่", "ใช้แบบนี้"):
            assert intent_to_act(
                message, action="template_publish", triggers=triggers,
            ).acts, message


class TestACommandSpelledWithANegation:
    """Some commands ARE negations. "SR-2026-0001 ไม่ผ่าน" rejects a
    report, "รับงานไม่ได้" declines a job, "ปิดดีล … ไม่สำเร็จ" closes one
    as lost. Each lives in its handler's trigger tuple, and reading its ไม่
    as a refusal refuses the refusal.

    The guard already had an escape for a trigger at the FRONT of the
    sentence. These name the record first, so it did not apply."""

    def test_a_negation_inside_a_trigger_is_not_a_negation(self):
        from chann_app.services.intent_guard import intent_to_act

        approval = chat.APPROVAL_REJECT_TRIGGERS + chat.APPROVAL_APPROVE_TRIGGERS
        for message in ("SR-2026-0001 ไม่ผ่าน", "SR-2026-0001 ไม่ให้ผ่าน",
                        "ไม่อนุมัติ SR-2026-0001 รูปไม่ครบ"):
            assert intent_to_act(
                message, action="approval_act", triggers=approval,
            ).acts, message
        for message in ("T-2026-0001 รับงานไม่ได้", "รับงานไม่ได้ครับ"):
            assert intent_to_act(
                message, action="job_reject", triggers=chat.TICKET_REJECT_TRIGGERS,
            ).acts, message

    def test_and_a_real_negation_of_it_still_holds(self):
        from chann_app.services.intent_guard import intent_to_act

        approval = chat.APPROVAL_REJECT_TRIGGERS + chat.APPROVAL_APPROVE_TRIGGERS
        for message in ("ไม่ต้องอนุมัติ SR-2026-0001",
                        "ยังไม่ต้องตีกลับ SR-2026-0001 รูปไม่ครบ"):
            assert not intent_to_act(
                message, action="approval_act", triggers=approval,
            ).acts, message
        assert not intent_to_act(
            "ไม่ต้องปฏิเสธงาน T-2026-0001", action="job_reject",
            triggers=chat.TICKET_REJECT_TRIGGERS,
        ).acts


class TestSubstringMatchersThatWroteGarbage:
    """Two matchers wrote real rows out of the wrong words. Found by the
    near-miss sweep of 188 shorter-form commands (10 ก.ย. 2569); both are
    the same root cause as the guard work — a substring test standing in
    for reading the sentence."""

    @staticmethod
    async def _say(message):
        client = FakeDataClient(
            role="sales", permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS],
        )
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [(c[0], c[2:]) for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    async def test_a_sales_team_is_not_a_technician_team_called_that(self):
        """The trailing space in "สร้างทีม " requires a separator, and
        `.strip()` threw it away — so "สร้างทีมขาย" ("create a sales team",
        which this product does not have) created a TECHNICIAN team named
        "ขาย"."""
        _, writes = await self._say("สร้างทีมขาย")
        assert not [w for w, _ in writes if w == "create_technician_team"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["สร้างทีมช่าง แอร์", "สร้างทีม แอร์"])
    async def test_the_real_command_still_creates_the_team(self, message):
        _, writes = await self._say(message)
        made = [args for w, args in writes if w == "create_technician_team"]
        assert made and made[0][0] == "แอร์"

    @pytest.mark.asyncio
    async def test_the_word_notes_is_not_the_command_note(self):
        """"note" alone matched inside "notes C-2026-0001" and saved a note
        whose body was the leftover "s"."""
        _, writes = await self._say("notes C-2026-0001")
        assert not [w for w, _ in writes if w == "create_note"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "บันทึกว่า C-2026-0001 สนใจรุ่นใหม่",
        "note that C-2026-0001 wants a discount",
    ])
    async def test_a_real_note_command_still_saves(self, message):
        _, writes = await self._say(message)
        assert [w for w, _ in writes if w == "create_note"]


class TestAHoldIsNotAnAnswer:
    """A guard that holds is saying "this is not my branch", not "I have
    dealt with the message". Where a sibling branch can still handle the
    sentence, the hold must fall through to it.

    "ไม่ได้จะยกเลิกงาน ขอแค่เลื่อนเป็นวันอาทิตย์" says both halves out
    loud: not a cancellation, and a reschedule. Returning the refusal at
    the cancel branch throws the second half away."""

    @staticmethod
    async def _customer(message):
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [{
            "id": "t1", "ticket_number": "T-2026-0001", "status": "open",
            "customer_chann_uid": "CHN-S-000001", "customer_name": "สมชาย",
            "issue_description": "แอร์ไม่เย็น",
            "scheduled_date": "2026-09-11", "scheduled_time": "10:00",
        }]
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="customer", oa="customer"),
            message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if not c[0].startswith(READS)]
        return (reply.text or ""), writes

    @pytest.mark.asyncio
    async def test_a_reschedule_is_not_swallowed_by_the_cancel_guard(self):
        text, _ = await self._customer("ไม่ได้จะยกเลิกงาน ขอแค่เลื่อนเป็นวันอาทิตย์")
        # It must not end at the cancel refusal — the sentence asked for
        # something, and something has to receive it.
        assert "ยังไม่ได้ยกเลิกงาน" not in text

    @pytest.mark.asyncio
    async def test_a_plain_reschedule_still_moves_the_visit(self):
        _, writes = await self._customer("ขอเลื่อนนัดเป็นวันอาทิตย์")
        assert "update_ticket" in writes

    @pytest.mark.asyncio
    async def test_and_the_plain_refusal_still_refuses(self):
        text, writes = await self._customer("ไม่ต้องยกเลิกนัด")
        assert writes == []
        assert "ยังไม่ได้ยกเลิกงาน" in text


class TestTheDoorsTheFirstPassLeftOpen:
    """Found by re-running the sweep adversarially against the fixed code.
    Five leaks survived, in three families — and two of them escaped
    through the first pass's own exclusions rather than through a gap."""

    @staticmethod
    async def _note(message):
        client = FakeDataClient(
            role="sales", permission_keys=KEYS, customers=[dict(c) for c in CUSTOMERS],
        )
        client._notes = [{
            "id": "NOTE-1", "entity_type": "customer", "entity_id": "CUST-1", "body": "เดิม",
        }]
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"), message=message, language="th",
        )
        writes = [c[0] for c in client.recorded if c[0].endswith("_note")]
        return (reply.text or ""), writes, client

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        # "บันทึกของ" is a LIST trigger, and the first pass skipped the
        # guard for anything that matched one — so an edit or a delete
        # phrased with "ของ" walked straight through. The how-to form
        # overwrote the note with the body "ของ  ยังไง".
        "แก้บันทึกของ C-2026-0001 ยังไง",
        "ไม่ต้องลบบันทึกของ C-2026-0001",
        "อย่าเพิ่งลบบันทึกของลูกค้ารายนี้",
    ])
    async def test_a_list_word_in_the_sentence_is_not_a_way_past(self, message):
        _, writes, client = await self._note(message)
        assert writes == [], f"{message!r} wrote {writes}"
        assert client._notes[0]["body"] == "เดิม"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,call", [
        ("แก้บันทึกของ C-2026-0001 เป็น ลูกค้าขอส่วนลด 10%", "update_note"),
        ("ลบบันทึกของ C-2026-0001", "delete_note"),
        ("บันทึกว่า C-2026-0001 สนใจรุ่นใหม่", "create_note"),
    ])
    async def test_the_same_words_as_a_command_still_work(self, message, call):
        _, writes, _ = await self._note(message)
        assert call in writes

    @pytest.mark.asyncio
    async def test_reading_notes_is_still_not_guarded(self):
        text, writes, _ = await self._note("ดูบันทึกของ C-2026-0001")
        assert writes == []
        assert "ยังไม่ได้บันทึก" not in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องลงทะเบียน ONLY00001",
        "ลงทะเบียนยังไง ONLY00001",
    ])
    async def test_a_customer_waving_off_registration_registers_nothing(self, message):
        """Both OAs arrive at one function and only the SALES half was
        guarded. A customer declining the registration prompt the welcome
        message pushes at them claimed the unit."""
        client = FakeDataClient(role="customer", permission_keys=[])
        client._warranties = [{
            "id": "w-1", "serial_number": "ONLY00001", "product_name": "แอร์",
            "product_id": "prod-1", "warranty_end": "2027-01-01",
            "status": "active", "customer_chann_uid": None,
        }]
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="customer", oa="customer"),
            message=message, language="th",
        )
        assert "claim_warranty" not in [c[0] for c in client.recorded]
        assert (reply.text or "").strip()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["ลงทะเบียน ONLY00001", "ONLY00001"])
    async def test_a_customer_who_means_it_still_registers(self, message):
        client = FakeDataClient(role="customer", permission_keys=[])
        client._warranties = [{
            "id": "w-1", "serial_number": "ONLY00001", "product_name": "แอร์",
            "product_id": "prod-1", "warranty_end": "2027-01-01",
            "status": "active", "customer_chann_uid": None,
        }]
        await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="customer", oa="customer"),
            message=message, language="th",
        )
        assert "claim_warranty" in [c[0] for c in client.recorded]

    def test_the_model_road_can_reach_notes_and_is_guarded_there(self):
        """The model can answer update/delete on entity=note for a sentence
        with no note word in it. The generic record_delete fallback LOOKED
        like cover and was inert: with no note word to find, intent_to_act
        had nothing to negate and returned ACT."""
        for action in ("create", "update", "delete"):
            assert chat._AI_GUARDED[("note", action)] == "note_write"
