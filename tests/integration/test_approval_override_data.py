"""Round 19f, through both tiers into PostgreSQL: a report whose step waits
on one named member can be approved by the owner (owner, 16 ก.ย. 2569:
"อนุมัติ" did nothing and the ticket stayed stuck)."""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_completion_notice_data import REPORT, _api, field_shop  # noqa: E402,F401


def _report_status(db, report_id: str) -> str:
    from sqlalchemy import text
    with db.connect() as conn:
        return conn.execute(text("SELECT status FROM service_reports WHERE id = :rid"), {"rid": report_id}).scalar_one()


@pytest.mark.usefixtures("migrated_db")
class TestTheOwnerApprovesAStepThatWaitsOnSomeoneElse:
    def test_pending_lists_it_and_approve_succeeds(self, field_shop):
        shop = field_shop
        http, license_id = shop["http"], shop["license_id"]
        # The ticket is opened by the technician's own member, so the default
        # workflow's `ticket_owner` step waits on THAT member — not the owner.
        shop["as_technician"]()
        response = http.post(_api(license_id, "/tickets"), json={
            "issue_description": "แอร์ไม่เย็น", "customer_name": "สมหญิง ใจดี", "customer_phone": "0812345678",
            "service_address": "99/1 ถนนสุขุมวิท", "scheduled_date": date(2026, 9, 21).isoformat(),
            "scheduled_time": time(10, 0).isoformat(),
        })
        assert response.status_code in (200, 201), response.text
        ticket = response.json()
        shop["as_owner"]()
        assigned = http.post(_api(license_id, f"/tickets/{ticket['id']}/assign"),
                             json={"target_type": "technician", "target_ref": shop["technician_member_id"]})
        assert assigned.status_code == 200, assigned.text
        shop["as_technician"]()
        for step_path, body in (("/claim", {}), ("/check-in", {}), ("/check-out", {"report_data": dict(REPORT)})):
            done = http.post(_api(license_id, f"/tickets/{ticket['id']}{step_path}"), json=body)
            assert done.status_code == 200, (step_path, done.text)
        report = done.json()
        assert _report_status(shop["db"], report["id"]) == "submitted"

        shop["as_owner"]()
        pending = http.get(_api(license_id, "/approvals/pending"))
        assert pending.status_code == 200, pending.text
        rows = [r for r in pending.json() if str(r["report"]["id"]) == str(report["id"])]
        assert rows, pending.json()
        step = rows[0]["step"]
        assert step["approver_type"] == "user" and step["approver_ref"] == shop["technician_member_id"], step

        approved = http.post(_api(license_id, f"/approvals/{step['id']}/approve"), json={})
        assert approved.status_code == 200, approved.text
        assert approved.json()["report_status"] == "approved"
        assert _report_status(shop["db"], report["id"]) == "approved"
