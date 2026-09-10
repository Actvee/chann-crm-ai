"""The completion notice, over HTTP, through both tiers, into PostgreSQL.

The unit suite proves the wiring against a fake Data client. What it cannot
prove is the thing this project has been bitten by repeatedly: a tier-seam
failure invisible from the side you are looking at. `notifications.entity_id`
is a UUID column and `target_chann_uid` is a foreign key into
`chann_identities` — a notification addressed to a customer who is not an
identity, or carrying a business code where a UUID belongs, is rejected by
the Data Tier and the message is silently lost (review E1, 6 Sep 2026, is
exactly that bug for a different notification).

So: a real technician finishes a real job through the Application Tier's own
check-out route, and the row is read back out of the database with SQL.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))

REPORT = {
    "found_issue": "คอมเพรสเซอร์รั่ว",
    "work_done": "เปลี่ยนคอมเพรสเซอร์และเติมน้ำยา",
    "parts_changed": "คอมเพรสเซอร์ 1 ตัว",
}


@pytest.fixture
def field_shop(migrated_db, monkeypatch):
    """A shop with a technician and a LINE customer, both tiers over HTTP.

    LINE pushes are stubbed: the row is what this file is about, and the
    push is attempted after it either way (notify.py's order).
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session, sessionmaker

    from chann_data import config as data_config
    from chann_data.db import get_session as data_get_session
    from chann_data.main import app as data_app
    from chann_data.models import ChannIdentity, LicenseMember
    from chann_data.repositories.phase65 import RegistrationRepository

    TestSession = sessionmaker(bind=migrated_db, future=True)

    def _session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    data_app.dependency_overrides[data_get_session] = _session
    monkeypatch.setattr(data_config.settings, "admin_secret", "test-internal-secret")

    suffix = uuid.uuid4().hex[:6]
    owner_uid = f"CHN-DN-{suffix}"
    tech_uid = f"CHN-DT-{suffix}"
    customer_uid = f"CHN-DC-{suffix}"

    with Session(migrated_db) as session:
        session.add_all([
            ChannIdentity(chann_uid=owner_uid, line_user_id=f"line-dn-{suffix}",
                          primary_role="sales", display_name="เจ้าของร้าน"),
            ChannIdentity(chann_uid=tech_uid, line_user_id=f"line-dt-{suffix}",
                          primary_role="technician", display_name="ช่างสมชาย"),
            # The customer is a LINE account of the customer OA. Without a
            # real identity row the notification's foreign key would fail.
            ChannIdentity(chann_uid=customer_uid, line_user_id=f"line-dc-{suffix}",
                          primary_role="customer", display_name="คุณสมหญิง"),
        ])
        session.commit()

    with Session(migrated_db) as session:
        licence = RegistrationRepository(session).create_license(
            company_name=f"ร้านแอร์ {suffix}", created_by_chann_uid=owner_uid,
        )
        session.commit()
        license_id = str(licence.id)

    with Session(migrated_db) as session:
        member = LicenseMember(
            id=uuid.uuid4(), license_id=licence.id, chann_uid=tech_uid,
            role="technician", channel="technician", status="active",
        )
        session.add(member)
        session.commit()
        technician_member_id = str(member.id)

    from chann_app import data_client as dc
    from chann_app.main import app as application_app
    from chann_app.routers_phase2 import get_tenant_principal
    from chann_app.services import notify as notify_module
    from chann_app.services.authorization import TenantPrincipal
    from chann_data.permissions import PERMISSION_KEYS

    pushed: list[tuple] = []

    async def fake_push_text(oa, to, text, client=None):
        pushed.append((oa, to, text))
        return [f"msg-{len(pushed)}"]

    async def fake_push_messages(oa, to, messages, client=None):
        pushed.append((oa, to, messages))
        return [f"msg-{len(pushed)}"]

    monkeypatch.setattr(notify_module, "push_text", fake_push_text)
    from chann_app.services import approval as approval_service
    from chann_app.services import chat as chat_service

    monkeypatch.setattr(approval_service, "push_messages", fake_push_messages)
    monkeypatch.setattr(chat_service._notify_mod, "push_text", fake_push_text)

    data_transport = httpx.ASGITransport(app=data_app)

    class _WiredClient(dc.DataClient):
        def __init__(self):
            super().__init__(base_url="http://data", secret="test-internal-secret")
            self._client = httpx.AsyncClient(
                transport=data_transport, base_url="http://data",
            )

    wired = _WiredClient()
    from chann_app.routers_admin import get_data_client

    application_app.dependency_overrides[get_data_client] = lambda: wired

    who = {"uid": owner_uid, "audience": "sales", "owner": True, "role": "owner"}

    application_app.dependency_overrides[get_tenant_principal] = (
        lambda: TenantPrincipal(
            chann_uid=who["uid"], license_id=license_id, role=who["role"],
            is_owner=who["owner"], permission_keys=sorted(PERMISSION_KEYS),
            audience=who["audience"],
        )
    )

    def as_technician():
        who.update(uid=tech_uid, audience="technician", owner=False, role="technician")

    def as_owner():
        who.update(uid=owner_uid, audience="sales", owner=True, role="owner")

    def as_customer():
        who.update(uid=customer_uid, audience="customer", owner=False, role="customer")

    client = TestClient(application_app)
    yield {
        "http": client, "license_id": license_id, "customer_uid": customer_uid,
        "technician_uid": tech_uid, "technician_member_id": technician_member_id,
        "owner_uid": owner_uid, "as_technician": as_technician, "as_owner": as_owner,
        "as_customer": as_customer, "pushed": pushed, "db": migrated_db,
    }

    application_app.dependency_overrides.clear()
    data_app.dependency_overrides.clear()


def _api(license_id: str, path: str = "") -> str:
    return f"/api/v1/licenses/{license_id}{path}"


def _notifications(db, license_id: str, chann_uid: str) -> list[dict]:
    """Straight out of PostgreSQL — not through the tier that wrote them."""
    from sqlalchemy import text

    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT type, message, message_en, entity_type, entity_id, "
                "       delivery_line, delivery_dashboard, read_at "
                "FROM notifications "
                "WHERE license_id = :lid AND target_chann_uid = :uid "
                "ORDER BY created_at, type"
            ),
            {"lid": license_id, "uid": chann_uid},
        ).mappings().all()
    return [dict(r) for r in rows]


