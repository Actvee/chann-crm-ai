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
    WELCOME_TECHNICIAN,
    handle_registration,
    is_unregistered,
    parse_create_company,
)


class FakeRegClient:
    """Records calls so tests can assert the AI was never involved."""
    async def lookup_serial(self, serial_number, actor_chann_uid=""):
        return {"serial_number": serial_number,
                "matches": list(getattr(self, "serial_matches", []))}

    async def set_pending_intent(self, chann_uid, oa, **kwargs):
        self.pending = {"oa": oa, **kwargs}

    async def get_pending_intent(self, chann_uid, oa):
        return getattr(self, "pending", None)

    async def clear_pending_intent(self, chann_uid, oa):
        self.pending = None


    def __init__(self, *, created=None, member=None, link=None, shops=None, raises=None):
        self._created = created or {
            "company_name": "ร้านสมชาย", "company_code": "ABCD2345"
        }
        self._member = member or {"company_name": "ร้านสมชาย", "role": "member"}
        self._link = link or {"company_name": "ร้านสมชาย", "license_id": "lic-1"}
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

    # Linking now continues into the report flow (3 Sep): the same client
    # must answer the calls that flow makes.
    async def my_shops(self, chann_uid):
        self.calls.append("my_shops")
        return [{"license_id": "lic-1", "license_code": self._link.get("company_code", "ABCD2345"),
                 "company_name": self._link.get("company_name", "ร้านสมชาย")}]

    async def get_profile(self, chann_uid):
        return None

    async def list_warranties(self, license_id, serial_number=None, customer_chann_uid=None):
        return list(getattr(self, "warranties", []))

    async def register_warranty(self, license_id, payload, actor_id=None):
        self.calls.append("register_warranty")
        return {"id": "w-1", "status": "active", **payload}

    async def create_ticket(self, license_id, payload, actor_id=None):
        self.calls.append("create_ticket")
        self.tickets = getattr(self, "tickets", []) + [payload]
        return {"id": "tk-1", "ticket_number": "T-2026-0001", "status": "open", **payload}


class Conflict(Exception):
    status_code = 409


class NotFound(Exception):
    status_code = 404


