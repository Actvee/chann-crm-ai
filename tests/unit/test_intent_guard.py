"""The one decision that stands in front of every write.

Written from the 9-10 Sep 2026 review (B01-B04, B07): 68 scenarios failed,
and 53 of them were one mistake wearing five faces — a sentence that
mentions an action performed it. Each case below is a sentence from that
review or its mirror image, and the mirror images matter as much: a guard
that refuses "ยกเลิกนัด C-2026-0001" has not fixed anything, it has moved
the damage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for part in ("application", "data"):
    if str(ROOT / part) not in sys.path:
        sys.path.insert(0, str(ROOT / part))

from chann_app.services import chat  # noqa: E402
from chann_app.services.intent_guard import (  # noqa: E402
    ACT, ASK, HOLD, Verdict, intent_to_act,
)


def verdict(message: str, action: str) -> Verdict:
    """What the chat itself asks, triggers and all."""
    return intent_to_act(
        message, action=action, triggers=chat._guard_triggers(action),
        canonical=chat._canonical(message),
    )


# --------------------------------------------------------------------------
# B01 — appointments. "ไม่ต้องยกเลิกนัด C-2026-0001" cancelled it.
# --------------------------------------------------------------------------
HELD_APPOINTMENTS = [
    ("ไม่ต้องยกเลิกนัด C-2026-0001", "appointment_cancel", "negated"),
    ("อย่าลบนัด C-2026-0001", "appointment_delete", "negated"),
    ("อย่าเลื่อนนัด C-2026-0001", "appointment_move", "negated"),
    ("ยังไม่เปลี่ยนเวลานัดเป็น 16:00", "appointment_move", "negated"),
    ("ยกเลิกนัด C-2026-0001 ไปหรือยัง", "appointment_cancel", "status"),
    ("ลบนัดไปแล้วหรือยัง", "appointment_delete", "status"),
    ("นัด C-2026-0001 ยังอยู่ใช่ไหม", "appointment_cancel", "status"),
    ("ถ้าจะเลื่อนนัดเป็น 16:00 ต้องทำอย่างไร", "appointment_move", "conditional"),
]
ACTED_APPOINTMENTS = [
    ("ยกเลิกนัด C-2026-0001", "appointment_cancel"),
    ("ยกเลิกเตือน C-2026-0011", "appointment_cancel"),
    ("ลบนัด C-2026-0001", "appointment_delete"),
    ("รบกวนยกเลิกนัดให้ที", "appointment_cancel"),
    ("ช่วยยกเลิกนัดของ C-2026-0001 ให้หน่อยได้ไหมครับ", "appointment_cancel"),
    ("เลื่อนนัด C-2026-0001 เป็น 16:00", "appointment_move"),
    ("เปลี่ยนเวลาเป็น 13.00", "appointment_move"),
    ("ตั้งนัด C-2026-0001 พรุ่งนี้ บ่าย 2", "appointment_create"),
    # A cancel spelled as a negation: the handler's own trigger, so still a command.
    ("ไม่ต้องเตือนเรื่องสมชายแล้ว", "appointment_cancel"),
]


@pytest.mark.parametrize("message,action,reason", HELD_APPOINTMENTS)
def test_an_appointment_is_not_touched_by_a_sentence_that_only_mentions_it(message, action, reason):
    got = verdict(message, action)
    assert got.outcome == HOLD, f"{message!r} would still write"
    assert got.reason == reason


@pytest.mark.parametrize("message,action", ACTED_APPOINTMENTS)
def test_the_appointment_commands_still_go_through(message, action):
    assert verdict(message, action).outcome == ACT, f"{message!r} was refused"


# --------------------------------------------------------------------------
# B02 — quotations. All twelve review cases issued a real Q-2026-0001.
# --------------------------------------------------------------------------
HELD_QUOTES = [
    ("ยังไม่สร้างใบเสนอราคา D-2026-0001", "negated"),
    ("อย่าเพิ่งสร้างใบเสนอราคา D-2026-0001", "negated"),
    ("ไม่ต้องออกใบเสนอราคา D-2026-0001", "negated"),
    ("สร้างใบเสนอราคา D-2026-0001 ไปหรือยัง", "status"),
    ("ถ้าจะสร้างใบเสนอราคา D-2026-0001 ต้องทำอย่างไร", "conditional"),
    ("แค่ถามวิธีสร้างใบเสนอราคา D-2026-0001", "howto"),
]


@pytest.mark.parametrize("message,reason", HELD_QUOTES)
def test_no_quotation_is_drafted_from_a_question_about_one(message, reason):
    got = verdict(message, "quote_create")
    assert got.outcome == HOLD
    assert got.reason == reason


@pytest.mark.parametrize("message", [
    "สร้างใบเสนอราคา D-2026-0001",
    "ออกใบเสนอราคาให้ดีลนี้หน่อย",
    "ขอใบเสนอราคา D-2026-0001 ครับ",
    "ช่วยทำใบเสนอราคา D-2026-0001 ให้ที",
])
def test_the_quotation_commands_still_go_through(message):
    assert verdict(message, "quote_create").outcome == ACT


# --------------------------------------------------------------------------
# B03 — check-in. The 8-9 Sep phrase list let these through.
# --------------------------------------------------------------------------
HELD_CHECKIN = [
    ("ยังไม่ถึงหน้างาน", "negated"),
    ("ไม่ถึงหน้างาน", "negated"),
    ("ไม่ใช่ว่าผมถึงหน้างานแล้ว", "negated"),
    ("ไม่ต้องเช็คอิน", "negated"),
    ("อย่าเช็คอิน", "negated"),
    ("ห้ามเช็คอิน", "negated"),
    ("เช็คอินต้องทำอย่างไร", "howto"),
    ("ทำไมต้องเช็คอิน", "howto"),
    ("เช็คอินไปหรือยัง", "status"),
    ("ถ้าถึงแล้วค่อยเช็คอิน", "conditional"),
    ("พรุ่งนี้จะเช็คอิน", "later"),
    ("ลูกค้าบอกว่าเช็คอินแล้ว", "reported"),
]


@pytest.mark.parametrize("message,reason", HELD_CHECKIN)
def test_a_job_is_not_checked_in_by_a_sentence_about_checking_in(message, reason):
    got = verdict(message, "check_in")
    assert got.outcome == HOLD
    assert got.reason == reason


@pytest.mark.parametrize("message", [
    "เช็คอิน",
    "เช็คอิน T-2026-0001",
    "ถึงหน้างานแล้วครับ",
    # The 9 Sep fix: a polite order is not an enquiry, question mark or not.
    "ช่วยเช็คอินให้หน่อยได้ไหมครับ",
    "รบกวนเช็คอินให้ที",
])
def test_the_check_in_commands_still_go_through(message):
    assert verdict(message, "check_in").outcome == ACT


def test_a_question_mark_does_not_turn_a_polite_order_into_a_question():
    assert verdict("ช่วยเช็คอินให้หน่อยได้ไหมครับ?", "check_in").outcome == ACT


def test_the_polite_clause_need_not_be_the_first_one():
    # "แอร์ไม่เย็นเลยครับ รบกวนช่วยส่งช่างมาดูให้หน่อยได้ไหมครับ" states the
    # fault, then asks for the visit.
    assert verdict(
        "แอร์ไม่เย็นเลยครับ รบกวนช่วยส่งช่างมาดูให้หน่อยได้ไหมครับ", "ticket_open",
    ).outcome == ACT


# --------------------------------------------------------------------------
# B04 — repair tickets. Eight customer sentences opened a real job.
# --------------------------------------------------------------------------
HELD_TICKETS = [
    ("อย่าเพิ่งเปิดงานซ่อม", "negated"),
    ("ยังไม่ขอนัดช่างนะครับ", "negated"),
    ("ไม่ต้องแจ้งซ่อมให้ผมนะ", "negated"),
    ("ถ้าแอร์เสียจะมาแจ้งอีกที", "conditional"),
    ("ทดสอบระบบ คำว่า แอร์เสีย", "example"),
    ("ผมพิมพ์ว่าแอร์เสียเป็นตัวอย่างเฉยๆ", "example"),
    ("ถ้าผมพิมพ์ว่าแอร์ไม่เย็น จะเกิดอะไรขึ้น", "example"),
    ("แอร์ซ่อมแล้ว ใช้ได้ปกติ", "resolved"),
    ("ตอนนี้แอร์ไม่เสียแล้ว", "resolved"),
    ("ช่างบอกว่าแอร์ไม่ได้เสีย", "reported"),
]


@pytest.mark.parametrize("message,reason", HELD_TICKETS)
def test_no_repair_job_is_opened_by_a_sentence_that_refuses_or_quotes_one(message, reason):
    got = verdict(message, "ticket_open")
    assert got.outcome == HOLD
    assert got.reason == reason


@pytest.mark.parametrize("message", [
    "แอร์ไม่เย็น",
    "แอร์เสียครับ",
    "แอร์ 12000 BTU ไม่เย็น",          # B07: a fault with a spec is a fault
    "เครื่องซักผ้าไม่ปั่น",
    "รบกวนช่างมาดูแอร์ให้หน่อยได้ไหมคะ",
    "ขอนัดช่างมาล้างแอร์ได้มั้ยคะ",
    # A negation about the PAST is a complaint, not a refusal.
    "แอร์ไม่ได้ซ่อมมานาน อยากให้ช่างมาดู",
])
def test_a_real_fault_still_opens_a_job(message):
    assert verdict(message, "ticket_open").outcome == ACT


def test_a_symptom_that_starts_with_the_negative_is_not_a_negated_action():
    """"ไม่เย็น", "ไม่ทำงาน", "ไม่ติด" are what a broken machine does."""
    for message in ("แอร์ไม่เย็น", "พัดลมไม่หมุน", "ทีวีไม่มีภาพ", "เตาไม่ติด"):
        assert verdict(message, "ticket_open").outcome == ACT, message


# --------------------------------------------------------------------------
# Abandoning an exchange that is waiting for one more answer.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("message", [
    "ยกเลิก", "ขอยกเลิกก่อนครับ", "หยุดก่อน", "พักไว้ก่อน", "เดี๋ยวค่อยทำ",
    "ยังไม่เพิ่มลูกค้านะ", "never mind", "cancel",
])
def test_a_flow_in_progress_can_be_dropped_in_the_words_people_use(message):
    assert verdict(message, "pending_flow").outcome == HOLD


@pytest.mark.parametrize("message", [
    "0812345678",
    "พรุ่งนี้",            # an ANSWER to "which day?", never an abandonment
    "ยังไม่รู้เบอร์",        # still answering, just without the number
    "สมชาย ใจดี",
])
def test_an_answer_to_the_open_question_is_not_an_abandonment(message):
    assert verdict(message, "pending_flow").outcome == ACT


# --------------------------------------------------------------------------
# The third answer: ask, rather than guess either way.
# --------------------------------------------------------------------------
def test_a_sentence_that_is_equally_an_order_and_a_question_is_asked_about():
    got = verdict("นัด C-2026-0001 ยกเลิกได้ไหม", "appointment_cancel")
    assert got.outcome == ASK


def test_the_question_it_asks_names_the_record():
    reply = chat._intent_guard_reply(
        "นัด C-2026-0001 ยกเลิกได้ไหม", action="appointment_cancel", language="th",
    )
    assert reply is not None
    assert "C-2026-0001" in reply.text
    assert reply.text.endswith('พิมพ์ "ยกเลิกนัด C-2026-0001"')


# --------------------------------------------------------------------------
# The wording of the refusal.
# --------------------------------------------------------------------------
def test_a_held_sentence_is_answered_with_the_command_that_would_do_it():
    reply = chat._intent_guard_reply(
        "ไม่ต้องยกเลิกนัด C-2026-0001", action="appointment_cancel", language="th",
    )
    assert reply is not None
    assert "ยังไม่ได้ยกเลิกนัดของ C-2026-0001" in reply.text
    assert '"ยกเลิกนัด C-2026-0001"' in reply.text


def test_the_refusal_is_never_the_generic_i_am_not_sure():
    """The scenario suite forbids it, and rightly: the person was clear."""
    for message, action in (
        ("อย่าเพิ่งเปิดงานซ่อม", "ticket_open"),
        ("เช็คอินต้องทำอย่างไร", "check_in"),
        ("ลบนัดไปแล้วหรือยัง", "appointment_delete"),
    ):
        reply = chat._intent_guard_reply(message, action=action, language="th")
        assert reply is not None
        assert "ยังไม่แน่ใจว่าต้องการอะไร" not in reply.text
        assert "ยังไม่มีสิทธิ์" not in reply.text
        assert "ไม่พบ" not in reply.text


def test_a_command_gets_no_reply_at_all_so_the_handler_runs():
    assert chat._intent_guard_reply(
        "ยกเลิกนัด C-2026-0001", action="appointment_cancel", language="th",
    ) is None


def test_english_says_the_same_things():
    assert verdict("do not check in", "check_in").outcome == HOLD
    assert verdict("how do I check in?", "check_in").outcome == HOLD
    assert verdict("please check me in", "check_in").outcome == ACT
    assert verdict("check in T-2026-0001", "check_in").outcome == ACT


def test_an_empty_message_is_left_to_the_handler():
    assert intent_to_act("", action="check_in").outcome == ACT


def test_the_guard_reads_the_same_canonical_text_as_every_other_matcher():
    """Spelling, dialect and karaoke folding are chat's, not a second copy."""
    assert verdict("บ่ต้องเช็คอิน", "check_in").outcome == HOLD


