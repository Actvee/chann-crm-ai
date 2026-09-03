"""Phase 12 — tickets, the dispatch gate, and who may see or take one.

Two things here are worth more than the rest of the suite combined.

The dispatch gate: a technician sent without an address, a phone number
or a time is a wasted trip somebody has to apologise for. Every way of
assigning a ticket must go through the same check, or the gate is
decoration.

Visibility: a technician browsing the open list must not be reading the
address and phone number of a private job belonging to a colleague. That
is a privacy failure, not a UI preference.
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
    TicketConflict,
    TicketNotFound,
)

COMPLETE = {
    "customer_name": "จุใจ มาติกา",
    "customer_phone": "0659635642",
    "service_address": "99/1 ถนนสุขุมวิท กรุงเทพฯ",
    "scheduled_date": date(2026, 9, 4),
    "scheduled_time": time(14, 0),
}


@pytest.fixture
def tenant(migrated_db):
    """A license with two technicians, a team containing one of them, and
    a session factory."""
    from sqlalchemy.orm import Session

    from chann_data.models import (
        ChannIdentity, LicenseMember, TechnicianTeam, TechnicianTeamMember,
    )
    from chann_data.repositories.phase65 import RegistrationRepository
    from chann_data.repositories.tenant_scope import TenantScope

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-TK-{suffix}", line_user_id=f"line-tk-{suffix}",
            primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"Ticket {suffix}", created_by_chann_uid=f"CHN-TK-{suffix}",
        )
        session.commit()
        license_id = lic.id

    members = []
    with Session(migrated_db) as session:
        for index in range(2):
            identity = ChannIdentity(
                chann_uid=f"CHN-TECH-{suffix}{index}",
                line_user_id=f"line-tech-{suffix}{index}",
                primary_role="technician",
            )
            session.add(identity)
            session.flush()
            member = LicenseMember(
                id=uuid.uuid4(), license_id=license_id,
                chann_uid=identity.chann_uid, role="technician", status="active",
            )
            session.add(member)
            session.flush()
            members.append(member.id)

        team = TechnicianTeam(id=uuid.uuid4(), license_id=license_id, team_name="AC Team")
        session.add(team)
        session.flush()
        session.add(TechnicianTeamMember(
            id=uuid.uuid4(), license_id=license_id, team_id=team.id, member_id=members[0],
        ))
        session.commit()
        team_id = team.id

    return {
        "scope": TenantScope(license_id=license_id),
        "license_id": license_id,
        "members": members,
        "team_id": team_id,
        "session": lambda: Session(migrated_db),
    }


class TestTicketCreation:
    def test_only_a_description_is_required(self, tenant):
        """A customer reporting a fault must never be blocked on details a
        CS person can chase later. Completeness is enforced at dispatch,
        where it actually matters."""
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).create(
                tenant["scope"], issue_description="แอร์ไม่เย็น",
            )
            session.commit()
            assert row.ticket_number.startswith("T-")
            assert row.status == "open"
            assert row.customer_name is None

    def test_an_empty_description_is_refused(self, tenant):
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).create(
                    tenant["scope"], issue_description="   ",
                )

    def test_ticket_numbers_increment_within_the_tenant(self, tenant):
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            numbers = [
                repo.create(tenant["scope"], issue_description=f"งาน {i}").ticket_number
                for i in range(3)
            ]
            session.commit()
        assert len(set(numbers)) == 3
        assert numbers == sorted(numbers)


class TestDispatchGate:
    """Master Spec 12.5."""

    def _ticket(self, tenant, **fields):
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).create(
                tenant["scope"], issue_description="แอร์ไม่เย็น", **fields,
            )
            session.commit()
            return row.id

    def test_a_ticket_missing_everything_lists_every_gap(self, tenant):
        """The caller reads this list out to a person. A bare "cannot
        dispatch" makes them guess between five possibilities."""
        ticket_id = self._ticket(tenant)
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            with pytest.raises(DispatchBlocked) as caught:
                repo.assign(
                    tenant["scope"], ticket_id,
                    target_type="technician", target_ref=tenant["members"][0],
                )
            assert len(caught.value.missing) == 5

    @pytest.mark.parametrize(
        "omit,expected",
        [
            ("customer_name", "ชื่อลูกค้า"),
            ("customer_phone", "เบอร์ลูกค้า"),
            ("service_address", "ที่อยู่"),
            ("scheduled_date", "วันนัด"),
            ("scheduled_time", "เวลานัด"),
        ],
    )
    def test_each_missing_field_blocks_dispatch_by_name(self, tenant, omit, expected):
        fields = {k: v for k, v in COMPLETE.items() if k != omit}
        ticket_id = self._ticket(tenant, **fields)
        with tenant["session"]() as session:
            with pytest.raises(DispatchBlocked) as caught:
                ServiceTicketRepository(session).assign(
                    tenant["scope"], ticket_id,
                    target_type="technician", target_ref=tenant["members"][0],
                )
            assert expected in caught.value.missing

    def test_a_complete_ticket_dispatches(self, tenant):
        ticket_id = self._ticket(tenant, **COMPLETE)
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).assign(
                tenant["scope"], ticket_id,
                target_type="technician", target_ref=tenant["members"][0],
            )
            session.commit()
            assert row.status == "assigned"
            assert row.accept_status == "pending"

    def test_the_gate_can_be_checked_without_attempting_a_dispatch(self, tenant):
        """So a form can show the gaps while someone is still filling it in,
        not only after they press assign."""
        ticket_id = self._ticket(tenant, customer_name="ก")
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            missing = repo.dispatch_blockers(repo.get(tenant["scope"], ticket_id))
        assert "ชื่อลูกค้า" not in missing
        assert "ที่อยู่" in missing

    def test_a_technician_from_another_tenant_cannot_be_dispatched_to(
        self, tenant, migrated_db,
    ):
        """Without this check a ticket could be sent to someone in another
        company, who would then have the customer's address."""
        ticket_id = self._ticket(tenant, **COMPLETE)
        with tenant["session"]() as session:
            with pytest.raises(TicketNotFound):
                ServiceTicketRepository(session).assign(
                    tenant["scope"], ticket_id,
                    target_type="technician", target_ref=uuid.uuid4(),
                )

    def test_a_completed_ticket_cannot_be_reassigned(self, tenant):
        ticket_id = self._ticket(tenant, **COMPLETE)
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            repo.set_status(tenant["scope"], ticket_id, status="completed")
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).assign(
                    tenant["scope"], ticket_id,
                    target_type="technician", target_ref=tenant["members"][0],
                )


