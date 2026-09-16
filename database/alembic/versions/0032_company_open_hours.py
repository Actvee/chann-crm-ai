"""The shop's opening hours, so "ร้านเปิดกี่โมง" has an answer.

Tester, 16 ก.ย. 2569: a customer asked three times — when the shop opens,
where it is, what its number is — and got the same "type your message and
the shop will get back to you" each time. The address and the phone were
merely empty; the hours had nowhere to be stored at all.

Free text (120 chars), because a shop says "จ-ส 9:00-18:00" or "ทุกวัน
8 โมง-2 ทุ่ม" and neither fits a schedule grid.

Revision ID: 0032_company_open_hours
Revises: 0031_ticket_photo_caption
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_company_open_hours"
down_revision = "0031_ticket_photo_caption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("licenses", sa.Column("open_hours", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("licenses", "open_hours")
