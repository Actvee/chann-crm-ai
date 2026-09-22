"""Invoices and receipts after the quotation.

Owner, 21 ก.ย. 2569: "ทำข้อ 2 … รวมเอาเรื่อง invoice" — item 2 of the gap
list: ใบแจ้งหนี้/ใบเสร็จหลังใบเสนอราคา + สถานะชำระ (มัดจำ/จ่ายแล้ว/ค้าง).
The quotation was the last document this system could produce; nothing
recorded what the customer owes after they say yes, what they have paid
(a deposit, the balance) and what is overdue.

Two tables. `invoices` is the demand: numbered per tenant like the quote
(INV-YYYY-NNNN), with the money columns copied from a frozen snapshot so
the PDF and every report agree to the satang, and `paid_amount` cached
from the payments so a list does not join them. `invoice_payments` is
the ledger: one row per receipt of money, never edited.

"Overdue" is deliberately not a column — it is `due_date < today` on an
issued or partially paid invoice, decided when read, so it needs no
nightly sweep and is never a day late.

Revision ID: 0035_invoices
Revises: 0034_chat_message_images
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0035_invoices"
down_revision = "0034_chat_message_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("invoice_id", sa.String(32), nullable=False),
        # SET NULL: a superseded quotation can go; the bill it produced
        # cannot. RESTRICT on the deal and the customer: a paid invoice
        # must never point at a customer who no longer exists.
        sa.Column(
            "quote_id", UUID(as_uuid=True),
            sa.ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "deal_id", UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column(
            "contact_id", UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="THB"),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        # A fraction (0.0700), or NULL for a shop that is not VAT-registered.
        sa.Column("vat_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("vat_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("paid_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("data_snapshot", JSONB, nullable=True),
        sa.Column(
            "generated_document_id", UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "receipt_document_id", UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("license_id", "invoice_id", name="uq_invoices_license_invoice_id"),
        sa.CheckConstraint(
            "status IN ('draft', 'issued', 'partially_paid', 'paid', 'void')",
            name="ck_invoices_status",
        ),
        sa.CheckConstraint("paid_amount >= 0", name="ck_invoices_paid_amount_non_negative"),
    )
    op.create_index("ix_invoices_license_id", "invoices", ["license_id"])
    op.create_index("ix_invoices_license_status", "invoices", ["license_id", "status"])
    op.create_index("ix_invoices_contact_id", "invoices", ["contact_id"])
    op.create_index("ix_invoices_quote_id", "invoices", ["quote_id"])

    op.create_table(
        "invoice_payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "invoice_id", UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("method", sa.String(16), nullable=False, server_default="transfer"),
        sa.Column(
            "paid_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("reference", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        # A payment of nothing, or of less than nothing, is not a payment.
        # Corrections are recorded as the shop's own words on a new
        # invoice, never as a negative line.
        sa.CheckConstraint("amount > 0", name="ck_invoice_payments_amount_positive"),
        sa.CheckConstraint(
            "method IN ('cash', 'transfer', 'promptpay', 'card', 'other')",
            name="ck_invoice_payments_method",
        ),
    )
    op.create_index("ix_invoice_payments_license_id", "invoice_payments", ["license_id"])
    op.create_index(
        "ix_invoice_payments_invoice_paid_at", "invoice_payments", ["invoice_id", "paid_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_invoice_paid_at", table_name="invoice_payments")
    op.drop_index("ix_invoice_payments_license_id", table_name="invoice_payments")
    op.drop_table("invoice_payments")
    op.drop_index("ix_invoices_quote_id", table_name="invoices")
    op.drop_index("ix_invoices_contact_id", table_name="invoices")
    op.drop_index("ix_invoices_license_status", table_name="invoices")
    op.drop_index("ix_invoices_license_id", table_name="invoices")
    op.drop_table("invoices")
