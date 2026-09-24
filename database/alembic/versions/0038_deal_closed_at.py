"""A deal records when it closed.

Owner, 23 ก.ย. 2569: "ยอดปิดสำเร็จเดือนนี้ เทียบเดือนที่แล้ว" — which the
database could not answer, because the only dates a deal had were the day
it was created and the day someone GUESSED it would close. Reports built
on the forecast put a September win in July.

Rows closed before this column existed are backfilled from `updated_at`:
the closest honest approximation there is, and the report says so in words
rather than pretending it is a real close date.

Revision ID: 0038_deal_closed_at
Revises: 0037_api_keys
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_deal_closed_at"
down_revision = "0037_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    # (license_id, closed_at): every report that reads it is one shop's
    # month, never every shop's.
    op.create_index("ix_deals_closed_at", "deals", ["license_id", "closed_at"])
    op.execute(
        "UPDATE deals SET closed_at = updated_at "
        "WHERE stage IN ('won', 'lost') AND closed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_deals_closed_at", table_name="deals")
    op.drop_column("deals", "closed_at")
