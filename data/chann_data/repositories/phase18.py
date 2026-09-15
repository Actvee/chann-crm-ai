"""Phase 18 — what the platform's own operator sees: every tenant with
its size, one tenant in detail, and the audit trail that crossed tenant
lines. Round 18 added the operator's writes: edit, extend the
subscription, soft-delete / purge a company, and manage members
(role, remove/reactivate, move to another company).

Deliberately unscoped: the caller is the platform admin, not a tenant.
These live on the internal API and are only reachable through the
Application tier's require_admin routes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .phase2 import MemberRoleRepository, Phase2Conflict, Phase2NotFound, RoleRepository
from .phase65 import LICENSE_STATUSES
from .tenant_scope import MemberConflict, MemberNotFound, MemberRepository, TenantScope

from ..models import (
    AuditLog, Base, ChannIdentity, Customer, CustomRole, Deal, License, LicenseMember, ServiceTicket,
)

OPEN_TICKET_STATUSES = ("open", "assigned", "in_progress")


class PlatformNotFound(Exception):
    pass


class PlatformConflict(Exception):
    """Well-formed but refused in the current state (an owner row, a
    duplicate membership, a role that belongs to the other OA)."""


MAX_EXTEND_DAYS = 3650


class PlatformRepository:
    def __init__(self, session: Session):
        self._s = session

    # ------------------------------------------------------------ counts

    def _count(self, model, *where) -> int:
        return int(self._s.execute(select(func.count()).select_from(model).where(*where)).scalar_one() or 0)

    def _counts(self, license_id: uuid.UUID) -> dict:
        last_ticket = self._s.execute(
            select(func.max(ServiceTicket.created_at)).where(ServiceTicket.license_id == license_id)
        ).scalar_one()
        last_deal = self._s.execute(
            select(func.max(Deal.created_at)).where(Deal.license_id == license_id)
        ).scalar_one()
        candidates = [t for t in (last_ticket, last_deal) if isinstance(t, datetime)]
        return {
            "members": self._count(LicenseMember, LicenseMember.license_id == license_id, LicenseMember.status == "active"),
            "customers": self._count(Customer, Customer.license_id == license_id),
            "tickets": self._count(ServiceTicket, ServiceTicket.license_id == license_id),
            "open_tickets": self._count(
                ServiceTicket, ServiceTicket.license_id == license_id, ServiceTicket.status.in_(OPEN_TICKET_STATUSES),
            ),
            "deals": self._count(Deal, Deal.license_id == license_id),
            "last_activity_at": max(candidates) if candidates else None,
        }

    def _owner(self, license_id: uuid.UUID) -> tuple[str | None, str | None]:
        role = self._s.execute(
            select(CustomRole).where(CustomRole.license_id == license_id, CustomRole.is_owner.is_(True))
        ).scalars().first()
        if role is None:
            return None, None
        member = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.license_id == license_id, LicenseMember.role == role.role_name,
                LicenseMember.status == "active",
            ).order_by(LicenseMember.created_at)
        ).scalars().first()
        if member is None:
            return None, None
        identity = self._s.get(ChannIdentity, member.chann_uid)
        return member.chann_uid, (identity.display_name if identity is not None else None)

    def _summary(self, row: License) -> dict:
        owner_uid, owner_name = self._owner(row.id)
        return {
            "id": row.id, "license_code": row.license_code, "company_name": row.company_name,
            "company_code": row.company_code, "status": row.status,
            "expires_at": row.expires_at, "deleted_at": row.deleted_at, "created_at": row.created_at,
            "owner_chann_uid": owner_uid, "owner_name": owner_name,
            **self._counts(row.id),
        }

    # ------------------------------------------------------------ reads

    def tenants(self, *, q: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        """Every tenant but the soft-deleted ones; ask for status="deleted"
        to see those (round 18)."""
        query = select(License)
        if status:
            query = query.where(License.status == status)
        else:
            query = query.where(License.status != "deleted")
        if q:
            needle = f"%{q.strip()}%"
            query = query.where(
                License.company_name.ilike(needle)
                | License.license_code.ilike(needle)
                | License.company_code.ilike(needle)
            )
        query = query.order_by(License.created_at.desc()).limit(max(1, min(limit, 500)))
        return [self._summary(row) for row in self._s.execute(query).scalars()]

    def tenant(self, license_id: uuid.UUID) -> dict:
        row = self._s.get(License, license_id)
        if row is None:
            raise PlatformNotFound("license not found")
        members = []
        for member in self._s.execute(
            select(LicenseMember).where(LicenseMember.license_id == license_id).order_by(LicenseMember.created_at)
        ).scalars():
            identity = self._s.get(ChannIdentity, member.chann_uid)
            members.append({
                "chann_uid": member.chann_uid, "role": member.role, "status": member.status,
                "channel": member.channel,
                "display_name": identity.display_name if identity is not None else None,
                "joined_at": member.created_at,
            })
        return {
            **self._summary(row),
            "legal_name": row.legal_name, "company_phone": row.company_phone,
            "company_email": row.company_email, "company_address": row.company_address,
            # "members_detail", not "members": the summary already carries the
            # member COUNT under "members", and this list used to overwrite
            # it — the route then popped the list and TenantSummaryOut failed
            # validation, so the admin console's tenant page was a 500 for
            # every tenant (owner, 7 Sep 2026: "กดเข้าไปข้อมูลแต่ละบริษัทไม่ได้").
            "tax_id": row.tax_id, "admin_notes": row.admin_notes, "members_detail": members,
        }

    # ------------------------------------------------------------ writes

    EDITABLE = (
        "status", "expires_at", "company_name", "legal_name",
        "company_phone", "company_email", "company_address", "tax_id",
        # Round 18: the operator's own notes, never shown to the tenant.
        "admin_notes",
    )

    def update(self, license_id: uuid.UUID, changes: dict) -> tuple[dict, License]:
        """Apply an operator's edits and hand back what the fields were, so
        the caller can write the audit diff. Only EDITABLE columns move; a
        status outside the known set is a conflict, not a silent write.
        The admin console had no way to change a trial's deadline or fix a
        shop's details (owner, 7 Sep 2026)."""
        row = self._s.get(License, license_id)
        if row is None:
            raise PlatformNotFound("license not found")
        unknown = set(changes) - set(self.EDITABLE)
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        if "status" in changes and changes["status"] not in LICENSE_STATUSES:
            raise ValueError(f"unknown license status '{changes['status']}'")
        if "company_name" in changes and not str(changes["company_name"] or "").strip():
            raise ValueError("company_name is required")
        before = {k: getattr(row, k) for k in changes}
        for key, value in changes.items():
            setattr(row, key, value.strip() if isinstance(value, str) else value)
        if "status" in changes:
            # Reversing a soft delete through the ordinary PATCH clears the
            # stamp; deleting through it stamps now (round 18).
            row.deleted_at = datetime.now(timezone.utc) if row.status == "deleted" else None
        self._s.flush()
        return before, row

    # ------------------------------------------------- subscription (round 18)

    def extend(self, license_id: uuid.UUID, days: int, *, now: datetime | None = None) -> tuple[dict, License]:
        """Push the expiry out by `days` from max(now, current expiry). The
        status stays — except a suspended tenant, which a renewal reopens;
        a deleted one must be restored first. Returns the before-values
        for the audit diff."""
        if not isinstance(days, int) or days < 1 or days > MAX_EXTEND_DAYS:
            raise ValueError(f"days must be 1..{MAX_EXTEND_DAYS}")
        row = self._s.get(License, license_id)
        if row is None:
            raise PlatformNotFound("license not found")
        if row.status == "deleted":
            raise PlatformConflict("a deleted company cannot be extended; restore it first")
        moment = now or datetime.now(timezone.utc)
        before = {"expires_at": row.expires_at, "status": row.status}
        base = row.expires_at if row.expires_at is not None and row.expires_at > moment else moment
        row.expires_at = base + timedelta(days=days)
        if row.status == "suspended":
            row.status = "active"
        self._s.flush()
        return before, row

    # ----------------------------------------------------- delete (round 18)

    def soft_delete(self, license_id: uuid.UUID) -> tuple[dict, License, list[LicenseMember]]:
        """status → "deleted", deleted_at → now, every member row →
        "removed" (the rows stay: restoring the company means the operator
        reactivates whom they want). Idempotent for an already-deleted
        company. Returns the before-values and the members it removed."""
        row = self._s.get(License, license_id)
        if row is None:
            raise PlatformNotFound("license not found")
        before = {"status": row.status, "deleted_at": row.deleted_at}
        row.status = "deleted"
        row.deleted_at = row.deleted_at or datetime.now(timezone.utc)
        removed: list[LicenseMember] = []
        for member in self._s.execute(
            select(LicenseMember).where(LicenseMember.license_id == license_id, LicenseMember.status == "active")
        ).scalars():
            member.status = "removed"
            removed.append(member)
        self._s.flush()
        return before, row, removed

    def purge(self, license_id: uuid.UUID) -> dict:
        """Hard delete: every row of every table that references the
        license, children before parents, then the license itself.

        The FK graph is RESTRICT almost everywhere (a tenant's data must
        never vanish by accident), so the database will not cascade this
        for us: the walk goes through the ORM metadata in reverse
        dependency order, so a table added later with a `license_id`
        column is covered without editing this method. Tables without a
        license column hang off a tenant table with CASCADE
        (deal_products, document_template_versions).

        The tenant's audit rows are DETACHED, not deleted (license_id →
        NULL): the trail of what happened in the company survives its
        purge, keyed by entity id, and the caller writes one more
        cross-tenant row naming what was purged. Returns per-table counts
        plus what the caller needs for that audit row."""
        row = self._s.get(License, license_id)
        if row is None:
            raise PlatformNotFound("license not found")
        identity = {
            "license_code": row.license_code, "company_name": row.company_name,
            "company_code": row.company_code, "status": row.status,
        }
        counts: dict[str, int] = {}
        detached = self._s.execute(
            update(AuditLog).where(AuditLog.license_id == license_id).values(license_id=None)
        ).rowcount
        detached_count = int(detached or 0)
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in ("licenses", "audit_log"):
                continue
            columns = [
                fk.parent for fk in table.foreign_keys
                if fk.column.table.name == "licenses" and fk.column.name == "id"
            ]
            for column in columns:
                result = self._s.execute(delete(table).where(column == license_id))
                if result.rowcount:
                    counts[table.name] = counts.get(table.name, 0) + int(result.rowcount)
        self._s.execute(delete(License).where(License.id == license_id))
        self._s.flush()
        return {**identity, "rows": counts, "audit_log_detached": detached_count}

    # ---------------------------------------------------- members (round 18)

    def set_member_role(self, license_id: uuid.UUID, chann_uid: str, role_name: str) -> tuple[dict, LicenseMember]:
        """The platform changes a member's role (owner only through
        break-glass — MemberRoleRepository refuses both directions). The
        row is found by chann_uid alone: the platform console does not
        know which OA the person registered on, and the role decides the
        channel anyway."""
        scope = TenantScope(license_id=license_id)
        member = MemberRepository(self._s).get(scope, chann_uid)
        if member is None:
            raise PlatformNotFound("member not found")
        before = {"role": member.role, "channel": member.channel}
        try:
            member = MemberRoleRepository(self._s).set_role(scope, chann_uid, role_name, channel=member.channel)
        except Phase2NotFound as exc:
            raise PlatformNotFound(str(exc)) from exc
        except Phase2Conflict as exc:
            raise PlatformConflict(str(exc)) from exc
        self._s.flush()
        return before, member

    def set_member_status(self, license_id: uuid.UUID, chann_uid: str, status: str) -> tuple[dict, LicenseMember, list]:
        """Remove or reactivate, with the tenant path's semantics (owner
        row → conflict; a removed technician's open jobs go back to the
        queue)."""
        scope = TenantScope(license_id=license_id)
        member = MemberRepository(self._s).get(scope, chann_uid)
        if member is None:
            raise PlatformNotFound("member not found")
        before = {"status": member.status, "channel": member.channel}
        try:
            member, unassigned = MemberRepository(self._s).set_status(
                scope, chann_uid, channel=member.channel, status=status,
            )
        except MemberNotFound as exc:
            raise PlatformNotFound(str(exc)) from exc
        except MemberConflict as exc:
            raise PlatformConflict(str(exc)) from exc
        return before, member, unassigned

    def move_member(
        self, license_id: uuid.UUID, chann_uid: str, *, target_license_id: uuid.UUID, role_name: str,
    ) -> dict:
        """Take one person out of this company and into another, on the
        same channel, with the given role — one transaction, so a failure
        on the target side leaves the source membership untouched.

        Refused: the owner's row; a role that does not exist at the
        target or is its owner role; a role for the other OA; someone
        already an active member of the target on that channel. A
        REMOVED row at the target is revived with the new role — that is
        a rejoin, not a duplicate."""
        from ..permissions import channel_for_role

        if target_license_id == license_id:
            raise PlatformConflict("target must be a different company")
        target = self._s.get(License, target_license_id)
        if target is None:
            raise PlatformNotFound("target company not found")
        if target.status == "deleted":
            raise PlatformConflict("target company is deleted")
        source_scope = TenantScope(license_id=license_id)
        target_scope = TenantScope(license_id=target_license_id)
        members = MemberRepository(self._s)
        source = members.get(source_scope, chann_uid)
        if source is None:
            raise PlatformNotFound("member not found")
        if members.is_owner_row(source_scope, source):
            raise PlatformConflict("the owner cannot be moved; transfer ownership first")
        role = RoleRepository(self._s).get(target_scope, role_name)
        if role is None:
            raise PlatformNotFound("role not found at the target company")
        if role.is_owner:
            raise PlatformConflict("owner changes require the transfer flow")
        if channel_for_role(role.role_name) != source.channel:
            raise PlatformConflict(f"role '{role.role_name}' belongs to the other OA")
        existing = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.license_id == target_license_id,
                LicenseMember.chann_uid == chann_uid,
                LicenseMember.channel == source.channel,
            )
        ).scalar_one_or_none()
        if existing is not None and existing.status == "active":
            raise PlatformConflict("already a member of the target company")

        source_before = source.status
        source, unassigned = members.set_status(
            source_scope, chann_uid, channel=source.channel, status="removed",
        )
        if existing is not None:
            existing.status = "active"
            existing.role = role.role_name
            added = existing
        else:
            added = LicenseMember(
                id=uuid.uuid4(), license_id=target_license_id, chann_uid=chann_uid,
                role=role.role_name, channel=source.channel, status="active",
            )
            self._s.add(added)
        self._s.flush()
        return {
            "source": source, "source_before": source_before, "unassigned": unassigned,
            "target": added, "target_company_name": target.company_name,
        }
