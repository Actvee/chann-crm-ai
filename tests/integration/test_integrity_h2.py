"""Integrity batch (6 Sep 2026) against Postgres: locks and guards on the
repair flow, ownership through role edits, tenant-bound team members,
duplicate phones on update, archived identities returning, the pipeline's
stated amount, archived rows out of reports, admin lockout, webhook-event
dedup, and running numbers under contention.
"""
from __future__ import annotations

import sys
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase12 import ServiceTicketRepository, TicketConflict  # noqa: E402
from chann_data.repositories.phase13 import FieldServiceRepository  # noqa: E402
from chann_data.repositories.phase14 import ApprovalConflict, ApprovalNotFound, ApprovalRepository  # noqa: E402
from chann_data.repositories.phase17 import ReportQueryRepository  # noqa: E402
from chann_data.repositories.phase2 import MemberRoleRepository, Phase2Conflict  # noqa: E402
from chann_data.repositories.phase7 import MasterDataNotFound, TechnicianTeamRepository  # noqa: E402
from chann_data.repositories.phase9 import CustomerRepository, DealRepository, Phase9Duplicate  # noqa: E402
from chann_data.repositories.tenant_scope import PlatformAdminRepository, TenantScope  # noqa: E402

COMPLETE = {
    "customer_name": "จุใจ มาติกา", "customer_phone": "0659635642",
    "service_address": "99/1 ถนนสุขุมวิท", "scheduled_date": date(2026, 9, 4), "scheduled_time": time(14, 0),
}
REPORT = {"found_issue": "คอมเพรสเซอร์รั่ว", "work_done": "เปลี่ยนคอมเพรสเซอร์"}


@pytest.fixture
def tenant(migrated_db):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity, LicenseMember, TechnicianTeam
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(chann_uid=f"CHN-IG-{suffix}", line_user_id=f"line-ig-{suffix}", primary_role="sales"))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(company_name=f"Integrity {suffix}", created_by_chann_uid=f"CHN-IG-{suffix}")
        session.commit()
        license_id = lic.id
    members = {}
    with Session(migrated_db) as session:
        for role in ("technician", "technician2", "cs", "admin"):
            identity = ChannIdentity(chann_uid=f"CHN-IG-{suffix}-{role}", line_user_id=f"line-ig-{suffix}-{role}", primary_role="technician")
            session.add(identity)
            session.flush()
            member = LicenseMember(id=uuid.uuid4(), license_id=license_id, chann_uid=identity.chann_uid,
                                   role="technician" if role.startswith("technician") else role, status="active")
            session.add(member)
            session.flush()
            members[role] = member.id
        team = TechnicianTeam(id=uuid.uuid4(), license_id=license_id, team_name="AC")
        session.add(team)
        session.commit()
        team_id = team.id
    return {"scope": TenantScope(license_id=license_id), "license_id": license_id, "members": members,
            "team_id": team_id, "owner_uid": f"CHN-IG-{suffix}", "session": lambda: Session(migrated_db)}


def _assigned_ticket(tenant, *, member, **extra):
    with tenant["session"]() as session:
        repo = ServiceTicketRepository(session)
        row = repo.create(tenant["scope"], issue_description="แอร์ไม่เย็น", **COMPLETE, **extra)
        session.flush()
        repo.assign(tenant["scope"], row.id, target_type="technician", target_ref=member)
        session.commit()
        return row.id