# --------------------------------------------------------------------------
# Line items. The numbers/time stream fixed the parser; whether these
# sentences should mutate at all is this guard's question.
# --------------------------------------------------------------------------
HELD_LINES = [
    ("ไม่ต้องเพิ่มพัดลมอีก 3 ตัว", "negated"),
    ("เพิ่มพัดลมอีก 3 ตัวได้เท่าไหร่", "question"),
    ("ถ้าเพิ่มพัดลมอีก 3 ตัวจะเท่าไหร่", "conditional"),
    ("อย่าเพิ่งลบพัดลมออก", "negated"),
]


@pytest.mark.parametrize("message,reason", HELD_LINES)
def test_a_price_question_about_a_line_does_not_change_the_line(message, reason):
    got = verdict(message, "line_item")
    assert got.outcome == HOLD
    assert got.reason == reason


@pytest.mark.parametrize("message", [
    "เพิ่มพัดลม ราคา 1000 อีก 2 ตัว",
    "เพิ่มพัดลมอีก 3 ตัว",
    "ลดพัดลม 1 ตัว",
    "ลบสินค้าพัดลมออก",
    "แก้จำนวนพัดลมเป็น 5",
])
def test_the_line_commands_still_go_through(message):
    assert verdict(message, "line_item").outcome == ACT
