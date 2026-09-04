"""ORM schema through Phase 2.

Each phase has its own Alembic revision and migration gate. Phase 2 adds
tenant-owned roles, permission grants, settings and the two-party ownership
transfer state required by the product flow.

Columns marked "placed early" are required by a later phase but are created
now because the Master Spec explicitly says so.
"""
import uuid
from decimal import Decimal
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChannIdentity(TimestampMixin, Base):
    """GLOBAL, cross-tenant. One row per LINE user, reusable across licenses.

    Privacy rule (Master Spec 1.7): a caller must never be able to learn which
    other tenants an identity belongs to. Enforced in the repository layer.
    """

    __tablename__ = "chann_identities"

    chann_uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    line_user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    primary_role: Mapped[str] = mapped_column(String(32), nullable=False)  # customer|sales|technician
    display_name: Mapped[str | None] = mapped_column(String(255))
    signature_url: Mapped[str | None] = mapped_column(String(512))  # placed early — Phase 13
    # Phase 16.5 — consent given at registration, and the mark that this
    # person asked to be forgotten (rows stay, words are gone).
    consent_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(32))
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Phase 8 ---
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["LicenseMember"]] = relationship(back_populates="identity")


class PlatformAdmin(TimestampMixin, Base):
    """GLOBAL. Platform-owner side. Break-glass capable, so treated as the
    most sensitive credential in the system."""

    __tablename__ = "platform_admins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # argon2


class License(TimestampMixin, Base):
    """A tenant."""

    __tablename__ = "licenses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # placed early — Phase 16
    auto_accept_new_customers: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Phase 6.5 ---
    # Short, human-typeable code an end customer uses to identify this shop in
    # chat. Nullable only so the migration can backfill existing rows; new
    # licenses always get one.
    company_code: Mapped[str | None] = mapped_column(String(8), unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="trial")
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who self-registered this tenant. Used for the 1-LINE-1-company limit
    # instead of a unique index on license_members.role='owner', because
    # ownership can legitimately be transferred (Phase 2) and the limit is
    # meant to cap *creation*, not lifetime ownership.
    created_by_chann_uid: Mapped[str | None] = mapped_column(String(32), index=True)

    # --- Phase 10 — what a customer-facing document legally has to show ---
    # `company_name` above is the shop's display name (used in chat and the
    # storefront); `legal_name` is the registered entity name that belongs on
    # a tax document, which is not always the same string. Falls back to
    # company_name when a tenant hasn't supplied one.
    legal_name: Mapped[str | None] = mapped_column(String(255))
    # Thai TIN is exactly 13 digits. Deliberately String, not an integer:
    # it is an identifier, never arithmetic, and leading zeros are significant.
    tax_id: Mapped[str | None] = mapped_column(String(13))
    company_address: Mapped[str | None] = mapped_column(Text)
    company_phone: Mapped[str | None] = mapped_column(String(32))
    company_email: Mapped[str | None] = mapped_column(String(255))
    # Stored as a fraction (0.0700 = 7%). NULL means "this tenant is not
    # VAT-registered" — a different state from 0%, and one where the document
    # should carry no VAT line at all rather than a zero one. Per-tenant
    # because not every Thai SMB is registered, and the rate itself is a
    # policy value that has changed before and can change again; freezing it
    # into each document's data_snapshot at render time is what keeps an old
    # document reproducible after a rate change.
    vat_rate: Mapped[object | None] = mapped_column(Numeric(5, 4))

    members: Mapped[list["LicenseMember"]] = relationship(back_populates="license")


