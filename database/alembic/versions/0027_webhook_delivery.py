"""Durable processing state for a LINE webhook event (review v3, T01).

The table recorded only that an event id had been SEEN, and the row was
written before the message was understood and before the answer was sent.
So a first delivery that failed — identity lookup down, or the reply
refused by LINE — was never recovered: LINE's redelivery found the id
already present and was dropped, acknowledged 200, with the work either
never done or done but never answered.

An event now carries what happened to it. `processing` is a claim held
under a lease, `handled` means the business effect is complete and the
answer is waiting in `reply`, `done` means the person has it. A failed
attempt deletes its own row so a redelivery can claim the event again;
the lease covers the process that dies without getting that far.

Revision ID: 0027_webhook_delivery
Revises: 0026_member_channel
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_webhook_delivery"
down_revision = "0026_member_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "line_webhook_events",
        # Existing rows are events that were seen and, as far as anything
        # knows, answered: the old code only ever wrote the row on the way
        # past. "done" is the honest reading of them, and it is also the
        # safe one — a redelivery of an old event is dropped exactly as it
        # was before this migration.
        sa.Column("status", sa.String(16), nullable=False, server_default="done"),
    )
    op.add_column(
        "line_webhook_events",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "line_webhook_events",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "line_webhook_events",
        # The answer that was built but not delivered: LINE's messages,
        # who they are for, and the message-entity bookkeeping the reply
        # would have done. Only ever set while status = 'handled'.
        sa.Column("reply", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # The sweep an operator runs to find work that stalled ("what has been
    # sitting in handled for an hour?") reads by status and by age.
    op.create_index(
        "ix_line_webhook_events_status",
        "line_webhook_events", ["status", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_line_webhook_events_status", table_name="line_webhook_events")
    op.drop_column("line_webhook_events", "reply")
    op.drop_column("line_webhook_events", "attempts")
    op.drop_column("line_webhook_events", "claimed_at")
    op.drop_column("line_webhook_events", "status")
