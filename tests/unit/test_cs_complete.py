"""The owner's second walk of the three OAs, 3 Sep 2569 (afternoon).

Sale OA: "รายชื่อช่าง", "ขอข้อมูลร้านค้า", "รหัสร้านค้า" came back as the
permission catalogue ("คุณสามารถทำสิ่งเหล่านี้ได้") — a list of rights is
not an answer. Tech OA: the profile card said "ลูกค้าของ" and the reports
page wore the Sales section strip. CS OA: the account belonged to several
shops and the app opened in the wrong one; registration accepted any
serial the customer typed, so nothing tied a machine to what the shop
sold.

Owner rule from that walk: the SHOP records the unit (serial); the
customer CLAIMS it by typing the sticker. Unknown at this shop → refused
with "ติดต่อร้าน", never invented.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.identity import (  # noqa: E402
    ResolvedContext,
    TenantResolution,
    apply_active_tenant,
)
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402

OTHER_LICENSE = "22222222-2222-2222-2222-222222222222"
SALES_KEYS = ["customer.read", "ticket.read", "ticket.create", "ticket.assign",
              "warranty.read", "warranty.create", "setting.manage"]


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


class NoMemberRow(FakeDataClient):
    async def authorization_context(self, license_id, chann_uid):
        return None


def _customer(**kw) -> ResolvedContext:
    return _ctx(primary_role="customer", oa="customer", **kw)


# ------------------------------------------------------------ several shops


class TestSeveralShops:
    async def test_the_stored_choice_narrows_to_one(self):
        client = FakeDataClient()
        client._active_tenant = {("CHN-S-000001", "customer"): OTHER_LICENSE}
        memberships = _ctx(TenantResolution.MULTIPLE).memberships
        chosen, others = await apply_active_tenant(client, "CHN-S-000001", "customer", memberships)
        assert [m["license_id"] for m in chosen] == [OTHER_LICENSE]
        assert [m["license_id"] for m in others] == [LICENSE_ID]

    async def test_a_stale_choice_is_ignored(self):
        """A link that was revoked must not keep someone in that shop."""
        client = FakeDataClient()
        client._active_tenant = {("CHN-S-000001", "customer"): "gone-gone-gone"}
        memberships = _ctx(TenantResolution.MULTIPLE).memberships
        chosen, others = await apply_active_tenant(client, "CHN-S-000001", "customer", memberships)
        assert len(chosen) == 2 and others == []

    async def test_no_choice_gets_buttons_not_a_dead_end(self):
        client = FakeDataClient()
        reply = await handle_chat_message(
            client, message="แอร์ไม่เย็น", ctx=_ctx(TenantResolution.MULTIPLE, oa="customer"),
        )
        sends = [send for _label, send in reply.quick_replies]
        assert any("COA" in s for s in sends) and any("COB" in s for s in sends)
        assert "พิมพ์ชื่อร้าน" not in reply.text

    async def test_naming_the_shop_stores_the_choice(self):
        client = FakeDataClient()
        reply = await handle_chat_message(
            client, message="ใช้ร้าน COB", ctx=_ctx(TenantResolution.MULTIPLE, oa="customer"),
        )
        stored = [r for r in client.recorded if r[0] == "set_active_tenant"]
        assert stored and stored[0][3] == OTHER_LICENSE
        assert "บริษัท ข" in reply.text

    async def test_the_company_name_alone_is_enough(self):
        client = FakeDataClient()
        await handle_chat_message(
            client, message="บริษัท ก", ctx=_ctx(TenantResolution.MULTIPLE, oa="sales"),
        )
        stored = [r for r in client.recorded if r[0] == "set_active_tenant"]
        assert stored and stored[0][3] == LICENSE_ID

    async def test_a_list_number_works_too(self):
        client = FakeDataClient()
        await handle_chat_message(
            client, message="2", ctx=_ctx(TenantResolution.MULTIPLE, oa="sales"),
        )
        stored = [r for r in client.recorded if r[0] == "set_active_tenant"]
        assert stored and stored[0][3] == OTHER_LICENSE

    async def test_switching_later_from_a_single_active_shop(self):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        ctx = _ctx()
        ctx.alternatives = [{"license_id": OTHER_LICENSE, "license_code": "COB",
                             "company_name": "บริษัท ข"}]
        reply = await handle_chat_message(client, message="เปลี่ยนร้าน", ctx=ctx)
        assert any("COB" in send for _l, send in reply.quick_replies)
        await handle_chat_message(client, message="ใช้ร้าน COB", ctx=ctx)
        stored = [r for r in client.recorded if r[0] == "set_active_tenant"]
        assert stored and stored[0][3] == OTHER_LICENSE

    async def test_an_ordinary_sentence_does_not_switch(self):
        """Mentioning another company's name mid-conversation is not a
        request to act there."""
        client = FakeDataClient(permission_keys=SALES_KEYS)
        ctx = _ctx()
        ctx.alternatives = [{"license_id": OTHER_LICENSE, "license_code": "COB",
                             "company_name": "บริษัท ข"}]
        await handle_chat_message(client, message="ค้นหาลูกค้า บริษัท ข", ctx=ctx)
        assert not [r for r in client.recorded if r[0] == "set_active_tenant"]


# ------------------------------------------------------- the LIFF principal


class TestCustomerPrincipal:
    async def test_a_customer_gets_a_principal_without_a_member_row(self, monkeypatch):
        from chann_app.services import authorization

        class Client(NoMemberRow):
            async def resolve_identity(self, line_user_id, primary_role, display_name=None):
                return {"chann_uid": "CHN-S-000001", "primary_role": primary_role}

            async def memberships_of(self, chann_uid, oa=None):
                assert oa == "customer", "the customer app must ask for customer links"
                return [{"license_id": LICENSE_ID, "license_code": "TESTCO",
                         "company_name": "บริษัททดสอบ"}]

        async def fake_verify(token, audience):
            return {"sub": "U1", "name": "x"}

        monkeypatch.setattr(authorization, "verify_id_token", fake_verify)
        principal = await authorization.resolve_tenant_principal(
            Client(), x_liff_id_token="t", x_liff_audience="customer", x_license_id="",
        )
        assert principal.is_customer
        assert principal.license_id == LICENSE_ID
        assert "ticket.create" in principal.permission_keys
        assert "ticket.assign" not in principal.permission_keys


# ---------------------------------------------------- register = claim


class TestClaim:
    async def test_a_serial_the_shop_recorded_is_claimed(self):
        client = NoMemberRow(role="customer", permission_keys=[])
        client._warranties = [{"id": "w-1", "serial_number": "SHOP00001", "product_name": "แอร์",
                               "status": "active", "customer_chann_uid": None,
                               "warranty_number": "W-2026-0001", "warranty_end": "2027-01-01"}]
        reply = await handle_chat_message(client, message="ลงทะเบียนสินค้า SHOP00001", ctx=_customer())
        assert client._warranties[0]["customer_chann_uid"] == "CHN-S-000001"
        assert "เป็นของคุณแล้ว" in reply.text
        assert not any(r[0] == "register_warranty" for r in client.recorded)

    async def test_a_serial_the_shop_never_recorded_is_refused(self):
        client = NoMemberRow(role="customer", permission_keys=[])
        reply = await handle_chat_message(client, message="ลงทะเบียนสินค้า NOPE00001", ctx=_customer())
        assert "ร้านยังไม่มีเครื่องหมายเลข" in reply.text
        assert not client.__dict__.get("_warranties")
        assert ("ติดต่อร้าน", "ติดต่อร้าน") in reply.quick_replies

    async def test_someone_elses_unit_is_refused(self):
        client = NoMemberRow(role="customer", permission_keys=[])
        client._warranties = [{"id": "w-1", "serial_number": "THEIRS001", "status": "active",
                               "customer_chann_uid": "CHN-S-000099"}]
        reply = await handle_chat_message(client, message="ลงทะเบียนสินค้า THEIRS001", ctx=_customer())
        assert "ลูกค้าท่านอื่น" in reply.text
        assert client._warranties[0]["customer_chann_uid"] == "CHN-S-000099"

    async def test_a_held_fault_waits_when_the_serial_is_unknown(self):
        client = NoMemberRow(role="customer", permission_keys=[])
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=_customer())
        reply = await handle_chat_message(client, message="NOPE00001", ctx=_customer())
        assert "ร้านยังไม่มีเครื่องหมายเลข" in reply.text
        assert not any(r[0] == "create_ticket" for r in client.recorded)
        # Still held: "ไม่มีหมายเลขเครื่อง" files it without one.
        reply = await handle_chat_message(client, message="ไม่มีหมายเลขเครื่อง", ctx=_customer())
        assert any(r[0] == "create_ticket" for r in client.recorded)

    async def test_staff_record_a_sold_unit_for_a_customer(self):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await client.create_customer(LICENSE_ID, {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        reply = await handle_chat_message(
            client, message="ลงทะเบียนสินค้า SN12345678 ให้ลูกค้า สมชาย", ctx=_ctx(),
        )
        registered = [r for r in client.recorded if r[0] == "register_warranty"]
        assert registered
        payload = registered[0][2]
        assert payload["serial_number"] == "SN12345678"
        assert payload["customer_chann_uid"] is None, "the customer claims it themselves"
        assert payload["contact_id"]
        assert "SN12345678" in reply.text and "สมชาย" in reply.text

    async def test_staff_see_the_book(self):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        client._warranties = [
            {"id": "w-1", "serial_number": "A1", "product_name": "แอร์", "status": "active",
             "customer_chann_uid": "CHN-S-000009"},
            {"id": "w-2", "serial_number": "B2", "product_name": None, "status": "active",
             "customer_chann_uid": None},
        ]
        reply = await handle_chat_message(client, message="รายการประกัน", ctx=_ctx())
        assert "A1" in reply.text and "B2" in reply.text
        assert "ยังไม่มีลูกค้าผูก" in reply.text


# ------------------------------------------------------ Sale OA questions


class TestSalesQuestions:
    async def test_shop_info_shows_the_code_for_any_member(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        for phrase in ("ขอข้อมูลร้านค้า", "รหัสร้านค้า", "ข้อมูลร้าน"):
            reply = await handle_chat_message(client, message=phrase, ctx=_ctx())
            assert "TESTCO" in reply.text, phrase
            assert "คุณสามารถทำสิ่งเหล่านี้ได้" not in reply.text

    async def test_technicians_are_listed_by_name(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        client._members = [
            {"id": "m-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"},
            {"id": "m-2", "chann_uid": "CHN-S-000002", "role": "sales", "status": "active"},
        ]
        client._profiles = {"CHN-T-000001": {"first_name": "สมศักดิ์", "last_name": "ช่างดี", "phone": "0899999999"}}
        reply = await handle_chat_message(client, message="รายชื่อช่าง", ctx=_ctx())
        assert "สมศักดิ์" in reply.text
        assert "CHN-S-000002" not in reply.text

    async def test_no_technicians_says_how_to_add_one(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        client._members = []
        reply = await handle_chat_message(client, message="รายชื่อช่าง", ctx=_ctx())
        assert "รหัสเชิญ" in reply.text

    def test_the_fallback_reads_as_guidance_not_a_rights_list(self):
        from chann_app.services.chat import SUGGEST_HEADER

        assert "วิธีใช้" in SUGGEST_HEADER["th"]
        assert not SUGGEST_HEADER["th"].startswith("คุณสามารถทำสิ่งเหล่านี้ได้")
