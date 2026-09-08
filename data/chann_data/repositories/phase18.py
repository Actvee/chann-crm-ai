"""Phase 18 — what the platform's own operator sees: every tenant with
its size, one tenant in detail, and the audit trail that crossed tenant
lines. Read-only walks; the writes (status, break-glass) already exist.

Deliberately unscoped: the caller is the platform admin, not a tenant.
These live on the internal API and are only reachable through the
Application tier's require_admin routes.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .phase65 import LICENSE_STATUSES

from ..models import (
    ChannIdentity, Customer, CustomRole, Deal, License, LicenseMember, ServiceTicket,
)

OPEN_TICKET_STATUSES = ("open", "assigned", "in_progress")


class PlatformNotFound(Exception):
    pass


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
            "trial_expires_at": row.trial_expires_at, "created_at": row.created_at,
            "owner_chann_uid": owner_uid, "owner_name": owner_name,
            **self._counts(row.id),
        }

    # ------------------------------------------------------------ reads

    def tenants(self, *, q: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        query = select(License)
        if status:
            query = query.where(License.status == status)
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
            "tax_id": row.tax_id, "members_detail": members,
        }

    # ------------------------------------------------------------ writes

    EDITABLE = (
        "status", "trial_expires_at", "company_name", "legal_name",
        "company_phone", "company_email", "company_address", "tax_id",
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
        self._s.flush()
        return before, row
