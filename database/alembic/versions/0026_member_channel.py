"""Per-OA persona separation (owner, 8 Sep 2026).

license_members gains `channel` — which official account the membership
is for: 'sales' (the Sales/CS OA: owner, admin, cs, custom roles) or
'technician' (the Technician OA). One LINE account is the same
chann_uid on every OA under the provider, and before this a membership
on one OA was read as a membership on the others: the owner who added
the Technician OA was told "already linked". The owner's rule is that
the three OAs are three separate registrations that share only the
person's identity, so a person may hold one row per (license, channel).

Backfill: role 'technician' → channel 'technician', everything else →
'sales'. The old (license_id, chann_uid) unique becomes
(license_id, chann_uid, channel).

Revision ID: 0026_member_channel
Revises: 0025_integrity
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_member_channel"
down_revision = "0025_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "license_members",
        sa.Column("channel", sa.String(16), nullable=False, server_default="sales"),
    )
    op.execute("UPDATE license_members SET channel = 'technician' WHERE role = 'technician'")
    op.create_check_constraint(
        "ck_license_members_channel",
        "license_members",
        "channel IN ('sales', 'technician')",
    )
    op.drop_constraint("uq_license_member", "license_members", type_="unique")
    op.create_unique_constraint(
        "uq_license_member_channel",
        "license_members",
        ["license_id", "chann_uid", "channel"],
    )


def downgrade() -> None:
    # Only safe while nobody holds two rows at one license; the sales row
    # is the one the old model knew about.
    op.execute(
        "DELETE FROM license_members lm USING license_members other "
        "WHERE lm.license_id = other.license_id AND lm.chann_uid = other.chann_uid "
        "AND lm.channel = 'technician' AND other.channel = 'sales'"
    )
    op.drop_constraint("uq_license_member_channel", "license_members", type_="unique")
    op.create_unique_constraint("uq_license_member", "license_members", ["license_id", "chann_uid"])
    op.drop_constraint("ck_license_members_channel", "license_members", type_="check")
    op.drop_column("license_members", "channel")
