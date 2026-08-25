"""Phase 6.5 registration-through-chat tests.

The Data-tier side (limits, invites, links, trial expiry) is covered by the
integration suite against real Postgres. These cover the conversational layer:
what a stranger sees, and that registration never reaches the AI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.identity import ResolvedContext, TenantResolution  # noqa: E402
from chann_app.services.registration import (  # noqa: E402
    COMPANY_CODE_RE,
    INVITE_CODE_RE,
    handle_registration,
    is_unregistered,
    parse_create_company,
)


class FakeRegClient:
    """Records calls so tests can assert the AI was never involved."""

    def __init__(self, *, created=None, member=None, link=None, shops=None, raises=None):
        self._created = created or {
            "company_name": "ร้านสมชาย", "company_code": "ABCD2345"
        }
        self._member = member or {"company_name": "ร้านสมชาย", "role": "member"}
        self._link = link or {"company_name": "ร้านสมชาย"}
        self._shops = shops if shops is not None else []
        self._raises = raises
        self.calls: list[str] = []

    async def create_license(self, **kw):
        self.calls.append("create_license")
        if self._raises:
            raise self._raises
        return self._created

    async def redeem_invite(self, **kw):
        self.calls.append("redeem_invite")
        if self._raises:
            raise self._raises
        return self._member

    async def link_customer(self, **kw):
        self.calls.append("link_customer")
        if self._raises:
            raise self._raises
        return self._link

    async def search_shops(self, q, limit=10):
        self.calls.append("search_shops")
        return self._shops


class Conflict(Exception):
    status_code = 409


class NotFound(Exception):
    status_code = 404


def _ctx(resolution=TenantResolution.NONE):
    return ResolvedContext(
        chann_uid="CHN-S-000009", primary_role="sales",
        display_name="สมชาย", resolution=resolution, memberships=[],
    )


class TestUnregisteredDetection:
    def test_no_tenant_is_unregistered(self):
        assert is_unregistered(_ctx()) is True

    def test_single_tenant_is_registered(self):
        assert is_unregistered(_ctx(TenantResolution.SINGLE)) is False


class TestCreateCompanyParsing:
    def test_thai_trigger_with_name(self):
        assert parse_create_company("เปิดบริษัทใหม่ ร้านสมชายการช่าง") == "ร้านสมชายการช่าง"

    def test_english_trigger_with_name(self):
        assert parse_create_company("create company Somchai Repairs") == "Somchai Repairs"

    def test_trigger_without_name_returns_empty_not_none(self):
        # "" and None must stay distinguishable: one asks for the name, the
        # other falls through to the welcome menu.
        assert parse_create_company("เปิดบริษัทใหม่") == ""
        assert parse_create_company("เปิดบริษัทใหม่") is not None

    def test_non_trigger_returns_none(self):
        assert parse_create_company("สวัสดี") is None
        assert parse_create_company("") is None


class TestCodeShapes:
    def test_invite_code_is_ten_chars(self):
        assert INVITE_CODE_RE.match("ABCDEFGHJK")
        assert not INVITE_CODE_RE.match("ABCDEFGH")      # too short (company)
        assert not INVITE_CODE_RE.match("ABCDEFGHJ0")    # 0 is excluded

    def test_company_code_is_eight_chars(self):
        assert COMPANY_CODE_RE.match("ABCD2345")
        assert not COMPANY_CODE_RE.match("ABCDEFGHJK")   # too long (invite)
        assert not COMPANY_CODE_RE.match("ABCD234I")     # I is excluded


class TestSalesRegistration:
    async def test_empty_message_shows_both_options(self):
        client = FakeRegClient()
        reply = await handle_registration(client, message="", ctx=_ctx())
        assert "เปิดบริษัทใหม่" in reply
        assert "รหัสเชิญ" in reply
        assert client.calls == []          # nothing hit the backend

    async def test_unrecognised_message_shows_menu_not_an_error(self):
        client = FakeRegClient()
        reply = await handle_registration(client, message="สวัสดีครับ", ctx=_ctx())
        assert "เปิดบริษัทใหม่" in reply
        assert client.calls == []

    async def test_create_company_returns_the_shop_code(self):
        client = FakeRegClient()
        reply = await handle_registration(
            client, message="เปิดบริษัทใหม่ ร้านสมชาย", ctx=_ctx()
        )
        assert "ABCD2345" in reply          # the code must be shown, it is the point
        assert "ร้านสมชาย" in reply
        assert "30" in reply                # trial length stated up front
        assert client.calls == ["create_license"]

    async def test_create_without_name_asks_for_the_name(self):
        client = FakeRegClient()
        reply = await handle_registration(client, message="เปิดบริษัทใหม่", ctx=_ctx())
        assert "ชื่อบริษัท" in reply
        assert client.calls == []           # not sent without a name

    async def test_second_company_is_refused_clearly(self):
        client = FakeRegClient(raises=Conflict("409"))
        reply = await handle_registration(
            client, message="เปิดบริษัทใหม่ บริษัทที่สอง", ctx=_ctx()
        )
        assert "บริษัทเดียว" in reply

    async def test_invite_code_joins(self):
        client = FakeRegClient()
        reply = await handle_registration(client, message="ABCDEFGHJK", ctx=_ctx())
        assert "เข้าร่วม" in reply
        assert client.calls == ["redeem_invite"]

    async def test_bad_invite_code_says_so(self):
        client = FakeRegClient(raises=NotFound("404"))
        reply = await handle_registration(client, message="ABCDEFGHJK", ctx=_ctx())
        assert "ไม่พบรหัส" in reply

    async def test_english_locale(self):
        client = FakeRegClient()
        reply = await handle_registration(
            client, message="", ctx=_ctx(), language="en"
        )
        assert "create company" in reply


class TestCustomerRegistration:
    async def test_company_code_links_the_shop(self):
        client = FakeRegClient()
        reply = await handle_registration(
            client, message="ABCD2345", ctx=_ctx(), audience="customer"
        )
        assert "ผูกกับร้าน" in reply
        assert client.calls == ["link_customer"]

    async def test_name_search_lists_shops_with_codes(self):
        client = FakeRegClient(shops=[
            {"company_name": "ร้านสมชาย", "company_code": "ABCD2345"},
            {"company_name": "ร้านสมหญิง", "company_code": "WXYZ6789"},
        ])
        reply = await handle_registration(
            client, message="ร้านสม", ctx=_ctx(), audience="customer"
        )
        assert "ABCD2345" in reply and "WXYZ6789" in reply
        assert client.calls == ["search_shops"]

    async def test_customer_is_never_offered_company_creation(self):
        """An end customer creating a tenant would be nonsense."""
        client = FakeRegClient()
        reply = await handle_registration(
            client, message="เปิดบริษัทใหม่ ร้านของฉัน", ctx=_ctx(), audience="customer"
        )
        assert "create_license" not in client.calls
        assert "สร้างบริษัท" not in reply

    async def test_no_match_explains_what_to_type(self):
        client = FakeRegClient(shops=[])
        reply = await handle_registration(
            client, message="zz", ctx=_ctx(), audience="customer"
        )
        assert "รหัสร้าน" in reply
