"""Phase 11 — assignment rules and the locking around capacity.

The lock is the part that matters. Ten tickets arriving at once for the
same team, with a five-a-day cap, must produce five assignments and five
overflows — not ten technicians each reading "load is 4" and all deciding
they are fine.

`SELECT ... FOR UPDATE` on the license row is the serialisation point.
Coarse on purpose: assignment is not a hot path (a handful per minute at
SMB scale), and a per-member lock would let two tickets pick two
different members concurrently and both blow the same team's cap.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AssignmentRule,
    Customer,
    Deal,
    License,
    LicenseMember,
    SalesGroup,
    SalesGroupMember,
    TechnicianTeam,
    TechnicianTeamMember,
)
from .tenant_scope import TenantScope


class AssignmentRuleNotFound(Exception):
    pass


def _candidate(member: LicenseMember) -> dict:
    """What the engine sees of a member. `last_assigned_at` is the
    round_robin key (0029); a never-assigned member sorts first."""
    stamp = member.last_assigned_at
    return {
        "id": str(member.id),
        "chann_uid": member.chann_uid,
        "role": member.role,
        "last_assigned_at": stamp.isoformat() if stamp else "",
    }


class AssignmentRuleRepository:
    def __init__(self, session: Session):
        self._s = session

    def get_active(self, scope: TenantScope, *, rule_scope: str) -> AssignmentRule | None:
        return self._s.execute(
            select(AssignmentRule).where(
                AssignmentRule.license_id == scope.license_id,
                AssignmentRule.scope == rule_scope,
                AssignmentRule.is_active.is_(True),
            )
        ).scalars().first()

    def upsert_active(
        self, scope: TenantScope, *, rule_scope: str, rules_json: dict,
        updated_by: uuid.UUID | None = None,
    ) -> AssignmentRule:
        """Replace the active rule for a scope, keeping the old one.

        Deactivates rather than overwrites: a rule that assigned work last
        month explains why those records look the way they do, and the
        audit trail points at it. The partial unique index means the old
        row must stop being active before the new one exists.
        """
        existing = self.get_active(scope, rule_scope=rule_scope)
        if existing is not None:
            existing.is_active = False
            self._s.flush()

        row = AssignmentRule(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            scope=rule_scope,
            rules_json=rules_json,
            is_active=True,
            updated_by=updated_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def deactivate_active(self, scope: TenantScope, *, rule_scope: str) -> AssignmentRule | None:
        """Switch the active rule for a scope off, keeping the row.

        The same shape as the deactivation upsert_active does before it
        writes a new rule — without the new rule. Until 21 ก.ย. 2569 a shop
        that set a rule from chat could only REPLACE it; there was no way
        to go back to "nobody is assigned automatically" (owner's gap
        list). None when there was nothing active, so the caller can say
        so rather than confirm a change that did not happen.
        """
        existing = self.get_active(scope, rule_scope=rule_scope)
        if existing is None:
            return None
        existing.is_active = False
        self._s.flush()
        return existing

    def list_for_license(self, scope: TenantScope) -> list[AssignmentRule]:
        return list(
            self._s.execute(
                select(AssignmentRule)
                .where(AssignmentRule.license_id == scope.license_id)
                .order_by(AssignmentRule.created_at.desc())
            ).scalars()
        )

    # ------------------------------------------------------------ members

    def team_members(
        self, scope: TenantScope, *, team_name: str, rule_scope: str = "technician",
    ) -> list[dict]:
        """Active members of a named team, as plain dicts for the engine.

        Returned as dicts rather than ORM rows so the engine stays pure and
        testable without a database — the ordering and capacity logic is
        where the bugs would be, and it should not need a session to
        exercise.

        A "sales" rule names a sales GROUP (Phase 7), a "technician" rule a
        technician TEAM (Phase 12): until round 18 (14 Sep 2026) only the
        team tables were consulted, so a sales rule could never find
        anyone.
        """
        if rule_scope == "sales":
            query = (
                select(LicenseMember)
                .join(SalesGroupMember, SalesGroupMember.member_id == LicenseMember.id)
                .join(SalesGroup, SalesGroup.id == SalesGroupMember.group_id)
                .where(
                    LicenseMember.license_id == scope.license_id,
                    SalesGroup.license_id == scope.license_id,
                    SalesGroup.group_name == team_name,
                    LicenseMember.status == "active",
                )
            )
        else:
            query = (
                select(LicenseMember)
                .join(
                    TechnicianTeamMember,
                    TechnicianTeamMember.member_id == LicenseMember.id,
                )
                .join(TechnicianTeam, TechnicianTeam.id == TechnicianTeamMember.team_id)
                .where(
                    LicenseMember.license_id == scope.license_id,
                    TechnicianTeam.license_id == scope.license_id,
                    TechnicianTeam.team_name == team_name,
                    LicenseMember.status == "active",
                )
            )
        rows = self._s.execute(query.order_by(LicenseMember.id)).scalars().all()
        return [_candidate(member) for member in rows]

    def team_members_with_lead(
        self, scope: TenantScope, *, team_id: uuid.UUID,
    ) -> list[tuple[LicenseMember, bool]]:
        """(member, is_lead) for a team — the lead flag is what the
        technician home and the teams page show, and what 12.4's
        lead-first acceptance is decided on."""
        rows = self._s.execute(
            select(LicenseMember, TechnicianTeamMember.is_lead)
            .join(
                TechnicianTeamMember,
                TechnicianTeamMember.member_id == LicenseMember.id,
            )
            .where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.team_id == team_id,
                LicenseMember.status == "active",
            )
        ).all()
        return [(member, bool(is_lead)) for member, is_lead in rows]

    def team_members_by_id(
        self, scope: TenantScope, *, team_id: uuid.UUID,
    ) -> list[LicenseMember]:
        """Members of a team, as ORM rows for a response model.

        By id rather than by name, unlike team_members above: a caller that
        already holds the team id should not have to round-trip through a
        name that a tenant may have chosen to be ambiguous.
        """
        rows = self._s.execute(
            select(LicenseMember)
            .join(
                TechnicianTeamMember,
                TechnicianTeamMember.member_id == LicenseMember.id,
            )
            .where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.team_id == team_id,
                LicenseMember.status == "active",
            )
            .order_by(LicenseMember.id)
        ).scalars()
        return list(rows)

    def active_members(
        self, scope: TenantScope, *, role: str | None = None, channel: str | None = None,
    ) -> list[dict]:
        """`channel` is the OA the membership belongs to ("sales" |
        "technician") — the same words as a rule's scope, which is what
        the no-criterion-matched fallback filters on. Without it the
        fallback pool for a technician rule was the whole staff list,
        salespeople included (round 18)."""
        query = select(LicenseMember).where(
            LicenseMember.license_id == scope.license_id,
            LicenseMember.status == "active",
        )
        if role:
            query = query.where(LicenseMember.role == role)
        if channel:
            query = query.where(LicenseMember.channel == channel)
        return [
            _candidate(m)
            for m in self._s.execute(query.order_by(LicenseMember.id)).scalars()
        ]

    def touch_assigned(self, scope: TenantScope, member_id: uuid.UUID, *, now: datetime | None = None) -> None:
        """Stamp the member the engine just picked, so round_robin has the
        memory its ordering reads (0029)."""
        row = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.id == member_id, LicenseMember.license_id == scope.license_id,
            )
        ).scalars().first()
        if row is not None:
            row.last_assigned_at = now or datetime.now(timezone.utc)
            self._s.flush()

    def owner_members(self, scope: TenantScope) -> list[dict]:
        """Who to fall back to when nobody else can take the work.

        11.1 requires an assignment to always land on someone: an
        unassigned job is one nobody is accountable for, which is worse
        than one assigned to a busy owner who can hand it on.
        """
        rows = self._s.execute(
            select(LicenseMember)
            .where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.status == "active",
                LicenseMember.role.in_(("owner", "admin")),
            )
            .order_by(LicenseMember.role, LicenseMember.id)
        ).scalars()
        return [_candidate(m) for m in rows]

    # ----------------------------------------------------------- capacity

    def lock_license(self, scope: TenantScope) -> None:
        """Serialise assignment within one tenant.

        Coarse on purpose. Assignment runs a handful of times a minute at
        SMB scale, so the contention cost is irrelevant; a finer per-member
        lock would let two concurrent tickets each pick a DIFFERENT member
        and both pass their own capacity check while together breaking the
        team's cap.

        Scoped to the license so one tenant's burst cannot stall another's.
        """
        self._s.execute(
            select(License.id).where(License.id == scope.license_id).with_for_update()
        ).first()

    def current_loads(
        self, scope: TenantScope, member_ids: list[str], *, on_day: date,
        entity_type: str = "deal",
    ) -> dict[str, int]:
        """How much each member has already been given today.

        Counted from the deals actually assigned rather than a counter
        column: a counter drifts the moment anything reassigns or deletes,
        and the number that matters is "how much work does this person
        really have", not "how many times did we increment".
        """
        if not member_ids:
            return {}
        ids = [uuid.UUID(m) for m in member_ids]
        start = datetime.combine(on_day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        if entity_type in ("ticket", "service_ticket"):
            # Technicians own no deals, so their load was always 0 and the
            # per-day cap never bit (review, 6 Sep 2026): count the jobs
            # given to them for that day. Both spellings: the caller says
            # "service_ticket" (the entity's own name), and the one-word
            # form here silently sent it down the deal branch (round 18).
            from ..models import ServiceTicket
            from sqlalchemy import and_, or_

            rows = self._s.execute(
                select(ServiceTicket.assigned_to_ref, func.count(ServiceTicket.id))
                .where(
                    ServiceTicket.license_id == scope.license_id,
                    ServiceTicket.assigned_target_type == "technician",
                    ServiceTicket.assigned_to_ref.in_(ids),
                    ServiceTicket.status.in_(("assigned", "in_progress")),
                    or_(
                        ServiceTicket.scheduled_date == on_day,
                        and_(
                            ServiceTicket.scheduled_date.is_(None),
                            ServiceTicket.updated_at >= start, ServiceTicket.updated_at < end,
                        ),
                    ),
                )
                .group_by(ServiceTicket.assigned_to_ref)
            ).all()
            loads = {str(member_id): count for member_id, count in rows}
            return {m: loads.get(m, 0) for m in member_ids}
        if entity_type == "customer":
            # A sales rule hands out new customers (leads): the day's load
            # is how many the person was given today.
            rows = self._s.execute(
                select(Customer.owner_member_id, func.count(Customer.id))
                .where(
                    Customer.license_id == scope.license_id,
                    Customer.owner_member_id.in_(ids),
                    Customer.created_at >= start,
                    Customer.created_at < end,
                )
                .group_by(Customer.owner_member_id)
            ).all()
            loads = {str(member_id): count for member_id, count in rows}
            return {m: loads.get(m, 0) for m in member_ids}
        rows = self._s.execute(
            select(Deal.owner_member_id, func.count(Deal.id))
            .where(
                Deal.license_id == scope.license_id,
                Deal.owner_member_id.in_(ids),
                Deal.created_at >= start,
                Deal.created_at < end,
            )
            .group_by(Deal.owner_member_id)
        ).all()
        loads = {str(member_id): count for member_id, count in rows}
        # Absent means zero, and the engine should not have to know that.
        return {m: loads.get(m, 0) for m in member_ids}

    def assign_customer(
        self, scope: TenantScope, customer_id: uuid.UUID, member_id: uuid.UUID,
    ) -> Customer:
        row = self._s.execute(
            select(Customer).where(Customer.id == customer_id, Customer.license_id == scope.license_id)
        ).scalars().first()
        if row is None:
            raise AssignmentRuleNotFound("customer not found in this tenant")
        row.owner_member_id = member_id
        self._s.flush()
        return row

    def assign_deal(
        self, scope: TenantScope, deal_id: uuid.UUID, member_id: uuid.UUID,
    ) -> Deal:
        row = self._s.execute(
            select(Deal).where(Deal.id == deal_id, Deal.license_id == scope.license_id)
        ).scalars().first()
        if row is None:
            raise AssignmentRuleNotFound("deal not found in this tenant")
        row.owner_member_id = member_id
        self._s.flush()
        return row
