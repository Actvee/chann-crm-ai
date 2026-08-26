"""Master data — Phase 7 (Master Spec 7.3-7.5).

Everything is tenant-scoped through TenantScope. The one thing worth reading
carefully is `upsert_product`: it is keyed on the *business* key
(license_id, product_id) rather than the surrogate id, because a CSV re-upload
must update rows rather than fail or duplicate them, and the uploader has no
idea what our UUIDs are.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Product,
    SalesGroup,
    SalesGroupMember,
    TechnicianTeam,
    TechnicianTeamMember,
)
from .tenant_scope import TenantScope

CSV_COLUMNS = ("product_id", "product_name", "sku", "category", "unit_price", "description")


class MasterDataConflict(RuntimeError):
    pass


class MasterDataNotFound(LookupError):
    pass


def parse_price(raw) -> Decimal | None:
    """Money as Decimal, never float.

    Accepts the shapes people actually paste from a spreadsheet — "25,000",
    "฿25000", " 25000.00 " — because rejecting a whole CSV row over a comma
    would send them back to hand entry, which is what the CSV is meant to
    replace.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("฿", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MasterDataConflict(f"invalid unit_price: {raw!r}") from exc
    if value < 0:
        raise MasterDataConflict("unit_price cannot be negative")
    return value


class ProductRepository:
    def __init__(self, session: Session):
        self._s = session

    def upsert(
        self,
        scope: TenantScope,
        *,
        product_id: str,
        product_name: str,
        sku: str | None = None,
        category: str | None = None,
        unit_price=None,
        description: str | None = None,
    ) -> Product:
        """Idempotent on the business key (7.5: duplicate product_id upserts).

        Un-archives on re-upload: someone re-adding a product they previously
        archived clearly wants it back, and leaving it archived while
        reporting success would be a silent lie.
        """
        product_id = (product_id or "").strip()
        product_name = (product_name or "").strip()
        if not product_id:
            raise MasterDataConflict("product_id is required")
        if not product_name:
            raise MasterDataConflict("product_name is required")

        row = self._s.execute(
            select(Product).where(
                Product.license_id == scope.license_id,
                Product.product_id == product_id,
            )
        ).scalars().first()

        price = parse_price(unit_price)

        if row is None:
            row = Product(
                id=uuid.uuid4(),
                license_id=scope.license_id,
                product_id=product_id,
                product_name=product_name,
                sku=sku,
                category=category,
                unit_price=price,
                description=description,
            )
            self._s.add(row)
        else:
            row.product_name = product_name
            row.sku = sku
            row.category = category
            row.unit_price = price
            row.description = description
            row.archived_at = None

        self._s.flush()
        return row

    def get(self, scope: TenantScope, product_id: str) -> Product | None:
        return self._s.execute(
            select(Product).where(
                Product.license_id == scope.license_id,
                Product.product_id == product_id,
            )
        ).scalars().first()

    def list(
        self,
        scope: TenantScope,
        *,
        category: str | None = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[Product]:
        limit = max(1, min(limit, 1000))
        stmt = select(Product).where(Product.license_id == scope.license_id)
        if not include_archived:
            stmt = stmt.where(Product.archived_at.is_(None))
        if category is not None:
            stmt = stmt.where(Product.category == category)
        return list(
            self._s.execute(stmt.order_by(Product.product_name.asc()).limit(limit)).scalars()
        )

    def archive(self, scope: TenantScope, product_id: str) -> Product:
        """7.5: delete must archive, never hard-delete.

        A product referenced by a past deal or quote must stay resolvable, or
        historical documents start rendering blanks.
        """
        row = self.get(scope, product_id)
        if row is None:
            raise MasterDataNotFound("product not found")
        if row.archived_at is None:
            row.archived_at = datetime.now(timezone.utc)
            self._s.flush()
        return row

    def upsert_csv(self, scope: TenantScope, content: str) -> dict:
        """Bulk upsert. Reports per-row errors instead of failing the file.

        A 200-row upload where row 137 has a typo should import 199 rows and
        say what was wrong with one — not reject everything and make the user
        hunt for it.
        """
        text = (content or "").lstrip("\ufeff")  # Excel writes a BOM
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise MasterDataConflict("CSV has no header row")

        headers = {(h or "").strip().lower() for h in reader.fieldnames}
        missing = {"product_id", "product_name"} - headers
        if missing:
            raise MasterDataConflict(
                f"CSV is missing required column(s): {', '.join(sorted(missing))}"
            )

        imported = 0
        errors: list[dict] = []
        # Header is line 1, so the first data row is line 2 — report the line
        # number the user sees in their spreadsheet.
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): v for k, v in raw.items()}
            try:
                self.upsert(
                    scope,
                    product_id=row.get("product_id", ""),
                    product_name=row.get("product_name", ""),
                    sku=(row.get("sku") or "").strip() or None,
                    category=(row.get("category") or "").strip() or None,
                    unit_price=row.get("unit_price"),
                    description=(row.get("description") or "").strip() or None,
                )
                imported += 1
            except MasterDataConflict as exc:
                errors.append({"line": line_no, "error": str(exc)})

        return {"imported": imported, "errors": errors}


class SalesGroupRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(self, scope: TenantScope, group_name: str) -> SalesGroup:
        name = (group_name or "").strip()
        if not name:
            raise MasterDataConflict("group_name is required")
        existing = self._s.execute(
            select(SalesGroup).where(
                SalesGroup.license_id == scope.license_id,
                SalesGroup.group_name == name,
            )
        ).scalars().first()
        if existing is not None:
            raise MasterDataConflict(f"group '{name}' already exists")
        row = SalesGroup(id=uuid.uuid4(), license_id=scope.license_id, group_name=name)
        self._s.add(row)
        self._s.flush()
        return row

    def list(self, scope: TenantScope) -> list[SalesGroup]:
        return list(
            self._s.execute(
                select(SalesGroup)
                .where(SalesGroup.license_id == scope.license_id)
                .order_by(SalesGroup.group_name.asc())
            ).scalars()
        )

    def delete(self, scope: TenantScope, group_id: uuid.UUID) -> None:
        """Deletes the group and its membership rows — never the people.

        The people are removed by the FK's ON DELETE CASCADE on group_id;
        member_id is RESTRICT, so a bug that tried to cascade into
        license_members would fail loudly instead of deleting staff.
        """
        row = self._s.execute(
            select(SalesGroup).where(
                SalesGroup.id == group_id, SalesGroup.license_id == scope.license_id
            )
        ).scalars().first()
        if row is None:
            raise MasterDataNotFound("group not found")
        self._s.delete(row)
        self._s.flush()

    def add_member(
        self, scope: TenantScope, group_id: uuid.UUID, member_id: uuid.UUID
    ) -> SalesGroupMember:
        """Idempotent. A salesperson may belong to several groups (7.5)."""
        group = self._s.execute(
            select(SalesGroup).where(
                SalesGroup.id == group_id, SalesGroup.license_id == scope.license_id
            )
        ).scalars().first()
        if group is None:
            raise MasterDataNotFound("group not found")

        existing = self._s.execute(
            select(SalesGroupMember).where(
                SalesGroupMember.group_id == group_id,
                SalesGroupMember.member_id == member_id,
            )
        ).scalars().first()
        if existing is not None:
            return existing

        row = SalesGroupMember(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            group_id=group_id,
            member_id=member_id,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def remove_member(
        self, scope: TenantScope, group_id: uuid.UUID, member_id: uuid.UUID
    ) -> None:
        row = self._s.execute(
            select(SalesGroupMember).where(
                SalesGroupMember.license_id == scope.license_id,
                SalesGroupMember.group_id == group_id,
                SalesGroupMember.member_id == member_id,
            )
        ).scalars().first()
        if row is None:
            raise MasterDataNotFound("membership not found")
        self._s.delete(row)
        self._s.flush()

    def members(self, scope: TenantScope, group_id: uuid.UUID) -> list[SalesGroupMember]:
        return list(
            self._s.execute(
                select(SalesGroupMember).where(
                    SalesGroupMember.license_id == scope.license_id,
                    SalesGroupMember.group_id == group_id,
                )
            ).scalars()
        )

    def groups_for_member(
        self, scope: TenantScope, member_id: uuid.UUID
    ) -> list[SalesGroup]:
        return list(
            self._s.execute(
                select(SalesGroup)
                .join(SalesGroupMember, SalesGroupMember.group_id == SalesGroup.id)
                .where(
                    SalesGroup.license_id == scope.license_id,
                    SalesGroupMember.member_id == member_id,
                )
                .order_by(SalesGroup.group_name.asc())
            ).scalars()
        )


class TechnicianTeamRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(self, scope: TenantScope, team_name: str) -> TechnicianTeam:
        name = (team_name or "").strip()
        if not name:
            raise MasterDataConflict("team_name is required")
        existing = self._s.execute(
            select(TechnicianTeam).where(
                TechnicianTeam.license_id == scope.license_id,
                TechnicianTeam.team_name == name,
            )
        ).scalars().first()
        if existing is not None:
            raise MasterDataConflict(f"team '{name}' already exists")
        row = TechnicianTeam(id=uuid.uuid4(), license_id=scope.license_id, team_name=name)
        self._s.add(row)
        self._s.flush()
        return row

    def list(self, scope: TenantScope) -> list[TechnicianTeam]:
        return list(
            self._s.execute(
                select(TechnicianTeam)
                .where(TechnicianTeam.license_id == scope.license_id)
                .order_by(TechnicianTeam.team_name.asc())
            ).scalars()
        )

    def delete(self, scope: TenantScope, team_id: uuid.UUID) -> None:
        row = self._s.execute(
            select(TechnicianTeam).where(
                TechnicianTeam.id == team_id,
                TechnicianTeam.license_id == scope.license_id,
            )
        ).scalars().first()
        if row is None:
            raise MasterDataNotFound("team not found")
        self._s.delete(row)
        self._s.flush()

    def add_member(
        self,
        scope: TenantScope,
        team_id: uuid.UUID,
        member_id: uuid.UUID,
        *,
        is_lead: bool = False,
    ) -> TechnicianTeamMember:
        """Idempotent; re-adding updates is_lead rather than erroring.

        A technician may be in several teams, and a team may have several
        leads (7.5) — neither is constrained.
        """
        team = self._s.execute(
            select(TechnicianTeam).where(
                TechnicianTeam.id == team_id,
                TechnicianTeam.license_id == scope.license_id,
            )
        ).scalars().first()
        if team is None:
            raise MasterDataNotFound("team not found")

        existing = self._s.execute(
            select(TechnicianTeamMember).where(
                TechnicianTeamMember.team_id == team_id,
                TechnicianTeamMember.member_id == member_id,
            )
        ).scalars().first()
        if existing is not None:
            if existing.is_lead != is_lead:
                existing.is_lead = is_lead
                self._s.flush()
            return existing

        row = TechnicianTeamMember(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            team_id=team_id,
            member_id=member_id,
            is_lead=is_lead,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def set_lead(
        self, scope: TenantScope, team_id: uuid.UUID, member_id: uuid.UUID, is_lead: bool
    ) -> TechnicianTeamMember:
        row = self._s.execute(
            select(TechnicianTeamMember).where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.team_id == team_id,
                TechnicianTeamMember.member_id == member_id,
            )
        ).scalars().first()
        if row is None:
            raise MasterDataNotFound("team membership not found")
        row.is_lead = is_lead
        self._s.flush()
        return row

    def remove_member(
        self, scope: TenantScope, team_id: uuid.UUID, member_id: uuid.UUID
    ) -> None:
        row = self._s.execute(
            select(TechnicianTeamMember).where(
                TechnicianTeamMember.license_id == scope.license_id,
                TechnicianTeamMember.team_id == team_id,
                TechnicianTeamMember.member_id == member_id,
            )
        ).scalars().first()
        if row is None:
            raise MasterDataNotFound("team membership not found")
        self._s.delete(row)
        self._s.flush()

    def members(
        self, scope: TenantScope, team_id: uuid.UUID
    ) -> list[TechnicianTeamMember]:
        return list(
            self._s.execute(
                select(TechnicianTeamMember).where(
                    TechnicianTeamMember.license_id == scope.license_id,
                    TechnicianTeamMember.team_id == team_id,
                )
            ).scalars()
        )

    def teams_for_member(
        self, scope: TenantScope, member_id: uuid.UUID
    ) -> list[TechnicianTeam]:
        return list(
            self._s.execute(
                select(TechnicianTeam)
                .join(
                    TechnicianTeamMember,
                    TechnicianTeamMember.team_id == TechnicianTeam.id,
                )
                .where(
                    TechnicianTeam.license_id == scope.license_id,
                    TechnicianTeamMember.member_id == member_id,
                )
                .order_by(TechnicianTeam.team_name.asc())
            ).scalars()
        )
