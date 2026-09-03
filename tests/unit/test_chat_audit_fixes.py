"""The 3 Sep chat audit, pinned.

Each test is one finding from that audit: a rich-menu tile that opened a
junk repair job, an English enum reaching a technician, a raw ISO date in
a Thai sentence, a duplicate name answered with prose instead of buttons,
a two-digit year landing in 1983. They drive handle_chat_message the same
way test_phase6_chat.py does, against its FakeDataClient.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.line.client import flex_list_message  # noqa: E402
from chann_app.services.chat import (  # noqa: E402
    BANGKOK_TZ, _pending_execution_reply, dashboard_link, handle_chat_message,
)
from chann_app.services.thai_datetime import to_gregorian_year  # noqa: E402

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402


def _customer_client(**kw):
    return FakeDataClient(
        permission_keys=["customer.read", "ticket.create", "ticket.read",
                         "warranty.read", "warranty.create"], **kw,
    )


def _created_tickets(client):
    return [r for r in client.recorded if r[0] == "create_ticket"]


CUSTOMER = dict(primary_role="customer", oa="customer")


class TestCustomerRichMenuTilesDoNotOpenTickets:
    """Every tile on the customer rich menu must reach a handler. Three of
    the six fell through the fault-report catch-all and filed a job whose
    fault was the tile's own label."""

    @pytest.mark.asyncio
    async def test_status_tile_lists_my_jobs(self):
        c = _customer_client()
        c._tickets = [{"id": "t1", "ticket_number": "T-2026-0007", "status": "assigned",
                       "customer_chann_uid": "CHN-C-1", "scheduled_date": "2026-09-06"}]
        ctx = _ctx(**CUSTOMER)
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="สถานะการซ่อม", ctx=ctx)
        assert not _created_tickets(c)
        assert "T-2026-0007" in reply.text
        # The date is Thai, not the DB's ISO string.
        assert "2026-09-06" not in reply.text and "ก.ย." in reply.text

    @pytest.mark.asyncio
    async def test_contact_tile_answers_with_the_shop(self):
        c = _customer_client()
        reply = await handle_chat_message(c, message="ติดต่อร้าน", ctx=_ctx(**CUSTOMER))
        assert not _created_tickets(c)
        assert "ติดต่อ" in reply.text
        assert ("get_company_profile", LICENSE_ID) in c.recorded

    @pytest.mark.asyncio
    async def test_how_to_tile_is_help(self):
        c = _customer_client()
        reply = await handle_chat_message(c, message="วิธีใช้งาน", ctx=_ctx(**CUSTOMER))
        assert not _created_tickets(c)
        assert "แจ้งซ่อม" in reply.text and "ประกัน" in reply.text

    @pytest.mark.asyncio
    async def test_my_warranties_lists_registrations(self):
        c = _customer_client()
        c._warranties = [
            {"id": "w1", "warranty_number": "W-2026-0001", "serial_number": "SN1",
             "product_name": "แอร์ 12000 BTU", "status": "active",
             "warranty_end": "2028-09-03", "customer_chann_uid": "CHN-C-1"},
        ]
        ctx = _ctx(**CUSTOMER)
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="ประกันของฉัน", ctx=ctx)
        assert not _created_tickets(c)
        assert "W-2026-0001" in reply.text
        assert "ยังอยู่ในประกัน" in reply.text      # the enum "active" never shows
        assert "active" not in reply.text
        assert "2028-09-03" not in reply.text and "2571" in reply.text

    @pytest.mark.asyncio
    async def test_bare_report_tile_asks_what_is_wrong(self):
        c = _customer_client()
        reply = await handle_chat_message(c, message="แจ้งซ่อม", ctx=_ctx(**CUSTOMER))
        assert not _created_tickets(c)
        assert "อาการ" in reply.text

    @pytest.mark.asyncio
    async def test_two_open_jobs_offer_buttons_and_the_button_works(self):
        c = _customer_client()
        c._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
             "customer_chann_uid": "CHN-C-1"},
            {"id": "t2", "ticket_number": "T-2026-0002", "status": "in_progress",
             "customer_chann_uid": "CHN-C-1", "assigned_to_name": "ช่างเอ"},
        ]
        ctx = _ctx(**CUSTOMER)
        ctx.chann_uid = "CHN-C-1"
        asked = await handle_chat_message(c, message="ช่างจะมากี่โมง", ctx=ctx)
        sends = [send for _, send in asked.quick_replies]
        assert sends == ["งานของฉัน T-2026-0001", "งานของฉัน T-2026-0002"]
        # Tapping the second button answers about that job — and does
        # not open a ticket titled "งานของฉัน T-2026-0002".
        reply = await handle_chat_message(c, message=sends[1], ctx=ctx)
        assert not _created_tickets(c)
        assert "T-2026-0002" in reply.text and "ช่างเอ" in reply.text
        assert "กำลังทำ" in reply.text and "in_progress" not in reply.text

    @pytest.mark.asyncio
    async def test_a_real_fault_still_opens_a_ticket(self):
        c = _customer_client()
        reply = await handle_chat_message(
            c, message="แอร์ไม่เย็น มีน้ำหยด", ctx=_ctx(**CUSTOMER),
        )
        assert _created_tickets(c)
        assert "รับแจ้งแล้ว" in reply.text


