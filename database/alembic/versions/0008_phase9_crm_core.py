"""Phase 9 — CRM core: customers (lead/contact), deals, deal_products.

Revision ID: 0008_phase9_crm_core
Revises: 0007_phase8_profiles
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_phase9_crm_core"
down_revision = "0007_phase8_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "customer_chann_uid", sa.String(32),
            sa.ForeignKey("chann_identities.chann_uid", ondelete="RESTRICT"),
        ),
        sa.Column("stage", sa.String(32), nullable=False, server_default="lead"),
        sa.Column(
            "owner_member_id", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"),
        ),
        sa.Column("first_name", sa.String(255)),
        sa.Column("last_name", sa.String(255)),
        sa.Column("phone", sa.String(32)),
        sa.Column("email", sa.String(255)),
        sa.Column("address", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("license_id", "customer_chann_uid"),
    )
    op.create_index("ix_customers_license_id", "customers", ["license_id"])

    op.create_table(
        "deals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("deal_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "contact_id", UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("stage", sa.String(32), nullable=False, server_default="new"),
        sa.Column(
            "owner_member_id", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"),
        ),
        sa.Column("notes", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_deals_license_id", "deals", ["license_id"])
    op.create_index("ix_deals_contact_id", "deals", ["contact_id"])

    op.create_table(
        "deal_products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deal_id", UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "product_id", UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
        ),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("quoted_unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_deal_products_deal_id", "deal_products", ["deal_id"])


def downgrade() -> None:
    op.drop_index("ix_deal_products_deal_id", table_name="deal_products")
    op.drop_table("deal_products")
    op.drop_index("ix_deals_contact_id", table_name="deals")
    op.drop_index("ix_deals_license_id", table_name="deals")
    op.drop_table("deals")
    op.drop_index("ix_customers_license_id", table_name="customers")
    op.drop_table("customers")
