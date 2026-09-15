"""Subscription expiry for every status (round 18).

The product is sold as a subscription, so the deadline on a license is no
longer a trial-only thing: `trial_expires_at` becomes `expires_at` and the
sweep reads it for trial AND active tenants. Two platform-only columns
ride along: `deleted_at` marks a soft-deleted company (status "deleted",
members removed, hidden from the console's default list, reversible until
purged) and `admin_notes` is the operator's own scratch text about a
tenant, never shown to the tenant.

Revision ID: 0028_subscription_expiry
Revises: 0027_webhook_delivery
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_subscription_expiry"
down_revision = "0027_webhook_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("licenses", "trial_expires_at", new_column_name="expires_at")
    op.add_column("licenses", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("licenses", sa.Column("admin_notes", sa.Text(), nullable=True))
    # "deleted" is a status now (soft delete from the platform console); the
    # CHECK from 0005 named only the three the trial flow knew.
    op.drop_constraint("ck_licenses_status", "licenses", type_="check")
    op.create_check_constraint(
        "ck_licenses_status", "licenses",
        "status IN ('trial', 'active', 'suspended', 'deleted')",
    )


def downgrade() -> None:
    op.execute("UPDATE licenses SET status = 'suspended' WHERE status = 'deleted'")
    op.drop_constraint("ck_licenses_status", "licenses", type_="check")
    op.create_check_constraint(
        "ck_licenses_status", "licenses", "status IN ('trial', 'active', 'suspended')",
    )
    op.drop_column("licenses", "admin_notes")
    op.drop_column("licenses", "deleted_at")
    op.alter_column("licenses", "expires_at", new_column_name="trial_expires_at")