class LicenseMember(TimestampMixin, Base):
    """Membership of a Chann Identity in a tenant.

    `role` is a plain string on purpose: Phase 2 replaces fixed roles with
    tenant-defined custom roles, and an enum here would have to be migrated
    away immediately.
    """

    __tablename__ = "license_members"
    __table_args__ = (UniqueConstraint("license_id", "chann_uid", name="uq_license_member"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chann_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="member")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    license: Mapped["License"] = relationship(back_populates="members")
    identity: Mapped["ChannIdentity"] = relationship(back_populates="memberships")


class CustomRole(TimestampMixin, Base):
    __tablename__ = "custom_roles"
    __table_args__ = (
        UniqueConstraint("license_id", "role_name", name="uq_custom_role_license_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "license_id", "role", "permission_key", name="uq_role_permission_grant"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_key: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LicenseSetting(TimestampMixin, Base):
    __tablename__ = "license_settings"
    __table_args__ = (
        UniqueConstraint("license_id", "setting_key", name="uq_license_setting_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    setting_key: Mapped[str] = mapped_column(String(128), nullable=False)
    setting_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=False
    )


class OwnershipTransfer(TimestampMixin, Base):
    """Two-party owner transfer state.

    This table is a necessary implementation detail for the Phase 2 flow even
    though the abbreviated table list in the Master Spec omits it. Keeping the
    pending request in a setting or cache would make authoritative transfer
    state mutable or non-transactional.
    """

    __tablename__ = "ownership_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT"), nullable=False
    )
    to_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Append-only. Phase 3 (Master Spec 3.3) — never updated or deleted once
    written, so no TimestampMixin (there is no updated_at to have).

    license_id is nullable because a few actions this table must record are
    not scoped to a single tenant by nature (e.g. a cross-tenant lookup that
    was correctly refused still needs an audit trail, and it may have been
    attempted against a license the caller does not belong to at all).
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)  # user|ai|system|platform_admin
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    field_changes: Mapped[dict | None] = mapped_column(JSONB)
    ai_reasoning: Mapped[str | None] = mapped_column(String)
    cross_tenant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class LineMessageEntityMap(Base):
    """Phase 6 (Master Spec 6.3/6.5) — which entity a LINE message referred to.

    Lets a user reply to an earlier bot message and have the reply act on the
    right record. Write-once: a LINE message ID never comes to mean a
    different entity later, so there is no updated_at.
    """

    __tablename__ = "line_message_entity_map"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # LINE message IDs are globally unique, so this is unique platform-wide
    # rather than per-tenant. The tenant check still happens on lookup — a
    # mapping is only usable by the license that owns it.
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Notification(TimestampMixin, Base):
    """Phase 6 (Master Spec 6.3/6.8).

    license_id is nullable for platform-level notifications, matching the spec.
    Both delivery channels are booleans rather than one enum because a single
    notification can legitimately go to both, or to neither (recorded but not
    delivered).
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"), index=True
    )
    target_chann_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="RESTRICT"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Thai is required, English optional: the product is Thai-first (Phase 5),
    # and a notification with no Thai text would be undisplayable by default.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    message_en: Mapped[str | None] = mapped_column(Text)
    delivery_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    delivery_dashboard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FollowUp(TimestampMixin, Base):
    """Phase 6 (Master Spec 6.3/6.7) — a dated reminder against any entity."""

    __tablename__ = "follow_ups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # DATE, not TIMESTAMPTZ, per spec. Postgres returns a naive date object for
    # this column — comparing it against an aware datetime raises, so the
    # repository works in dates throughout rather than mixing the two.
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Optional clock time, which turns a whole-day reminder into an
    # appointment. NULL keeps the original meaning exactly.
    #
    # TIME without a zone on purpose: a Thai SMB's appointments are in its
    # own local time, and storing UTC would make "14:00" render differently
    # depending on where it is read — the opposite of what someone writing
    # "บ่ายสอง" means.
    due_time: Mapped[time | None] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class Note(TimestampMixin, Base):
    """A dated, attributed note against any entity (Master Spec 6.3).

    ACTION_PERMISSIONS has promised note.create/read/update since Phase 6,
    but no table existed: what there was is a single `notes` TEXT column on
    customers, deals and follow-ups — one blob per record, overwritten on
    every edit, with no author and no history. Those columns still exist and
    still hold their data; new notes are rows here.

    Polymorphic by (entity_type, entity_id) like FollowUp, so a note can
    hang off a customer, a deal or a quote without a table per entity.
    """

    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # The person, not their membership: a note keeps its author even after
    # that person's access is revoked.
    author_chann_uid: Mapped[str | None] = mapped_column(String(32))


class LicenseInvite(TimestampMixin, Base):
    """Phase 6.5 — an Owner-generated code that grants membership.

    Separate from company_code on purpose: an invite makes someone a MEMBER
    (with permissions), while company_code only lets an end customer say which
    shop they are talking about. Conflating them would hand tenant permissions
    to every customer who knew the shop code.
    """

    __tablename__ = "license_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerLicenseLink(Base):
    """Phase 6.5 — an end customer remembering which shop they deal with.

    Deliberately NOT a license_members row: a customer must never inherit the
    tenant's permissions. No TimestampMixin — the link is created once and
    either exists or is removed; there is nothing to update.
    """

    __tablename__ = "customer_license_links"
    __table_args__ = (UniqueConstraint("chann_uid", "license_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    chann_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Product(TimestampMixin, Base):
    """Phase 7 (Master Spec 7.3).

    NOTE ON A SPEC CONTRADICTION: 7.3 marks product_id as globally
    `UNIQUE NOT NULL` and *also* declares `UNIQUE(license_id, product_id)`.
    Those cannot both hold — a global unique would stop two tenants both
    using "P001", which 7.5's test_multi_tenant_product explicitly requires
    ("product_id ซ้ำข้าม tenant ได้"). Only the composite is implemented.

    archived_at exists because 7.5 requires delete to be an archive, not a
    hard delete, but 7.3's column list has nowhere to record that.
    """

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("license_id", "product_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(64))
    # Drives Assignment Rules in Phase 11, so it is indexed even though
    # nothing reads it by category yet.
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    # NUMERIC, never float: money that rounds differently on two machines is
    # a defect people notice on an invoice.
    unit_price: Mapped[object | None] = mapped_column(Numeric(18, 2))
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SalesGroup(TimestampMixin, Base):
    """Phase 7 (Master Spec 7.3)."""

    __tablename__ = "sales_groups"
    __table_args__ = (UniqueConstraint("license_id", "group_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    group_name: Mapped[str] = mapped_column(String(128), nullable=False)


class SalesGroupMember(Base):
    """Join row. No TimestampMixin — membership is added or removed, never edited."""

    __tablename__ = "sales_group_members"
    __table_args__ = (UniqueConstraint("group_id", "member_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False
    )
    # CASCADE here and only here: deleting a group must delete its membership
    # rows, but 7.5 requires it must NOT delete the people themselves — hence
    # RESTRICT on member_id.
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_groups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceTicket(TimestampMixin, Base):
    """Phase 12 (Master Spec 12.3) — a reported fault and the visit to fix it.

    Deliberately no separate work_orders table (12.1). A ticket IS the unit
    of work; splitting the report from the job would mean keeping two rows
    in step for a distinction nobody in an SMB would ever act on.

    `assigned_target_type` + `assigned_to_ref` point at either a member or
    a team, with no foreign key on the ref. Two nullable FKs would permit a
    row pointing at both at once, which means nothing; the pair means
    exactly one, and the repository enforces that.

    customer_name/customer_phone are copied onto the ticket rather than
    read through contact_id. The dispatch gate checks what a technician
    will be told for THIS visit, and a customer record edited next month
    must not retroactively change what was dispatched.
    """

    __tablename__ = "service_tickets"
    __table_args__ = (
        UniqueConstraint("license_id", "ticket_number", name="uq_service_tickets_license_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    ticket_number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_chann_uid: Mapped[str | None] = mapped_column(String(32))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_phone: Mapped[str | None] = mapped_column(String(32))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )
    serial_number: Mapped[str | None] = mapped_column(String(128))
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="public")
    assigned_target_type: Mapped[str | None] = mapped_column(String(32))
    assigned_to_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    accept_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    service_address: Mapped[str | None] = mapped_column(Text)
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    scheduled_time: Mapped[time | None] = mapped_column(Time)
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )


class QuoteProduct(Base):
    """A line on a quote.

    COPIED from the deal when the quote is created, and independent
    afterwards. A quote used to read the deal's products live, which made
    two quotes on one deal necessarily identical and let an edit to the
    deal silently rewrite every draft quote already under discussion.

    The deal records what the customer is buying; each quote records what
    they were OFFERED, which is a different thing and outlives the
    negotiation.
    """

    __tablename__ = "quote_products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quoted_unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text)
    # A quote is read top to bottom, so the order is part of the document
    # rather than whatever the database returns.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Warranty(TimestampMixin, Base):
    """Master Spec 7.5 — a registered product and its cover.

    The row that makes a serial number meaningful. Without it a customer
    reporting a fault has to know which shop they bought from and describe
    the product in prose; with it, one serial identifies the product, the
    shop and the entitlement at once — which is how someone holding a
    broken appliance actually thinks about it.

    product_name is copied alongside product_id because a product record
    can be renamed or archived years after a certificate was issued, and
    the paper in the customer's hand says what it said on the day.
    """

    __tablename__ = "warranties"
    __table_args__ = (
        UniqueConstraint("license_id", "warranty_number", name="uq_warranties_license_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    warranty_number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_chann_uid: Mapped[str | None] = mapped_column(String(32))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT")
    )
    product_name: Mapped[str | None] = mapped_column(String(255))
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False)
    warranty_start: Mapped[date] = mapped_column(Date, nullable=False)
    warranty_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    pdf_path: Mapped[str | None] = mapped_column(String(512))
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generated_documents.id", ondelete="SET NULL")
    )


class UserDisplayPreference(Base):
    """Master Spec 16.3 — how one person wants to be spoken to.

    Keyed on chann_uid, not on a membership: 16.5 requires the preference
    to follow the PERSON across every company they deal with. Someone who
    reads English at one shop reads English at all of them.
    """

    __tablename__ = "user_display_preferences"

    chann_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="CASCADE"),
        primary_key=True,
    )
    date_format: Mapped[str] = mapped_column(String(16), nullable=False, default="dd/mm/yyyy")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="th")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Bangkok")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceReport(TimestampMixin, Base):
    """Phase 13 (Master Spec 13.3) — what the technician found and did.

    `report_data` is JSONB rather than columns because the fields a report
    needs differ by trade: an air-conditioning report and a plumbing one
    share almost nothing, and a column per field would mean a migration
    every time a tenant wanted one more.

    No separate template table — 13.3 requires the generic
    document_templates/document_template_versions from Phase 10 with
    document_type='service_report'. A second versioning model would be the
    same problem solved twice, and the copies would drift.
    """

    __tablename__ = "service_reports"
    __table_args__ = (
        UniqueConstraint("license_id", "report_id", name="uq_service_reports_license_report"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    report_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_tickets.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    technician_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    report_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pdf_path: Mapped[str | None] = mapped_column(String(512))
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generated_documents.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")


class TicketPhoto(Base):
    """Phase 13 (Master Spec 13.3) — evidence from a visit.

    GPS lives on the photo, not the ticket. A check-in and a check-out
    happen in the same place hours apart, and one pair of columns on the
    ticket could record only one of them — which is the one that matters
    when a customer disputes whether anyone turned up.

    Coordinates are Numeric, not float: seven decimals is about a
    centimetre, and float rounding on something that may be evidence is a
    loss nobody can account for later.
    """

    __tablename__ = "ticket_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_tickets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    photo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    photo_type: Mapped[str] = mapped_column(String(32), nullable=False, default="evidence")
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gps_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    gps_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssignmentRule(TimestampMixin, Base):
    """Phase 11 (Master Spec 11.3) — how work is handed to a person.

    `rules_json` is written once, at configuration time, by the AI turning
    a typed policy into structured criteria; the runtime engine only ever
    READS it. That split is the whole point of the phase: an assignment
    that changed depending on what a model felt like today would be
    impossible to explain to the person who did not get the job.

    Superseded rules are kept with is_active=false rather than deleted —
    a rule that assigned work last month is part of why those records look
    the way they do, and the audit trail references it. A partial unique
    index keeps exactly one ACTIVE rule per (license, scope), because two
    would make assignment depend on row order.
    """

    __tablename__ = "assignment_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )


class TechnicianTeam(TimestampMixin, Base):
    """Phase 7 (Master Spec 7.3)."""

    __tablename__ = "technician_teams"
    __table_args__ = (UniqueConstraint("license_id", "team_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    team_name: Mapped[str] = mapped_column(String(128), nullable=False)


class TechnicianTeamMember(Base):
    """Join row. is_lead is not unique per team — 7.5 requires a team to be
    able to have several leads."""

    __tablename__ = "technician_team_members"
    __table_args__ = (UniqueConstraint("team_id", "member_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("technician_teams.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    is_lead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Customer(TimestampMixin, Base):
    """Phase 9 (Master Spec 9.3) — Lead and Contact are the same row, only
    `stage` differs. Splitting them into separate tables would need a
    migration the moment a Lead is promoted, for no real gain: nothing about
    a Contact's shape differs from a Lead's.

    `customer_chann_uid` is nullable, unlike the spec's literal column list —
    a walk-in customer added by chat with just a name and phone number has no
    LINE account at all, and every other customer this project relies on
    knowing something about is far more likely to lack a chann_uid early on
    than to have one. Postgres treats multiple NULLs in a unique constraint
    as distinct, so this does not weaken the "1 customer per chann_uid per
    tenant" rule the spec asks for — it only exempts the walk-in case, which
    the rule was never meant to constrain in the first place.
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("license_id", "customer_chann_uid"),
        UniqueConstraint("license_id", "customer_id", name="uq_customers_license_customer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # The code a person uses to refer to this customer, e.g. C-2026-0001.
    # Deals and quotes always had one; customers did not, which made them
    # the one entity that could be listed but never referred to afterwards.
    #
    # Numbered per license, unlike deal_id which is unique platform-wide.
    # That difference is deliberate: global numbering would give a new
    # tenant's first customer a code like C-2026-0847, which looks broken
    # and quietly discloses how much the whole platform is being used.
    customer_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer_chann_uid: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="RESTRICT"),
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="lead")
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deal(TimestampMixin, Base):
    """Phase 9 (Master Spec 9.3/9.6).

    `deal_id` is per-tenant, matching quotes.quote_id and customers.
    customer_id.

    This is a deliberate, owner-approved departure from the Master Spec,
    which marks deal_id plainly `UNIQUE NOT NULL` with no "แยกต่อบริษัท"
    ("separated per company") qualifier — a qualifier it does give
    quotes.quote_id. An earlier reading treated that difference as
    intentional, on the grounds that a deal code is an internal staff
    reference rather than a customer-facing document number.

    Changed because global numbering means a newly registered tenant's very
    first deal is called something like D-2026-0847: it looks broken to that
    tenant, and it quietly discloses how much the platform as a whole is
    being used. Consistency across the three codes a person actually types
    (C-, D-, Q-) was judged worth more than the one benefit global numbering
    had, which was letting cross-tenant support talk about a deal without
    naming its tenant — still possible via the UUID.
    """

    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("license_id", "deal_id", name="uq_deals_license_deal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    deal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    # Without a date there is no forecast: "how much closes this month" is
    # what a pipeline exists to answer, and a stage alone cannot — a deal
    # sitting in "proposed" for a day and one sitting there for five
    # months look identical.
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    # Free text on purpose. Every shop loses deals for its own reasons,
    # and a fixed list is either wrong for most of them or so generic
    # nobody learns anything from it.
    lost_reason: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DealProduct(Base):
    """Phase 9 (Master Spec 9.3) — a line item quoted on a deal.

    `product_id` is nullable on purpose: 9.3 allows a product outside the
    tenant's own catalogue ("สินค้านอก list"), so `product_name` and
    `quoted_unit_price` are captured directly rather than only ever
    resolved through a Product row.
    """

    __tablename__ = "deal_products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quoted_unit_price: Mapped[object] = mapped_column(Numeric(18, 2), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text)
    # Explicit, because created_at cannot order these: Postgres'
    # now() is fixed for a transaction, so lines added in one message
    # all share a timestamp and sort arbitrarily.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Quote(TimestampMixin, Base):
    """Phase 10 (Master Spec 10.3) — a price quote generated from a Deal.

    `quote_id` IS per-tenant ("Q-YYYY-NNNN แยกต่อบริษัท", explicitly, unlike
    Deal.deal_id which is deliberately global — see that model's
    docstring). A quote is a customer-facing document number, closer in
    kind to an invoice than to an internal staff reference.

    `generated_document_id` is nullable because a quote can exist in
    "draft" status before any PDF has ever been rendered — 10.4's
    authoring/runtime split means document generation is a distinct,
    later step, not something that happens automatically at quote
    creation.
    """

    __tablename__ = "quotes"
    __table_args__ = (UniqueConstraint("license_id", "quote_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    quote_id: Mapped[str] = mapped_column(String(32), nullable=False)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="RESTRICT"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generated_documents.id", ondelete="SET NULL")
    )
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )

    # A quote with no expiry is a price the shop is bound to forever. The
    # "expired" status has existed since Phase 10 with nothing able to set
    # it, because nothing recorded when the offer stopped standing.
    valid_until: Mapped[date | None] = mapped_column(Date)
    # Percent OR amount, never both: "10% and also 500 off" is ambiguous
    # about which applies first, and the order changes the total. A CHECK
    # constraint enforces it too.
    #
    # On the quote rather than across its lines: editing unit prices to
    # represent a negotiation destroys the list price, so afterwards
    # nobody can see what was given away.
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

