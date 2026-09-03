"""Master Spec 12.4, the team half, as written: "Private → ทีม: หัวหน้าทีมกด
รับ = ทีมรับ → เปิด public ในทีม → สมาชิกกดรับ".

Plan B2 (docs/PLAN_3OA.md). Before this, any team member's claim took the
job for themselves in one step, which is fine for a two-person team and
wrong for a team with a lead who decides who goes.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_data.repositories.phase12 import (  # noqa: E402
    ServiceTicketRepository,
    TicketConflict,
)
from test_phase12_tickets import COMPLETE, tenant  # noqa: E402,F401


def _lead(tenant, member_id, is_lead=True):
    """Put member_id on the team, as lead or not (idempotent)."""
    from chann_data.models import TechnicianTeamMember

    with tenant["session"]() as session:
        row = session.query(TechnicianTeamMember).filter_by(
            team_id=tenant["team_id"], member_id=member_id,
        ).first()
        if row is None:
            session.add(TechnicianTeamMember(
                id=uuid.uuid4(), license_id=tenant["license_id"],
                team_id=tenant["team_id"], member_id=member_id, is_lead=is_lead,
            ))
        else:
            row.is_lead = is_lead
        session.commit()


def _team_ticket(tenant):
    with tenant["session"]() as session:
        repo = ServiceTicketRepository(session)
        row = repo.create(
            tenant["scope"], issue_description="แอร์ไม่เย็น", visibility="private",
            **COMPLETE,
        )
        session.flush()
        repo.assign(
            tenant["scope"], row.id, target_type="technician_team", target_ref=tenant["team_id"],
        )
        session.commit()
        return row.id


class TestLeadFirst:
    def test_a_team_without_a_lead_lets_any_member_accept_for_it(self, tenant):
        ticket_id = _team_ticket(tenant)
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).claim(
                tenant["scope"], ticket_id, member_id=tenant["members"][0],
            )
            session.commit()
            # Accepted FOR the team: still the team's, now open inside it.
            assert row.accept_status == "accepted"
            assert row.assigned_target_type == "technician_team"
            assert row.assigned_to_ref == tenant["team_id"]
            assert row.status == "assigned"

    def test_with_a_lead_only_the_lead_accepts(self, tenant):
        _lead(tenant, tenant["members"][1], is_lead=True)   # members[1] joins as lead
        ticket_id = _team_ticket(tenant)
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).claim(
                    tenant["scope"], ticket_id, member_id=tenant["members"][0],
                )
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).claim(
                tenant["scope"], ticket_id, member_id=tenant["members"][1],
            )
            session.commit()
            assert row.accept_status == "accepted" and row.assigned_to_ref == tenant["team_id"]

    def test_after_the_team_accepted_a_member_takes_it(self, tenant):
        _lead(tenant, tenant["members"][1], is_lead=True)
        ticket_id = _team_ticket(tenant)
        with tenant["session"]() as session:
            ServiceTicketRepository(session).claim(
                tenant["scope"], ticket_id, member_id=tenant["members"][1],
            )
            session.commit()
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).claim(
                tenant["scope"], ticket_id, member_id=tenant["members"][0],
            )
            session.commit()
            assert row.assigned_target_type == "technician"
            assert row.assigned_to_ref == tenant["members"][0]
            assert row.status == "assigned"

    def test_an_outsider_cannot_take_a_private_team_job(self, tenant):
        """members[1] is not on the team here."""
        ticket_id = _team_ticket(tenant)
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).claim(
                    tenant["scope"], ticket_id, member_id=tenant["members"][1],
                )

    def test_the_lead_may_decline_for_the_team(self, tenant):
        _lead(tenant, tenant["members"][1], is_lead=True)
        ticket_id = _team_ticket(tenant)
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).reject(
                    tenant["scope"], ticket_id, member_id=tenant["members"][0],
                )
        with tenant["session"]() as session:
            row = ServiceTicketRepository(session).reject(
                tenant["scope"], ticket_id, member_id=tenant["members"][1],
            )
            session.commit()
            assert row.accept_status == "rejected" and row.status == "open"

    def test_a_taken_job_cannot_be_taken_again(self, tenant):
        ticket_id = _team_ticket(tenant)
        with tenant["session"]() as session:
            repo = ServiceTicketRepository(session)
            repo.claim(tenant["scope"], ticket_id, member_id=tenant["members"][0])  # team accepts
            repo.claim(tenant["scope"], ticket_id, member_id=tenant["members"][0])  # member takes
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(TicketConflict):
                ServiceTicketRepository(session).claim(
                    tenant["scope"], ticket_id, member_id=tenant["members"][0],
                )
