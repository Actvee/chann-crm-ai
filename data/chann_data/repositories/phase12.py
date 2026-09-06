"""Phase 12 — service tickets, dispatching them, and who may take one.

The dispatch gate (12.5) is the part that earns its place. A technician
sent to a job without an address, a phone number, or a time is a wasted
trip that someone has to apologise for; refusing to dispatch until those
exist is cheaper than any recovery afterwards.

The gate lives here, in the Data tier, alongside the write it guards.
Putting it in the Application tier would leave the endpoint that actually
sets assigned_to_ref reachable without it — and a gate you can walk
around is decoration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LicenseMember, ServiceTicket, TechnicianTeam, TechnicianTeamMember
from .locks import serialise
from .tenant_scope import TenantScope

TICKET_STATUSES = frozenset(
    {"open", "assigned", "in_progress", "completed", "cancelled"}
)
VISIBILITIES = frozenset({"public", "private"})
TARGET_TYPES = frozenset({"technician", "technician_team"})
ACCEPT_STATUSES = frozenset({"pending", "accepted", "rejected"})

# What must be on a ticket before anyone is sent to it (12.5). Ordered the
# way a person would ask for them, because the list is read aloud in a
# chat reply.
DISPATCH_REQUIRED = (
    ("customer_name", "ชื่อลูกค้า"),
    ("customer_phone", "เบอร์ลูกค้า"),
    ("service_address", "ที่อยู่"),
    ("scheduled_date", "วันนัด"),
    ("scheduled_time", "เวลานัด"),
)


class TicketNotFound(Exception):
    pass


class TicketConflict(Exception):
    """A legal ticket in an illegal transition."""


class DispatchBlocked(Exception):
    """The ticket is missing something a technician would need on site.

    Carries the field labels so the caller can name them; a bare "cannot
    dispatch" makes the person guess which of five things is missing.
    """

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(", ".join(missing))


class ServiceTicketRepository:
    def __init__(self, session: Session):
        self._s = session

    # -------------------------------------------------------------- codes

    def _next_ticket_number(self, scope: TenantScope) -> str:
        """T-YYYY-NNNN, numbered within this tenant.

        Per-tenant like customer_id, deal_id and quote_id — see the Deal
        model's docstring for why global numbering was abandoned across
        this project.
        """
        year = datetime.now(timezone.utc).year
        prefix = f"T-{year}-"
        serialise(self._s, f"{scope.license_id}:ticket")
        existing = self._s.execute(
            select(ServiceTicket.ticket_number).where(
                ServiceTicket.license_id == scope.license_id,
                ServiceTicket.ticket_number.like(f"{prefix}%"),
            )
        ).scalars().all()
        used = {
            int(code.rsplit("-", 1)[1])
            for code in existing
            if code.rsplit("-", 1)[1].isdigit()
        }
        return f"{prefix}{(max(used) + 1) if used else 1:04d}"

    # ------------------------------------------------------------- create

    def create(
        self,
        scope: TenantScope,
        *,
        issue_description: str,
        customer_chann_uid: str | None = None,
        contact_id: uuid.UUID | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        product_id: uuid.UUID | None = None,
        serial_number: str | None = None,
        service_address: str | None = None,
        scheduled_date=None,
        scheduled_time=None,
        visibility: str = "public",
        owner_member_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> ServiceTicket:
        """Record a reported fault.

        Only the description is required. A customer reporting a problem
        should never be blocked on details a CS person can chase later —
        the dispatch gate is where completeness is enforced, at the point
        it actually matters.
        """
        description = (issue_description or "").strip()
        if not description:
            raise TicketConflict("a ticket needs a description of the problem")
        if visibility not in VISIBILITIES:
            raise TicketConflict(f"unknown visibility: {visibility!r}")

        row = ServiceTicket(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            ticket_number=self._next_ticket_number(scope),
            customer_chann_uid=customer_chann_uid,
            contact_id=contact_id,
            customer_name=(customer_name or "").strip() or None,
            customer_phone=(customer_phone or "").strip() or None,
            product_id=product_id,
            serial_number=(serial_number or "").strip() or None,
            issue_description=description,
            status="open",
            visibility=visibility,
            accept_status="pending",
            service_address=(service_address or "").strip() or None,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            owner_member_id=owner_member_id,
            created_by=created_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    # --------------------------------------------------------------- read

    def get(self, scope: TenantScope, ticket_id: uuid.UUID) -> ServiceTicket | None:
        return self._s.execute(
            select(ServiceTicket).where(
                ServiceTicket.id == ticket_id,
                ServiceTicket.license_id == scope.license_id,
            )
        ).scalars().first()

    def _get_locked(self, scope: TenantScope, ticket_id: uuid.UUID) -> ServiceTicket | None:
        """The row, locked until commit — claim/reject are read-check-write
        and two technicians tapping "รับงาน" at once both used to win
        (review, 6 Sep 2026)."""
        return self._s.execute(
            select(ServiceTicket).where(
                ServiceTicket.id == ticket_id,
                ServiceTicket.license_id == scope.license_id,
            ).with_for_update()
        ).scalars().first()

    def get_by_number(self, scope: TenantScope, number: str) -> ServiceTicket | None:
        return self._s.execute(
            select(ServiceTicket).where(
                ServiceTicket.license_id == scope.license_id,
                ServiceTicket.ticket_number == number,
            )
        ).scalars().first()

    def list_for_license(
        self, scope: TenantScope, *, status: str | None = None, limit: int = 100,
    ) -> list[ServiceTicket]:
        query = select(ServiceTicket).where(ServiceTicket.license_id == scope.license_id)
        if status:
            query = query.where(ServiceTicket.status == status)
        return list(
            self._s.execute(
                query.order_by(ServiceTicket.created_at.desc()).limit(max(1, min(limit, 500)))
            ).scalars()
        )

    def list_visible_to(
        self, scope: TenantScope, *, member_id: uuid.UUID, limit: int = 100,
    ) -> list[ServiceTicket]:
        """What one technician may see (12.1 visibility).

        Public tickets are visible to everyone so they can be claimed.
        Private ones are visible only to the member or team they were
        given to — a technician browsing a list should not be reading
        another customer's address because a colleague happens to own that
        job.
        """
        team_ids = [
            row for row in self._s.execute(
                select(TechnicianTeamMember.team_id).where(
                    TechnicianTeamMember.license_id == scope.license_id,
                    TechnicianTeamMember.member_id == member_id,
                )
            ).scalars()
        ]

        visible_private = ServiceTicket.assigned_to_ref == member_id
        if team_ids:
            visible_private = visible_private | ServiceTicket.assigned_to_ref.in_(team_ids)

        query = select(ServiceTicket).where(
            ServiceTicket.license_id == scope.license_id,
            (ServiceTicket.visibility == "public") | visible_private,
        )
        return list(
            self._s.execute(
                query.order_by(ServiceTicket.created_at.desc()).limit(max(1, min(limit, 500)))
            ).scalars()
        )

    # ------------------------------------------------------------- update

    def update(self, scope: TenantScope, ticket_id: uuid.UUID, fields: dict) -> ServiceTicket:
        """Edit a ticket's own details.

        status, visibility and every assignment column are excluded: each
        has its own method with its own rules, and letting a generic patch
        set them would make those rules advisory.
        """
        row = self.get(scope, ticket_id)
        if row is None:
            raise TicketNotFound("ticket not found in this tenant")
        allowed = {
            "customer_name", "customer_phone", "service_address", "serial_number",
            "issue_description", "scheduled_date", "scheduled_time", "owner_member_id",
        }
        for key, value in fields.items():
            if key not in allowed:
                continue
            if isinstance(value, str):
                value = value.strip() or None
            setattr(row, key, value)
        self._s.flush()
        return row

    # ----------------------------------------------------- dispatch gate

    def dispatch_blockers(self, ticket: ServiceTicket) -> list[str]:
        """What is still missing before anyone can be sent (12.5).

        Returns labels rather than field names because the list is read by
        a person in a chat message, and "scheduled_time" is not something
        they typed or would recognise.
        """
        return [
            label for field, label in DISPATCH_REQUIRED
            if not getattr(ticket, field, None)
        ]

    def assign(
        self, scope: TenantScope, ticket_id: uuid.UUID, *,
        target_type: str, target_ref: uuid.UUID,
    ) -> ServiceTicket:
        """Send a ticket to a technician or a team, if it is ready to go."""
        if target_type not in TARGET_TYPES:
            raise TicketConflict(f"unknown assignment target: {target_type!r}")

        row = self.get(scope, ticket_id)
        if row is None:
            raise TicketNotFound("ticket not found in this tenant")
        if row.status in ("completed", "cancelled"):
            raise TicketConflict(f"a {row.status} ticket cannot be assigned")

        blockers = self.dispatch_blockers(row)
        if blockers:
            raise DispatchBlocked(blockers)

        # The target must belong to this tenant. Without this check a
        # ticket could be dispatched to a technician in another company,
        # who would then see the customer's address.
        if target_type == "technician":
            exists = self._s.execute(
                select(LicenseMember.id).where(
                    LicenseMember.id == target_ref,
                    LicenseMember.license_id == scope.license_id,
                    LicenseMember.status == "active",
                )
            ).first()
            if exists is None:
                raise TicketNotFound("no such active technician in this tenant")
        else:
            exists = self._s.execute(
                select(TechnicianTeam.id).where(
                    TechnicianTeam.id == target_ref,
                    TechnicianTeam.license_id == scope.license_id,
                )
            ).first()
            if exists is None:
                raise TicketNotFound("no such team in this tenant")

        row.assigned_target_type = target_type
        row.assigned_to_ref = target_ref
        row.status = "assigned"
        # Reset on every assignment: a ticket handed to someone new has not
        # been accepted by them, whatever the previous assignee said.
        row.accept_status = "pending"
        self._s.flush()
        return row

    def claim(
        self, scope: TenantScope, ticket_id: uuid.UUID, *, member_id: uuid.UUID,
    ) -> ServiceTicket:
        """A technician takes a job (12.4).

        Public tickets are first-come. Private ones may only be taken by
        the member they were given to, or by a member of the team they
        were given to — which is how a team lead accepting on the team's
        behalf works, without a separate mechanism.

        Claiming an already-accepted ticket raises rather than silently
        transferring it: two technicians turning up is worse than one
        being told they were too late.
        """
        row = self._get_locked(scope, ticket_id)
        if row is None:
            raise TicketNotFound("ticket not found in this tenant")
        if row.status in ("completed", "cancelled"):
            raise TicketConflict(f"a {row.status} ticket cannot be claimed")
        if row.accept_status == "accepted" and row.assigned_target_type != "technician_team":
            raise TicketConflict("this ticket has already been accepted")

        if row.assigned_target_type == "technician_team" and row.assigned_to_ref:
            # 12.4, the team flow: the lead accepts on the team's behalf
            # (the ticket stays the team's, marked accepted), which opens
            # it inside the team; then a member takes it for themselves.
            # A team with no lead lets any member accept for it, so a
            # two-person shop is not stuck. Someone outside the team may
            # take it only if it is public.
            membership = self._team_membership(scope, row.assigned_to_ref, member_id)
            if membership is None:
                if row.visibility == "private":
                    raise TicketConflict("this ticket is not open to you")
            elif row.accept_status != "accepted":
                leads = self._team_has_lead(scope, row.assigned_to_ref)
                if leads and not membership.is_lead:
                    raise TicketConflict("the team lead accepts a team job first")
                row.accept_status = "accepted"
                row.status = "assigned"
                self._s.flush()
                return row
            # Team-accepted (or public): the member takes it below.
        elif row.visibility == "private" and row.assigned_to_ref != member_id:
            raise TicketConflict("this ticket is not open to you")

        row.assigned_target_type = "technician"
        row.assigned_to_ref = member_id
        row.accept_status = "accepted"
        # Taking a job is not arriving at it. This used to jump straight
        # to in_progress, so the technician home offered "ปิดงาน" the
        # moment a job was claimed and check-in never happened (owner, 3
        # Sep). 13.4: check-in is what makes a ticket in_progress.
        row.status = "assigned"
        self._s.flush()
        return row

    def _team_membership(self, scope: TenantScope, team_id, member_id) -> TechnicianTeamMember | None:
        return self._s.execute(
            select(TechnicianTeamMember).where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.team_id == team_id,
                TechnicianTeamMember.member_id == member_id,
            )
        ).scalars().first()

    def _team_has_lead(self, scope: TenantScope, team_id) -> bool:
        return self._s.execute(
            select(TechnicianTeamMember.id).where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.team_id == team_id,
                TechnicianTeamMember.is_lead.is_(True),
            )
        ).first() is not None

    def reject(
        self, scope: TenantScope, ticket_id: uuid.UUID, *, member_id: uuid.UUID,
    ) -> ServiceTicket:
        """Decline an assignment.

        Does NOT reassign (12.4, explicitly). The person who dispatched it
        decides what happens next; automatically passing it to the next
        technician would hide the fact that the first one said no, which is
        usually information the dispatcher needs.
        """
        row = self._get_locked(scope, ticket_id)
        if row is None:
            raise TicketNotFound("ticket not found in this tenant")
        if row.status in ("completed", "cancelled", "in_progress"):
            # A finished job, or one the technician is standing in the
            # middle of, cannot be handed back with a word (review, 6 Sep
            # 2026: "ปฏิเสธงาน" after check-out reopened a closed ticket).
            raise TicketConflict(f"a {row.status} ticket cannot be declined")
        if row.assigned_to_ref != member_id:
            # A team lead may decline for the team (12.4: "ไม่รับ → แจ้ง
            # กลับผู้มอบหมาย" applies to the team the job was given to).
            is_lead_of_team = (
                row.assigned_target_type == "technician_team"
                and (m := self._team_membership(scope, row.assigned_to_ref, member_id)) is not None
                and (m.is_lead or not self._team_has_lead(scope, row.assigned_to_ref))
            )
            if not is_lead_of_team:
                raise TicketConflict("this ticket was not assigned to you")
        row.accept_status = "rejected"
        # Back to open so it shows up in the dispatcher's queue again — and
        # no longer theirs: with the assignee left in place the decliner
        # could still check in, and their home screen kept offering the
        # job they had just turned down. The audit row says who declined.
        row.status = "open"
        row.assigned_to_ref = None
        row.assigned_target_type = None
        self._s.flush()
        return row

    def set_status(
        self, scope: TenantScope, ticket_id: uuid.UUID, *, status: str,
    ) -> ServiceTicket:
        if status not in TICKET_STATUSES:
            raise TicketConflict(f"unknown ticket status: {status!r}")
        row = self.get(scope, ticket_id)
        if row is None:
            raise TicketNotFound("ticket not found in this tenant")
        # A finished ticket stays finished. Reopening one would rewrite
        # history that a satisfaction survey or an invoice may already
        # reference.
        if row.status in ("completed", "cancelled") and status != row.status:
            raise TicketConflict(f"a {row.status} ticket cannot change status")
        row.status = status
        self._s.flush()
        return row
