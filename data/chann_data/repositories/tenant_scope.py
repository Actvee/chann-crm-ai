"""Tenant-scoped repository access.

Cross-cutting principle 4: multi-tenant isolation is strict, and every
cross-license read must be audited.

The design decision here is that `license_id` filtering happens in the
repository, not in the router. Routers get rewritten often; a filter that
lives in a router is one careless refactor away from leaking another
tenant's data. Putting it here means a caller cannot construct a tenant
query without supplying a scope.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from sqlalchemy import Sequence, select
from sqlalchemy.orm import Session

from ..models import ChannIdentity, License, LicenseMember, PlatformAdmin


class CrossTenantAccessDenied(PermissionError):
    """Raised when a scoped caller reaches for another tenant's row."""


@dataclass(frozen=True)
class TenantScope:
    """Proof that the caller is entitled to exactly one license."""

    license_id: uuid.UUID

    def assert_owns(self, license_id: uuid.UUID) -> None:
        if license_id != self.license_id:
            raise CrossTenantAccessDenied(
                f"scope={self.license_id} attempted access to license={license_id}"
            )


class LicenseRepository:
    def __init__(self, session: Session):
        self._s = session

    def get_scoped(self, scope: TenantScope) -> License | None:
        return self._s.execute(
            select(License).where(License.id == scope.license_id)
        ).scalar_one_or_none()

    def get_by_code(self, license_code: str) -> License | None:
        """Unscoped by necessity: onboarding looks a tenant up before any
        membership exists. Returns identity of the tenant only."""
        return self._s.execute(
            select(License).where(License.license_code == license_code)
        ).scalar_one_or_none()


class MemberNotFound(LookupError):
    """No such membership row in this tenant (for the given channel)."""


class MemberConflict(Exception):
    """A membership change the rules refuse — chiefly touching the owner."""


class MemberRepository:
    def __init__(self, session: Session):
        self._s = session

    def list_for_license(self, scope: TenantScope) -> list[LicenseMember]:
        return list(
            self._s.execute(
                select(LicenseMember)
                .where(LicenseMember.license_id == scope.license_id)
                .order_by(LicenseMember.joined_at, LicenseMember.channel)
            ).scalars()
        )

    def get(
        self, scope: TenantScope, chann_uid: str, *, channel: str | None = None,
    ) -> LicenseMember | None:
        """One membership row. `channel` names which OA's row is wanted
        ("sales" | "technician"); a caller that does not say gets the
        sales row when there is one, else the technician row — the
        explicit form is the right one wherever the caller knows which OA
        it is acting for, because the two rows have different ids and
        different roles."""
        query = select(LicenseMember).where(
            LicenseMember.license_id == scope.license_id,
            LicenseMember.chann_uid == chann_uid,
        )
        if channel is not None:
            return self._s.execute(query.where(LicenseMember.channel == channel)).scalar_one_or_none()
        # "sales" sorts before "technician"; among two rows an active one
        # first, so a removed sales row does not hide a live technician.
        return self._s.execute(
            query.order_by(
                (LicenseMember.status != "active"), LicenseMember.channel,
            ).limit(1)
        ).scalar_one_or_none()

    def memberships_of(
        self, chann_uid: str, *, oa: str | None = None
    ) -> list[LicenseMember]:
        """Deliberately unscoped by default — used at webhook time to decide
        which tenant a message belongs to, before any scope exists.

        The result must NOT be returned to a tenant caller: exposing it would
        reveal which other companies a person works with. Callers inside the
        Application Tier use it only to select a scope.

        `oa` is the official account the message arrived on, and it
        selects the rows for THAT channel only (owner, 8 Sep 2026): LINE
        gives one physical account the same userId across every OA under
        a provider, and the owner's rule is that the OAs are separate
        registrations that share nothing but the person. An owner or CS
        who adds the Technician OA is not a technician there until they
        redeem a technician invite; a technician is not Sales staff. No
        inference from the role or its permissions — the earlier
        "anyone with ticket.read" reading is exactly what told the owner
        "already linked" on an OA they had never registered on.

        "sales" → channel "sales"; "technician" → channel "technician";
        omitted → every active row (identity-level callers only).
        """
        query = select(LicenseMember).where(
            LicenseMember.chann_uid == chann_uid,
            LicenseMember.status == "active",
        )
        if oa is not None:
            query = query.where(LicenseMember.channel == oa)
        return list(self._s.execute(query.order_by(LicenseMember.joined_at)).scalars())

    def is_owner_row(self, scope: TenantScope, member: LicenseMember) -> bool:
        from ..models import CustomRole

        role = self._s.execute(
            select(CustomRole).where(
                CustomRole.license_id == scope.license_id,
                CustomRole.role_name == member.role,
            )
        ).scalars().first()
        return bool(role is not None and role.is_owner)

    def set_status(
        self, scope: TenantScope, chann_uid: str, *, channel: str, status: str,
    ) -> tuple[LicenseMember, list]:
        """Remove (status "removed") or reactivate ("active") one channel's
        row. The row stays for audit — a removed member's tickets and
        reports still name them. The owner's row is never removed.

        Removing a technician row also takes them off every team and
        hands their open jobs back to the queue; the tickets returned are
        the ones unassigned, so the caller can tell the dispatchers.
        """
        if status not in ("active", "removed"):
            raise ValueError("status must be 'active' or 'removed'")
        member = self.get(scope, chann_uid, channel=channel)
        if member is None:
            raise MemberNotFound("member not found on this channel")
        if status == "removed" and self.is_owner_row(scope, member):
            raise MemberConflict("the owner cannot be removed or demoted")
        unassigned: list = []
        if status == "removed" and member.status != "removed" and channel == "technician":
            unassigned = self._release_field_work(scope, member)
        member.status = status
        self._s.flush()
        return member, unassigned

    def _release_field_work(self, scope: TenantScope, member: LicenseMember) -> list:
        from sqlalchemy import delete

        from ..models import ServiceTicket, TechnicianTeamMember

        self._s.execute(
            delete(TechnicianTeamMember).where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.member_id == member.id,
            )
        )
        tickets = list(
            self._s.execute(
                select(ServiceTicket).where(
                    ServiceTicket.license_id == scope.license_id,
                    ServiceTicket.assigned_target_type == "technician",
                    ServiceTicket.assigned_to_ref == member.id,
                    ServiceTicket.status.notin_(("completed", "cancelled")),
                )
            ).scalars()
        )
        for ticket in tickets:
            ticket.assigned_target_type = None
            ticket.assigned_to_ref = None
            ticket.accept_status = "pending"
            if ticket.status in ("assigned", "in_progress"):
                ticket.status = "open"
        self._s.flush()
        return tickets


