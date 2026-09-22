"""Whoever holds a registered unit is a customer, not a lead.

Owner, 22 ก.ย. 2569: "ลูกค้าที่เข้ามาผ่านการลงทะเบียนรับประกันสินค้า ควรจะ
กลายเป็นลูกค้าเลย ไม่ใช่ลูกค้ามุ่งหวัง". From round 20Z a claim promotes
the person's record (and creates one when the shop has none); this does
the same once for the rows already registered, and fills in the unit →
customer link that a claim never wrote.

Only leads are promoted and only NULL links are filled: a stage or a
contact the shop set by hand is the shop's.

Revision ID: 0036_warranty_contacts
Revises: 0035_invoices
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_warranty_contacts"
down_revision = "0035_invoices"
branch_labels = None
depends_on = None

#: Shared with the test that proves the statements on a real database.
PROMOTE_SQL = """
UPDATE customers AS c
SET stage = 'contact'
FROM warranties AS w
WHERE c.stage = 'lead'
  AND c.archived_at IS NULL
  AND w.license_id = c.license_id
  AND w.status <> 'void'
  AND (w.contact_id = c.id OR w.customer_chann_uid = c.customer_chann_uid)
"""

LINK_SQL = """
UPDATE warranties AS w
SET contact_id = c.id
FROM customers AS c
WHERE w.contact_id IS NULL
  AND w.customer_chann_uid IS NOT NULL
  AND c.license_id = w.license_id
  AND c.customer_chann_uid = w.customer_chann_uid
"""


def upgrade() -> None:
    op.execute(sa.text(PROMOTE_SQL))
    op.execute(sa.text(LINK_SQL))


def downgrade() -> None:
    # A stage and a link are facts about the shop's customers, not a
    # schema change: putting them back would guess at what was a lead.
    pass