class TestRepairFlowGuards:
    def test_declining_clears_the_assignee_and_a_started_job_cannot_be_declined(self, tenant):
        tech = tenant["members"]["technician"]
        ticket_id = _assigned_ticket(tenant, member=tech)
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).reject(tenant["scope"], ticket_id, member_id=tech)
            session.commit()
            assert row.status == "open" and row.accept_status == "rejected"
            assert row.assigned_to_ref is None and row.assigned_target_type is None
        ticket_id = _assigned_ticket(tenant, member=tech)
        with tenant["session"]() as session:
            FieldServiceRepository(session).check_in(tenant["scope"], ticket_id, member_id=tech)
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).reject(tenant["scope"], ticket_id, member_id=tech)

    def test_a_rejected_report_reopens_the_job_and_the_resubmit_is_approved(self, tenant):
        from chann_data.models import ServiceReport, ServiceTicket

        tech, cs = tenant["members"]["technician"], tenant["members"]["cs"]
        ticket_id = _assigned_ticket(tenant, member=tech, owner_member_id=cs)
        with tenant["session"]() as session:
            fs = FieldServiceRepository(session)
            fs.check_in(tenant["scope"], ticket_id, member_id=tech)
            report = fs.check_out(tenant["scope"], ticket_id, member_id=tech, report_data=dict(REPORT))
            [step] = ApprovalRepository(session).open_steps_for_report(tenant["scope"], report)
            session.commit()
            step_id, report_id = step.id, report.id
        with tenant["session"]() as session:
            _, status, _ = ApprovalRepository(session).act(
                tenant["scope"], step_id, approve=False, reason="รูปไม่ครบ", member_id=cs, role_names=["cs"],
            )
            session.commit()
            assert status == "rejected"
            assert session.get(ServiceTicket, ticket_id).status == "in_progress"
        with tenant["session"]() as session:
            fs = FieldServiceRepository(session)
            second = fs.check_out(tenant["scope"], ticket_id, member_id=tech, report_data={**REPORT, "notes": "แนบรูปแล้ว"})
            [step2] = ApprovalRepository(session).open_steps_for_report(tenant["scope"], second)
            session.commit()
            assert second.id != report_id and session.get(ServiceTicket, ticket_id).status == "completed"
            step2_id = step2.id
        with tenant["session"]() as session:
            _, status, survey = ApprovalRepository(session).act(
                tenant["scope"], step2_id, approve=True, member_id=cs, role_names=["cs"],
            )
            session.commit()
            assert status == "approved" and survey is not None
            assert session.get(ServiceReport, report_id).status == "rejected"

    def test_a_rejected_first_step_blocks_the_second(self, tenant):
        tech, cs, admin = tenant["members"]["technician"], tenant["members"]["cs"], tenant["members"]["admin"]
        ticket_id = _assigned_ticket(tenant, member=tech, owner_member_id=cs)
        with tenant["session"]() as session:
            fs = FieldServiceRepository(session)
            fs.check_in(tenant["scope"], ticket_id, member_id=tech)
            report = fs.check_out(tenant["scope"], ticket_id, member_id=tech, report_data=dict(REPORT))
            repo = ApprovalRepository(session)
            repo.replace_workflow(tenant["scope"], "service_report", {"steps": [
                {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
                {"order": 2, "approver_type": "role", "approver_ref": "admin"},
            ]}, updated_by=admin)
            steps = repo.open_steps_for_report(tenant["scope"], report)
            session.commit()
            first, second = steps[0].id, steps[1].id
        with tenant["session"]() as session:
            ApprovalRepository(session).act(tenant["scope"], first, approve=False, reason="x", member_id=cs, role_names=["cs"])
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(ApprovalConflict):
                ApprovalRepository(session).act(tenant["scope"], second, approve=True, member_id=admin, role_names=["admin"])

    def test_a_survey_is_answered_only_by_its_customer(self, tenant):
        tech, cs = tenant["members"]["technician"], tenant["members"]["cs"]
        ticket_id = _assigned_ticket(tenant, member=tech, owner_member_id=cs, customer_chann_uid="CHN-CUST-A")
        with tenant["session"]() as session:
            fs = FieldServiceRepository(session)
            fs.check_in(tenant["scope"], ticket_id, member_id=tech)
            report = fs.check_out(tenant["scope"], ticket_id, member_id=tech, report_data=dict(REPORT))
            repo = ApprovalRepository(session)
            [step] = repo.open_steps_for_report(tenant["scope"], report)
            _, _, survey = repo.act(tenant["scope"], step.id, approve=True, member_id=cs, role_names=["cs"])
            session.commit()
            survey_id = survey.id
        with tenant["session"]() as session:
            with pytest.raises(ApprovalNotFound):
                ApprovalRepository(session).submit_survey(tenant["scope"], survey_id, score=3, comment=None, actor_chann_uid="CHN-CUST-B")
        with tenant["session"]() as session:
            row = ApprovalRepository(session).submit_survey(tenant["scope"], survey_id, score=3, comment=None, actor_chann_uid="CHN-CUST-A")
            session.commit()
            assert row.score == 3

    def test_an_owner_only_shop_gets_its_owner_as_the_fallback_approver(self, tenant):
        """No admin holds the admin role here: the step names the owner
        role, not a role nobody has."""
        from chann_data.models import LicenseMember

        tech = tenant["members"]["technician"]
        with tenant["session"]() as session:
            admin = session.get(LicenseMember, tenant["members"]["admin"])
            admin.status = "inactive"
            session.commit()
        ticket_id = _assigned_ticket(tenant, member=tech)
        with tenant["session"]() as session:
            fs = FieldServiceRepository(session)
            fs.check_in(tenant["scope"], ticket_id, member_id=tech)
            report = fs.check_out(tenant["scope"], ticket_id, member_id=tech, report_data=dict(REPORT))
            [step] = ApprovalRepository(session).open_steps_for_report(tenant["scope"], report)
            session.commit()
            assert (step.approver_type, step.approver_ref) == ("role", "owner")


class TestWhoMayBecomeWhat:
    def test_nobody_becomes_owner_through_a_role_edit(self, tenant):
        from chann_data.models import LicenseMember

        with tenant["session"]() as session:
            cs = session.get(LicenseMember, tenant["members"]["cs"])
            with pytest.raises(Phase2Conflict):
                MemberRoleRepository(session).set_role(tenant["scope"], cs.chann_uid, "owner")
            # ordinary role changes still work
            MemberRoleRepository(session).set_role(tenant["scope"], cs.chann_uid, "admin")
            session.commit()

    def test_a_team_member_must_belong_to_the_tenant(self, tenant):
        with tenant["session"]() as session:
            with pytest.raises(MasterDataNotFound):
                TechnicianTeamRepository(session).add_member(tenant["scope"], tenant["team_id"], uuid.uuid4())
            row = TechnicianTeamRepository(session).add_member(tenant["scope"], tenant["team_id"], tenant["members"]["technician"])
            session.commit()
            assert row.member_id == tenant["members"]["technician"]


class TestCustomersAndDeals:
    def test_duplicate_phone_is_refused_on_update_too(self, tenant):
        with tenant["session"]() as session:
            repo = CustomerRepository(session)
            a = repo.create(tenant["scope"], first_name="A", phone="0811111111")
            b = repo.create(tenant["scope"], first_name="B", phone="0822222222")
            session.commit()
            with pytest.raises(Phase9Duplicate):
                repo.update(tenant["scope"], b.id, {"phone": "081-111-1111"})
            repo.update(tenant["scope"], b.id, {"phone": "0822222222", "notes": "same number, fine"})
            session.commit()

    def test_an_archived_identity_comes_back_instead_of_being_walled_out(self, tenant):
        from chann_data.models import ChannIdentity

        uid = f"CHN-RET-{uuid.uuid4().hex[:6]}"
        with tenant["session"]() as session:
            session.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="customer"))
            session.commit()
        with tenant["session"]() as session:
            repo = CustomerRepository(session)
            row = repo.create(tenant["scope"], first_name="เก่า", phone="0833333333", customer_chann_uid=uid)
            repo.archive(tenant["scope"], row.id)
            session.commit()
            again = repo.create(tenant["scope"], first_name="กลับมา", customer_chann_uid=uid)
            session.commit()
            assert again.id == row.id and again.archived_at is None and again.first_name == "กลับมา"

    def test_owner_can_be_set_and_moved_within_the_tenant(self, tenant):
        with tenant["session"]() as session:
            repo = CustomerRepository(session)
            row = repo.create(tenant["scope"], first_name="ของ", phone="0844444444", owner_member_id=tenant["members"]["cs"])
            session.commit()
            moved = repo.set_owner(tenant["scope"], row.id, tenant["members"]["admin"])
            assert moved.owner_member_id == tenant["members"]["admin"]
            from chann_data.repositories.phase9 import Phase9NotFound

            with pytest.raises(Phase9NotFound):
                repo.set_owner(tenant["scope"], row.id, uuid.uuid4())

    def test_pipeline_counts_the_stated_amount_until_lines_exist(self, tenant):
        with tenant["session"]() as session:
            customers = CustomerRepository(session)
            deals = DealRepository(session)
            c = customers.create(tenant["scope"], first_name="ดีล", phone="0855555555")
            deals.create(tenant["scope"], contact_id=c.id, amount=Decimal("250000"))
            session.commit()
            summary = deals.pipeline_summary(tenant["scope"])
            assert Decimal(summary["open_value"]) == Decimal("250000")

    def test_reports_do_not_count_archived_rows(self, tenant):
        with tenant["session"]() as session:
            repo = CustomerRepository(session)
            keep = repo.create(tenant["scope"], first_name="อยู่", phone="0866666666")
            gone = repo.create(tenant["scope"], first_name="ไป", phone="0877777777")
            repo.archive(tenant["scope"], gone.id)
            session.commit()
            assert ReportQueryRepository(session).run(tenant["scope"], {"entity": "customers"})["total"] == 1


