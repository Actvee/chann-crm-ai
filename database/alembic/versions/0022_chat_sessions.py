"""Phase 15 — live chat: chat_sessions and chat_messages.

Master Spec 15.3, plus one column the spec's list lacks: `escalated_at`
on the session, so the SLA sweep tells the shop once per overdue
conversation instead of on every run.

Revision ID: 0022_chat_sessions
Revises: 0021_approvals
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_chat_sessions"
down_revision = "0021_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_chann_uid", sa.String(32),
                  sa.ForeignKey("chann_identities.chann_uid", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("license_members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_sessions_license_id", "chat_sessions", ["license_id"])
    op.create_index("ix_chat_sessions_customer_chann_uid", "chat_sessions", ["customer_chann_uid"])
    op.create_index("ix_chat_sessions_license_status", "chat_sessions", ["license_id", "status"])
    # One LIVE conversation per (shop, customer). "คุยกับร้าน" twice joins
    # the one that is running; the shop never sees two threads for one
    # person. Closed and timed-out ones are history and may repeat.
    op.create_index(
        "ix_chat_sessions_live_one", "chat_sessions", ["license_id", "customer_chann_uid"],
        unique=True, postgresql_where=sa.text("status IN ('open', 'assigned')"),
    )
    # The sweeps read across tenants by time; without these two the sweep
    # would scan every conversation ever held to find the few that are late.
    op.create_index("ix_chat_sessions_sla_deadline", "chat_sessions", ["sla_deadline"])
    op.create_index("ix_chat_sessions_timeout_at", "chat_sessions", ["timeout_at"])

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("license_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_type", sa.String(16), nullable=False),
        sa.Column("sender_chann_uid", sa.String(32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_en", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_license_id", "chat_messages", ["license_id"])
    op.create_index(
        "ix_chat_messages_session_created", "chat_messages", ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_license_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_timeout_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_sla_deadline", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_live_one", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_license_status", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_customer_chann_uid", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_license_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
