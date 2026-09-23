"""API keys for outside systems (round 21B).

Owner, 23 ก.ย. 2569: "ทำ API เลย … การ authori เอาแค่ให้เจ้าของร้าน
generate api code ให้คนภายนอกสำหรับใช้ Api ก็พอแล้ว". One table: the key's
hash (the lookup), a display prefix, who made it, when it was last used
and when it was revoked. No permission list — a key acts as the shop's
staff, and the exposed surface is bounded by the ext router itself.

Revision ID: 0037_api_keys
Revises: 0036_warranty_contacts
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0037_api_keys"
down_revision = "0036_warranty_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_by_chann_uid", sa.String(32)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_license_id", "api_keys", ["license_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_license_id", table_name="api_keys")
    op.drop_table("api_keys")
