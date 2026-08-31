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
    Deal,
    License,
    LicenseMember,
    TechnicianTeam,
    TechnicianTeamMember,
)
from .tenant_scope import TenantScope


class AssignmentRuleNotFound(Exception):
    pass


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
        self, scope: TenantScope, *, team_name: str,
    ) -> list[dict]:
        """Active members of a named team, as plain dicts for the engine.

        Returned as dicts rather than ORM rows so the engine stays pure and
        testable without a database — the ordering and capacity logic is
        where the bugs would be, and it should not need a session to
        exercise.
        """
        rows = self._s.execute(
            select(LicenseMember, TechnicianTeamMember)
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
            .order_by(LicenseMember.id)
        ).all()
        return [
            {"id": str(member.id), "chann_uid": member.chann_uid, "role": member.role}
            for member, _link in rows
        ]

    def active_members(self, scope: TenantScope, *, role: str | None = None) -> list[dict]:
        query = select(LicenseMember).where(
            LicenseMember.license_id == scope.license_id,
            LicenseMember.status == "active",
        )
        if role:
            query = query.where(LicenseMember.role == role)
        return [
            {"id": str(m.id), "chann_uid": m.chann_uid, "role": m.role}
            for m in self._s.execute(query.order_by(LicenseMember.id)).scalars()
        ]

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
        return [{"id": str(m.id), "chann_uid": m.chann_uid, "role": m.role} for m in rows]

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
