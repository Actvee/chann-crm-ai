"""A line of a conversation may be a picture.

Owner, 21 ก.ย. 2569: "เพิ่มเติมฟีเจอร์ส่งรูปให้ลูกค้าได้ไหม" — the shop
answers a customer with a photo (a price list, the part they mean, the
spot on the unit) the way it does with words. The bytes go to the same
object store the job photos use; the row keeps the stored path, and the
text column carries the caption, or nothing.

Revision ID: 0034_chat_message_images
Revises: 0033_names_from_identity
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_chat_message_images"
down_revision = "0033_names_from_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("image_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "image_path")
