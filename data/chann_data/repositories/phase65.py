"""Tenant registration — Phase 6.5.

Three mechanisms that must not be confused with each other:

  license creation  -> you become an owner
  invite code       -> you become a member
  company code      -> an end customer records which shop they deal with,
                       and gains NO permissions at all

The third is the one worth guarding: a customer who knows a shop code must
never end up with a `license_members` row, or every customer inherits the
tenant's permissions.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    ChannIdentity,
    CustomerLicenseLink,
    License,
    LicenseInvite,
    LicenseMember,
)
from ..permissions import DEFAULT_ROLE_TEMPLATES

# No 0/O/1/I/L: these get typed into a chat by hand and read aloud on the phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
COMPANY_CODE_LEN = 8
INVITE_CODE_LEN = 10
TRIAL_DAYS = 30
# seed_reference.py keys the owner role off this exact name, and Phase 2 keys
# `is_owner` off it too. Defined once here rather than repeated as a literal.
OWNER_ROLE_NAME = "owner"
LICENSE_STATUSES = frozenset({"trial", "active", "suspended"})


class RegistrationConflict(RuntimeError):
    """Well-formed but not allowed in the current state."""


class RegistrationNotFound(LookupError):
    pass


def _code(length: int) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


class RegistrationRepository:
    def __init__(self, session: Session):
        self._s = session

    # ---------------------------------------------------------------- create

    def create_license(
        self,
        *,
        company_name: str,
        created_by_chann_uid: str,
        display_name: str | None = None,
        trial_days: int = TRIAL_DAYS,
    ) -> License:
        """Self-service tenant creation. One per LINE identity.

        The limit is checked here AND enforced by a partial unique index on
        `created_by_chann_uid`. The check gives a clean error message; the
        index is what actually holds when two webhook deliveries race, which
        LINE makes entirely possible by redelivering.
        """
        company_name = (company_name or "").strip()
        if not company_name:
            raise RegistrationConflict("company_name is required")

        existing = self._s.execute(
            select(License).where(License.created_by_chann_uid == created_by_chann_uid)
        ).scalars().first()
        if existing is not None:
            raise RegistrationConflict(
                "this account already created a company"
            )

        license_row = License(
            id=uuid.uuid4(),
            license_code=self._unique_license_code(),
            company_name=company_name,
            company_code=self._unique_company_code(),
            status="trial",
            trial_expires_at=datetime.now(timezone.utc) + timedelta(days=trial_days),
            created_by_chann_uid=created_by_chann_uid,
        )
        self._s.add(license_row)

        try:
            self._s.flush()
        except IntegrityError as exc:
            self._s.rollback()
            # The index fired: a concurrent request won the race.
            raise RegistrationConflict(
                "this account already created a company"
            ) from exc

        self._seed_role_templates(license_row.id)

        self._s.add(
            LicenseMember(
                id=uuid.uuid4(),
                license_id=license_row.id,
                chann_uid=created_by_chann_uid,
                role=OWNER_ROLE_NAME,
                status="active",
            )
        )
        self._set_display_name(created_by_chann_uid, display_name)
        self._s.flush()
        return license_row

    def _set_display_name(self, chann_uid: str, display_name: str | None) -> None:
        """display_name lives on chann_identities, not license_members.

        Only filled when currently empty: the identity may already carry a
        name the person set deliberately, and a LINE profile name picked up
        during registration should not overwrite it.
        """
        if not display_name:
            return
        identity = self._s.get(ChannIdentity, chann_uid)
        if identity is not None and not identity.display_name:
            identity.display_name = display_name

    def _unique_company_code(self) -> str:
        for _ in range(50):
            candidate = _code(COMPANY_CODE_LEN)
            clash = self._s.execute(
                select(License.id).where(License.company_code == candidate)
            ).first()
            if clash is None:
                return candidate
        raise RegistrationConflict("could not allocate a unique company code")

    def _unique_license_code(self) -> str:
        for _ in range(50):
            candidate = "CO" + _code(6)
            clash = self._s.execute(
                select(License.id).where(License.license_code == candidate)
            ).first()
            if clash is None:
                return candidate
        raise RegistrationConflict("could not allocate a unique license code")

    def _seed_role_templates(self, license_id: uuid.UUID) -> None:
        """Same templates seed_reference.py writes, for a brand-new tenant.

        Deliberately mirrors that script exactly — including `allowed=True` and
        the `permission_keys is None` case for owner (whose permissions are
        implicit via is_owner). A self-registered tenant that got a different
        role set than a seeded one would be a lasting, hard-to-spot
        inconsistency between how a tenant came into existence.
        """
        from ..models import CustomRole, RolePermission

        for role_name, permission_keys in DEFAULT_ROLE_TEMPLATES.items():
            self._s.add(
                CustomRole(
                    id=uuid.uuid4(),
                    license_id=license_id,
                    role_name=role_name,
                    is_owner=role_name == OWNER_ROLE_NAME,
                )
            )
            self._s.flush()
            if permission_keys is None:
                continue
            self._s.add_all(
                RolePermission(
                    id=uuid.uuid4(),
                    license_id=license_id,
                    role=role_name,
                    permission_key=key,
                    allowed=True,
                )
                for key in sorted(permission_keys)
            )
        self._s.flush()

    # ---------------------------------------------------------------- invites

    def create_invite(
        self,
        license_id: uuid.UUID,
        *,
        role: str,
        max_uses: int = 1,
        expires_in_days: int | None = 7,
        created_by_member_id: uuid.UUID | None = None,
    ) -> LicenseInvite:
        if max_uses < 1:
            raise RegistrationConflict("max_uses must be at least 1")

        from ..models import CustomRole, RolePermission
        from ..permissions import DEFAULT_ROLE_TEMPLATES

        known = self._s.execute(
            select(CustomRole).where(
                CustomRole.license_id == license_id, CustomRole.role_name == role
            )
        ).scalars().first()
        if known is None and role in DEFAULT_ROLE_TEMPLATES:
            # "technician" (and any other spec-defined default role) is a
            # universal persona, not a tenant-customisable one — a tenant
            # should never have to visit a role editor before its first
            # technician can be invited. Self-heals tenants created before
            # this role template existed, with no migration required: the
            # same permission set _seed_role_templates would have written
            # at creation time, written now instead.
            known = CustomRole(
                id=uuid.uuid4(), license_id=license_id, role_name=role,
                is_owner=False,
            )
            self._s.add(known)
            self._s.flush()
            permission_keys = DEFAULT_ROLE_TEMPLATES[role]
            if permission_keys is not None:
                self._s.add_all(
                    RolePermission(
                        id=uuid.uuid4(), license_id=license_id, role=role,
                        permission_key=key, allowed=True,
                    )
                    for key in sorted(permission_keys)
                )
                self._s.flush()
        if known is None:
            raise RegistrationConflict(f"role '{role}' does not exist in this tenant")
        # An invite that hands out ownership would bypass the two-party
        # transfer flow Phase 2 built specifically to prevent that.
        if known.is_owner:
            raise RegistrationConflict("cannot invite directly into the owner role")

        invite = LicenseInvite(
            id=uuid.uuid4(),
            license_id=license_id,
            invite_code=self._unique_invite_code(),
            role=role,
            max_uses=max_uses,
            used_count=0,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(days=expires_in_days)
                if expires_in_days
                else None
            ),
            created_by_member_id=created_by_member_id,
        )
        self._s.add(invite)
        self._s.flush()
        return invite

    def _unique_invite_code(self) -> str:
        for _ in range(50):
            candidate = _code(INVITE_CODE_LEN)
            clash = self._s.execute(
                select(LicenseInvite.id).where(LicenseInvite.invite_code == candidate)
            ).first()
            if clash is None:
                return candidate
        raise RegistrationConflict("could not allocate a unique invite code")

    def revoke_invite(self, license_id: uuid.UUID, invite_id: uuid.UUID) -> LicenseInvite:
        invite = self._s.execute(
            select(LicenseInvite).where(
                LicenseInvite.id == invite_id, LicenseInvite.license_id == license_id
            )
        ).scalars().first()
        if invite is None:
            raise RegistrationNotFound("invite not found")
        if invite.revoked_at is None:
            invite.revoked_at = datetime.now(timezone.utc)
            self._s.flush()
        return invite

    def list_invites(self, license_id: uuid.UUID) -> list[LicenseInvite]:
        return list(
            self._s.execute(
                select(LicenseInvite)
                .where(LicenseInvite.license_id == license_id)
                .order_by(LicenseInvite.created_at.desc())
            ).scalars()
        )

    def redeem_invite(
        self, *, invite_code: str, chann_uid: str, display_name: str | None = None
    ) -> LicenseMember:
        """Join a tenant. Idempotent for someone who is already a member."""
        invite = self._s.execute(
            select(LicenseInvite)
            .where(LicenseInvite.invite_code == (invite_code or "").strip().upper())
            .with_for_update()
        ).scalars().first()
        if invite is None:
            raise RegistrationNotFound("invite code not found")

        if invite.revoked_at is not None:
            raise RegistrationConflict("invite code has been revoked")
        if invite.expires_at is not None and invite.expires_at <= datetime.now(timezone.utc):
            raise RegistrationConflict("invite code has expired")

        existing = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.license_id == invite.license_id,
                LicenseMember.chann_uid == chann_uid,
            )
        ).scalars().first()
        if existing is not None:
            # Already a member: return them unchanged and do NOT burn a use.
            # Otherwise re-tapping the same link would silently exhaust a
            # multi-use invite meant for other people.
            return existing

        if invite.used_count >= invite.max_uses:
            raise RegistrationConflict("invite code has no uses left")

        member = LicenseMember(
            id=uuid.uuid4(),
            license_id=invite.license_id,
            chann_uid=chann_uid,
            role=invite.role,
            status="active",
        )
        self._s.add(member)
        self._set_display_name(chann_uid, display_name)
        invite.used_count += 1
        self._s.flush()
        return member

    # ------------------------------------------------------- customer links

    def find_shops(self, query: str, *, limit: int = 10) -> list[License]:
        """Public shop search — the one deliberately un-tenant-scoped read.

        A customer who has not linked any shop has no tenant to be scoped to,
        so this cannot use TenantScope. Callers MUST project only
        company_name + company_code out of the result; the ORM objects
        returned here carry more than a stranger should see.
        """
        q = (query or "").strip()
        if len(q) < 2:
            # Two characters would match most of the table and turn this into
            # an enumeration endpoint.
            return []
        limit = max(1, min(limit, 25))
        pattern = f"%{q}%"
        return list(
            self._s.execute(
                select(License)
                .where(
                    License.status != "suspended",
                    or_(
                        License.company_name.ilike(pattern),
                        License.company_code == q.upper(),
                    ),
                )
                .order_by(License.company_name.asc())
                .limit(limit)
            ).scalars()
        )

    def link_customer(self, *, chann_uid: str, company_code: str) -> CustomerLicenseLink:
        """Bind a customer to a shop. Idempotent. Grants no permissions."""
        code = (company_code or "").strip().upper()
        license_row = self._s.execute(
            select(License).where(License.company_code == code)
        ).scalars().first()
        if license_row is None:
            raise RegistrationNotFound("company code not found")
        if license_row.status == "suspended":
            raise RegistrationConflict("this company is not accepting customers")

        existing = self._s.execute(
            select(CustomerLicenseLink).where(
                CustomerLicenseLink.chann_uid == chann_uid,
                CustomerLicenseLink.license_id == license_row.id,
            )
        ).scalars().first()
        if existing is not None:
            return existing

        link = CustomerLicenseLink(
            id=uuid.uuid4(), chann_uid=chann_uid, license_id=license_row.id
        )
        self._s.add(link)
        self._s.flush()
        return link

    def my_shops(self, chann_uid: str) -> list[License]:
        return list(
            self._s.execute(
                select(License)
                .join(CustomerLicenseLink, CustomerLicenseLink.license_id == License.id)
                .where(CustomerLicenseLink.chann_uid == chann_uid)
                .order_by(License.company_name.asc())
            ).scalars()
        )

    # ---------------------------------------------------------------- status

    def set_status(self, license_id: uuid.UUID, status: str) -> License:
        if status not in LICENSE_STATUSES:
            raise RegistrationConflict(f"unknown license status '{status}'")
        row = self._s.get(License, license_id)
        if row is None:
            raise RegistrationNotFound("license not found")
        row.status = status
        self._s.flush()
        return row

    def expire_due_trials(self, *, now: datetime | None = None) -> list[License]:
        """Suspend trials past their date. Suspended means read-only, not deleted.

        `now` is injectable so the sweep is testable without freezing the clock.
        """
        moment = now or datetime.now(timezone.utc)
        due = list(
            self._s.execute(
                select(License)
                .where(
                    License.status == "trial",
                    License.trial_expires_at.is_not(None),
                    License.trial_expires_at <= moment,
                )
                .with_for_update()
            ).scalars()
        )
        for row in due:
            row.status = "suspended"
        if due:
            self._s.flush()
        return due