class TestPlatformAndWebhook:
    def test_five_wrong_passwords_lock_the_admin_for_a_while(self, tenant):
        from argon2 import PasswordHasher

        from chann_data.models import PlatformAdmin

        name = f"admin-{uuid.uuid4().hex[:6]}"
        with tenant["session"]() as session:
            session.add(PlatformAdmin(id=uuid.uuid4(), username=name, password_hash=PasswordHasher().hash("right")))
            session.commit()
        with tenant["session"]() as session:
            repo = PlatformAdminRepository(session)
            for _ in range(5):
                assert repo.authenticate(name, "wrong") is None
            session.commit()
            assert repo.authenticate(name, "right") is None  # locked, even with the right password
            admin = session.execute(__import__("sqlalchemy").select(PlatformAdmin).where(PlatformAdmin.username == name)).scalar_one()
            assert admin.locked_until is not None and admin.locked_until > datetime.now(timezone.utc)
            admin.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
            session.commit()
            assert repo.authenticate(name, "right") is not None

    def test_a_webhook_event_id_is_recorded_once(self, tenant):
        from chann_data.models import LineWebhookEvent

        event_id = f"evt-{uuid.uuid4().hex}"
        with tenant["session"]() as session:
            session.add(LineWebhookEvent(event_id=event_id, oa="customer"))
            session.commit()
        with tenant["session"]() as session:
            session.add(LineWebhookEvent(event_id=event_id, oa="customer"))
            with pytest.raises(IntegrityError):
                session.commit()


class TestRunningNumbers:
    def test_two_sessions_cannot_allocate_the_same_ticket_number(self, tenant):
        """The second allocation waits for the first commit, so both
        tickets get their own number instead of one dying on the unique
        constraint."""
        numbers: list[str] = []
        errors: list[Exception] = []
        first_allocated = threading.Event()
        release_first = threading.Event()

        def worker(hold: bool):
            try:
                with tenant["session"]() as session:
                    repo = ServiceTicketRepository(session)
                    row = repo.create(tenant["scope"], issue_description="race")
                    if hold:
                        first_allocated.set()
                        release_first.wait(timeout=10)
                    session.commit()
                    numbers.append(row.ticket_number)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        a = threading.Thread(target=worker, args=(True,))
        b = threading.Thread(target=worker, args=(False,))
        a.start()
        assert first_allocated.wait(timeout=10)
        b.start()
        b.join(timeout=1)
        assert b.is_alive(), "the second allocation should be waiting on the lock"
        release_first.set()
        a.join(timeout=10)
        b.join(timeout=10)
        assert not errors, errors
        assert len(set(numbers)) == 2
