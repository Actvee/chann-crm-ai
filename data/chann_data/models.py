"""ORM schema through Phase 2.

Each phase has its own Alembic revision and migration gate. Phase 2 adds
tenant-owned roles, permission grants, settings and the two-party ownership
transfer state required by the product flow.

Columns marked "placed early" are required by a later phase but are created
now because the Master Spec explicitly says so.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
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
