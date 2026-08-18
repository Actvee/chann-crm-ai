"""Phase 2 — permission matrix, license settings and owner transfer.

Revision ID: 0002_phase2_permissions
Revises: 0001_phase1_baseline
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_phase2_permissions"
down_revision = "0001_phase1_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_name", sa.String(64), nullable=False),
        sa.Column("is_owner", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("license_id", "role_name", name="uq_custom_role_license_name"),
    )
    op.create_index("ix_custom_roles_license_id", "custom_roles", ["license_id"])

    op.create_table(
        "role_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("permission_key", sa.String(128), nullable=False),
        sa.Column("allowed", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["license_id", "role"],
            ["custom_roles.license_id", "custom_roles.role_name"],
            name="fk_role_permission_custom_role",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.UniqueConstraint(
            "license_id", "role", "permission_key", name="uq_role_permission_grant"
        ),
    )
    op.create_index("ix_role_permissions_license_id", "role_permissions", ["license_id"])

    op.create_table(
        "license_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("setting_key", sa.String(128), nullable=False),
        sa.Column("setting_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("license_id", "setting_key", name="uq_license_setting_key"),
    )
    op.create_index("ix_license_settings_license_id", "license_settings", ["license_id"])

    op.create_table(
        "ownership_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["from_member_id"], ["license_members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["to_member_id"], ["license_members.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("from_member_id <> to_member_id", name="ck_owner_transfer_distinct_members"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'cancelled')",
            name="ck_owner_transfer_status",
        ),
    )
    op.create_index("ix_ownership_transfers_license_id", "ownership_transfers", ["license_id"])
    op.create_index(
        "uq_ownership_transfer_pending_per_license",
        "ownership_transfers",
        ["license_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("ownership_transfers")
    op.drop_table("license_settings")
    op.drop_table("role_permissions")
    op.drop_table("custom_roles")
