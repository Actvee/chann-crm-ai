"""Phase 14 — approval workflow + satisfaction survey (Master Spec §14.6).

Owner decisions under test (3 Sep 2569): the default flow is the CS who
owns the ticket, one step; "ปิดงาน" is the last approver passing, and the
survey exists from that same moment; a reject stops the flow and marks
the report rejected; a resubmit starts fresh.

Multi-tenant isolation is tested because an approval step is a decision
about someone's work — an approver in tenant A passing tenant B's report
would be both a leak and a forgery.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase12 import ServiceTicketRepository  # noqa: E402
from chann_data.repositories.phase13 import FieldServiceRepository  # noqa: E402
from chann_data.repositories.phase14 import (  # noqa: E402
    ApprovalConflict,
    ApprovalRepository,
)
from chann_data.repositories.tenant_scope import (  # noqa: E402
    CrossTenantAccessDenied,
    TenantScope,
)

COMPLETE_TICKET = {
    "customer_name": "จุใจ มาติกา",
    "customer_phone": "0659635642",
    "service_address": "99/1 ถนนสุขุมวิท",
    "scheduled_date": date(2026, 9, 4),
    "scheduled_time": time(14, 0),
}
GOOD_REPORT = {
    "found_issue": "คอมเพรสเซอร์รั่ว",
    "work_done": "เปลี่ยนคอมเพรสเซอร์และเติมน้ำยา",
    "parts_changed": "คอมเพรสเซอร์ 1 ตัว",
    "notes": "แนะนำล้างแอร์ทุก 6 เดือน",
}


def _make_tenant(migrated_db, suffix):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity, LicenseMember
    from chann_data.repositories.phase65 import RegistrationRepository

    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-AP-{suffix}", line_user_id=f"line-ap-{suffix}",
            primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"Approve {suffix}", created_by_chann_uid=f"CHN-AP-{suffix}",
        )
        session.commit()
        license_id = lic.id

    members = {}
    with Session(migrated_db) as session:
        for role in ("technician", "cs", "admin"):
            identity = ChannIdentity(
                chann_uid=f"CHN-AR-{suffix}-{role}",
                line_user_id=f"line-ar-{suffix}-{role}",
                primary_role=role if role != "cs" else "sales",
            )
            session.add(identity)
            session.flush()
            member = LicenseMember(
                id=uuid.uuid4(), license_id=license_id,
                chann_uid=identity.chann_uid, role=role, status="active",
            )
            session.add(member)
            session.flush()
            members[role] = member.id
        session.commit()

    return {
        "scope": TenantScope(license_id=license_id),
        "members": members,
        "session": lambda: Session(migrated_db),
    }


@pytest.fixture
def tenant(migrated_db):
    return _make_tenant(migrated_db, uuid.uuid4().hex[:6])


def _submitted_report(tenant, *, owner_member_id):
    """Ticket owned by CS, worked by the technician, checked out — the
    exact state the approval flow starts from."""
    with tenant["session"]() as session:
        repo = ServiceTicketRepository(session)
        ticket = repo.create(
            tenant["scope"], issue_description="แอร์ไม่เย็น", **COMPLETE_TICKET,
        )
        session.flush()
        ticket.owner_member_id = owner_member_id
        repo.assign(
            tenant["scope"], ticket.id,
            target_type="technician", target_ref=tenant["members"]["technician"],
        )
        session.commit()
        ticket_id = ticket.id
    with tenant["session"]() as session:
        fs = FieldServiceRepository(session)
        fs.check_in(tenant["scope"], ticket_id, member_id=tenant["members"]["technician"],
                    gps_lat=13.75, gps_lng=100.5)
        report = fs.check_out(
            tenant["scope"], ticket_id, member_id=tenant["members"]["technician"],
            report_data=dict(GOOD_REPORT),
        )
        session.commit()
        return ticket_id, report.id


class TestApprovalWorkflow:
    def test_a_submitted_report_gets_one_step_for_the_ticket_owner(self, tenant):
        _, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            steps = ApprovalRepository(session).open_steps_for_report(
                tenant["scope"], session.get(ServiceReport, report_id),
            )
            session.commit()
            assert len(steps) == 1
            assert steps[0].approver_type == "user"
            assert steps[0].approver_ref == str(tenant["members"]["cs"])
            assert steps[0].status == "pending"

    def test_a_ticket_with_no_owner_falls_back_to_admin(self, tenant):
        _, report_id = _submitted_report(tenant, owner_member_id=None)
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            steps = ApprovalRepository(session).open_steps_for_report(
                tenant["scope"], session.get(ServiceReport, report_id),
            )
            session.commit()
            assert (steps[0].approver_type, steps[0].approver_ref) == ("role", "admin")

    def test_the_last_approval_closes_the_job_and_creates_the_survey(self, tenant):
        """The owner's "ปิดงาน": report approved and survey created in the
        same transaction as the final step."""
        ticket_id, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            [step] = repo.open_steps_for_report(tenant["scope"], session.get(ServiceReport, report_id))
            session.commit()
            step_id = step.id
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            step, status, survey = repo.act(
                tenant["scope"], step_id, approve=True,
                member_id=tenant["members"]["cs"], role_names=["cs"],
            )
            session.commit()
            assert status == "approved"
            assert survey is not None and survey.ticket_id == ticket_id
            assert survey.submitted_at is None
            assert session.get(ServiceReport, report_id).status == "approved"

    def test_a_reject_marks_the_report_rejected_and_stops(self, tenant):
        _, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            [step] = repo.open_steps_for_report(tenant["scope"], session.get(ServiceReport, report_id))
            session.commit()
            _, status, survey = repo.act(
                tenant["scope"], step.id, approve=False, reason="รูปไม่ชัด",
                member_id=tenant["members"]["cs"], role_names=["cs"],
            )
            session.commit()
            assert status == "rejected" and survey is None
            assert session.get(ServiceReport, report_id).status == "rejected"
            assert step.reason == "รูปไม่ชัด"

    def test_a_two_step_flow_waits_for_step_one(self, tenant):
        """Custom flow: CS then admin. Step 2 cannot be acted on first and
        the survey only exists after BOTH pass."""
        _, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            repo.replace_workflow(
                tenant["scope"], "service_report",
                {"steps": [
                    {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
                    {"order": 2, "approver_type": "role", "approver_ref": "admin"},
                ]},
                updated_by=tenant["members"]["admin"],
            )
            steps = repo.open_steps_for_report(tenant["scope"], session.get(ServiceReport, report_id))
            session.commit()
            assert [s.step_order for s in steps] == [1, 2]

            with pytest.raises(ApprovalConflict):
                repo.act(tenant["scope"], steps[1].id, approve=True,
                         member_id=tenant["members"]["admin"], role_names=["admin"])
            _, status, survey = repo.act(
                tenant["scope"], steps[0].id, approve=True,
                member_id=tenant["members"]["cs"], role_names=["cs"],
            )
            assert status == "submitted" and survey is None
            _, status, survey = repo.act(
                tenant["scope"], steps[1].id, approve=True,
                member_id=tenant["members"]["admin"], role_names=["admin"],
            )
            session.commit()
            assert status == "approved" and survey is not None

    def test_only_the_named_approver_may_act(self, tenant):
        _, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            [step] = repo.open_steps_for_report(tenant["scope"], session.get(ServiceReport, report_id))
            session.commit()
            with pytest.raises(ApprovalConflict):
                repo.act(tenant["scope"], step.id, approve=True,
                         member_id=tenant["members"]["technician"], role_names=["technician"])

    def test_pending_for_shows_only_the_actionable_step(self, tenant):
        _, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            repo.open_steps_for_report(tenant["scope"], session.get(ServiceReport, report_id))
            session.commit()
            assert len(repo.pending_for(tenant["scope"], member_id=tenant["members"]["cs"],
                                        role_names=["cs"])) == 1
            assert repo.pending_for(tenant["scope"], member_id=tenant["members"]["technician"],
                                    role_names=["technician"]) == []


class TestSurvey:
    def _approved(self, tenant):
        ticket_id, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        with tenant["session"]() as session:
            from chann_data.models import ServiceReport

            repo = ApprovalRepository(session)
            [step] = repo.open_steps_for_report(tenant["scope"], session.get(ServiceReport, report_id))
            _, _, survey = repo.act(tenant["scope"], step.id, approve=True,
                                    member_id=tenant["members"]["cs"], role_names=["cs"])
            session.commit()
            return ticket_id, survey.id

    def test_the_customer_answer_is_recorded(self, tenant):
        _, survey_id = self._approved(tenant)
        with tenant["session"]() as session:
            row = ApprovalRepository(session).submit_survey(
                tenant["scope"], survey_id, score=3, comment="ช่างสุภาพมาก",
            )
            session.commit()
            assert row.score == 3 and row.submitted_at is not None

    def test_a_score_off_the_scale_is_refused(self, tenant):
        _, survey_id = self._approved(tenant)
        with tenant["session"]() as session:
            with pytest.raises(ApprovalConflict):
                ApprovalRepository(session).submit_survey(
                    tenant["scope"], survey_id, score=7, comment=None,
                )

    def test_not_answering_is_allowed_and_still_pending(self, tenant):
        ticket_id, _ = self._approved(tenant)
        with tenant["session"]() as session:
            pending = ApprovalRepository(session).pending_survey_for_ticket(
                tenant["scope"], ticket_id,
            )
            assert pending is not None and pending.score is None


class TestMultiTenantApproval:
    def test_a_step_in_tenant_a_is_invisible_and_untouchable_from_tenant_b(
        self, migrated_db,
    ):
        a = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        b = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        _, report_id = _submitted_report(a, owner_member_id=a["members"]["cs"])
        with a["session"]() as session:
            from chann_data.models import ServiceReport

            [step] = ApprovalRepository(session).open_steps_for_report(
                a["scope"], session.get(ServiceReport, report_id),
            )
            session.commit()
            step_id = step.id
        with b["session"]() as session:
            repo = ApprovalRepository(session)
            assert repo.pending_for(b["scope"], member_id=b["members"]["cs"],
                                    role_names=["cs", "admin"]) == []
            with pytest.raises(CrossTenantAccessDenied):
                repo.act(b["scope"], step_id, approve=True,
                         member_id=b["members"]["cs"], role_names=["cs", "admin"])


class TestApprovalRoutes:
    """The HTTP surface the Application tier will call — driven end to end
    so a NameError inside a route body cannot hide behind a green
    repository suite (it did, once, for ten minutes, in this very file's
    first draft)."""

    def test_open_act_and_survey_over_http(self, tenant, migrated_db, monkeypatch):
        from fastapi.testclient import TestClient

        from chann_data import config as data_config
        from chann_data.db import get_session
        from chann_data.main import app

        def _session():
            with tenant["session"]() as s:
                yield s

        app.dependency_overrides[get_session] = _session
        monkeypatch.setattr(data_config.settings, "admin_secret", "test-internal-secret")
        client = TestClient(app, headers={"X-Internal-Secret": "test-internal-secret"})
        lic = str(tenant["scope"].license_id)
        _, report_id = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])

        r = client.post(f"/internal/v1/licenses/{lic}/service-reports/{report_id}/approval-steps")
        assert r.status_code == 201, r.text
        [step] = r.json()

        r = client.get(f"/internal/v1/licenses/{lic}/approval-steps/pending",
                       params={"member_id": str(tenant["members"]["cs"]), "roles": "cs"})
        assert [s["id"] for s in r.json()] == [step["id"]]

        r = client.post(f"/internal/v1/licenses/{lic}/approval-steps/{step['id']}/act",
                        json={"approve": True, "member_id": str(tenant["members"]["cs"]),
                              "roles": ["cs"]}, headers={"X-Actor-Id": "CHN-test"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report_status"] == "approved" and body["survey"] is not None

        survey_id = body["survey"]["id"]
        r = client.post(f"/internal/v1/licenses/{lic}/surveys/{survey_id}/answer",
                        json={"score": 2, "comment": None})
        assert r.status_code == 200 and r.json()["score"] == 2
        r = client.post(f"/internal/v1/licenses/{lic}/surveys/{survey_id}/answer",
                        json={"score": 3})
        assert r.status_code == 409
        app.dependency_overrides.clear()