class IdentityRepository:
    """Chann Identity is global by design (ADR-011). It is not tenant data,
    so it is not tenant-scoped — but see memberships_of above for the part
    that must never leak."""

    def __init__(self, session: Session):
        self._s = session

    def get_by_line_user_id(self, line_user_id: str) -> ChannIdentity | None:
        return self._s.execute(
            select(ChannIdentity).where(ChannIdentity.line_user_id == line_user_id)
        ).scalar_one_or_none()

    def get(self, chann_uid: str) -> ChannIdentity | None:
        return self._s.get(ChannIdentity, chann_uid)

    def create(self, chann_uid: str, line_user_id: str, primary_role: str,
               display_name: str | None = None) -> ChannIdentity:
        identity = ChannIdentity(
            chann_uid=chann_uid,
            line_user_id=line_user_id,
            primary_role=primary_role,
            display_name=display_name,
        )
        self._s.add(identity)
        self._s.flush()
        return identity

    def next_chann_uid(self, primary_role: str) -> str:
        """CHN-C-000123 / CHN-S-000045 / CHN-T-000012.

        A PostgreSQL sequence is required from the first phase. Counting rows
        allows two concurrent first-contact webhooks to allocate the same ID.
        """
        prefix = {"customer": "C", "sales": "S", "technician": "T"}[primary_role]
        sequence = Sequence(f"chann_identity_{prefix.lower()}_seq")
        number = self._s.execute(select(sequence.next_value())).scalar_one()
        return f"CHN-{prefix}-{number:06d}"


class PlatformAdminLocked(Exception):
    """The account is locked out (five wrong passwords). Carries WHEN it
    opens again, so the login page can say "locked until 14:35" instead
    of "wrong password" — which had the operator retrying against a lock
    they could not see (review, 6 Sep 2026)."""

    def __init__(self, locked_until):
        super().__init__(f"account locked until {locked_until.isoformat()}")
        self.locked_until = locked_until


class PlatformAdminRepository:
    def __init__(self, session: Session):
        self._s = session
        self._hasher = PasswordHasher()

    MAX_FAILED = 5
    LOCK_MINUTES = 15

    def authenticate(self, username: str, password: str) -> PlatformAdmin | None:
        """None on a wrong password; PlatformAdminLocked while locked out —
        five failures in a row lock the account for fifteen minutes, and
        the failure that trips the lock reports it too. The caller commits,
        so the counter survives a refused login."""
        from datetime import datetime, timedelta, timezone

        admin = self._s.execute(
            select(PlatformAdmin).where(PlatformAdmin.username == username).with_for_update()
        ).scalar_one_or_none()
        if admin is None:
            return None
        now = datetime.now(timezone.utc)
        if admin.locked_until is not None and admin.locked_until > now:
            raise PlatformAdminLocked(admin.locked_until)
        try:
            ok = self._hasher.verify(admin.password_hash, password)
        except Exception:
            ok = False
        if not ok:
            admin.failed_attempts = int(admin.failed_attempts or 0) + 1
            if admin.failed_attempts >= self.MAX_FAILED:
                admin.locked_until = now + timedelta(minutes=self.LOCK_MINUTES)
                admin.failed_attempts = 0
                self._s.flush()
                raise PlatformAdminLocked(admin.locked_until)
            self._s.flush()
            return None
        admin.failed_attempts = 0
        admin.locked_until = None
        self._s.flush()
        return admin