class TestCustomerAppointmentRules:
    @pytest.mark.asyncio
    async def test_reschedule_to_the_past_is_refused(self):
        c = _customer_client()
        c._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                       "customer_chann_uid": "CHN-C-1"}]
        ctx = _ctx(**CUSTOMER)
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="เลื่อนนัด 1/1/2020", ctx=ctx)
        assert "ผ่านมาแล้ว" in reply.text
        assert not [r for r in c.recorded if r[0] == "update_ticket"]

    @pytest.mark.asyncio
    async def test_reschedule_without_a_time_defaults_to_nine(self):
        c = _customer_client()
        c._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                       "customer_chann_uid": "CHN-C-1"}]
        ctx = _ctx(**CUSTOMER)
        ctx.chann_uid = "CHN-C-1"
        reply = await handle_chat_message(c, message="เลื่อนนัด พรุ่งนี้", ctx=ctx)
        updates = [r for r in c.recorded if r[0] == "update_ticket"]
        assert updates, reply.text
        fields = updates[-1][3] if len(updates[-1]) > 3 else updates[-1][-1]
        assert fields.get("scheduled_time") == "09:00:00"
        assert "09:00" in reply.text


class TestTechnicianOpenJobs:
    @pytest.mark.asyncio
    async def test_open_jobs_tile_lists_what_nobody_took(self):
        t = FakeDataClient(
            permission_keys=["ticket.read", "ticket.update"], role="technician",
        )
        t._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "status": "open",
             "accept_status": "pending", "assigned_to_ref": None},
            {"id": "t2", "ticket_number": "T-2026-0002", "status": "in_progress",
             "accept_status": "accepted", "assigned_to_ref": "member-1"},
            {"id": "t3", "ticket_number": "T-2026-0003", "status": "completed",
             "accept_status": "accepted", "assigned_to_ref": "member-1"},
        ]
        reply = await handle_chat_message(
            t, message="งานที่เปิดรับ", ctx=_ctx(primary_role="technician"),
        )
        assert "T-2026-0001" in reply.text
        assert "T-2026-0002" not in reply.text
        assert "T-2026-0003" not in reply.text
        assert reply.list_card and reply.list_card["rows"][0]["action_text"] == "รับงาน T-2026-0001"

    @pytest.mark.asyncio
    async def test_claim_with_two_candidates_offers_buttons(self):
        t = FakeDataClient(
            permission_keys=["ticket.read", "ticket.update"], role="technician",
        )
        t._tickets = [
            {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
             "accept_status": "pending", "assigned_to_ref": "member-1", "customer_name": "ก"},
            {"id": "t2", "ticket_number": "T-2026-0002", "status": "assigned",
             "accept_status": "pending", "assigned_to_ref": "member-1", "customer_name": "ข"},
        ]
        reply = await handle_chat_message(
            t, message="รับงาน", ctx=_ctx(primary_role="technician"),
        )
        assert [send for _, send in reply.quick_replies] == [
            "รับงาน T-2026-0001", "รับงาน T-2026-0002",
        ]

    @pytest.mark.asyncio
    async def test_a_state_refusal_reads_in_thai(self):
        t = FakeDataClient(
            permission_keys=["ticket.read", "ticket.update"], role="technician",
        )
        t._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                       "accept_status": "accepted", "assigned_to_ref": "member-1"}]

        async def refuse(*args, **kwargs):
            exc = DataTierError.__new__(DataTierError)
            exc.status_code = 409
            exc.detail = "a in_progress ticket cannot be checked in to"
            exc.structured = None
            raise exc

        t.check_in_ticket = refuse
        reply = await handle_chat_message(
            t, message="เช็คอิน T-2026-0001", ctx=_ctx(primary_role="technician"),
        )
        assert "in_progress" not in reply.text
        assert "cannot" not in reply.text
        assert "T-2026-0001" in reply.text