class DocumentTemplate(TimestampMixin, Base):
    """Phase 10 (Master Spec 10.3) — a named template slot a tenant fills
    with versions over time (e.g. "our standard quote template"). The
    generic shape is deliberately reused across every document type this
    project will ever render (quote now; warranty, service report, PDPA
    export, invoice later) rather than each type inventing its own table —
    10.1 states this explicitly.
    """

    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("license_id", "template_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DocumentTemplateVersion(Base):
    """Phase 10 (Master Spec 10.3/10.4/10.5) — one immutable-once-published
    revision of a template's content.

    `status` follows 10.4's DRAFT -> PREVIEWED -> PUBLISHED -> ARCHIVED
    state machine (repository enforces the transitions and immutability;
    this table only records the current state). `intermediate_model` is
    10.5's provider-neutral abstraction — the stable boundary between AI
    authoring and whatever renderer adapter is used, so a future renderer
    swap never has to re-derive it from the original DOCX.

    No TimestampMixin: `created_at` and `published_at` are both meaningful
    on their own (a version can sit in draft for a long time before ever
    being published, or never), so they're modeled explicitly rather than
    via the mixin's created/updated pair.
    """

    __tablename__ = "document_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_templates.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    source_docx_path: Mapped[str] = mapped_column(String(512), nullable=False)
    intermediate_model: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mapping_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    compiled_template_path: Mapped[str] = mapped_column(String(512), nullable=False)
    renderer: Mapped[str] = mapped_column(String(32), nullable=False, default="smartbrowz")
    renderer_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="html_convert")
    smartbrowz_template_id: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GeneratedDocument(Base):
    """Phase 10 (Master Spec 10.3/10.4) — one deterministic render's audit
    trail: exactly which template version and which data snapshot produced
    this file, so any PDF a customer received can be reproduced or
    verified byte-for-byte later. `sha256` exists specifically so
    "was this the file we actually sent" is answerable without trusting
    GCS metadata alone.

    `source_entity_type`/`source_entity_id` are generic (not a `quote_id`
    FK) for the same reuse reason `DocumentTemplate` is generic — a
    warranty certificate or service report will point at their own source
    rows through the same two columns later.
    """

    __tablename__ = "generated_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    renderer: Mapped[str] = mapped_column(String(32), nullable=False, default="smartbrowz")
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ------------------------------------------------------------ Phase 14
# Approval workflows and satisfaction surveys. Owner decisions (3 Sep):
# the default flow is a single step, the CS who owns the ticket; "ปิดงาน"
# means the last approver passing, and the survey goes out in that same
# moment. See database/alembic/versions/0021_approvals.py.


