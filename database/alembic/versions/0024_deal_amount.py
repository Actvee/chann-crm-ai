"""Deal amount and currency (user review, 4 Sep 2026).

A deal had no value of its own — the pipeline read it off the quoted line
items, and "มูลค่าดีล 250,000 บาท" typed in chat had nowhere to go. Two
nullable-safe columns: `amount` (what the salesperson expects the deal to
be worth) and `currency` (ISO 4217, THB unless said otherwise).

Revision ID: 0024_deal_amount
Revises: 0023_pdpa
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_deal_amount"
down_revision = "0023_pdpa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("amount", sa.Numeric(18, 2), nullable=True))
    op.add_column(
        "deals",
        sa.Column("currency", sa.String(3), nullable=False, server_default="THB"),
    )


def downgrade() -> None:
    op.drop_column("deals", "currency")
    op.drop_column("deals", "amount")