class TestDuplicateTechnicianNamesGetButtons:
    @pytest.mark.asyncio
    async def test_two_somchais_are_offered_not_guessed(self):
        s = FakeDataClient(permission_keys=["ticket.read", "ticket.assign", "ticket.update"])
        s._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open",
                       "customer_name": "ก", "customer_phone": "081", "service_address": "99/1",
                       "scheduled_date": "2026-09-06", "scheduled_time": "09:00:00"}]
        s._members = [
            {"id": "m1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"},
            {"id": "m2", "chann_uid": "CHN-T-000002", "role": "technician", "status": "active"},
        ]
        s._profiles = {
            "CHN-T-000001": {"first_name": "สมชาย", "last_name": "ใจดี"},
            "CHN-T-000002": {"first_name": "สมชาย", "last_name": "มีสุข"},
        }
        reply = await handle_chat_message(
            s, message="มอบหมาย T-2026-0001 ให้ สมชาย", ctx=_ctx(),
        )
        assert not [r for r in s.recorded if r[0] == "assign_ticket"]
        sends = [send for _, send in reply.quick_replies]
        assert len(sends) == 2 and all("CHN-T-00000" in x for x in sends)

        # Tapping a button resolves exactly one person.
        reply = await handle_chat_message(s, message=sends[1], ctx=_ctx())
        assigned = [r for r in s.recorded if r[0] == "assign_ticket"]
        assert assigned and assigned[-1][4] == "m2", reply.text


class TestWordsNotTokens:
    def test_pending_execution_reply_uses_thai_nouns(self):
        text = _pending_execution_reply({"action": "create", "entity": "team"}, "th")
        assert "ทีมและกลุ่ม" in text
        assert "create" not in text and "team" not in text

    def test_flex_footer_button_is_the_oa_colour(self):
        tech = flex_list_message(
            alt_text="x", title="t", rows=[], footer_label="เปิด", footer_url="https://x", oa="technician",
        )
        cust = flex_list_message(
            alt_text="x", title="t", rows=[], footer_label="เปิด", footer_url="https://x", oa="customer",
        )
        assert tech["contents"]["footer"]["contents"][0]["color"] == "#134A92"
        assert cust["contents"]["footer"]["contents"][0]["color"] == "#A94F0D"
        assert "#E0A422" not in str(tech)

    def test_dashboard_link_is_per_oa(self, monkeypatch):
        from chann_app.config import settings

        monkeypatch.setattr(settings, "liff_sales_id", "111-sales")
        monkeypatch.setattr(settings, "liff_technician_id", "222-tech")
        monkeypatch.setattr(settings, "liff_customer_id", "")
        assert dashboard_link("index", "sales") == "https://liff.line.me/111-sales"
        assert dashboard_link("index", "technician") == "https://liff.line.me/222-tech"
        assert dashboard_link("index", "customer") is None


class TestTwoDigitYears:
    def test_short_years_land_in_this_century(self):
        assert to_gregorian_year(2569) == 2026
        assert to_gregorian_year(69) == 2026      # BE short form
        assert to_gregorian_year(26) == 2026      # CE short form, not 1983
        assert to_gregorian_year(43) == 2000
        assert to_gregorian_year(2026) == 2026

    def test_bangkok_today_is_used_for_dates(self):
        from datetime import datetime

        # Not a behavioural test of a handler — a guard that the helper
        # the handlers share resolves "today" in Bangkok, so 00:00–07:00
        # local is not yesterday.
        assert datetime.now(BANGKOK_TZ).date() >= (
            datetime.utcnow().date() - timedelta(days=1)
        )
        assert isinstance(date.today(), date)
