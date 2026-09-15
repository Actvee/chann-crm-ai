"""Round-robin needs a memory (round 18, 14 Sep 2026).

`assignment_engine.order_candidates` sorts round_robin candidates by
`last_assigned_at`, a key no candidate builder ever supplied, so the
"take turns" strategy handed every job to the lowest member id. The
engine endpoint now stamps the member it picks, and the repository
returns the stamp with each candidate.

Revision ID: 0029_member_last_assigned
Revises: 0028_subscription_expiry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_member_last_assigned"
down_revision = "0028_subscription_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "license_members",
        sa.Column("last_assigned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("license_members", "last_assigned_at")
