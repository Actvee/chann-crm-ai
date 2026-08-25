"""ORM schema through Phase 2.

Each phase has its own Alembic revision and migration gate. Phase 2 adds
tenant-owned roles, permission grants, settings and the two-party ownership
transfer state required by the product flow.

Columns marked "placed early" are required by a later phase but are created
now because the Master Spec explicitly says so.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("license_members.id", ondelete="RESTRICT")
    )
    notes: Mapped[str | None] = mapped_column(Text)


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
