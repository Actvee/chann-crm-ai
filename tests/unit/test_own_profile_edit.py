"""Editing your own details from the card that invites it (owner, 10 Sep 2026).

A technician opened "ข้อมูลของฉัน", was told 'แก้ได้เลย เช่น "แก้เบอร์เป็น …"',
typed "ชื่อ ทดสอบ1 มีทดสอบ" and was asked for a phone number — the sentence had
been read as a new CUSTOMER. The phone they then sent was refused as a
sales-only command. Two faults: the invited shape was not understood, and a
capability this OA does not have was allowed to open a slot-fill.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402

settings.openrouter_api_key = "test"
settings.openrouter_model = "test"

from chann_app.services import chat  # noqa: E402


class TestTheShapesTheCardInvites:
    def test_a_name_with_two_words_sets_both(self):
        assert chat._profile_field_edit("ชื่อ ทดสอบ1 มีทดสอบ") == {
            "first_name": "ทดสอบ1", "last_name": "มีทดสอบ",
        }

    def test_a_single_word_name(self):
        assert chat._profile_field_edit("ชื่อ สมชาย") == {"first_name": "สมชาย"}

    def test_every_label_people_use(self):
        cases = {
            "เบอร์ 0812345678": {"phone": "0812345678"},
            "เบอร์โทร 081-234-5678": {"phone": "081-234-5678"},
            "แก้เบอร์เป็น 0899999999": None,      # the existing edit path owns this
            "อีเมล somchai@example.com": {"email": "somchai@example.com"},
            "ที่อยู่ 99/1 ถ.สุขุมวิท": {"address": "99/1 ถ.สุขุมวิท"},
            "นามสกุล ใจดี": {"last_name": "ใจดี"},
            "name Somchai Jaidee": {"first_name": "Somchai", "last_name": "Jaidee"},
        }
        for text, want in cases.items():
            got = chat._profile_field_edit(text)
            if want is None:
                continue          # only asserting the ones this parser owns
            assert got == want, text

    def test_a_label_with_nothing_after_it_is_a_question(self):
        for text in ("ชื่อ", "เบอร์", "อีเมล", "ที่อยู่"):
            assert chat._profile_field_edit(text) is None, text

    def test_a_sentence_that_merely_contains_a_label_is_not_an_edit(self):
        for text in ("งานของฉันชื่ออะไร", "ลูกค้าชื่อสมชายโทรมา", "ขอเบอร์ลูกค้าหน่อย"):
            assert chat._profile_field_edit(text) is None, text


class TestABareValueAnswersTheCard:
    def test_a_phone_fills_a_blank_phone(self):
        assert chat._bare_profile_value("0869768057", ["first_name", "phone"]) == {"phone": "0869768057"}

    def test_an_email_fills_a_blank_email(self):
        assert chat._bare_profile_value("a@b.co", ["email"]) == {"email": "a@b.co"}

    def test_nothing_is_taken_when_the_field_is_already_set(self):
        assert chat._bare_profile_value("0869768057", ["email"]) is None

    def test_a_bare_word_is_never_a_name(self):
        # Too much else is a bare word; the label form stays the way to set one.
        assert chat._bare_profile_value("สมชาย", ["first_name"]) is None

    def test_a_ticket_code_is_not_a_phone(self):
        assert chat._bare_profile_value("T-2026-0001", ["phone"]) is None
