"""The daily reminder sweep.

Both bugs covered here reached production and neither was caught by the
existing suite, for the same reason: nothing exercised `sweep_due_follow_ups`
against realistic Data-tier output at all.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.reminders import _coerce_date, sweep_due_follow_ups  # noqa: E402

BANGKOK = timezone(timedelta(hours=7))


class TestCoerceDate:
    """A TypeError comparing str to date crashed the whole sweep in
    production, with code that read correctly in isolation and could not be
    reproduced from any obvious input. Rather than keep guessing which wire
    format caused it, the parser now accepts every plausible one — these
    pin that down."""

    def test_a_plain_iso_date_string(self):
        assert _coerce_date("2026-09-01") == date(2026, 9, 1)

    def test_an_iso_datetime_string(self):
        assert _coerce_date("2026-09-01T14:30:00") == date(2026, 9, 1)

    def test_an_iso_datetime_with_offset(self):
        assert _coerce_date("2026-09-01T14:30:00+07:00") == date(2026, 9, 1)

    def test_a_zulu_timestamp(self):
        assert _coerce_date("2026-09-01T07:30:00Z") == date(2026, 9, 1)

    def test_an_actual_date_object_passes_through(self):
        assert _coerce_date(date(2026, 9, 1)) == date(2026, 9, 1)

    def test_a_datetime_object_is_narrowed_to_its_date(self):
        assert _coerce_date(datetime(2026, 9, 1, 14, 30)) == date(2026, 9, 1)

    def test_unreadable_values_return_none_rather_than_raising(self):
        """None is what lets the caller skip one bad row and log it, instead
        of one malformed record stopping every other tenant's reminders."""
        for bad in (None, "", "   ", "not a date", "31/02/2026", 12345):
            assert _coerce_date(bad) is None, f"{bad!r} should not parse"


class _FakeClient:
    def __init__(self, licenses, due_by_license):
        self._licenses = licenses
        self._due = due_by_license
        self.list_calls: list[dict] = []
        self.pushed: list[dict] = []

    async def list_licenses(self, status=None, exclude_status=None):
        self.list_calls.append({"status": status, "exclude_status": exclude_status})
        rows = list(self._licenses)
        if status:
            rows = [r for r in rows if r["status"] == status]
        if exclude_status:
            rows = [r for r in rows if r["status"] != exclude_status]
        return rows

    async def due_follow_ups(self, license_id, days=1):
        return list(self._due.get(license_id, []))

    async def create_notification(self, license_id, **kwargs):
        self.pushed.append({"license_id": license_id, **kwargs})
        return {"id": "notif-1", **kwargs}


@pytest.fixture
def no_line_push(monkeypatch):
    """send_notification records and pushes; the push needs LINE credentials
    this test has no business needing, so only the recording half runs."""
    import chann_app.services.reminders as reminders

    async def fake_send(client, **kwargs):
        return await client.create_notification(kwargs.pop("license_id"), **kwargs)

    monkeypatch.setattr(reminders, "send_notification", fake_send)


class TestTenantSelection:
    async def test_trial_tenants_are_swept_not_just_active_ones(self, no_line_push):
        """The original filter was status="active". A license defaults to
        "trial" (see License.status), so the sweep silently processed zero
        tenants and reported a clean {"tenants": 0} — a bug that looks
        exactly like success."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[
                {"id": "lic-trial", "status": "trial", "created_by_chann_uid": "CHN-1"},
                {"id": "lic-active", "status": "active", "created_by_chann_uid": "CHN-2"},
            ],
            due_by_license={
                "lic-trial": [{
                    "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                    "due_date": today.isoformat(), "due_time": None, "notes": "ตามลูกค้า",
                }],
                "lic-active": [{
                    "id": "fu-2", "entity_type": "deal", "entity_id": "d-2",
                    "due_date": today.isoformat(), "due_time": None, "notes": None,
                }],
            },
        )
        summary = await sweep_due_follow_ups(client)
        assert summary["tenants"] == 2, "a trial tenant must not be skipped"
        assert summary["sent"] == 2

    async def test_suspended_tenants_are_excluded(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[
                {"id": "lic-ok", "status": "trial", "created_by_chann_uid": "CHN-1"},
                {"id": "lic-off", "status": "suspended", "created_by_chann_uid": "CHN-2"},
            ],
            due_by_license={
                "lic-ok": [{
                    "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                    "due_date": today.isoformat(), "due_time": None, "notes": None,
                }],
                "lic-off": [{
                    "id": "fu-2", "entity_type": "deal", "entity_id": "d-2",
                    "due_date": today.isoformat(), "due_time": None, "notes": None,
                }],
            },
        )
        summary = await sweep_due_follow_ups(client)
        assert summary["tenants"] == 1
        assert [p["license_id"] for p in client.pushed] == ["lic-ok"]


class TestSweepResilience:
    async def test_a_datetime_shaped_due_date_does_not_crash_the_sweep(self, no_line_push):
        """The production failure: a due_date the comparison could not handle
        took down the entire sweep, for every tenant, not just that row."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                "due_date": f"{today.isoformat()}T00:00:00+07:00",
                "due_time": None, "notes": "ตามงาน",
            }]},
        )
        summary = await sweep_due_follow_ups(client)
        assert summary["sent"] == 1

    async def test_one_unreadable_row_is_skipped_not_fatal(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [
                {"id": "bad", "entity_type": "deal", "entity_id": "d-0",
                 "due_date": "definitely not a date", "due_time": None, "notes": None},
                {"id": "good", "entity_type": "deal", "entity_id": "d-1",
                 "due_date": today.isoformat(), "due_time": None, "notes": None},
            ]},
        )
        summary = await sweep_due_follow_ups(client)
        assert summary["skipped"] >= 1
        assert summary["sent"] == 1, "the good row must still go out"

    async def test_future_rows_beyond_the_window_are_not_announced_early(self, no_line_push):
        """due_follow_ups takes a day count, so a sweep for today would
        otherwise announce next week's work today and again next week."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                "due_date": (today + timedelta(days=6)).isoformat(),
                "due_time": None, "notes": None,
            }]},
        )
        summary = await sweep_due_follow_ups(client, days=0)
        assert summary["sent"] == 0

    async def test_a_row_with_no_owner_is_counted_not_silently_dropped(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": None}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        summary = await sweep_due_follow_ups(client)
        assert summary["skipped"] == 1
        assert summary["sent"] == 0
