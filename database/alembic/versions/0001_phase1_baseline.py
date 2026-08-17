"""Phase 1 baseline — Architecture & Security Foundation

Creates the four Phase 1 tables. Later phases add their own revisions rather
than extending this one, so each phase has an independently testable
migration gate.

No compatibility with the old Chann1 schema is preserved: this is a
greenfield application (00_START_HERE.md).

Revision ID: 0001_phase1_baseline
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_phase1_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for role_prefix in ("c", "s", "t"):
        op.execute(sa.schema.CreateSequence(sa.Sequence(f"chann_identity_{role_prefix}_seq")))
    op.create_table(
        "chann_identities",
        sa.Column("chann_uid", sa.String(32), primary_key=True),
        sa.Column("line_user_id", sa.String(128), nullable=False),
        sa.Column("primary_role", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255)),
        # placed early on purpose — consumed by Phase 13 service reports
        sa.Column("signature_url", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_identity_line_user", "chann_identities", ["line_user_id"])
    op.create_index("ix_identity_line_user", "chann_identities", ["line_user_id"])
    op.create_table(
        "platform_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_platform_admin_username", "platform_admins", ["username"])

    op.create_table(
        "licenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_code", sa.String(32), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        # placed early on purpose — consumed by Phase 16 cross-company routing
        sa.Column("auto_accept_new_customers", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_license_code", "licenses", ["license_code"])

    op.create_table(
        "license_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chann_uid", sa.String(32), nullable=False),
        sa.Column("role", sa.String(64), nullable=False, server_default="member"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_foreign_key(
        "fk_member_license", "license_members", "licenses", ["license_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_member_identity", "license_members", "chann_identities",
        ["chann_uid"], ["chann_uid"], ondelete="CASCADE",
    )
    # The isolation guarantee leans on this: one membership row per
    # (tenant, identity), so a duplicate cannot create a second role path.
    op.create_unique_constraint("uq_license_member", "license_members", ["license_id", "chann_uid"])
    op.create_index("ix_member_license", "license_members", ["license_id"])
    op.create_index("ix_member_chann_uid", "license_members", ["chann_uid"])


def downgrade() -> None:
    op.drop_table("license_members")
    op.drop_table("licenses")
    op.drop_table("platform_admins")
    op.drop_table("chann_identities")
    for role_prefix in ("c", "s", "t"):
        op.execute(sa.schema.DropSequence(sa.Sequence(f"chann_identity_{role_prefix}_seq")))
