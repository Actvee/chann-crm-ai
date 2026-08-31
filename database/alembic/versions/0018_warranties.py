"""Warranties (7.5) and personal display preferences (16.3).

Revision ID: 0018_warranties
Revises: 0017_audit_actions

The warranty is what makes Phase 16 possible and what makes the customer
flow make sense. Today a customer reporting a fault has to know which shop
they are talking to and describe their product in prose; with a warranty
row, a serial number identifies the product, the shop AND the entitlement
in one step — which is how someone with a broken appliance actually thinks
about it.

`serial_number` is indexed WITHOUT license_id on purpose. Every other index
in this schema is tenant-scoped, and deliberately so; this one supports
16.4's cross-company lookup, where the whole question is "which tenant does
this serial belong to". The lookup is audited with cross_tenant=true, and
the answer it returns is a tenant id, never another tenant's data.

Serials are unique per tenant, not globally: two manufacturers can and do
issue the same serial, and a global constraint would make one shop's
registration block another's.

`user_display_preferences` is keyed on chann_uid rather than on a
membership, because 16.5 requires the preference to follow the PERSON
across every company they deal with. Someone who reads English at one shop
reads English at all of them.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0018_warranties"
down_revision = "0017_audit_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warranties",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column("warranty_number", sa.String(32), nullable=False),
        sa.Column("customer_chann_uid", sa.String(32), nullable=True),
        sa.Column(
            "contact_id", UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "product_id", UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=True,
        ),
        # Kept alongside product_id: a product record can be renamed or
        # archived years after a warranty was issued, and the certificate
        # in the customer's hand says what it said on the day.
        sa.Column("product_name", sa.String(255), nullable=True),
        sa.Column("serial_number", sa.String(128), nullable=False),
        sa.Column("warranty_start", sa.Date(), nullable=False),
        sa.Column("warranty_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("pdf_path", sa.String(512), nullable=True),
        sa.Column(
            "generated_document_id", UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_warranties_license_number", "warranties", ["license_id", "warranty_number"],
    )
    # One live registration per serial per tenant: registering the same
    # unit twice is a mistake, not a second warranty.
    op.create_index(
        "uq_warranties_license_serial_active", "warranties",
        ["license_id", "serial_number"],
        unique=True, postgresql_where=sa.text("status <> 'void'"),
    )
    # NOT tenant-scoped, and the only such index in this schema. 16.4's
    # question is precisely "which tenant owns this serial", and answering
    # it needs an index that spans them.
    op.create_index("ix_warranties_serial", "warranties", ["serial_number"])

    op.create_table(
        "user_display_preferences",
        sa.Column(
            "chann_uid", sa.String(32),
            sa.ForeignKey("chann_identities.chann_uid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("date_format", sa.String(16), nullable=False, server_default="dd/mm/yyyy"),
        sa.Column("language", sa.String(8), nullable=False, server_default="th"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Bangkok"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_display_preferences")
    op.drop_index("ix_warranties_serial", table_name="warranties")
    op.drop_index("uq_warranties_license_serial_active", table_name="warranties")
    op.drop_constraint("uq_warranties_license_number", "warranties", type_="unique")
    op.drop_table("warranties")
