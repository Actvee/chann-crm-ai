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

    def memberships_of(self, chann_uid: str) -> list[LicenseMember]:
        """Deliberately unscoped — used at webhook time to decide which tenant
        a message belongs to, before any scope exists.

        The result must NOT be returned to a tenant caller: exposing it would
        reveal which other companies a person works with. Callers inside the
        Application Tier use it only to select a scope.
        """
        return list(
            self._s.execute(
                select(LicenseMember).where(
                    LicenseMember.chann_uid == chann_uid,
                    LicenseMember.status == "active",
                )
            ).scalars()
        )


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

    def authenticate(self, username: str, password: str) -> PlatformAdmin | None:
        admin = self._s.execute(
            select(PlatformAdmin).where(PlatformAdmin.username == username)
        ).scalar_one_or_none()
        if admin is None:
            return None
        try:
            if not self._hasher.verify(admin.password_hash, password):
                return None
        except Exception:
            return None
        return admin