class TestVisibility:
    """Master Spec 12.1/12.4 — who may see which ticket."""

    def test_a_public_ticket_is_visible_to_everyone(self, tenant):
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            repo.create(tenant["scope"], issue_description="งานเปิด", visibility="public")
            session.commit()
        with tenant["session"]() as session:
            visible = ServiceTicketRepository(session).list_visible_to(
                tenant["scope"], member_id=tenant["members"][1],
            )
        assert len(visible) == 1

    def test_a_private_ticket_is_hidden_from_others(self, tenant):
        """The privacy case: browsing the list must not expose another
        customer's address because a colleague owns that job."""
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            row = repo.create(
                tenant["scope"], issue_description="งานส่วนตัว",
                visibility="private", **COMPLETE,
            )
            session.flush()
            repo.assign(
                tenant["scope"], row.id,
                target_type="technician", target_ref=tenant["members"][0],
            )
            session.commit()

        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            assert len(repo.list_visible_to(
                tenant["scope"], member_id=tenant["members"][0],
            )) == 1
            assert repo.list_visible_to(
                tenant["scope"], member_id=tenant["members"][1],
            ) == []

    def test_a_private_team_ticket_is_visible_to_that_team(self, tenant):
        """A team lead accepting on the team's behalf needs no separate
        mechanism — team membership IS the visibility."""
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            row = repo.create(
                tenant["scope"], issue_description="งานทีม",
                visibility="private", **COMPLETE,
            )
            session.flush()
            repo.assign(
                tenant["scope"], row.id,
                target_type="technician_team", target_ref=tenant["team_id"],
            )
            session.commit()

        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            # members[0] is in the team, members[1] is not.
            assert len(repo.list_visible_to(
                tenant["scope"], member_id=tenant["members"][0],
            )) == 1
            assert repo.list_visible_to(
                tenant["scope"], member_id=tenant["members"][1],
            ) == []


