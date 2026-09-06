"""Phase 9 — CRM core: customers (lead/contact), deals, deal products,
storefront cross-tenant search + auto-lead (Master Spec 9.4-9.6).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, Deal, DealProduct, License, LicenseMember, Product
from .locks import serialise
from .tenant_scope import TenantScope

# 9.6's transition table. A value of None as the destination set means "no
# further transitions" — won/lost only reopen back to new, and that path is
# gated by deal.reopen at the caller (permission is not this repository's
# concern; only whether the state machine allows the move at all).
DEAL_STAGES = frozenset({"new", "proposed", "won", "lost"})
# OWNER-APPROVED DEPARTURE FROM MASTER SPEC 9.6.
#
# The spec lists new → proposed, proposed → won, proposed → lost and
# won → new. It has no new → lost, which makes a deal that dies before
# anyone quotes it impossible to close — and that is the most ordinary
# way for a deal to end: the customer changes their mind, buys elsewhere,
# or stops replying, all before a quote exists.
#
# Without it the only options were to leave the deal open forever, or to
# move it to proposed — inventing a quote that was never made — and then
# lose it. Both corrupt the pipeline numbers the stage exists to produce.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"proposed", "lost"}),
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


class Phase9Duplicate(Phase9Conflict):
    """A record already exists that the caller should be using instead.

    Carries the existing id and code so the caller can point at it rather
    than just refusing — "already exists" without saying WHICH leaves the
    person to go and search for it themselves.
    """

    def __init__(self, message: str, *, existing_id: str, existing_code: str, field: str = "phone"):
        super().__init__(message)
        self.existing_id = existing_id
        self.existing_code = existing_code
        self.field = field  # which identifier matched: phone or email


def _normalise_phone(phone: str | None) -> str:
    """Digits only, so formatting differences do not create duplicates.

    "081-234-5678", "0812345678" and "+66812345678" are one person. The
    leading country code is stripped to a local zero because a Thai shop
    saves the same number both ways depending on where it was copied from.
    """
    if not phone:
        return ""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("66") and len(digits) > 9:
        digits = "0" + digits[2:]
    return digits


class CustomerRepository:
    def __init__(self, session: Session):
        self._s = session

    def _unique_customer_code(self, scope: TenantScope) -> str:
        """Allocate the next C-YYYY-NNNN for this tenant.

        Scoped to the license, unlike _unique_deal_id which searches
        platform-wide: every tenant should see its own customers numbered
        from 1, and global numbering would both look broken to a new tenant
        and disclose the platform's total volume.

        Retries on collision rather than locking a counter row, matching the
        pattern already used for deal, license and invite codes. The unique
        constraint on (license_id, customer_id) is what actually guarantees
        correctness under a race; this loop just avoids the error.
        """
        year = datetime.now(timezone.utc).year
        prefix = f"C-{year}-"
        serialise(self._s, f"{scope.license_id}:customer")
        for _ in range(50):
            existing = self._s.execute(
                select(Customer.customer_id).where(
                    Customer.license_id == scope.license_id,
                    Customer.customer_id.like(f"{prefix}%"),
                )
            ).scalars().all()
            used = {
                int(code.rsplit("-", 1)[1])
                for code in existing
                if code.rsplit("-", 1)[1].isdigit()
            }
            candidate = f"{prefix}{(max(used) + 1) if used else 1:04d}"
            clash = self._s.execute(
                select(Customer.id).where(
                    Customer.license_id == scope.license_id,
                    Customer.customer_id == candidate,
                )
            ).first()
            if clash is None:
                return candidate
        raise Phase9Conflict("could not allocate a unique customer_id")

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

        # One phone number, one customer. A repair shop's customer calls in
        # months apart; two records for the same person split their service
        # history in half, so the technician arriving sees no previous
        # visit and the shop cannot tell a repeat customer from a new one.
        #
        # Raises rather than silently returning the existing row: the
        # caller asked to CREATE, and quietly handing back a different
        # record with a different name is how a customer's details get
        # overwritten by someone else's.
        # Serialised per tenant: two creates for the same number in the
        # same instant both passed the scan and both succeeded (review, 6
        # Sep 2026). The lock also covers the number allocation below.
        serialise(self._s, f"{scope.license_id}:customer")
        self._refuse_duplicates(scope, phone=phone, email=email)
        if customer_chann_uid:
            existing = self._s.execute(
                select(Customer).where(
                    Customer.license_id == scope.license_id,
                    Customer.customer_chann_uid == customer_chann_uid,
                )
            ).scalars().first()
            if existing is not None and existing.archived_at is not None:
                # The same person back after their lead was archived (by
                # hand or by the inactivity sweep): the record returns to
                # the list with whatever is newly known — it used to be
                # a permanent "already a customer" wall (review, 6 Sep).
                existing.archived_at = None
                existing.stage = stage
                for key, value in (
                    ("first_name", first_name), ("last_name", last_name), ("phone", phone),
                    ("email", email), ("address", address), ("notes", notes),
                ):
                    if value:
                        setattr(existing, key, value)
                if owner_member_id is not None:
                    existing.owner_member_id = owner_member_id
                self._s.flush()
                return existing
            if existing is not None:
                raise Phase9Conflict("this identity is already a customer of this tenant")
        row = Customer(
            id=uuid.uuid4(), license_id=scope.license_id,
            customer_id=self._unique_customer_code(scope),
            customer_chann_uid=customer_chann_uid, stage=stage,
            owner_member_id=owner_member_id,
            first_name=first_name, last_name=last_name, phone=phone,
            email=email, address=address, notes=notes,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def _refuse_duplicates(
        self, scope: TenantScope, *, phone: str | None, email: str | None,
        except_id: uuid.UUID | None = None,
    ) -> None:
        """One phone number, one customer; one email, one customer —
        checked on create AND on update, which used to skip it."""
        normalised = _normalise_phone(phone)
        normalised_email = _normalise_email(email)
        if not (normalised or normalised_email):
            return
        existing = self._s.execute(
            select(Customer).where(
                Customer.license_id == scope.license_id,
                Customer.archived_at.is_(None),
            )
        ).scalars().all()
        for row in existing:
            if except_id is not None and row.id == except_id:
                continue
            if normalised and _normalise_phone(row.phone) == normalised:
                raise Phase9Duplicate(
                    f"{row.customer_id} already has this phone number",
                    existing_id=str(row.id),
                    existing_code=row.customer_id,
                    field="phone",
                )
            if normalised_email and _normalise_email(row.email) == normalised_email:
                raise Phase9Duplicate(
                    f"{row.customer_id} already has this email",
                    existing_id=str(row.id),
                    existing_code=row.customer_id,
                    field="email",
                )

    def get(self, scope: TenantScope, customer_id: uuid.UUID) -> Customer | None:
        return self._s.execute(
            select(Customer).where(
                Customer.id == customer_id, Customer.license_id == scope.license_id,
            )
        ).scalars().first()

    def set_owner(self, scope: TenantScope, customer_id: uuid.UUID, member_id: uuid.UUID | None) -> Customer:
        """reassign_records: hand the record to a colleague in this tenant."""
        row = self.get(scope, customer_id)
        if row is None:
            raise Phase9NotFound("customer not found")
        self._require_member(scope, member_id)
        row.owner_member_id = member_id
        self._s.flush()
        return row

    def _require_member(self, scope: TenantScope, member_id: uuid.UUID | None) -> None:
        if member_id is None:
            return
        found = self._s.execute(
            select(LicenseMember.id).where(
                LicenseMember.id == member_id,
                LicenseMember.license_id == scope.license_id,
                LicenseMember.status == "active",
            )
        ).first()
        if found is None:
            raise Phase9NotFound("member not found in this tenant")

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
        if "phone" in fields or "email" in fields:
            serialise(self._s, f"{scope.license_id}:customer")
            self._refuse_duplicates(
                scope, phone=fields.get("phone", row.phone), email=fields.get("email", row.email),
                except_id=row.id,
            )
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

    def archive_inactive_leads(
        self, scope: TenantScope, *, days: int, now: datetime | None = None,
    ) -> list[Customer]:
        """Soft-delete leads nobody has touched for `days` days.

        Last activity is the newest of: the record's own updated_at, its
        deals, its tickets, its notes and its follow-ups. Only stage
        "lead" — a contact (a confirmed customer) is never swept. Returns
        the rows archived so the caller can audit each one.
        """
        from ..models import FollowUp, Note, ServiceTicket

        moment = now or datetime.now(timezone.utc)
        cutoff = moment - timedelta(days=max(1, int(days)))
        deal_last = (
            select(func.max(Deal.updated_at)).where(Deal.contact_id == Customer.id)
            .correlate(Customer).scalar_subquery()
        )
        ticket_last = (
            select(func.max(ServiceTicket.updated_at)).where(ServiceTicket.contact_id == Customer.id)
            .correlate(Customer).scalar_subquery()
        )
        note_last = (
            select(func.max(Note.created_at))
            .where(Note.entity_type == "customer", Note.entity_id == Customer.id)
            .correlate(Customer).scalar_subquery()
        )
        follow_last = (
            select(func.max(FollowUp.updated_at))
            .where(FollowUp.entity_type == "customer", FollowUp.entity_id == Customer.id)
            .correlate(Customer).scalar_subquery()
        )
        last_activity = func.greatest(
            Customer.updated_at,
            func.coalesce(deal_last, Customer.updated_at),
            func.coalesce(ticket_last, Customer.updated_at),
            func.coalesce(note_last, Customer.updated_at),
            func.coalesce(follow_last, Customer.updated_at),
        )
        rows = list(self._s.execute(
            select(Customer).where(
                Customer.license_id == scope.license_id,
                Customer.stage == "lead",
                Customer.archived_at.is_(None),
                last_activity < cutoff,
            ).with_for_update()
        ).scalars())
        for row in rows:
            row.archived_at = moment
        self._s.flush()
        return rows


class DealRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(
        self, scope: TenantScope, *,
        contact_id: uuid.UUID, notes: str | None = None,
        owner_member_id: uuid.UUID | None = None,
        products: list[dict] | None = None,
        amount=None, currency: str | None = None, expected_close_date=None,
    ) -> Deal:
        contact = self._s.execute(
            select(Customer).where(
                Customer.id == contact_id, Customer.license_id == scope.license_id,
            )
        ).scalars().first()
        if contact is None:
            raise Phase9NotFound("contact not found in this tenant")

        # One OPEN deal per customer. Two live deals for the same person
        # means two salespeople quoting them different numbers and neither
        # knowing about the other.
        #
        # Open, not ever: a customer who bought last year and comes back is
        # the point of keeping the record. Closing the old deal — won or
        # lost — frees them to start another.
        open_deal = self._s.execute(
            select(Deal).where(
                Deal.license_id == scope.license_id,
                Deal.contact_id == contact_id,
                Deal.stage.in_(("new", "proposed")),
                Deal.archived_at.is_(None),
            )
        ).scalars().first()
        if open_deal is not None:
            raise Phase9Duplicate(
                f"{open_deal.deal_id} is still open for this customer",
                existing_id=str(open_deal.id),
                existing_code=open_deal.deal_id,
            )

        row = Deal(
            id=uuid.uuid4(), license_id=scope.license_id,
            deal_id=self._unique_deal_id(scope), contact_id=contact_id,
            stage="new", owner_member_id=owner_member_id, notes=notes,
            amount=amount, currency=(currency or "THB").upper()[:3], expected_close_date=expected_close_date,
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

    def set_owner(self, scope: TenantScope, deal_id: uuid.UUID, member_id: uuid.UUID | None) -> Deal:
        row = self.get(scope, deal_id)
        if row is None:
            raise Phase9NotFound("deal not found")
        if member_id is not None:
            found = self._s.execute(
                select(LicenseMember.id).where(
                    LicenseMember.id == member_id,
                    LicenseMember.license_id == scope.license_id,
                    LicenseMember.status == "active",
                )
            ).first()
            if found is None:
                raise Phase9NotFound("member not found in this tenant")
        row.owner_member_id = member_id
        self._s.flush()
        return row

    def _unique_deal_id(self, scope: TenantScope) -> str:
        """Per-tenant, matching quote_id and customer_id (see Deal's
        docstring in models.py for why this departs from the spec).

        Retries on a collision rather than locking a counter row — deal
        creation is not so frequent that a handful of retries costs
        anything noticeable, and this mirrors the retry pattern already used
        for license/invite codes in phase65.py. The unique constraint on
        (license_id, deal_id) is what actually guarantees correctness under
        a race; the loop just avoids surfacing the error.
        """
        year = datetime.now(timezone.utc).year
        serialise(self._s, f"{scope.license_id}:deal")
        for _ in range(50):
            existing = self._s.execute(
                select(Deal.deal_id).where(
                    Deal.license_id == scope.license_id,
                    Deal.deal_id.like(f"D-{year}-%"),
                )
            ).scalars().all()
            used = {int(code.rsplit("-", 1)[1]) for code in existing if code.rsplit("-", 1)[1].isdigit()}
            next_n = (max(used) + 1) if used else 1
            candidate = f"D-{year}-{next_n:04d}"
            clash = self._s.execute(
                select(Deal.id).where(
                    Deal.license_id == scope.license_id, Deal.deal_id == candidate,
                )
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

    def list_for_contact(self, scope: TenantScope, contact_id: uuid.UUID) -> list[Deal]:
        """One customer's deals in this tenant — their purchase history."""
        return list(self._s.execute(
            select(Deal).where(
                Deal.license_id == scope.license_id,
                Deal.contact_id == contact_id,
                Deal.archived_at.is_(None),
            ).order_by(Deal.created_at.desc())
        ).scalars())

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
        last = self._s.execute(
            select(func.max(DealProduct.position)).where(DealProduct.deal_id == deal_id)
        ).scalar()
        row = DealProduct(
            id=uuid.uuid4(), deal_id=deal_id, product_id=product_id,
            product_name=product_name, quoted_unit_price=_decimal(quoted_unit_price),
            qty=qty, notes=notes,
            position=(last + 1) if last is not None else 0,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def update_product(
        self, scope: TenantScope, deal_id: uuid.UUID, deal_product_id: uuid.UUID,
        fields: dict,
    ) -> DealProduct:
        """Change a line already on a deal.

        Deals could add and delete lines but never edit one, so correcting
        a quantity meant deleting and retyping — which loses the line's
        position and, on a deal with several similar products, is easy to
        do to the wrong one.
        """
        deal = self.get(scope, deal_id)
        if deal is None:
            raise Phase9NotFound("deal not found in this tenant")
        row = self._s.execute(
            select(DealProduct).where(
                DealProduct.id == deal_product_id,
                DealProduct.deal_id == deal_id,
            )
        ).scalars().first()
        if row is None:
            raise Phase9NotFound("line not found on this deal")

        if "quoted_unit_price" in fields and fields["quoted_unit_price"] is not None:
            price = _decimal(fields["quoted_unit_price"])
            if price < 0:
                raise Phase9Conflict("a price cannot be negative")
            row.quoted_unit_price = price
        if "qty" in fields and fields["qty"] is not None:
            qty = int(fields["qty"])
            if qty < 1:
                raise Phase9Conflict("qty must be at least 1")
            row.qty = qty
        if "product_name" in fields and str(fields["product_name"] or "").strip():
            row.product_name = str(fields["product_name"]).strip()
        if "notes" in fields:
            row.notes = fields["notes"]
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
        allowed = {"notes", "owner_member_id", "expected_close_date", "amount", "currency", "lost_reason"}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if isinstance(value, str):
                value = value.strip() or None
            setattr(row, key, value)
        self._s.flush()
        return row

    def pipeline_summary(self, scope: TenantScope) -> dict:
        """What is in the pipeline, and what is closing.

        The reason expected_close_date exists. Counting deals by stage is
        not a forecast — a stage says where a deal is, not when or whether
        it lands — so the value comes from the line items and the timing
        from the date.

        Overdue is separated from this month deliberately: a deal whose
        close date has passed and is still open is not a forecast, it is a
        deal nobody has touched, and averaging the two hides that.
        """
        from datetime import date as _date, datetime as _dt, timezone as _tz

        today = _dt.now(_tz.utc).date()
        month_end = (
            _date(today.year + 1, 1, 1) if today.month == 12
            else _date(today.year, today.month + 1, 1)
        )

        rows = self._s.execute(
            select(
                Deal.id, Deal.stage, Deal.expected_close_date,
                # Line items when there are any; otherwise the amount the
                # salesperson stated (0024) — a 250,000 deal with no lines
                # yet counted as 0 on the pipeline card (review, 6 Sep).
                func.coalesce(
                    func.sum(DealProduct.quoted_unit_price * DealProduct.qty), Deal.amount, 0,
                ).label("value"),
            )
            .outerjoin(DealProduct, DealProduct.deal_id == Deal.id)
            .where(
                Deal.license_id == scope.license_id,
                Deal.archived_at.is_(None),
            )
            .group_by(Deal.id, Deal.stage, Deal.expected_close_date, Deal.amount)
        ).all()

        by_stage: dict[str, dict] = {
            stage: {"count": 0, "value": Decimal("0")} for stage in DEAL_STAGES
        }
        open_value = Decimal("0")
        closing_this_month = Decimal("0")
        overdue_count = 0
        undated_open = 0

        for row in rows:
            value = Decimal(str(row.value or 0))
            bucket = by_stage.setdefault(
                row.stage, {"count": 0, "value": Decimal("0")}
            )
            bucket["count"] += 1
            bucket["value"] += value

            if row.stage in ("new", "proposed"):
                open_value += value
                if row.expected_close_date is None:
                    # Counted, not hidden: a pipeline where half the deals
                    # have no date has a forecast that means very little,
                    # and the reader should be able to see that.
                    undated_open += 1
                elif row.expected_close_date < today:
                    overdue_count += 1
                elif row.expected_close_date < month_end:
                    closing_this_month += value

        return {
            "by_stage": {
                stage: {"count": b["count"], "value": str(b["value"])}
                for stage, b in by_stage.items()
            },
            "open_value": str(open_value),
            "closing_this_month": str(closing_this_month),
            "overdue_count": overdue_count,
            "undated_open_count": undated_open,
        }

    def products_of(self, deal_id: uuid.UUID) -> list[DealProduct]:
        return list(
            self._s.execute(
                select(DealProduct).where(DealProduct.deal_id == deal_id)
                # id breaks the tie: created_at is a timestamp, and two
                # lines added in the same message share it, so ordering on
                # it alone let a deal's contents reorder between reads —
                # and a quote copied from it inherit a different order
                # each time.
                .order_by(DealProduct.position.asc(), DealProduct.created_at.asc())
            ).scalars()
        )

    def transition_stage(
        self, scope: TenantScope, deal_id: uuid.UUID, *, to_stage: str, allow_reopen: bool,
        lost_reason: str | None = None,
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

        if to_stage == "lost":
            # Recorded when offered, never demanded. Requiring a reason
            # before a deal can be closed gets you a column full of "-":
            # it looks answered and teaches nothing, which is worse than
            # an empty one that at least reads as unknown.
            if lost_reason and lost_reason.strip():
                deal.lost_reason = lost_reason.strip()
        elif deal.lost_reason:
            # Reopened. The old reason describes a loss that no longer
            # happened, and keeping it would leave the deal explaining why
            # it was lost while sitting in "new".
            deal.lost_reason = None

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

    def browse_products(self, *, limit: int = 20) -> list[dict]:
        """"สินค้าทั้งหมด" — the storefront with no search term (spec page 1,
        tile 2). Same projection and same rule as search_products: product
        info only, from shops that are open for business."""
        limit = max(1, min(int(limit or 20), 50))
        rows = self._s.execute(
            select(Product, License)
            .join(License, License.id == Product.license_id)
            .where(
                Product.archived_at.is_(None),
                License.status.in_(("trial", "active")),
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


def _normalise_email(value: str | None) -> str:
    """Lower-cased, trimmed; an empty or None value is "" so it never
    matches another empty one."""
    return (value or "").strip().lower()
