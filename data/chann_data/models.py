"""Phase 1 schema — Architecture & Security Foundation.

Only the four Phase 1 tables live here. Later phases add their own tables in
their own Alembic revisions, so every phase gets its own migration gate
(see the Phase 1 Readiness Plan, 10.8).

Columns marked "placed early" are required by a later phase but are created
now because the Master Spec explicitly says so.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
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
