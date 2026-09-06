"""Warranties (7.5) and the cross-company serial lookup (16.4).

The lookup is the sensitive part. Every other query in this codebase is
tenant-scoped and refuses to be otherwise; this one deliberately spans
tenants, because the question a customer is really asking — "my thing is
broken, who do I talk to" — cannot be answered inside one.

Three rules keep that from becoming a leak:

* It returns only what identifies a SHOP: license id, company name, and
  the serial that matched. Never the other tenant's customer, price, or
  history.
* Every call is audited with cross_tenant=true, which is what the
  audit_log column was added for in Phase 3.
* It matches on an exact serial. No prefix search, no fuzzy matching —
  those would let someone enumerate other companies' inventory.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .locks import serialise

from ..models import Customer, License, Product, Warranty
from .tenant_scope import TenantScope

WARRANTY_STATUSES = frozenset({"active", "expired", "void"})

# Used when a product says nothing about its own cover. One year is the
# common floor in Thai retail; a tenant that wants different puts it on
# the product.
DEFAULT_WARRANTY_MONTHS = 12


class WarrantyNotFound(Exception):
    pass


class WarrantyConflict(Exception):
    pass


class WarrantyRepository:
    def __init__(self, session: Session):
        self._s = session

    # -------------------------------------------------------- registration

    def _next_warranty_number(self, scope: TenantScope) -> str:
        year = datetime.now(timezone.utc).year
        prefix = f"W-{year}-"
        serialise(self._s, f"{scope.license_id}:warranty")
        existing = self._s.execute(
            select(Warranty.warranty_number).where(
                Warranty.license_id == scope.license_id,
                Warranty.warranty_number.like(f"{prefix}%"),
            )
        ).scalars().all()
        used = {
            int(code.rsplit("-", 1)[1])
            for code in existing
            if code.rsplit("-", 1)[1].isdigit()
        }
        return f"{prefix}{(max(used) + 1) if used else 1:04d}"

    def register(
        self,
        scope: TenantScope,
        *,
        serial_number: str,
        product_id: uuid.UUID | None = None,
        product_name: str | None = None,
        customer_chann_uid: str | None = None,
        contact_id: uuid.UUID | None = None,
        warranty_start: date | None = None,
        warranty_months: int | None = None,
    ) -> Warranty:
        """Register one unit.

        The serial is required and the product is not: a customer often
        knows the sticker on the back of the machine and not what the shop
        calls the model, and refusing the registration over that would
        lose the very record that makes everything afterwards work.
        """
        serial = (serial_number or "").strip()
        if not serial:
            raise WarrantyConflict("a serial number is required")

        existing = self._s.execute(
            select(Warranty).where(
                Warranty.license_id == scope.license_id,
                Warranty.serial_number == serial,
                Warranty.status != "void",
            )
        ).scalars().first()
        if existing is not None:
            # Registering the same unit twice is a mistake, not a second
            # warranty — and silently creating one would leave two
            # different expiry dates for the same machine.
            raise WarrantyConflict(
                f"serial {serial} is already registered as {existing.warranty_number}"
            )

        resolved_name = product_name
        if product_id is not None and not resolved_name:
            product = self._s.execute(
                select(Product).where(
                    Product.id == product_id, Product.license_id == scope.license_id
                )
            ).scalars().first()
            if product is None:
                raise WarrantyNotFound("product not found in this tenant")
            resolved_name = product.product_name

        start = warranty_start or datetime.now(timezone.utc).date()
        months = warranty_months or DEFAULT_WARRANTY_MONTHS
        # Month arithmetic without dateutil: add the months to the month
        # index, then clamp the day so 31 Jan + 1 month is the last day of
        # February rather than an error.
        end_month = start.month - 1 + months
        end_year = start.year + end_month // 12
        end_month = end_month % 12 + 1
        last_day = [31, 29 if end_year % 4 == 0 and (end_year % 100 != 0 or end_year % 400 == 0)
                    else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][end_month - 1]
        end = date(end_year, end_month, min(start.day, last_day))

        row = Warranty(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            warranty_number=self._next_warranty_number(scope),
            customer_chann_uid=customer_chann_uid,
            contact_id=contact_id,
            product_id=product_id,
            product_name=resolved_name,
            serial_number=serial,
            warranty_start=start,
            warranty_end=end,
            status="active",
        )
        self._s.add(row)
        self._s.flush()
        return row

    # --------------------------------------------------------------- reads

    def get(self, scope: TenantScope, warranty_id: uuid.UUID) -> Warranty | None:
        return self._s.execute(
            select(Warranty).where(
                Warranty.id == warranty_id, Warranty.license_id == scope.license_id
            )
        ).scalars().first()

    def claim(
        self, scope: TenantScope, *, serial_number: str, customer_chann_uid: str,
    ) -> Warranty:
        """Attach a customer to a unit the shop already registered.

        Owner rule (3 Sep): a customer cannot invent a serial. The shop
        records the units it sells (register, above); the customer types
        the sticker and is matched to that row. Unknown here → NotFound,
        which the chat turns into "ติดต่อร้าน"; taken by someone else →
        Conflict. Re-claiming one's own unit is a no-op, not an error.
        """
        serial = (serial_number or "").strip()
        if not serial:
            raise WarrantyConflict("a serial number is required")
        row = self.by_serial(scope, serial)
        if row is None:
            raise WarrantyNotFound(f"serial {serial} is not registered at this shop")
        if row.customer_chann_uid and row.customer_chann_uid != customer_chann_uid:
            raise WarrantyConflict(
                f"serial {serial} is already claimed by another customer"
            )
        row.customer_chann_uid = customer_chann_uid
        self._s.flush()
        return row

    def by_serial(self, scope: TenantScope, serial_number: str) -> Warranty | None:
        return self._s.execute(
            select(Warranty).where(
                Warranty.license_id == scope.license_id,
                Warranty.serial_number == (serial_number or "").strip(),
                Warranty.status != "void",
            )
        ).scalars().first()

    def for_customer(self, scope: TenantScope, chann_uid: str) -> list[Warranty]:
        return list(
            self._s.execute(
                select(Warranty)
                .where(
                    Warranty.license_id == scope.license_id,
                    Warranty.customer_chann_uid == chann_uid,
                )
                .order_by(Warranty.created_at.desc())
            ).scalars()
        )

    def list_for_license(self, scope: TenantScope, limit: int = 100) -> list[Warranty]:
        return list(
            self._s.execute(
                select(Warranty)
                .where(Warranty.license_id == scope.license_id)
                .order_by(Warranty.created_at.desc())
                .limit(max(1, min(limit, 500)))
            ).scalars()
        )

    # ------------------------------------------------- cross-tenant (16.4)

    def find_shops_by_serial(self, serial_number: str) -> list[dict]:
        """Which shops have registered this exact serial.

        THE deliberate cross-tenant query. It answers "who do I talk to",
        which is unanswerable inside one tenant, and it returns only what
        identifies a shop — license id, company name, company code, and
        the product name on that registration.

        Never the other tenant's customer, price or history. Exact match
        only: a prefix or fuzzy search here would let anyone enumerate
        another company's inventory one keystroke at a time.

        The caller MUST audit this with cross_tenant=true.
        """
        serial = (serial_number or "").strip()
        if not serial:
            return []

        rows = self._s.execute(
            select(
                Warranty.license_id,
                Warranty.warranty_number,
                Warranty.product_name,
                Warranty.warranty_end,
                Warranty.status,
                License.company_name,
                License.company_code,
            )
            .join(License, License.id == Warranty.license_id)
            .where(Warranty.serial_number == serial, Warranty.status != "void")
            .order_by(License.company_name)
        ).all()

        return [
            {
                "license_id": str(r.license_id),
                "company_name": r.company_name,
                "company_code": r.company_code,
                "warranty_number": r.warranty_number,
                "product_name": r.product_name,
                "warranty_end": r.warranty_end.isoformat() if r.warranty_end else None,
                "status": r.status,
            }
            for r in rows
        ]

    # -------------------------------------------------------------- status

    def set_status(
        self, scope: TenantScope, warranty_id: uuid.UUID, *, status: str,
    ) -> Warranty:
        if status not in WARRANTY_STATUSES:
            raise WarrantyConflict(f"unknown warranty status: {status!r}")
        row = self.get(scope, warranty_id)
        if row is None:
            raise WarrantyNotFound("warranty not found in this tenant")
        row.status = status
        self._s.flush()
        return row

    def expire_overdue(self, scope: TenantScope, *, on_day: date | None = None) -> int:
        """Mark cover that has run out.

        Computed rather than trusted: `status` is a cache of a date
        comparison, and a row nobody has swept is still expired in fact.
        Callers that need certainty should compare warranty_end directly.
        """
        today = on_day or datetime.now(timezone.utc).date()
        rows = self._s.execute(
            select(Warranty).where(
                Warranty.license_id == scope.license_id,
                Warranty.status == "active",
                Warranty.warranty_end < today,
            )
        ).scalars().all()
        for row in rows:
            row.status = "expired"
        self._s.flush()
        return len(rows)


class DisplayPreferenceRepository:
    """16.3/16.5 — how one person wants to be spoken to, everywhere."""

    def __init__(self, session: Session):
        self._s = session

    def get(self, chann_uid: str) -> dict:
        from ..models import UserDisplayPreference

        row = self._s.execute(
            select(UserDisplayPreference).where(
                UserDisplayPreference.chann_uid == chann_uid
            )
        ).scalars().first()
        if row is None:
            # Defaults rather than None: every caller wants a usable
            # preference, and making each one handle absence separately is
            # how one of them ends up rendering "None" to a person.
            return {
                "chann_uid": chann_uid,
                "date_format": "dd/mm/yyyy",
                "language": "th",
                "timezone": "Asia/Bangkok",
            }
        return {
            "chann_uid": row.chann_uid,
            "date_format": row.date_format,
            "language": row.language,
            "timezone": row.timezone,
        }

    def upsert(self, chann_uid: str, fields: dict) -> dict:
        from ..models import UserDisplayPreference

        allowed = {"date_format", "language", "timezone"}
        row = self._s.execute(
            select(UserDisplayPreference).where(
                UserDisplayPreference.chann_uid == chann_uid
            )
        ).scalars().first()
        if row is None:
            row = UserDisplayPreference(chann_uid=chann_uid)
            self._s.add(row)
        for key, value in fields.items():
            if key in allowed and value:
                setattr(row, key, value)
        row.updated_at = func.now()
        self._s.flush()
        return self.get(chann_uid)
