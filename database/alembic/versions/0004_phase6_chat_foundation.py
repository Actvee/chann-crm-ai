"""Phase 6 — chat foundation, notifications, follow-ups.

Revision ID: 0004_phase6_chat_foundation
Revises: 0003_phase3_audit_log
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_phase6_chat_foundation"
down_revision = "0003_phase3_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "line_message_entity_map",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # LINE message IDs are globally unique — the UNIQUE is platform-wide,
        # not per-tenant. Tenant ownership is still enforced on lookup.
        sa.Column("message_id", sa.String(64), nullable=False, unique=True),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_line_message_entity_map_license", "line_message_entity_map", ["license_id"]
    )
    op.create_index(
        "ix_line_message_entity_map_entity",
        "line_message_entity_map",
        ["entity_type", "entity_id"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Nullable: platform-level notifications belong to no single tenant.
        sa.Column("license_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_chann_uid", sa.String(32), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64)),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("message_en", sa.Text()),
        sa.Column("delivery_line", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("delivery_dashboard", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["target_chann_uid"], ["chann_identities.chann_uid"], ondelete="RESTRICT"
        ),
    )
    # The unread badge polls constantly (6.8), so the partial index covers
    # exactly that query and stays small — read notifications drop out of it.
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["target_chann_uid", "license_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_target_created",
        "notifications",
        ["target_chann_uid", sa.text("created_at DESC")],
    )

    op.create_table(
        "follow_ups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("owner_member_id", postgresql.UUID(as_uuid=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["owner_member_id"], ["license_members.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled')",
            name="ck_follow_ups_status",
        ),
    )
    # The due-scan (6.7) reads pending rows by date; keeping it partial means
    # completed and cancelled history never slows that sweep down.
    op.create_index(
        "ix_follow_ups_due_pending",
        "follow_ups",
        ["license_id", "due_date"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_follow_ups_entity", "follow_ups", ["entity_type", "entity_id"]
    )


def downgrade() -> None:
    op.drop_table("follow_ups")
    op.drop_table("notifications")
    op.drop_table("line_message_entity_map")
