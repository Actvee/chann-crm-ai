"""Customer OA onboarding, as the owner walked it on 3 Sep 2569.

The transcript: add the CS OA as a friend → silence. Type "ร้าน dev
company" → "บัญชีนี้ดูแลหลายร้าน … พิมพ์ชื่อร้าน" → type it again → the same
line, forever. And under it, the bug no test had caught: a customer who IS
linked was told "ยังไม่พบบริษัทที่ผูกไว้" on every message, because the chat
looked them up as a license member, which a customer never is.

The rule the owner set: a customer registers the product (serial) before a
fault can be filed against it, so the shop knows which customer and which
machine. Everything here is that rule and the loop's repair.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.identity import ResolvedContext, TenantResolution  # noqa: E402
from chann_app.services.registration import (  # noqa: E402
    first_contact,
    handle_registration,
    shop_query,
)
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402
from test_phase65_registration import FakeRegClient  # noqa: E402
from test_phase65_registration import _ctx as _reg_ctx  # noqa: E402

CUSTOMER_KEYS: list[str] = []  # a customer holds none — by design


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _customer(**kw) -> ResolvedContext:
    return _ctx(primary_role="customer", oa="customer", **kw)


class NoMemberRow(FakeDataClient):
    """The real Data Tier for a customer: no license_members row, so the
    authorization lookup 404s and the client returns None."""

    async def authorization_context(self, license_id, chann_uid):
        return None


# --------------------------------------------------------------- follow event


class TestAddingTheOA:
    """Adding any of the three OAs gets a welcome that says what to do."""

    @pytest.mark.parametrize("oa", ["sales", "technician", "customer"])
    def test_an_unregistered_person_is_told_the_first_step(self, oa):
        text, quick = first_contact(oa, _reg_ctx(oa=oa, primary_role=oa))
        assert text.strip()
        assert "ยินดีต้อนรับ" in text or "สวัสดี" in text
        if oa == "customer":
            # The customer's first step is the serial or the shop — not a
            # company code they were never given.
            assert "หมายเลขเครื่อง" in text or "S/N" in text
            assert "ชื่อร้าน" in text
            assert quick, "the customer welcome offers the first step as buttons"

    def test_a_linked_customer_is_greeted_by_shop_not_re_onboarded(self):
        text, _quick = first_contact("customer", _customer())
        assert "บริษัททดสอบ" in text or "สวัสดี" in text
        assert "พิมพ์รหัสร้าน" not in text


# ------------------------------------------------------- the shop-name loop


class TestShopNameLoop:
    def test_shop_prefix_is_stripped_before_searching(self):
        assert shop_query("ร้าน dev company") == "dev company"
        assert shop_query("บริษัท แอร์ดี จำกัด") == "แอร์ดี"
        assert shop_query("ACME") == "ACME"

    async def test_one_match_links_without_asking_again(self):
        client = FakeRegClient(
            shops=[{"license_id": "lic-1", "company_code": "DEV001",
                    "company_name": "Dev Company"}],
            link={"company_name": "Dev Company", "company_code": "DEV001"},
        )
        reply = await handle_registration(
            client, message="ร้าน dev company", ctx=_reg_ctx(oa="customer", primary_role="customer"),
            audience="customer",
        )
        text = reply.text if hasattr(reply, "text") else str(reply)
        assert "link_customer" in client.calls
        assert "Dev Company" in text
        assert "พิมพ์ชื่อร้าน" not in text, "the loop the owner hit"

    async def test_no_match_holds_the_message_and_says_so_once(self):
        client = FakeRegClient(shops=[])
        reply = await handle_registration(
            client, message="แอร์ไม่เย็น", ctx=_reg_ctx(oa="customer", primary_role="customer"),
            audience="customer",
        )
        text = str(getattr(reply, "text", reply))
        assert "หมายเลขเครื่อง" in text or "ชื่อร้าน" in text
        assert client.pending and client.pending["entity"] == "pending_customer_message"

    async def test_a_held_fault_is_filed_once_the_serial_links_the_shop(self):
        """แอร์ไม่เย็น → (unknown) → ABC123456 (known at one shop): the
        person is linked AND the fault is filed, with that serial."""
        client = FakeRegClient(shops=[], link={"company_name": "ร้านแอร์ดี", "company_code": "ACME01"})
        client.pending = {
            "oa": "customer", "action": "report", "entity": "pending_customer_message",
            "fields": {"message": "แอร์ไม่เย็น"}, "missing": ["shop"],
        }
        client.serial_matches = [
            {"license_id": "lic-1", "company_name": "ร้านแอร์ดี", "company_code": "ACME01",
             "product_name": "แอร์", "warranty_end": None,
             "warranty_number": "W-2026-0001", "status": "active"},
        ]
        reply = await handle_registration(
            client, message="ABC123456", ctx=_reg_ctx(oa="customer", primary_role="customer"),
            audience="customer",
        )
        text = str(getattr(reply, "text", reply))
        assert "link_customer" in client.calls
        assert "create_ticket" in client.calls
        assert client.tickets[0]["issue_description"] == "แอร์ไม่เย็น"
        assert client.tickets[0]["serial_number"] == "ABC123456"
        assert "ร้านแอร์ดี" in text


# ------------------------------------------------ Bug C: no member row


class TestLinkedCustomerCanTalk:
    async def test_no_member_row_is_not_treated_as_unregistered(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        reply = await handle_chat_message(client, message="งานของฉัน", ctx=_customer())
        assert "ยังไม่พบบริษัทที่ผูกไว้" not in reply.text

    async def test_help_still_answers(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        reply = await handle_chat_message(client, message="วิธีใช้", ctx=_customer())
        assert "ลงทะเบียนสินค้า" in reply.text


# -------------------------------------------------------- register first


class TestRegisterFirst:
    async def test_no_product_holds_the_fault_and_asks_for_the_serial(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        reply = await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        assert "หมายเลขเครื่อง" in reply.text
        assert "แอร์ไม่เย็น" in reply.text, "the fault is echoed so they know it was kept"
        assert not any(r[0] == "create_ticket" for r in client.recorded)
        assert ("ไม่มีหมายเลขเครื่อง", "ไม่มีหมายเลขเครื่อง") in reply.quick_replies

    async def test_the_serial_claims_the_shops_unit_and_files_the_held_fault(self):
        """The shop recorded ABC123456 when it sold it (owner rule, 3 Sep
        afternoon); the customer's serial attaches them to that row."""
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        client._warranties = [{"id": "w-1", "serial_number": "ABC123456", "product_name": "แอร์",
                               "status": "active", "customer_chann_uid": None}]
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        reply = await handle_chat_message(client, message="ABC123456", ctx=_customer())
        claimed = [r for r in client.recorded if r[0] == "claim_warranty"]
        assert claimed and claimed[0][2]["serial_number"] == "ABC123456"
        assert client._warranties[0]["customer_chann_uid"] == "CHN-S-000001"
        created = [r for r in client.recorded if r[0] == "create_ticket"]
        assert created and created[0][2]["issue_description"] == "แอร์ไม่เย็น"
        assert created[0][2]["serial_number"] == "ABC123456"
        # The report flow then asks for the address, as it always did.
        assert "ที่อยู่" in reply.text

    async def test_no_serial_files_without_one(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        reply = await handle_chat_message(client, message="ไม่มีหมายเลขเครื่อง", ctx=_customer())
        created = [r for r in client.recorded if r[0] == "create_ticket"]
        assert created and "serial_number" not in created[0][2]
        assert "ที่อยู่" in reply.text

    async def test_an_address_typed_too_early_does_not_become_the_fault(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        reply = await handle_chat_message(client, message="99/1 ถ.สุขุมวิท", ctx=_customer())
        assert "แอร์ไม่เย็น" in reply.text
        assert not any(r[0] == "create_ticket" for r in client.recorded)

    async def test_a_menu_tap_drops_the_hold(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        await handle_chat_message(client, message="งานของฉัน", ctx=_customer())
        assert (client._pending or {}).get("entity") != "pending_customer_message"

    async def test_one_registered_product_is_used_without_asking(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        client._warranties = [{
            "id": "w-1", "serial_number": "ONLY00001", "product_name": "แอร์",
            "status": "active", "customer_chann_uid": "CHN-S-000001",
        }]
        reply = await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        created = [r for r in client.recorded if r[0] == "create_ticket"]
        assert created and created[0][2]["serial_number"] == "ONLY00001"
        assert "ที่อยู่" in reply.text

    async def test_several_products_get_buttons(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        client._warranties = [
            {"id": "w-1", "serial_number": "AAA00001", "product_name": "แอร์ห้องนอน",
             "status": "active", "customer_chann_uid": "CHN-S-000001"},
            {"id": "w-2", "serial_number": "BBB00002", "product_name": "แอร์ห้องนั่งเล่น",
             "status": "active", "customer_chann_uid": "CHN-S-000001"},
        ]
        reply = await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        sends = [send for _label, send in reply.quick_replies]
        assert "AAA00001" in sends and "BBB00002" in sends
        reply = await handle_chat_message(client, message="BBB00002", ctx=_customer())
        created = [r for r in client.recorded if r[0] == "create_ticket"]
        assert created and created[0][2]["serial_number"] == "BBB00002"
        # Already registered: the 409 is "known", not a failure.
        assert "ที่อยู่" in reply.text

    async def test_a_bare_serial_with_nothing_held_claims_the_product(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        client._warranties = [{"id": "w-1", "serial_number": "NEW0000123", "product_name": "แอร์",
                               "status": "active", "customer_chann_uid": None}]
        reply = await handle_chat_message(client, message="NEW0000123", ctx=_customer())
        assert any(r[0] == "claim_warranty" for r in client.recorded)
        assert not any(r[0] == "create_ticket" for r in client.recorded)
        assert "NEW0000123" in reply.text


# --------------------------------------------------------------- profile


class TestCustomerProfile:
    async def test_my_details_show_shop_and_products(self):
        client = NoMemberRow(role="customer", permission_keys=CUSTOMER_KEYS)
        client._warranties = [{
            "id": "w-1", "serial_number": "ONLY00001", "product_name": "แอร์",
            "status": "active", "customer_chann_uid": "CHN-S-000001",
        }]
        reply = await handle_chat_message(client, message="ข้อมูลของฉัน", ctx=_customer())
        assert "บริษัททดสอบ" in reply.text
        assert "ONLY00001" in reply.text
        assert "แก้เบอร์เป็น" in reply.text


# ---------------------------------------------------------- technician help


class TestTechnicianHelp:
    async def test_how_to_is_the_technicians_day_not_the_sales_catalogue(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        reply = await handle_chat_message(
            client, message="วิธีใช้", ctx=_ctx(primary_role="technician", oa="technician"),
        )
        assert "เช็คอิน" in reply.text and "ปิดงาน" in reply.text
        assert "ใบเสนอราคา" not in reply.text
