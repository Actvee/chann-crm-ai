"""Warranty: the purchase date is optional, and the period comes from the product.

Owner (tester note, 16 ก.ย. 2569): registering a sold unit should not
demand the purchase date — without one there is no end date yet — and
each product carries its own default warranty period, used whenever a
unit of it is registered (by the shop, from the dashboard, or by the
customer claiming it).

Revision ID: 0030_warranty_purchase_optional
Revises: 0029_member_last_assigned
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_warranty_purchase_optional"
down_revision = "0029_member_last_assigned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("warranty_months", sa.Integer(), nullable=True))
    op.alter_column("warranties", "warranty_start", existing_type=sa.Date(), nullable=True)
    op.alter_column("warranties", "warranty_end", existing_type=sa.Date(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE warranties SET warranty_start = created_at::date WHERE warranty_start IS NULL")
    op.execute("UPDATE warranties SET warranty_end = warranty_start + INTERVAL '12 months' WHERE warranty_end IS NULL")
    op.alter_column("warranties", "warranty_end", existing_type=sa.Date(), nullable=False)
    op.alter_column("warranties", "warranty_start", existing_type=sa.Date(), nullable=False)
    op.drop_column("products", "warranty_months")