def _ticket_status(db, ticket_id: str) -> str:
    """The ticket's own row, read with SQL — the Application Tier has no
    single-ticket GET, and the database is the source of truth anyway."""
    from sqlalchemy import text

    with db.connect() as conn:
        return conn.execute(
            text("SELECT status FROM service_tickets WHERE id = :tid"),
            {"tid": ticket_id},
        ).scalar_one()


def _finish_a_job(shop, *, linked: bool = True) -> dict:
    """Open a job, dispatch it, arrive, and close it — every step through
    the Application Tier's own routes.

    `linked` decides whether the customer filed it themselves on the
    customer OA (so the ticket carries their chann_uid) or a CS logged a
    walk-in over the phone (so it carries nobody).
    """
    http, license_id = shop["http"], shop["license_id"]

    body = {
        "issue_description": "แอร์ไม่เย็น มีน้ำหยด",
        "customer_name": "สมหญิง ใจดี",
        "customer_phone": "0812345678",
        "service_address": "99/1 ถนนสุขุมวิท",
        "scheduled_date": date(2026, 9, 11).isoformat(),
        "scheduled_time": time(10, 0).isoformat(),
    }
    # A ticket is linked to a LINE customer by the customer filing it — the
    # staff form has no field for someone else's chann_uid, by design.
    shop["as_customer"]() if linked else shop["as_owner"]()
    response = http.post(_api(license_id, "/tickets"), json=body)
    assert response.status_code in (200, 201), response.text
    ticket = response.json()
    assert bool(ticket.get("customer_chann_uid")) is linked, ticket

    shop["as_owner"]()

    response = http.post(
        _api(license_id, f"/tickets/{ticket['id']}/assign"),
        json={"target_type": "technician", "target_ref": shop["technician_member_id"]},
    )
    assert response.status_code == 200, response.text

    shop["as_technician"]()
    response = http.post(_api(license_id, f"/tickets/{ticket['id']}/claim"), json={})
    assert response.status_code == 200, response.text
    response = http.post(_api(license_id, f"/tickets/{ticket['id']}/check-in"), json={})
    assert response.status_code == 200, response.text

    response = http.post(
        _api(license_id, f"/tickets/{ticket['id']}/check-out"),
        json={"report_data": dict(REPORT)},
    )
    assert response.status_code == 200, response.text
    return {"ticket": ticket, "report": response.json()}


