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


class MemberRepository:
    def __init__(self, session: Session):
        self._s = session

    def list_for_license(self, scope: TenantScope) -> list[LicenseMember]:
        return list(
            self._s.execute(
                select(LicenseMember).where(LicenseMember.license_id == scope.license_id)
            ).scalars()
        )

    def get(self, scope: TenantScope, chann_uid: str) -> LicenseMember | None:
        return self._s.execute(
            select(LicenseMember).where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.chann_uid == chann_uid,
            )
        ).scalar_one_or_none()

    def memberships_of(
        self, chann_uid: str, *, oa: str | None = None
    ) -> list[LicenseMember]:
        """Deliberately unscoped by default — used at webhook time to decide
        which tenant a message belongs to, before any scope exists.

        The result must NOT be returned to a tenant caller: exposing it would
        reveal which other companies a person works with. Callers inside the
        Application Tier use it only to select a scope.

        `oa` narrows by role when given, because holding ANY active
        membership at a company is not the same as being onboarded for that
        specific channel's persona. LINE gives one physical account the same
        userId across every OA under a provider (see cache.k_pending_intent
        for the fuller explanation), so a person who is Sales staff at
        Company X was, before this filter existed, treated as already
        "belonging" to Company X the instant they messaged the Technician
        OA too — despite never having been invited as a technician there.

        "sales" (or omitted): every role except "technician" — Master Spec
        section 6 lists Sales OA as Sales/CS/Admin/Owner, technician is a
        separate persona with its own onboarding.

        "technician": any role whose permissions include ticket.read, at
        the owner's direction. Requiring role == "technician" exactly was
        right about the risk — a salesperson should not silently become a
        technician — but wrong about who works: in a small shop the owner
        goes out on jobs, and the rule left them told they were "not
        linked to any company as a technician" at their own company.

        Capability, not job title, is also what the rest of the system
        already uses; OA_ALLOWED_PERMISSION_KEYS gates the channel's
        actions the same way. A role with no ticket.read still cannot get
        in, which is the protection that mattered.
        """
        query = select(LicenseMember).where(
            LicenseMember.chann_uid == chann_uid,
            LicenseMember.status == "active",
        )
        rows = list(self._s.execute(query).scalars())

        if oa == "technician":
            return [row for row in rows if self._can_do_field_work(row)]
        if oa is not None:
            return [row for row in rows if row.role != "technician"]
        return rows

    def _can_do_field_work(self, member: LicenseMember) -> bool:
        """Does this member's role let them see service tickets?

        Custom roles are read from the tenant's own definitions; the
        built-in ones fall back to the template. A tenant that removed
        ticket.read from a role has said that role does not do field work,
        and this must honour that rather than assuming from the name.
        """
        from ..models import RolePermission
        from ..permissions import DEFAULT_ROLE_TEMPLATES

        # A tenant's explicit grant wins. Overrides live per key, so the
        # question is whether THIS key is granted, not whether the role
        # has any overrides at all.
        override = self._s.execute(
            select(RolePermission).where(
                RolePermission.license_id == member.license_id,
                RolePermission.role == member.role,
                RolePermission.permission_key == "ticket.read",
            )
        ).scalars().first()
        if override is not None:
            return bool(override.allowed)

        if member.role not in DEFAULT_ROLE_TEMPLATES:
            # An unknown role name. Refuse rather than assume: a typo or a
            # role deleted after members were assigned to it must not open
            # a channel, and `.get()` returning None for a missing key
            # looks identical to the owner template's deliberate None.
            return False

        template = DEFAULT_ROLE_TEMPLATES[member.role]
        # Only the owner template is None, and it means everything.
        if template is None:
            return True
        return "ticket.read" in template


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


class PlatformAdminRepository:
    def __init__(self, session: Session):
        self._s = session
        self._hasher = PasswordHasher()

    MAX_FAILED = 5
    LOCK_MINUTES = 15

    def authenticate(self, username: str, password: str) -> PlatformAdmin | None:
        """None on a wrong password AND while locked out: five failures in
        a row lock the account for fifteen minutes. The caller commits, so
        the counter survives a refused login."""
        from datetime import datetime, timedelta, timezone

        admin = self._s.execute(
            select(PlatformAdmin).where(PlatformAdmin.username == username).with_for_update()
        ).scalar_one_or_none()
        if admin is None:
            return None
        now = datetime.now(timezone.utc)
        if admin.locked_until is not None and admin.locked_until > now:
            return None
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
            return None
        admin.failed_attempts = 0
        admin.locked_until = None
        self._s.flush()
        return admin