def _ctx(resolution=TenantResolution.NONE, primary_role="sales", oa=None):
    if oa is None:
        oa = primary_role
    return ResolvedContext(
        chann_uid="CHN-S-000009", primary_role=primary_role,
        display_name="สมชาย", resolution=resolution, memberships=[], oa=oa,
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

    def test_shorter_natural_phrasings_also_match(self):
        """Found live: the menu says "เปิดบริษัทใหม่", the user typed the
        shorter "เปิดบริษัท", and it matched nothing."""
        for text, expected in [
            ("เปิดบริษัท ร้านทดสอบ", "ร้านทดสอบ"),
            ("สร้างบริษัท ร้าน ก", "ร้าน ก"),
            ("ลงทะเบียนบริษัท ร้าน ข", "ร้าน ข"),
            ("สมัครบริษัท ร้าน ค", "ร้าน ค"),
            ("create new company Acme", "Acme"),
            ("register company Acme", "Acme"),
        ]:
            assert parse_create_company(text) == expected, text

    def test_longest_trigger_wins(self):
        """A shorter trigger that prefixes a longer one must not leave its
        remainder in the company name."""
        assert parse_create_company("เปิดบริษัทใหม่ ร้าน ก") == "ร้าน ก"
        assert parse_create_company("เปิดบริษัทใหม่") == ""      # not "ใหม่"
        assert parse_create_company("create new company X") == "X"

    def test_separators_after_the_trigger_are_stripped(self):
        assert parse_create_company("เปิดบริษัทใหม่: ร้าน ง") == "ร้าน ง"
        assert parse_create_company("create company - Acme") == "Acme"


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

    async def test_no_match_keeps_what_the_person_said(self):
        """Someone who scanned a QR code at a shop and typed "แอร์เสีย" was
        answered with a demand for a shop code they have never seen, and
        their message was discarded.

        The reply now repeats it back — so they can see it was not lost —
        and asks for the shop's NAME, which they know, rather than its
        code, which they do not.
        """
        client = FakeRegClient(shops=[])
        reply = await handle_registration(
            client, message="แอร์ไม่เย็น", ctx=_ctx(), audience="customer"
        )
        assert "แอร์ไม่เย็น" in reply, "the customer's own words were dropped"
        assert "ชื่อร้าน" in reply

    async def test_an_empty_first_message_gets_a_welcome_not_an_instruction(self):
        client = FakeRegClient(shops=[])
        reply = await handle_registration(
            client, message="", ctx=_ctx(), audience="customer"
        )
        assert "ยินดีต้อนรับ" in reply


class TestSchemaHeadGuard:
    """The /health schema check is only useful if EXPECTED_MIGRATION_HEAD is
    kept current — so pin it to the actual latest migration file."""

    def test_expected_head_matches_the_newest_migration(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        versions = sorted((root / "database/alembic/versions").glob("*.py"))
        assert versions, "no migrations found"

        # the file whose revision nothing else declares as down_revision
        revs, downs = {}, set()
        for f in versions:
            t = f.read_text(encoding="utf-8")
            rev = re.search(r'^revision = "([^"]+)"', t, re.M).group(1)
            down = re.search(r'^down_revision = (?:"([^"]+)"|None)', t, re.M).group(1)
            revs[rev] = f.name
            if down:
                downs.add(down)
        heads = set(revs) - downs
        assert len(heads) == 1, f"migration chain has {len(heads)} heads: {heads}"

        import sys
        sys.path.insert(0, str(root / "data"))
        import os
        os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@l/x")
        from chann_data.main import EXPECTED_MIGRATION_HEAD

        assert EXPECTED_MIGRATION_HEAD == heads.pop(), (
            "EXPECTED_MIGRATION_HEAD in data/chann_data/main.py is out of date — "
            "bump it in the same commit as the new migration"
        )

    def test_every_revision_id_fits_alembic_version_column(self):
        """Alembic's own `alembic_version.version_num` is VARCHAR(32).

        A longer revision id creates the migration file, imports fine,
        passes every static check, and then fails at the very last
        statement of `alembic upgrade head` — the UPDATE that stamps the
        new version — taking down every integration test at once with an
        error (`value too long for type character varying(32)`) that
        names neither the migration nor the id that caused it. That is
        exactly what happened when `0010_phase10_company_document_identity`
        (38 chars) was first written. Caught here in milliseconds, with
        no database, instead of ~8 minutes into a full verify run.
        """
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        too_long = []
        for f in sorted((root / "database/alembic/versions").glob("*.py")):
            rev = re.search(r'^revision = "([^"]+)"', f.read_text(encoding="utf-8"), re.M).group(1)
            if len(rev) > 32:
                too_long.append((f.name, rev, len(rev)))

        assert not too_long, (
            "revision id longer than alembic_version.version_num VARCHAR(32): "
            + ", ".join(f"{name}: {rev!r} is {n} chars" for name, rev, n in too_long)
        )


class TestTechnicianOAHasNoCreateCompanyOption:
    """Technician OA is a distinct persona from Sales OA, even for the same
    LINE account (see identity.resolve_context / MemberRepository.
    memberships_of): a technician joins an existing company via invite code,
    they never create one through this channel."""

    async def test_empty_message_gets_the_technician_welcome_not_the_generic_one(self):
        reply = await handle_registration(
            FakeRegClient(), message="", ctx=_ctx(oa="technician"), audience="technician",
        )
        assert reply == WELCOME_TECHNICIAN["th"]
        assert "เปิดบริษัทใหม่" not in reply

    async def test_create_company_trigger_is_not_recognised_on_technician_oa(self):
        """The generic create-company trigger words must not fire here even
        if someone types them — this channel does not offer that action."""
        reply = await handle_registration(
            FakeRegClient(), message="เปิดบริษัทใหม่ ร้านสมชาย",
            ctx=_ctx(oa="technician"), audience="technician",
        )
        assert reply == WELCOME_TECHNICIAN["th"]

    async def test_invite_code_still_redeems_normally_on_technician_oa(self):
        client = FakeRegClient(member={"company_name": "ร้านสมชาย", "role": "technician"})
        reply = await handle_registration(
            client, message="ABC234XY7Z", ctx=_ctx(oa="technician"), audience="technician",
        )
        assert client.calls == ["redeem_invite"]
        assert "ร้านสมชาย" in reply
        assert "technician" in reply

    async def test_bad_code_on_technician_oa_gets_the_same_bad_code_reply(self):
        client = FakeRegClient(raises=NotFound("nope"))
        reply = await handle_registration(
            client, message="ZZZZZZZZZZ", ctx=_ctx(oa="technician"), audience="technician",
        )
        assert "ไม่พบรหัสนี้" in reply


class TestSerialFirstOnboarding:
    """A customer whose appliance has failed has the sticker on the
    machine, not a shop code they were never given.

    Phase 16.4 in the place it matters most: before they are linked to
    anyone, when the alternative is asking them to look up a code.
    """

    async def test_a_serial_known_at_one_shop_links_straight_to_it(self):
        client = FakeRegClient(shops=[])
        client.serial_matches = [
            {"license_id": "lic-1", "company_name": "ร้านแอร์ดี",
             "company_code": "ACME01", "product_name": "แอร์", "warranty_end": None,
             "warranty_number": "W-2026-0001", "status": "active"},
        ]
        reply = await handle_registration(
            client, message="ABC123456", ctx=_ctx(), audience="customer",
        )
        # Linked, and told what to do next (3 Sep: the link is the start
        # of the report, not the end of onboarding).
        text = str(getattr(reply, "text", reply))
        assert "ผูกกับร้าน" in text
        assert "ABC123456" in text
        reply = text
        # Confirming the only possible answer is a question with no purpose.
        assert "พิมพ์รหัสร้าน" not in reply

    async def test_a_serial_at_several_shops_asks_which(self):
        client = FakeRegClient(shops=[])
        client.serial_matches = [
            {"license_id": "lic-1", "company_name": "ร้าน ก", "company_code": "AAA111",
             "product_name": None, "warranty_end": None,
             "warranty_number": "W-1", "status": "active"},
            {"license_id": "lic-2", "company_name": "ร้าน ข", "company_code": "BBB222",
             "product_name": None, "warranty_end": None,
             "warranty_number": "W-2", "status": "active"},
        ]
        reply = await handle_registration(
            client, message="SHARED12345", ctx=_ctx(), audience="customer",
        )
        assert "AAA111" in reply and "BBB222" in reply

    async def test_an_unknown_serial_falls_back_to_the_normal_flow(self):
        """It must not dead-end: a serial nobody registered is just text,
        and the shop-name path still applies."""
        client = FakeRegClient(shops=[])
        client.serial_matches = []
        reply = await handle_registration(
            client, message="ZZZZ99999", ctx=_ctx(), audience="customer",
        )
        assert "ชื่อร้าน" in reply or "ยินดีต้อนรับ" in reply
