"""Phase 16 closing: display preferences that actually shape the text
(16.3), and the shop's side of a new customer link (16.4).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import onboarding, thai_datetime  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.thai_datetime import format_thai_date, local_today, set_display_prefs  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    set_display_prefs({})
    yield
    set_display_prefs({})


class TestDateFormat:
    def test_default_is_thai_text_with_the_buddhist_year(self):
        assert format_thai_date(date(2026, 9, 4)) == "4 ก.ย. 2569"

    def test_numeric_formats_follow_the_preference(self):
        set_display_prefs({"date_format": "dd/mm/yyyy", "language": "th"})
        assert format_thai_date(date(2026, 9, 4)) == "04/09/2569"
        set_display_prefs({"date_format": "yyyy-mm-dd", "language": "en"})
        assert format_thai_date(date(2026, 9, 4)) == "2026-09-04"
        set_display_prefs({"date_format": "mm/dd/yyyy", "language": "en"})
        assert format_thai_date(date(2026, 9, 4)) == "09/04/2026"

    def test_english_readers_get_a_gregorian_year(self):
        set_display_prefs({"language": "en"})
        assert format_thai_date(date(2026, 9, 4)) == "4 Sep 2026"

    def test_today_follows_the_timezone(self):
        set_display_prefs({"timezone": "Pacific/Kiritimati"})
        east = local_today()
        set_display_prefs({"timezone": "Pacific/Pago_Pago"})
        west = local_today()
        # UTC+14 and UTC-11 are 25 hours apart: for one hour a day (10:00–11:00 UTC)
        # their calendars differ by two days, not one.
        assert (east - west).days in (0, 1, 2)
        set_display_prefs({"timezone": "Not/AZone"})
        assert local_today() is not None


class TestPreferenceCommands:
    async def test_date_format_is_stored(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        reply = await handle_chat_message(client, message="รูปแบบวันที่ yyyy-mm-dd", ctx=_ctx())
        stored = [r for r in client.recorded if r[0] == "set_display_preferences"]
        assert stored and stored[0][2] == {"date_format": "yyyy-mm-dd"}
        assert "2569" in reply.text or "20" in reply.text

    async def test_an_unknown_format_lists_the_choices(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        reply = await handle_chat_message(client, message="รูปแบบวันที่ อะไรก็ได้", ctx=_ctx())
        assert "dd/mm/yyyy" in reply.text
        assert not [r for r in client.recorded if r[0] == "set_display_preferences"]

    async def test_timezone_is_validated_and_stored(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        await handle_chat_message(client, message="เขตเวลา Asia/Tokyo", ctx=_ctx())
        stored = [r for r in client.recorded if r[0] == "set_display_preferences"]
        assert stored and stored[0][2] == {"timezone": "Asia/Tokyo"}
        client2 = FakeDataClient(permission_keys=["ticket.read"])
        reply = await handle_chat_message(client2, message="เขตเวลา Mars/Olympus", ctx=_ctx())
        assert not [r for r in client2.recorded if r[0] == "set_display_preferences"]
        assert "Asia/Bangkok" in reply.text


class TestNewCustomerLink:
    def _members(self, client):
        client._members = [
            {"id": "m-1", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"},
            {"id": "m-2", "chann_uid": "CHN-TECH", "role": "technician", "status": "active"},
        ]

    async def test_auto_accept_on_creates_the_customer_and_tells_the_owner(self):
        client = FakeDataClient()
        self._members(client)
        client._settings = [{"setting_key": "auto_accept_new_customers", "setting_value": True}]
        client._profiles = {"CHN-C-1": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"}}
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name="Somchai",
        )
        assert result["created"] is True
        created = [r for r in client.recorded if r[0] == "create_customer"]
        assert created and created[0][2]["customer_chann_uid"] == "CHN-C-1"
        assert result["notified"] == 1
        notes = [r for r in client.recorded if r[0] == "create_notification"]
        assert notes and "เพิ่มเข้ารายชื่อลูกค้าให้แล้ว" in str(notes[-1])

    async def test_auto_accept_off_asks_the_shop(self):
        client = FakeDataClient()
        self._members(client)
        client._profiles = {"CHN-C-1": {"first_name": "สมชาย", "phone": "0812345678"}}
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name="Somchai",
        )
        assert result["created"] is False
        assert not [r for r in client.recorded if r[0] == "create_customer"]
        notes = [r for r in client.recorded if r[0] == "create_notification"]
        assert notes and "สร้างลูกค้า สมชาย 0812345678" in str(notes[-1])

    async def test_an_incomplete_profile_is_not_auto_created(self):
        client = FakeDataClient()
        self._members(client)
        client._settings = [{"setting_key": "auto_accept_new_customers", "setting_value": "true"}]
        client._profiles = {"CHN-C-1": {"first_name": "สมชาย"}}
        result = await onboarding.after_customer_linked(
            client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name=None,
        )
        assert result["created"] is False

    async def test_the_owner_switches_it_on_from_chat(self):
        client = FakeDataClient(permission_keys=["setting.manage", "ticket.read"])
        reply = await handle_chat_message(client, message="ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด", ctx=_ctx())
        stored = [r for r in client.recorded if r[0] == "put_license_setting"]
        assert stored and stored[0][2] == "auto_accept_new_customers" and stored[0][3] is True
        assert "เปิด" in reply.text

    async def test_without_setting_manage_it_is_refused(self):
        client = FakeDataClient(permission_keys=["ticket.read"])
        reply = await handle_chat_message(client, message="ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด", ctx=_ctx())
        assert not [r for r in client.recorded if r[0] == "put_license_setting"]
        assert "ไม่มีสิทธิ์" in reply.text