class TestClaimAndReject:
    """Master Spec 12.4."""

    def _assigned(self, tenant, *, visibility="public", to_team=False):
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            row = repo.create(
                tenant["scope"], issue_description="งาน", visibility=visibility, **COMPLETE,
            )
            session.flush()
            repo.assign(
                tenant["scope"], row.id,
                target_type="technician_team" if to_team else "technician",
                target_ref=tenant["team_id"] if to_team else tenant["members"][0],
            )
            session.commit()
            return row.id

    def test_anyone_may_claim_a_public_ticket(self, tenant):
        ticket_id = self._assigned(tenant, visibility="public")
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).claim(
                tenant["scope"], ticket_id, member_id=tenant["members"][1],
            )
            session.commit()
            assert row.accept_status == "accepted"
            # Claiming assigns; only check-in (13.4) makes it in_progress.
            assert row.status == "assigned"
            assert row.assigned_to_ref == tenant["members"][1]

    def test_a_private_ticket_may_not_be_claimed_by_an_outsider(self, tenant):
        ticket_id = self._assigned(tenant, visibility="private")
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).claim(
                    tenant["scope"], ticket_id, member_id=tenant["members"][1],
                )

    def test_a_team_member_may_claim_a_ticket_given_to_their_team(self, tenant):
        """12.4 in two steps: the team accepts (any member, since this
        team has no lead), then a member takes it for themselves."""
        ticket_id = self._assigned(tenant, visibility="private", to_team=True)
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            accepted = repo.claim(tenant["scope"], ticket_id, member_id=tenant["members"][0])
            assert accepted.assigned_target_type == "technician_team"
            assert accepted.accept_status == "accepted"
            row = repo.claim(tenant["scope"], ticket_id, member_id=tenant["members"][0])
            session.commit()
            # Claiming narrows a team assignment down to one person.
            assert row.assigned_target_type == "technician"
            assert row.assigned_to_ref == tenant["members"][0]

    def test_an_already_accepted_ticket_cannot_be_claimed_again(self, tenant):
        """Two technicians turning up is worse than one being told they
        were too late."""
        ticket_id = self._assigned(tenant)
        with tenant["session"]() as session:
            ServiceTicketRepository(session).claim(
                tenant["scope"], ticket_id, member_id=tenant["members"][0],
            )
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).claim(
                    tenant["scope"], ticket_id, member_id=tenant["members"][1],
                )

    def test_rejecting_returns_it_to_the_queue_without_reassigning(self, tenant):
        """12.4 explicitly: no auto-reassign. Silently passing it on would
        hide that the first technician said no, which the dispatcher
        usually needs to know."""
        ticket_id = self._assigned(tenant)
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).reject(
                tenant["scope"], ticket_id, member_id=tenant["members"][0],
            )
            session.commit()
            assert row.accept_status == "rejected"
            assert row.status == "open"

    def test_only_the_assignee_may_reject(self, tenant):
        ticket_id = self._assigned(tenant)
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).reject(
                    tenant["scope"], ticket_id, member_id=tenant["members"][1],
                )

    def test_assigning_again_resets_acceptance(self, tenant):
        """A ticket handed to someone new has not been accepted by them,
        whatever the previous assignee said."""
        ticket_id = self._assigned(tenant)
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            repo.claim(tenant["scope"], ticket_id, member_id=tenant["members"][0])
            session.commit()
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).assign(
                tenant["scope"], ticket_id,
                target_type="technician", target_ref=tenant["members"][1],
            )
            session.commit()
            assert row.accept_status == "pending"


class TestStatus:
    def test_a_completed_ticket_stays_completed(self, tenant):
        """Reopening would rewrite history a survey or an invoice may
        already reference."""
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            row = repo.create(tenant["scope"], issue_description="งาน")
            session.flush()
            repo.set_status(tenant["scope"], row.id, status="completed")
            session.commit()
            ticket_id = row.id
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).set_status(
                    tenant["scope"], ticket_id, status="open",
                )

    def test_an_unknown_status_is_refused(self, tenant):
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            row = repo.create(tenant["scope"], issue_description="งาน")
            session.flush()
            with pytest.raises(TicketConflict):
                repo.set_status(tenant["scope"], row.id, status="ยกเลิกไปแล้วมั้ง")


class TestTenantIsolation:
    def test_a_ticket_is_invisible_to_another_tenant(self, tenant, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).create(
                tenant["scope"], issue_description="ความลับ",
            )
            session.commit()
            ticket_id = row.id

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-OT-{suffix}", line_user_id=f"line-ot-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            other = RegistrationRepository(session).create_license(
                company_name=f"Other {suffix}", created_by_chann_uid=f"CHN-OT-{suffix}",
            )
            session.commit()
            other_scope = TenantScope(license_id=other.id)

        with Session(migrated_db) as session:
            assert ServiceTicketRepository(session).get(other_scope, ticket_id) is None
