"""Phase 9/10 — a human-facing customer code.

Revision ID: 0011_customer_code
Revises: 0010_phase10_company_identity

`deals` have D-2026-0001 and `quotes` have Q-2026-0001, but `customers`
had only a UUID. Every chat command addresses a record by its code, so
customers were the one entity that could be listed but not then referred
to: a list row's "view" button had nothing to put in the message and sent
the literal string "None".

Backfilled in the same migration rather than left nullable-and-empty. A
code that exists for some rows and not others is worse than no code,
because the UI cannot tell which case it is in. Numbering restarts per
license (the code is only ever shown inside one tenant) and is ordered by
creation, so the oldest customer in each tenant is 0001.

The unique constraint is on (license_id, customer_id): two tenants both
having a C-2026-0001 is correct and expected.
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_customer_code"
down_revision = "0010_phase10_company_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("customer_id", sa.String(32)))

    # Backfill with a window function so each tenant numbers from 1
    # independently, in creation order. Done in SQL rather than Python
    # because it has to hold for however many rows exist, in one statement,
    # inside the migration's transaction.
    op.execute(
        """
        UPDATE customers AS c
        SET customer_id = numbered.code
        FROM (
            SELECT
                id,
                'C-' || to_char(created_at, 'YYYY') || '-' ||
                lpad(
                    row_number() OVER (
                        PARTITION BY license_id, to_char(created_at, 'YYYY')
                        ORDER BY created_at, id
                    )::text,
                    4, '0'
                ) AS code
            FROM customers
        ) AS numbered
        WHERE c.id = numbered.id
        """
    )

    op.alter_column("customers", "customer_id", nullable=False)
    op.create_unique_constraint(
        "uq_customers_license_customer_id", "customers", ["license_id", "customer_id"],
    )
    op.create_index("ix_customers_customer_id", "customers", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customers_customer_id", table_name="customers")
    op.drop_constraint("uq_customers_license_customer_id", "customers", type_="unique")
    op.drop_column("customers", "customer_id")
