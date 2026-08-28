"""Phase 10 — company identity fields required on customer-facing documents.

Revision ID: 0010_phase10_company_identity
Revises: 0009_phase10_quotes_templates

A Thai quote/invoice must show the issuing company's legal identity —
name, address, tax ID — and `licenses` carried none of it. `company_name`
existed, but it is the shop's display name used in chat, not necessarily
the registered entity name that belongs on a tax document, so a separate
`legal_name` is added rather than overloading it (nullable: it falls back
to `company_name` when a tenant has not supplied one).

All columns are nullable. Existing tenants predate this migration and
cannot be backfilled with information the platform never collected; the
document-rendering layer is responsible for refusing to render — never
for inventing a blank or placeholder tax ID — when a tenant has not
filled these in. That refusal belongs in application logic, not in a NOT
NULL constraint that would break every existing row.

`vat_rate` is per-tenant and nullable rather than a hardcoded 7%: not
every Thai SMB is VAT-registered, and "not registered" (NULL, no VAT
line on the document at all) is a genuinely different state from "0%".
Numeric(5,4) stores it as a fraction (0.0700 = 7%), so the stored value
never depends on how a template chooses to format it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_phase10_company_identity"
down_revision = "0009_phase10_quotes_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("licenses", sa.Column("legal_name", sa.String(255)))
    op.add_column("licenses", sa.Column("tax_id", sa.String(13)))
    op.add_column("licenses", sa.Column("company_address", sa.Text()))
    op.add_column("licenses", sa.Column("company_phone", sa.String(32)))
    op.add_column("licenses", sa.Column("company_email", sa.String(255)))
    op.add_column("licenses", sa.Column("vat_rate", sa.Numeric(5, 4)))


def downgrade() -> None:
    op.drop_column("licenses", "vat_rate")
    op.drop_column("licenses", "company_email")
    op.drop_column("licenses", "company_phone")
    op.drop_column("licenses", "company_address")
    op.drop_column("licenses", "tax_id")
    op.drop_column("licenses", "legal_name")
