"""Pasting a numbered list of customers (owner, 9 Sep 2026).

The owner typed a heading and two numbered lines and the assistant asked
for an email address. Three separate faults: "ลูกค้าใหม่" was not a bulk
heading, the list number stuck to the first name so the line no longer
looked like "name … phone", and the model's optional missing fields were
put to the user as a demand.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402

settings.openrouter_api_key = "test"
settings.openrouter_model = "test"

from chann_app.services import chat  # noqa: E402


def names(entries):
    return [(e["first_name"], e["last_name"], e["phone"]) for e in entries or []]


class TestTheHeadingPeopleActuallyType:
    def test_luk_kha_mai_heads_a_pasted_list(self):
        entries = chat._bulk_customer_entries(
            "ลูกค้าใหม่\nสมชาย ใจดี 0811111111\nสมหญิง รักดี 0822222222"
        )
        assert names(entries) == [
            ("สมชาย", "ใจดี", "0811111111"),
            ("สมหญิง", "รักดี", "0822222222"),
        ]

    def test_one_line_is_still_one_customer(self):
        # The single-customer flow owns this shape; bulk must decline it.
        assert chat._bulk_customer_entries("ลูกค้าใหม่ สมชาย ใจดี 0812345678") is None


class TestListMarkers:
    def test_the_owners_message(self):
        entries = chat._bulk_customer_entries(
            "ลูกค้าใหม่\n1.ธรรสมนัา พรหมวัด 0112224555\n2.จิตกร มานุสรณ์ 0812345678"
        )
        assert names(entries) == [
            ("ธรรสมนัา", "พรหมวัด", "0112224555"),
            ("จิตกร", "มานุสรณ์", "0812345678"),
        ]

    def test_every_marker_people_type(self):
        for head in ("1.", "1)", "(1)", "[1]", "-", "–", "•", "*", "๑."):
            body = "ลูกค้าใหม่\n%s สมชาย 0811111111\n%s สมหญิง 0822222222" % (head, head)
            assert names(chat._bulk_customer_entries(body)) == [
                ("สมชาย", None, "0811111111"),
                ("สมหญิง", None, "0822222222"),
            ], head

    def test_no_space_after_the_marker(self):
        entries = chat._bulk_customer_entries("ลูกค้าใหม่\n1.สมชาย 0811111111\n2.สมหญิง 0822222222")
        assert names(entries) == [("สมชาย", None, "0811111111"), ("สมหญิง", None, "0822222222")]

    def test_a_dotted_phone_is_not_item_081(self):
        entries = chat._bulk_customer_entries("ลูกค้าใหม่\n081.234.5678 สมชาย\n082.345.6789 สมหญิง")
        assert names(entries) == [
            ("สมชาย", None, "081.234.5678"),
            ("สมหญิง", None, "082.345.6789"),
        ]


class TestNeverDemandAnOptionalField:
    """What the owner actually hit was an email demand.

    address and notes belong with it: nothing in this codebase needs them
    to make a customer. `last_name` was swept in with them and should not
    have been — the create handler has always required a surname, so
    hiding the model's report of it only moved the question one turn
    later ("กรุณาระบุเบอร์โทร", then "กรุณาระบุนามสกุล" after the phone
    arrived). Both sides now read capabilities.CUSTOMER_CREATE, which
    says last_name and phone are required and email/address/notes are
    never asked for (10 ก.ย. 2569).
    """

    def test_email_is_not_asked_for(self):
        intent = {"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย"}}
        assert chat._prune_missing(["email", "phone"], intent, "ลูกค้าใหม่ สมชาย") == ["phone"]

    def test_address_and_notes_are_not_asked_for(self):
        intent = {"action": "create", "entity": "customer", "fields": {}}
        assert chat._prune_missing(
            ["address", "notes", "first_name"], intent, "ลูกค้าใหม่"
        ) == ["first_name"]

    def test_the_names_and_phone_survive_because_the_handler_requires_them(self):
        """Owner, 11 ก.ย. 2569: a customer needs a first name too."""
        from chann_app.services.capabilities import CUSTOMER_CREATE

        intent = {"action": "create", "entity": "customer", "fields": {}}
        assert chat._prune_missing(
            ["address", "first_name", "last_name", "phone"], intent, "ลูกค้าใหม่"
        ) == ["first_name", "last_name", "phone"]
        assert set(CUSTOMER_CREATE.required) == {"first_name", "last_name", "phone"}

    def test_a_pasted_line_with_one_name_is_still_created(self):
        """The declared exception: a paste is not held to the surname rule."""
        entries = chat._bulk_customer_entries("ลูกค้าใหม่\n1. สมชาย 0811111111\n2. สมหญิง 0822222222")
        assert names(entries) == [("สมชาย", None, "0811111111"), ("สมหญิง", None, "0822222222")]

    def test_other_entities_are_untouched(self):
        intent = {"action": "create", "entity": "quote", "fields": {}}
        assert chat._prune_missing(["email"], intent, "สร้างใบเสนอราคา") == ["email"]