class ApprovalWorkflow(TimestampMixin, Base):
    __tablename__ = "approval_workflows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="SET NULL")
    )


class ApprovalStep(Base):
    __tablename__ = "approval_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_workflows.id", ondelete="SET NULL")
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_type: Mapped[str] = mapped_column(String(16), nullable=False)
    approver_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    acted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="SET NULL")
    )
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    ai_reasoning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SatisfactionSurvey(Base):
    __tablename__ = "satisfaction_surveys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_tickets.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    scale_config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------- Phase 15
class ChatSession(TimestampMixin, Base):
    """Phase 15 (Master Spec 15.3) — a customer's "คุยกับร้าน" conversation.

    One OPEN conversation per (shop, customer): a second "คุยกับร้าน" while
    one is running joins it rather than starting another, so the shop sees
    one thread per person. Closed ones stay as history.

    `sla_deadline` is set when the customer is waiting (their message is
    the latest) and cleared when the shop answers — so "SLA reset" in the
    spec is literally "no deadline until the customer speaks again".
    `escalated_at` is not in the spec's column list; without it the sweep
    would nag about the same overdue conversation every run.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    customer_chann_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="SET NULL"),
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"),
    )
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    """One line of a Phase 15 conversation. `sender_type` is customer,
    agent, ai or system; `sender_chann_uid` is null for the last two."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_chann_uid: Mapped[str | None] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_en: Mapped[str | None] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# --------------------------------------------------------------- Phase 16.5
class DataSubjectRequest(TimestampMixin, Base):
    """Phase 16.5 (Master Spec 16.5.3) — a person's request over their own
    data: erasure (anonymise everywhere), export (a copy of everything),
    consent withdrawal. Global like the identity it belongs to; each
    tenant touched by processing it gets its own cross-tenant audit row."""

    __tablename__ = "data_subject_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    chann_uid: Mapped[str] = mapped_column(
        String(32), ForeignKey("chann_identities.chann_uid", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    requested_via: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_admins.id", ondelete="SET NULL"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(512))
    result_json: Mapped[dict | None] = mapped_column(JSONB)
