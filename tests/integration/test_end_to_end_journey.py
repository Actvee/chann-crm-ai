"""The whole journey, one test.

Every other suite exercises one layer: a repository called directly, or a
chat handler against a fake client. That is why two things reached
production undetected — Phase 12's ticket endpoints existed only in the
Data tier so every dashboard call 404'd, and nothing could create a ticket
at all because the customer's own entry point was never built.

Neither gap is visible from inside a layer. Both are obvious the moment
you try to walk from "a customer reports a fault" to "a technician closes
the job", which is what this file does.

Deliberately end-to-end through the repositories against a real database
rather than through HTTP: the routing is checked by the endpoint smoke
test, and what matters here is that the STEPS connect — that each one's
output is something the next one can actually use.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase12 import (  # noqa: E402
    DispatchBlocked,
    ServiceTicketRepository,
)
from chann_data.repositories.phase13 import (  # noqa: E402
    CheckoutBlocked,
    FieldServiceRepository,
)
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402


@pytest.fixture
def shop(migrated_db):
    """A shop with a CS owner, two technicians and a team."""
    from sqlalchemy.orm import Session

    from chann_data.models import (
        ChannIdentity, LicenseMember, TechnicianTeam, TechnicianTeamMember,
    )
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-E2E-{suffix}", line_user_id=f"line-e2e-{suffix}",
            primary_role="sales",
        ))
        # The customer is a person too, and exists before any CRM record
        # for them does — which is the situation a fault report starts in.
        session.add(ChannIdentity(
            chann_uid=f"CHN-CUST-{suffix}", line_user_id=f"line-cust-{suffix}",
            primary_role="customer",
        ))
        session.commit()

    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"ร้านแอร์ {suffix}", created_by_chann_uid=f"CHN-E2E-{suffix}",
        )
        session.commit()
        license_id = lic.id

    with Session(migrated_db) as session:
        identity = ChannIdentity(
            chann_uid=f"CHN-TECH-{suffix}", line_user_id=f"line-tech-{suffix}",
            primary_role="technician",
        )
        session.add(identity)
        session.flush()
        technician = LicenseMember(
            id=uuid.uuid4(), license_id=license_id,
            chann_uid=identity.chann_uid, role="technician", status="active",
        )
        session.add(technician)
        session.flush()
        team = TechnicianTeam(
            id=uuid.uuid4(), license_id=license_id, team_name="ทีมแอร์",
        )
        session.add(team)
        session.flush()
        session.add(TechnicianTeamMember(
            id=uuid.uuid4(), license_id=license_id,
            team_id=team.id, member_id=technician.id,
        ))
        session.commit()
        technician_id = technician.id
        team_id = team.id

    return {
        "scope": TenantScope(license_id=license_id),
        "customer_uid": f"CHN-CUST-{suffix}",
        "technician_id": technician_id,
        "team_id": team_id,
        "session": lambda: Session(migrated_db),
    }


class TestTheWholeServiceJourney:
    def test_a_fault_report_becomes_a_completed_job(self, shop):
        """Customer reports → CS dispatches → technician takes it, arrives,
        and closes it with a report.

        Every step consumes what the previous one produced. A gap anywhere
        breaks this and nothing else.
        """
        scope = shop["scope"]

        # 1. A customer reports a fault. Only a description — they are
        #    standing in front of a broken air conditioner, not filling in
        #    a form.
        with shop["session"]() as session:
            ticket = ServiceTicketRepository(session).create(
                scope,
                issue_description="แอร์ไม่เย็น มีน้ำหยด",
                customer_chann_uid=shop["customer_uid"],
            )
            session.commit()
            ticket_id = ticket.id
            assert ticket.status == "open"
            assert ticket.ticket_number.startswith("T-")

        # 2. CS tries to dispatch immediately and is stopped, by name.
        with shop["session"]() as session:
            with pytest.raises(DispatchBlocked) as caught:
                ServiceTicketRepository(session).assign(
                    scope, ticket_id,
                    target_type="technician_team", target_ref=shop["team_id"],
                )
            assert "ที่อยู่" in caught.value.missing

        # 3. The missing details are collected — which is what the
        #    customer conversation does after the report is accepted.
        with shop["session"]() as session:
            ServiceTicketRepository(session).update(scope, ticket_id, {
                "customer_name": "จุใจ มาติกา",
                "customer_phone": "0659635642",
                "service_address": "99/1 ถนนสุขุมวิท",
                "scheduled_date": date(2026, 9, 4),
                "scheduled_time": time(14, 0),
            })
            session.commit()

        # 4. Now it dispatches, to the team.
        with shop["session"]() as session:
            row = ServiceTicketRepository(session).assign(
                scope, ticket_id,
                target_type="technician_team", target_ref=shop["team_id"],
            )
            session.commit()
            assert row.status == "assigned"
            assert row.accept_status == "pending"

        # 5. A technician on that team takes it.
        with shop["session"]() as session:
            row = ServiceTicketRepository(session).claim(
                scope, ticket_id, member_id=shop["technician_id"],
            )
            session.commit()
            assert row.assigned_to_ref == shop["technician_id"]
            assert row.status == "in_progress"

        # 6. They arrive.
        with shop["session"]() as session:
            FieldServiceRepository(session).check_in(
                scope, ticket_id, member_id=shop["technician_id"],
                gps_lat=13.7563309, gps_lng=100.5017651,
            )
            session.commit()

        # 7. They try to leave without writing anything down, and cannot.
        with shop["session"]() as session:
            with pytest.raises(CheckoutBlocked):
                FieldServiceRepository(session).check_out(
                    scope, ticket_id, member_id=shop["technician_id"], report_data={},
                )

        # 8. They write the report and the job closes.
        with shop["session"]() as session:
            report = FieldServiceRepository(session).check_out(
                scope, ticket_id, member_id=shop["technician_id"],
                report_data={
                    "found_issue": "ท่อน้ำทิ้งตัน",
                    "work_done": "ล้างท่อและเติมน้ำยา",
                },
                gps_lat=13.7563309, gps_lng=100.5017651,
            )
            session.commit()
            assert report.status == "submitted"

        # 9. What the customer would now see.
        with shop["session"]() as session:
            final = ServiceTicketRepository(session).get(scope, ticket_id)
            assert final.status == "completed"
            photos = FieldServiceRepository(session).list_photos(scope, ticket_id)
            # Arrival and departure are both on the record — which is the
            # point of putting GPS on the photo rather than the ticket.
            assert {p.photo_type for p in photos} == {"checkin", "checkout"}

    def test_a_customer_only_ever_sees_their_own_report(self, shop, migrated_db):
        """A ticket holds an address and a phone number. Another shop's
        customer must not be able to read it."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        scope = shop["scope"]
        with shop["session"]() as session:
            ticket = ServiceTicketRepository(session).create(
                scope, issue_description="ความลับ",
                customer_chann_uid=shop["customer_uid"],
            )
            session.commit()
            ticket_id = ticket.id

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-OTHER-{suffix}", line_user_id=f"line-other-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            other = RegistrationRepository(session).create_license(
                company_name=f"อีกร้าน {suffix}",
                created_by_chann_uid=f"CHN-OTHER-{suffix}",
            )
            session.commit()
            other_scope = TenantScope(license_id=other.id)

        with Session(migrated_db) as session:
            assert ServiceTicketRepository(session).get(other_scope, ticket_id) is None

    def test_a_technician_cannot_close_a_job_that_is_not_theirs(self, shop, migrated_db):
        """Two technicians, one job. The one who did not take it must not
        be able to file the report for it."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, LicenseMember
        from chann_data.repositories.phase13 import ReportConflict

        scope = shop["scope"]
        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            identity = ChannIdentity(
                chann_uid=f"CHN-T2-{suffix}", line_user_id=f"line-t2-{suffix}",
                primary_role="technician",
            )
            session.add(identity)
            session.flush()
            other_tech = LicenseMember(
                id=uuid.uuid4(), license_id=scope.license_id,
                chann_uid=identity.chann_uid, role="technician", status="active",
            )
            session.add(other_tech)
            session.commit()
            other_tech_id = other_tech.id

        with shop["session"]() as session:
            repo = ServiceTicketRepository(session)
            ticket = repo.create(
                scope, issue_description="งาน",
                customer_name="ก", customer_phone="0800000000",
                service_address="99/1", scheduled_date=date(2026, 9, 4),
                scheduled_time=time(9, 0),
            )
            session.flush()
            repo.assign(
                scope, ticket.id,
                target_type="technician", target_ref=shop["technician_id"],
            )
            session.commit()
            ticket_id = ticket.id

        with shop["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).check_out(
                    scope, ticket_id, member_id=other_tech_id,
                    report_data={"found_issue": "ก", "work_done": "ข"},
                )