class TestFinishingAJobTellsTheCustomer:
    def test_the_notification_row_is_in_postgresql_after_check_out(self, field_shop):
        shop = field_shop
        done = _finish_a_job(shop)
        ticket, report = done["ticket"], done["report"]

        # The Data Tier really did finish the job.
        shop["as_owner"]()
        assert _ticket_status(shop["db"], ticket["id"]) == "completed"
        assert report["status"] == "submitted", "not approved yet — no PDF is due"

        rows = _notifications(shop["db"], shop["license_id"], shop["customer_uid"])
        completed = [r for r in rows if r["type"] == "ticket_completed"]
        assert len(completed) == 1, rows
        notice = completed[0]

        assert ticket["ticket_number"] in notice["message"]
        assert "เสร็จแล้ว" in notice["message"]
        # The summary is the technician's own "แก้:" answer, stored once and
        # read back through the database.
        assert REPORT["work_done"] in notice["message"]
        assert ticket["ticket_number"] in notice["message_en"]
        assert "has finished" in notice["message_en"]

        # The link back to the job survived the UUID column that has lost
        # notifications before.
        assert notice["entity_type"] == "service_ticket"
        assert str(notice["entity_id"]) == str(ticket["id"])
        assert notice["delivery_line"] is True and notice["delivery_dashboard"] is True
        assert notice["read_at"] is None

        # No PDF and no rating request at this point: the report is not
        # approved, so neither exists yet.
        assert "http" not in notice["message"]
        assert "PDF" not in notice["message"]

        # It went out on the CUSTOMER OA, to the customer's LINE account —
        # once, alongside (not instead of) the assignment line they already
        # got when the job was dispatched.
        finished = [
            p for p in shop["pushed"]
            if p[0] == "customer" and isinstance(p[2], str) and "เสร็จแล้ว" in p[2]
        ]
        assert len(finished) == 1, shop["pushed"]
        assert finished[0][1] == f"line-dc-{shop['customer_uid'].split('-')[-1]}"

    def test_the_shop_still_gets_its_own_approval_request(self, field_shop):
        """The completion notice is additional, not instead of."""
        shop = field_shop
        _finish_a_job(shop)
        owner = _notifications(shop["db"], shop["license_id"], shop["owner_uid"])
        assert [r["type"] for r in owner if r["type"] == "approval_pending"]
        assert not [r for r in owner if r["type"].startswith("ticket_completed")]

    def test_a_walk_in_job_closes_and_writes_no_customer_row(self, field_shop):
        """No LINE account on the job at all: the shop must still be able to
        finish it, and nothing may be written against an identity that does
        not exist."""
        shop = field_shop
        done = _finish_a_job(shop, linked=False)
        shop["as_owner"]()
        assert _ticket_status(shop["db"], done["ticket"]["id"]) == "completed"

        from sqlalchemy import text

        with shop["db"].connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM notifications "
                     "WHERE license_id = :lid AND type IN ('ticket_completed','ticket_reopened')"),
                {"lid": shop["license_id"]},
            ).scalar()
        assert count == 0
        assert [p for p in shop["pushed"] if p[0] == "customer"] == []

    def test_an_english_customer_gets_the_english_text_pushed(self, field_shop):
        shop = field_shop
        shop["as_owner"]()
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase16 import DisplayPreferenceRepository

        with Session(shop["db"]) as session:
            DisplayPreferenceRepository(session).upsert(
                shop["customer_uid"], {"language": "en"},
            )
            session.commit()

        _finish_a_job(shop)
        finished = [
            p for p in shop["pushed"]
            if p[0] == "customer" and isinstance(p[2], str) and "has finished" in p[2]
        ]
        assert len(finished) == 1, shop["pushed"]
        assert "เสร็จแล้ว" not in finished[0][2]
        # Both texts are on the row either way — Thai is the non-null column.
        rows = _notifications(shop["db"], shop["license_id"], shop["customer_uid"])
        notice = next(r for r in rows if r["type"] == "ticket_completed")
        assert "เสร็จแล้ว" in notice["message"] and "has finished" in notice["message_en"]


