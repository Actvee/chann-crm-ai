"""Phase 9 — CRM core: customers (lead/contact), deals, deal products,
storefront cross-tenant search + auto-lead (Master Spec 9.4-9.6).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, Deal, DealProduct, License, Product
from .tenant_scope import TenantScope

# 9.6's transition table. A value of None as the destination set means "no
# further transitions" — won/lost only reopen back to new, and that path is
# gated by deal.reopen at the caller (permission is not this repository's
# concern; only whether the state machine allows the move at all).
DEAL_STAGES = frozenset({"new", "proposed", "won", "lost"})
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"proposed"}),
    "proposed": frozenset({"won", "lost"}),
    "won": frozenset({"new"}),
    "lost": frozenset({"new"}),
}
# Transitions into these destinations from these origins additionally need
# deal.reopen — the caller checks this against the actor's permission_keys.
REOPEN_TRANSITIONS = frozenset({("won", "new"), ("lost", "new")})


class Phase9Conflict(RuntimeError):
    """Well-formed but not allowed in the current state."""


class Phase9NotFound(LookupError):
    pass


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise Phase9Conflict(f"not a valid price: {value!r}") from exc


class CustomerRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(
        self, scope: TenantScope, *,
        first_name: str | None = None, last_name: str | None = None,
        phone: str | None = None, email: str | None = None,
        address: str | None = None, notes: str | None = None,
        customer_chann_uid: str | None = None,
        owner_member_id: uuid.UUID | None = None,
        stage: str = "lead",
    ) -> Customer:
        if not any([first_name, last_name, phone, email, customer_chann_uid]):
            raise Phase9Conflict("a customer needs at least a name, phone, email, or chann_uid")
        if customer_chann_uid:
            existing = self._s.execute(
                select(Customer).where(
                    Customer.license_id == scope.license_id,
                    Customer.customer_chann_uid == customer_chann_uid,
                )
            ).scalars().first()
            if existing is not None:
                raise Phase9Conflict("this identity is already a customer of this tenant")
        row = Customer(
            id=uuid.uuid4(), license_id=scope.license_id,
            customer_chann_uid=customer_chann_uid, stage=stage,
            owner_member_id=owner_member_id,
            first_name=first_name, last_name=last_name, phone=phone,
            email=email, address=address, notes=notes,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get(self, scope: TenantScope, customer_id: uuid.UUID) -> Customer | None:
        return self._s.execute(
            select(Customer).where(
                Customer.id == customer_id, Customer.license_id == scope.license_id,
            )
        ).scalars().first()

    def list_for_license(self, scope: TenantScope, *, stage: str | None = None) -> list[Customer]:
        query = select(Customer).where(
            Customer.license_id == scope.license_id, Customer.archived_at.is_(None),
        )
        if stage:
            query = query.where(Customer.stage == stage)
        return list(self._s.execute(query.order_by(Customer.created_at.desc())).scalars())

    def update(self, scope: TenantScope, customer_id: uuid.UUID, fields: dict) -> Customer:
        row = self.get(scope, customer_id)
        if row is None:
            raise Phase9NotFound("customer not found")
        for key in ("first_name", "last_name", "phone", "email", "address", "notes"):
            if key in fields:
                setattr(row, key, fields[key])
        self._s.flush()
        return row

    def promote_to_contact(self, scope: TenantScope, customer_id: uuid.UUID) -> Customer:
        """9.5 — Lead -> Contact. Idempotent: promoting an already-Contact
        record is a no-op success, not an error, since the caller's intent
        ("ยืนยันลูกค้าสมชาย") is already satisfied."""
        row = self.get(scope, customer_id)
        if row is None:
            raise Phase9NotFound("customer not found")
        row.stage = "contact"
        self._s.flush()
        return row

    def archive(self, scope: TenantScope, customer_id: uuid.UUID) -> Customer:
        row = self.get(scope, customer_id)
        if row is None:
            raise Phase9NotFound("customer not found")
        if row.archived_at is None:
            row.archived_at = datetime.now(timezone.utc)
            self._s.flush()
        return row

    def find_by_chann_uid(self, scope: TenantScope, chann_uid: str) -> Customer | None:
        return self._s.execute(
            select(Customer).where(
                Customer.license_id == scope.license_id,
                Customer.customer_chann_uid == chann_uid,
            )
        ).scalars().first()


class DealRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(
        self, scope: TenantScope, *,
        contact_id: uuid.UUID, notes: str | None = None,
        owner_member_id: uuid.UUID | None = None,
        products: list[dict] | None = None,
    ) -> Deal:
        contact = self._s.execute(
            select(Customer).where(
                Customer.id == contact_id, Customer.license_id == scope.license_id,
            )
        ).scalars().first()
        if contact is None:
            raise Phase9NotFound("contact not found in this tenant")

        row = Deal(
            id=uuid.uuid4(), license_id=scope.license_id,
            deal_id=self._unique_deal_id(), contact_id=contact_id,
            stage="new", owner_member_id=owner_member_id, notes=notes,
        )
        self._s.add(row)
        self._s.flush()
        for p in (products or []):
            self.add_product(
                scope, row.id,
                product_id=p.get("product_id"), product_name=p["product_name"],
                quoted_unit_price=p["quoted_unit_price"], qty=p.get("qty", 1),
                notes=p.get("notes"),
            )
        return row

    def _unique_deal_id(self) -> str:
        """Global, not per-tenant (see Deal's docstring in models.py).
        Retries on a collision rather than locking a counter row — deal
        creation is not so frequent that a handful of retries costs
        anything noticeable, and this mirrors the retry pattern already used
        for license/invite codes in phase65.py.
        """
        year = datetime.now(timezone.utc).year
        for _ in range(50):
            existing = self._s.execute(
                select(Deal.deal_id).where(Deal.deal_id.like(f"D-{year}-%"))
            ).scalars().all()
            used = {int(code.rsplit("-", 1)[1]) for code in existing if code.rsplit("-", 1)[1].isdigit()}
            next_n = (max(used) + 1) if used else 1
            candidate = f"D-{year}-{next_n:04d}"
            clash = self._s.execute(
                select(Deal.id).where(Deal.deal_id == candidate)
            ).first()
            if clash is None:
                return candidate
        raise Phase9Conflict("could not allocate a unique deal_id")

    def get(self, scope: TenantScope, deal_id: uuid.UUID) -> Deal | None:
        return self._s.execute(
            select(Deal).where(Deal.id == deal_id, Deal.license_id == scope.license_id)
        ).scalars().first()

    def list_for_license(self, scope: TenantScope, *, stage: str | None = None) -> list[Deal]:
        query = select(Deal).where(
            Deal.license_id == scope.license_id, Deal.archived_at.is_(None),
        )
        if stage:
            query = query.where(Deal.stage == stage)
        return list(self._s.execute(query.order_by(Deal.created_at.desc())).scalars())

    def add_product(
        self, scope: TenantScope, deal_id: uuid.UUID, *,
        product_id: uuid.UUID | None, product_name: str,
        quoted_unit_price, qty: int = 1, notes: str | None = None,
    ) -> DealProduct:
        deal = self.get(scope, deal_id)
        if deal is None:
            raise Phase9NotFound("deal not found in this tenant")
        product_name = (product_name or "").strip()
        if not product_name:
            raise Phase9Conflict("product_name is required")
        if qty < 1:
            raise Phase9Conflict("qty must be at least 1")
        row = DealProduct(
            id=uuid.uuid4(), deal_id=deal_id, product_id=product_id,
            product_name=product_name, quoted_unit_price=_decimal(quoted_unit_price),
            qty=qty, notes=notes,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def remove_product(
        self, scope: TenantScope, deal_id: uuid.UUID, deal_product_id: uuid.UUID,
    ) -> DealProduct:
        """Take a line item off a deal.

        The deal is fetched through the tenant-scoped getter first, so a
        caller cannot delete a line item by guessing its id: the row has to
        hang off a deal that belongs to this license. Returns the removed
        row so the caller can name it in an audit entry and in the reply —
        "removed พัดลม" is a far better confirmation than "removed item 2".
        """
        deal = self.get(scope, deal_id)
        if deal is None:
            raise Phase9NotFound("deal not found in this tenant")
        row = self._s.execute(
            select(DealProduct).where(
                DealProduct.id == deal_product_id, DealProduct.deal_id == deal_id,
            )
        ).scalars().first()
        if row is None:
            raise Phase9NotFound("deal product not found on this deal")
        self._s.delete(row)
        self._s.flush()
        return row

    def update(
        self, scope: TenantScope, deal_id: uuid.UUID, fields: dict,
    ) -> Deal:
        """Partial update of a deal's own attributes.

        Stage is deliberately NOT settable here: it has its own transition
        method with the state machine and the reopen permission behind it,
        and letting a generic patch bypass that would make the machine
        advisory rather than enforced.
        """
        row = self.get(scope, deal_id)
        if row is None:
            raise Phase9NotFound("deal not found in this tenant")
        allowed = {"notes", "owner_member_id"}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if isinstance(value, str):
                value = value.strip() or None
            setattr(row, key, value)
        self._s.flush()
        return row

    def products_of(self, deal_id: uuid.UUID) -> list[DealProduct]:
        return list(
            self._s.execute(
                select(DealProduct).where(DealProduct.deal_id == deal_id)
                .order_by(DealProduct.created_at.asc())
            ).scalars()
        )

    def transition_stage(
        self, scope: TenantScope, deal_id: uuid.UUID, *, to_stage: str, allow_reopen: bool,
    ) -> Deal:
        """9.6's state machine. `allow_reopen` is decided by the caller
        against the actor's permission_keys — this method only enforces
        that the transition is legal at all, not who may perform it."""
        if to_stage not in DEAL_STAGES:
            raise Phase9Conflict(f"unknown deal stage: {to_stage!r}")
        deal = self.get(scope, deal_id)
        if deal is None:
            raise Phase9NotFound("deal not found in this tenant")
        allowed = _ALLOWED_TRANSITIONS.get(deal.stage, frozenset())
        if to_stage not in allowed:
            raise Phase9Conflict(f"cannot move a deal from {deal.stage!r} to {to_stage!r}")
        if (deal.stage, to_stage) in REOPEN_TRANSITIONS and not allow_reopen:
            raise Phase9Conflict("reopening a closed deal requires deal.reopen")
        deal.stage = to_stage
        self._s.flush()
        return deal

    def archive(self, scope: TenantScope, deal_id: uuid.UUID) -> Deal:
        deal = self.get(scope, deal_id)
        if deal is None:
            raise Phase9NotFound("deal not found in this tenant")
        if deal.archived_at is None:
            deal.archived_at = datetime.now(timezone.utc)
            self._s.flush()
        return deal


class StorefrontRepository:
    """9.4 — cross-tenant product search + auto-lead. Deliberately NOT
    tenant-scoped: the whole point is to search across every tenant's
    catalogue at once, the same reasoning RegistrationRepository.find_shops
    already documents for its own un-scoped shop search.
    """

    def __init__(self, session: Session):
        self._s = session

    def search_products(self, query: str, *, limit: int = 10) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []
        rows = self._s.execute(
            select(Product, License)
            .join(License, License.id == Product.license_id)
            .where(
                Product.archived_at.is_(None),
                License.status.in_(("trial", "active")),
                Product.product_name.ilike(f"%{query}%"),
            )
            .order_by(Product.product_name.asc())
            .limit(limit)
        ).all()
        return [
            {
                "product_id": product.product_id,
                "product_name": product.product_name,
                "sku": product.sku,
                "category": product.category,
                "unit_price": product.unit_price,
                "license_id": product.license_id,
                "company_name": license_row.company_name,
            }
            for product, license_row in rows
        ]

    def record_interest(
        self, *, chann_uid: str, license_id: uuid.UUID, product_name: str,
    ) -> Customer:
        """"กดสนใจ" — auto-creates (or reuses) a Lead in the CHOSEN tenant
        only. The customer's interest in shop B must never be visible to
        shop A even though the search that found both was cross-tenant."""
        scope = TenantScope(license_id=license_id)
        existing = CustomerRepository(self._s).find_by_chann_uid(scope, chann_uid)
        if existing is not None:
            note = f"สนใจสินค้า: {product_name}"
            existing.notes = f"{existing.notes}\n{note}" if existing.notes else note
            self._s.flush()
            return existing
        return CustomerRepository(self._s).create(
            scope, customer_chann_uid=chann_uid, notes=f"สนใจสินค้า: {product_name}",
            stage="lead",
        )
