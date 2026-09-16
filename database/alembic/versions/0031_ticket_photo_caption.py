"""A ticket photo carries a name, so a list of them reads as more than "รูปที่ 3".

Owner, 16 ก.ย. 2569: "ในหน้า ticket ของ tech oa พอกดแนบรูปแล้วน่าจะมีขึ้น
โชว์เป็นรายการชื่อให้หน่อยว่าแนบรูปอะไรไปบ้าง … เพื่อลบออกเพิ่มใหม่หรือแก้ไข
รายละเอียด". The dashboard sends the file's own name when a picture is
attached from the phone; anyone may replace it with words that mean
something ("ก่อนซ่อม", "คอมที่รั่ว").

Revision ID: 0031_ticket_photo_caption
Revises: 0030_warranty_purchase_optional
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_ticket_photo_caption"
down_revision = "0030_warranty_purchase_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_photos", sa.Column("caption", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("ticket_photos", "caption")
