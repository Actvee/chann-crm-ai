"""Round 19f, through both tiers into PostgreSQL: a customer's report is
private until the shop opens it; a technician cannot take it before, can
after; the release refuses an incomplete job the way assign does."""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_completion_notice_data import _api, field_shop  # noqa: E402,F401 — the fixture


def _visibility(db, ticket_id: str) -> str:
    from sqlalchemy import text
    with db.connect() as conn:
        return conn.execute(text("SELECT visibility FROM service_tickets WHERE id = :tid"), {"tid": ticket_id}).scalar_one()


@pytest.mark.usefixtures("migrated_db")
class TestAHeldJobIsOpenedByTheShop:
    def test_private_until_released_then_first_come(self, field_shop):
        shop = field_shop
        http, license_id = shop["http"], shop["license_id"]
        shop["as_customer"]()
        response = http.post(_api(license_id, "/tickets"), json={
            "issue_description": "พัดลมไม่หมุน", "customer_name": "สมหญิง ใจดี", "customer_phone": "0812345678",
            "service_address": "99/1 ถนนสุขุมวิท", "scheduled_date": date(2026, 9, 20).isoformat(),
            "scheduled_time": time(10, 0).isoformat(),
        })
        assert response.status_code in (200, 201), response.text
        ticket = response.json()
        assert _visibility(shop["db"], ticket["id"]) == "private"

        shop["as_technician"]()
        refused = http.post(_api(license_id, f"/tickets/{ticket['id']}/claim"), json={})
        assert refused.status_code == 409, refused.text
        assert "not open to you" in refused.text
        listed = http.get(_api(license_id, "/tickets"), params={"visible_to": shop["technician_member_id"]})
        assert ticket["ticket_number"] not in listed.text

        shop["as_owner"]()
        released = http.post(_api(license_id, f"/tickets/{ticket['id']}/release"))
        assert released.status_code == 200, released.text
        assert released.json()["visibility"] == "public"
        assert _visibility(shop["db"], ticket["id"]) == "public"
        assert any(oa == "technician" and "รับงาน" in str(text) for oa, _to, text in shop["pushed"]), shop["pushed"]

        shop["as_technician"]()
        taken = http.post(_api(license_id, f"/tickets/{ticket['id']}/claim"), json={})
        assert taken.status_code == 200, taken.text
        assert taken.json()["accept_status"] == "accepted"

    def test_an_incomplete_job_cannot_be_released(self, field_shop):
        shop = field_shop
        http, license_id = shop["http"], shop["license_id"]
        shop["as_customer"]()
        response = http.post(_api(license_id, "/tickets"), json={"issue_description": "แอร์ไม่เย็น", "customer_name": "สมหญิง", "customer_phone": "0812345678"})
        assert response.status_code in (200, 201), response.text
        ticket = response.json()
        shop["as_owner"]()
        blocked = http.post(_api(license_id, f"/tickets/{ticket['id']}/release"))
        assert blocked.status_code == 409, blocked.text
        assert "dispatch_blocked" in blocked.text
        assert _visibility(shop["db"], ticket["id"]) == "private"
