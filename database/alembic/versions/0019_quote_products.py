"""Quotes get their own line items.

Revision ID: 0019_quote_products
Revises: 0018_warranties

A quote used to be a pointer at a deal, and its contents were whatever the
deal happened to contain when someone looked. That worked until a customer
changed their mind, which is the normal case rather than the exception:

* Two quotes on one deal were necessarily identical, so "here is the
  three-item version and here is the two-item one" was impossible.
* Editing the deal silently rewrote every draft quote already sent for
  discussion.
* There was no way to discount a single quote without discounting the
  deal, and therefore every other quote from it.

Line items are COPIED from the deal when the quote is created, and the two
are independent afterwards. The deal stays the record of what the customer
is buying; each quote is a record of what they were offered, which is a
different thing and outlives the negotiation.

Existing quotes are backfilled from their deal's current products. That is
the best available reconstruction — it is what those quotes were already
displaying — and leaving them empty would make them un-issuable under the
"a quote needs at least one line" rule.
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0019_quote_products"
down_revision = "0018_warranties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column(
            "quote_id", UUID(as_uuid=True),
            sa.ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        # Nullable, like deal_products: a one-off line ("ค่าติดตั้ง") is a
        # legitimate thing to quote and does not belong in the catalogue.
        sa.Column(
            "product_id", UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("quoted_unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text(), nullable=True),
        # Explicit ordering: a quote is a document a person reads top to
        # bottom, and "whatever order the database returns" is not an
        # order anyone chose.
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_quote_products_quote_position", "quote_products", ["quote_id", "position"],
    )

    # Backfill from each quote's deal — what those quotes were already
    # showing. Without it every existing quote becomes empty, and an empty
    # quote cannot be issued.
    #
    # Ids are generated in Python rather than with gen_random_uuid(),
    # which is only built in from Postgres 13 and otherwise needs the
    # pgcrypto extension. The Cloud SQL instance is read through a
    # Terraform data source, so its version is not visible from this
    # repository — and a migration that fails on the real database
    # because of an assumption is not worth the shorter SQL.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT
                q.license_id, q.id AS quote_id, dp.product_id, dp.product_name,
                dp.quoted_unit_price, dp.qty, dp.notes,
                row_number() OVER (
                    PARTITION BY q.id ORDER BY dp.created_at, dp.id
                ) - 1 AS position
            FROM quotes q
            JOIN deal_products dp ON dp.deal_id = q.deal_id
            """
        )
    ).mappings().all()

    if rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO quote_products (
                    id, license_id, quote_id, product_id, product_name,
                    quoted_unit_price, qty, notes, position, created_at
                ) VALUES (
                    :id, :license_id, :quote_id, :product_id, :product_name,
                    :quoted_unit_price, :qty, :notes, :position, now()
                )
                """
            ),
            [{"id": uuid.uuid4(), **dict(row)} for row in rows],
        )


    # deal_products needs the same thing, for the same reason. Its reads
    # ordered on created_at alone, and server_default=now() returns ONE
    # value for a whole transaction — so every line added in a single
    # message shares a timestamp and the order came out differently on
    # each read. A quote copied from that deal inherited whichever order
    # it happened to get.
    op.add_column(
        "deal_products",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE deal_products dp
        SET position = ordered.rn
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY deal_id ORDER BY created_at, id
            ) - 1 AS rn
            FROM deal_products
        ) ordered
        WHERE dp.id = ordered.id
        """
    )
    op.create_index(
        "ix_deal_products_deal_position", "deal_products", ["deal_id", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_deal_products_deal_position", table_name="deal_products")
    op.drop_column("deal_products", "position")
    op.drop_index("ix_quote_products_quote_position", table_name="quote_products")
    op.drop_table("quote_products")
