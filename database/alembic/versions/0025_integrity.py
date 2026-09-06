"""Admin lockout and webhook-event dedup (review, 6 Sep 2026).

platform_admins gains a failed-attempt counter and a lock timestamp: the
most sensitive credential had neither. line_webhook_events keeps one row
per LINE webhook event id so a redelivered event is dropped instead of
filing a second ticket.

Revision ID: 0025_integrity
Revises: 0024_deal_amount
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_integrity"
down_revision = "0024_deal_amount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_admins",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("platform_admins", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "line_webhook_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("oa", sa.String(16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("line_webhook_events")
    op.drop_column("platform_admins", "locked_until")
    op.drop_column("platform_admins", "failed_attempts")
