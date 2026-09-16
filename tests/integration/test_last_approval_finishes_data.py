"""Round 19r, through both tiers into PostgreSQL: the LAST approval
actually finishes the report.

Owner, 16 ก.ย. 2569: "กดอนุมัติไปแล้วแต่ยังไม่มีแจ้งเตือนไปยังลูกค้าหรือทีม
ช่าง" and "ทั้งที่ร้านมีแค่คนเดียวแต่ยังขึ้น 'อนุมัติขั้นถัดไป'".

`ApprovalRepository.act` set `step.status = "approved"` in memory and then
asked the database whether any step was still pending. Production's session
is built with autoflush=False (chann_data/db.py), so the step it had just
approved was still `pending` on disk: the report stayed "submitted", no
document was issued, no survey was created, and the shop was told a step
remained. This suite passed throughout, because its own session was built
with SQLAlchemy's default autoflush=True — the harness was kinder than
production, which is the one thing a harness must never be.
"""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_completion_notice_data import REPORT, _api, field_shop  # noqa: E402,F401


def _one(db, sql: str, **params):
    with db.connect() as conn:
        return conn.execute(text(sql), params).first()


@pytest.mark.usefixtures("migrated_db")
class TestTheLastApprovalFinishesTheReport:
    def _filed_report(self, shop):
        http, license_id = shop["http"], shop["license_id"]
        shop["as_technician"]()
        ticket = http.post(_api(license_id, "/tickets"), json={
            "issue_description": "แอร์ไม่เย็น", "customer_name": "สมหญิง ใจดี",
            "customer_phone": "0812345678", "service_address": "99/1",
            "scheduled_date": date(2026, 9, 21).isoformat(),
            "scheduled_time": time(10, 0).isoformat(),
        }).json()
        shop["as_owner"]()
        http.post(_api(license_id, f"/tickets/{ticket['id']}/assign"),
                  json={"target_type": "technician", "target_ref": shop["technician_member_id"]})
        shop["as_technician"]()
        for path, body in (("/claim", {}), ("/check-in", {}), ("/check-out", {"report_data": dict(REPORT)})):
            done = http.post(_api(license_id, f"/tickets/{ticket['id']}{path}"), json=body)
            assert done.status_code == 200, (path, done.text)
        return ticket, done.json()

    def test_the_report_is_approved_and_the_survey_exists(self, field_shop):
        shop = field_shop
        http, license_id = shop["http"], shop["license_id"]
        ticket, report = self._filed_report(shop)

        shop["as_owner"]()
        pending = http.get(_api(license_id, "/approvals/pending")).json()
        mine = [r for r in pending if str(r["report"]["id"]) == str(report["id"])]
        assert mine, pending
        out = http.post(_api(license_id, f"/approvals/{mine[0]['step']['id']}/approve"), json={})
        assert out.status_code == 200, out.text

        # What the shop is told, and what is true.
        assert out.json()["report_status"] == "approved", out.json()
        assert _one(shop["db"], "SELECT status FROM service_reports WHERE id=:r", r=report["id"])[0] == "approved"
        assert _one(
            shop["db"], "SELECT id FROM satisfaction_surveys WHERE ticket_id=:t", t=ticket["id"],
        ) is not None, "the customer has nothing to answer"

    def test_no_step_is_left_pending_after_the_last_one(self, field_shop):
        shop = field_shop
        http, license_id = shop["http"], shop["license_id"]
        _ticket, report = self._filed_report(shop)
        shop["as_owner"]()
        pending = http.get(_api(license_id, "/approvals/pending")).json()
        step = next(r["step"] for r in pending if str(r["report"]["id"]) == str(report["id"]))
        http.post(_api(license_id, f"/approvals/{step['id']}/approve"), json={})
        left = _one(
            shop["db"],
            "SELECT count(*) FROM approval_steps WHERE entity_id=:r AND status='pending'",
            r=report["id"],
        )[0]
        assert left == 0, "a step this call approved was still pending"
