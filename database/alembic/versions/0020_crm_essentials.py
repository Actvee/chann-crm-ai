"""The CRM fields every commercial system has and this one did not.

Revision ID: 0020_crm_essentials
Revises: 0019_quote_products

Four gaps, found by walking the deal-to-quote flow against how Zoho,
HubSpot and Pipedrive model the same objects.

`deals.expected_close_date` — without it there is no forecast at all.
"How much will we close this month" is the question a sales pipeline
exists to answer, and a stage alone cannot answer it: a deal has been in
"proposed" for a day or for five months and the record looks identical.

`deals.lost_reason` — every CRM asks why, because a shop that loses six
deals to price and two to slow response should do different things about
each. Without it the only record of a loss is the word "lost", and the
pattern is invisible.

`quotes.valid_until` — the `expired` status has existed since Phase 10
with nothing able to set it, because nothing recorded when a quote
stopped being an offer. A quote with no expiry is a price the shop is
bound to indefinitely.

`quotes.discount_*` — real negotiation is a discount on the total, not a
silent edit of each unit price. Editing the lines loses what the list
price was, so nobody can see afterwards what was given away.
"""
import sqlalchemy as sa
from alembic import op

revision = "0020_crm_essentials"
down_revision = "0019_quote_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("expected_close_date", sa.Date(), nullable=True))
    # Free text, not an enum. Every shop loses deals for its own reasons,
    # and a fixed list would either be wrong for most of them or so
    # generic ("other") that nobody learns anything.
    op.add_column("deals", sa.Column("lost_reason", sa.Text(), nullable=True))
    # Indexed because "what is closing this month" is the query the field
    # exists to serve, and it is per-tenant like everything else.
    op.create_index(
        "ix_deals_expected_close", "deals", ["license_id", "expected_close_date"],
    )

    op.add_column("quotes", sa.Column("valid_until", sa.Date(), nullable=True))
    # Percent OR amount, never both — a quote saying "10% and also ฿500
    # off" is ambiguous about the order they apply in, and the answer
    # changes the total.
    op.add_column(
        "quotes", sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "quotes", sa.Column("discount_amount", sa.Numeric(18, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_quotes_one_discount_kind",
        "quotes",
        "discount_percent IS NULL OR discount_amount IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_quotes_one_discount_kind", "quotes", type_="check")
    op.drop_column("quotes", "discount_amount")
    op.drop_column("quotes", "discount_percent")
    op.drop_column("quotes", "valid_until")
    op.drop_index("ix_deals_expected_close", table_name="deals")
    op.drop_column("deals", "lost_reason")
    op.drop_column("deals", "expected_close_date")
