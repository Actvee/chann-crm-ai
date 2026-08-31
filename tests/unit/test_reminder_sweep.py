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
        self.line_lookups: list[str] = []
        # Every owner has a LINE account unless a test says otherwise.
        self.line_targets: dict[str, str | None] = {
            "CHN-1": "Uline1", "CHN-2": "Uline2",
        }
        self.customers: list[dict] = []
        self.deals: list[dict] = []
        self.already_announced: dict[str, list[str]] = {}

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

    async def announced_today(self, license_id, notification_type):
        return set(self.already_announced.get(license_id, []))

    async def line_target_of(self, chann_uid):
        self.line_lookups.append(chann_uid)
        return self.line_targets.get(chann_uid)

    async def list_customers(self, license_id, stage=None):
        return list(self.customers)

    async def list_deals(self, license_id, stage=None):
        return list(self.deals)

    async def list_quotes(self, license_id, status=None):
        return []


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


class TestActualDelivery:
    """A reminder that is recorded but never pushed is worse than one that
    fails loudly: the sweep reported {"sent": 1} while nothing arrived in
    LINE, because target_line_user_id was hardcoded to None and
    send_notification treats that as "dashboard only".
    """

    async def test_the_line_target_is_resolved_before_sending(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        await sweep_due_follow_ups(client)
        assert client.line_lookups == ["CHN-1"], "the owner's LINE id must be looked up"
        assert client.pushed[0]["target_line_user_id"] == "Uline1", (
            "a resolved LINE target must reach send_notification, or the push "
            "is silently downgraded to a dashboard-only record"
        )

    async def test_the_follow_up_s_own_owner_wins_over_the_license_creator(self, no_line_push):
        """owner_chann_uid comes off the follow-up now that the Data tier
        resolves it; the license creator is only the fallback."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                "owner_chann_uid": "CHN-2",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        await sweep_due_follow_ups(client)
        assert client.pushed[0]["target_chann_uid"] == "CHN-2"
        assert client.pushed[0]["target_line_user_id"] == "Uline2"

    async def test_an_owner_with_no_line_account_still_records_the_notification(
        self, no_line_push,
    ):
        """Undeliverable over LINE is not the same as not worth recording —
        it should still show up on the dashboard."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-NOLINE"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d-1",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        client.line_targets["CHN-NOLINE"] = None
        summary = await sweep_due_follow_ups(client)
        assert summary["sent"] == 1
        assert client.pushed[0]["target_line_user_id"] is None


class TestReminderMessageContent:
    """What the message actually says.

    Every reminder used to read "เตือนงานวันนี้: customer" — the entity type
    as a fallback, because notes were never stored and nothing resolved the
    record to a name. It was true, delivered, counted as sent, and told the
    salesperson nothing about who or what.

    Nothing in this suite checked the message body until that reached a real
    user, which is why these assert on text rather than on counters.
    """

    async def test_a_customer_reminder_names_the_customer(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "customer", "entity_id": "cust-1",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        client.customers = [{
            "id": "cust-1", "customer_id": "C-2026-0005",
            "first_name": "จุใจ", "last_name": "มาติกา",
        }]
        await sweep_due_follow_ups(client)
        message = client.pushed[0]["message"]
        assert "จุใจ มาติกา" in message
        assert "C-2026-0005" in message
        assert message.strip() != "เตือนงานวันนี้: customer"

    async def test_the_subject_the_person_typed_is_included(self, no_line_push):
        """"นัดดูสินค้า" is the whole point of the reminder; a date alone
        does not tell anyone what to do."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "customer", "entity_id": "cust-1",
                "due_date": today.isoformat(), "due_time": "15:00:00",
                "notes": "ดูสินค้า",
            }]},
        )
        client.customers = [{
            "id": "cust-1", "customer_id": "C-2026-0005",
            "first_name": "จุใจ", "last_name": "มาติกา",
        }]
        await sweep_due_follow_ups(client)
        message = client.pushed[0]["message"]
        assert "ดูสินค้า" in message
        assert "15:00" in message
        assert "จุใจ" in message

    async def test_a_deal_reminder_names_the_deal_code(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "deal-1",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        client.deals = [{"id": "deal-1", "deal_id": "D-2026-0003"}]
        await sweep_due_follow_ups(client)
        assert "D-2026-0003" in client.pushed[0]["message"]

    async def test_an_unresolvable_record_still_sends_something_readable(self, no_line_push):
        """A reminder that says less is still worth sending — but it must
        not crash, and it must not be blank."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "customer", "entity_id": "gone",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        await sweep_due_follow_ups(client)
        message = client.pushed[0]["message"]
        assert message.strip()
        assert "ลูกค้า" in message


class TestDigestBatching:
    """One message per person, not one per follow-up.

    Five due items used to arrive as five separate LINE pushes — harder to
    read than a list, and five of the 500 free pushes a LINE account gets
    each month.
    """

    def _client_with(self, items, license_id="lic-1", owner="CHN-1"):
        today = datetime.now(BANGKOK).date()
        due = []
        for index, (entity_type, entity_id, due_time, notes) in enumerate(items):
            due.append({
                "id": f"fu-{index}", "entity_type": entity_type, "entity_id": entity_id,
                "due_date": today.isoformat(), "due_time": due_time, "notes": notes,
                "owner_chann_uid": owner,
            })
        client = _FakeClient(
            licenses=[{"id": license_id, "status": "trial", "created_by_chann_uid": owner}],
            due_by_license={license_id: due},
        )
        client.customers = [
            {"id": "c1", "customer_id": "C-2026-0005", "first_name": "จุใจ", "last_name": "มาติกา"},
            {"id": "c2", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": ""},
        ]
        client.deals = [{"id": "d1", "deal_id": "D-2026-0003"}]
        return client

    async def test_three_follow_ups_produce_one_line_message(self, no_line_push):
        client = self._client_with([
            ("customer", "c1", "15:00:00", "ดูสินค้า"),
            ("deal", "d1", "09:00:00", "ประชุมสรุปราคา"),
            ("customer", "c2", None, ""),
        ])
        summary = await sweep_due_follow_ups(client)
        line_pushes = [p for p in client.pushed if p.get("delivery_line") is not False]
        assert len(line_pushes) == 1, f"expected one push, got {len(line_pushes)}"
        assert summary["sent"] == 1
        assert summary["due"] == 3

    async def test_the_digest_contains_every_item(self, no_line_push):
        client = self._client_with([
            ("customer", "c1", "15:00:00", "ดูสินค้า"),
            ("deal", "d1", "09:00:00", "ประชุมสรุปราคา"),
            ("customer", "c2", None, ""),
        ])
        await sweep_due_follow_ups(client)
        message = client.pushed[0]["message"]
        for expected in ("จุใจ", "D-2026-0003", "สมชาย", "ดูสินค้า", "ประชุมสรุปราคา"):
            assert expected in message, f"{expected!r} missing from digest:\n{message}"

    async def test_timed_items_come_first_and_in_clock_order(self, no_line_push):
        """Someone reading this at 08:00 wants to know what is coming next."""
        client = self._client_with([
            ("customer", "c2", None, "ไม่มีเวลา"),
            ("customer", "c1", "15:00:00", "บ่าย"),
            ("deal", "d1", "09:00:00", "เช้า"),
        ])
        await sweep_due_follow_ups(client)
        message = client.pushed[0]["message"]
        assert message.index("09:00") < message.index("15:00") < message.index("ไม่มีเวลา")

    async def test_two_people_get_their_own_digest(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [
                {"id": "a", "entity_type": "deal", "entity_id": "d1",
                 "due_date": today.isoformat(), "due_time": None, "notes": None,
                 "owner_chann_uid": "CHN-1"},
                {"id": "b", "entity_type": "deal", "entity_id": "d1",
                 "due_date": today.isoformat(), "due_time": None, "notes": None,
                 "owner_chann_uid": "CHN-2"},
            ]},
        )
        client.deals = [{"id": "d1", "deal_id": "D-2026-0003"}]
        await sweep_due_follow_ups(client)
        line_pushes = [p for p in client.pushed if p.get("delivery_line") is not False]
        assert {p["target_chann_uid"] for p in line_pushes} == {"CHN-1", "CHN-2"}


class TestIdempotency:
    """Cloud Scheduler retries a failed run, and a run can fail after sending
    some of its messages. Re-sending what already went out teaches people to
    ignore the reminders."""

    async def test_an_already_announced_item_is_not_resent(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [{
                "id": "fu-1", "entity_type": "deal", "entity_id": "d1",
                "due_date": today.isoformat(), "due_time": None, "notes": None,
            }]},
        )
        client.deals = [{"id": "d1", "deal_id": "D-2026-0003"}]
        client.already_announced = {"lic-1": ["d1"]}
        summary = await sweep_due_follow_ups(client)
        assert summary["sent"] == 0
        assert summary["skipped"] == 1
        assert not [p for p in client.pushed if p.get("delivery_line") is not False]

    async def test_every_digest_item_is_recorded_so_a_retry_sees_it(self, no_line_push):
        """The pushed message names only the first item; the rest need their
        own rows or a retry would rebuild a digest containing them."""
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [
                {"id": "a", "entity_type": "deal", "entity_id": "d1",
                 "due_date": today.isoformat(), "due_time": "09:00:00", "notes": None},
                {"id": "b", "entity_type": "customer", "entity_id": "c1",
                 "due_date": today.isoformat(), "due_time": "10:00:00", "notes": None},
            ]},
        )
        client.deals = [{"id": "d1", "deal_id": "D-2026-0003"}]
        client.customers = [{"id": "c1", "customer_id": "C-2026-0001", "first_name": "ก", "last_name": ""}]
        await sweep_due_follow_ups(client)
        recorded = {p["entity_id"] for p in client.pushed}
        assert recorded == {"d1", "c1"}, f"not every item was recorded: {recorded}"

    async def test_the_marker_rows_do_not_send_a_second_line_message(self, no_line_push):
        today = datetime.now(BANGKOK).date()
        client = _FakeClient(
            licenses=[{"id": "lic-1", "status": "trial", "created_by_chann_uid": "CHN-1"}],
            due_by_license={"lic-1": [
                {"id": "a", "entity_type": "deal", "entity_id": "d1",
                 "due_date": today.isoformat(), "due_time": "09:00:00", "notes": None},
                {"id": "b", "entity_type": "deal", "entity_id": "d2",
                 "due_date": today.isoformat(), "due_time": "10:00:00", "notes": None},
            ]},
        )
        client.deals = [
            {"id": "d1", "deal_id": "D-2026-0003"}, {"id": "d2", "deal_id": "D-2026-0004"},
        ]
        await sweep_due_follow_ups(client)
        line_pushes = [p for p in client.pushed if p.get("delivery_line") is not False]
        assert len(line_pushes) == 1