class TestSendingTheReportBackTellsTheCustomerToo:
    def test_a_rejected_report_reopens_the_job_and_the_customer_hears(self, field_shop):
        shop = field_shop
        done = _finish_a_job(shop)
        ticket = done["ticket"]

        shop["as_owner"]()
        steps = shop["http"].get(_api(shop["license_id"], "/approvals/pending")).json()
        assert steps, steps
        step_id = steps[0]["step"]["id"] if "step" in steps[0] else steps[0]["id"]
        response = shop["http"].post(
            _api(shop["license_id"], f"/approvals/{step_id}/reject"),
            json={"reason": "รูปหน้างานไม่ครบ"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["report_status"] == "rejected"

        # The Data Tier put the job back to in_progress so the technician can
        # fix it and close again (phase14.act).
        assert _ticket_status(shop["db"], ticket["id"]) == "in_progress"

        rows = _notifications(shop["db"], shop["license_id"], shop["customer_uid"])
        types = [r["type"] for r in rows]
        assert types.count("ticket_completed") == 1
        assert types.count("ticket_reopened") == 1
        reopened = next(r for r in rows if r["type"] == "ticket_reopened")
        assert ticket["ticket_number"] in reopened["message"]
        assert str(reopened["entity_id"]) == str(ticket["id"])
        # The approver's reason belongs to the shop and its technician.
        assert "รูปหน้างานไม่ครบ" not in reopened["message"]
        assert "รูปหน้างานไม่ครบ" not in (reopened["message_en"] or "")

    def test_closing_it_again_tells_the_customer_again(self, field_shop):
        """Two genuine completions, two notices — the guard is the Data
        Tier's own "already completed" refusal, not a time window, so a job
        that is really finished twice really says so twice."""
        shop = field_shop
        done = _finish_a_job(shop)
        ticket = done["ticket"]

        shop["as_owner"]()
        steps = shop["http"].get(_api(shop["license_id"], "/approvals/pending")).json()
        step_id = steps[0]["step"]["id"] if "step" in steps[0] else steps[0]["id"]
        shop["http"].post(
            _api(shop["license_id"], f"/approvals/{step_id}/reject"), json={"reason": "แก้อีกนิด"},
        )

        shop["as_technician"]()
        response = shop["http"].post(
            _api(shop["license_id"], f"/tickets/{ticket['id']}/check-out"),
            json={"report_data": {**REPORT, "work_done": "เปลี่ยนคอมเพรสเซอร์และล้างคอยล์"}},
        )
        assert response.status_code == 200, response.text

        rows = _notifications(shop["db"], shop["license_id"], shop["customer_uid"])
        completed = [r for r in rows if r["type"] == "ticket_completed"]
        assert len(completed) == 2
        assert "ล้างคอยล์" in completed[-1]["message"]

    def test_the_second_check_out_of_a_finished_job_is_refused(self, field_shop):
        """Where "nothing twice for one event" actually comes from: the
        transition can only happen once, so the notice can only fire once."""
        shop = field_shop
        done = _finish_a_job(shop)
        shop["as_technician"]()
        response = shop["http"].post(
            _api(shop["license_id"], f"/tickets/{done['ticket']['id']}/check-out"),
            json={"report_data": dict(REPORT)},
        )
        assert response.status_code == 409, response.text
        rows = _notifications(shop["db"], shop["license_id"], shop["customer_uid"])
        assert [r["type"] for r in rows].count("ticket_completed") == 1


class TestTheSurveyDoesNotSayItAgain:
    def test_approval_sends_a_rating_request_not_a_second_announcement(self, field_shop):
        shop = field_shop
        done = _finish_a_job(shop)
        ticket = done["ticket"]

        shop["as_owner"]()
        steps = shop["http"].get(_api(shop["license_id"], "/approvals/pending")).json()
        assert steps, steps
        approved = None
        for _ in range(5):
            steps = shop["http"].get(_api(shop["license_id"], "/approvals/pending")).json()
            if not steps:
                break
            step_id = steps[0]["step"]["id"] if "step" in steps[0] else steps[0]["id"]
            approved = shop["http"].post(
                _api(shop["license_id"], f"/approvals/{step_id}/approve"), json={},
            )
            assert approved.status_code == 200, approved.text
            if approved.json().get("report_status") == "approved":
                break
        assert approved is not None and approved.json()["report_status"] == "approved"

        # The survey went out as a quick-reply message on the customer OA.
        survey = [p for p in shop["pushed"] if p[0] == "customer" and isinstance(p[2], list)]
        assert len(survey) == 1, shop["pushed"]
        text = survey[0][2][0]["text"]
        assert ticket["ticket_number"] in text
        assert "ให้คะแนน" in text
        # It must not announce completion a second time — that was said at
        # check-out, possibly days earlier.
        assert "เสร็จเรียบร้อยแล้ว" not in text
        assert "เสร็จแล้ว" not in text

        # And exactly one thing in the whole flow told the customer the work
        # was done.
        rows = _notifications(shop["db"], shop["license_id"], shop["customer_uid"])
        assert [r["type"] for r in rows].count("ticket_completed") == 1
