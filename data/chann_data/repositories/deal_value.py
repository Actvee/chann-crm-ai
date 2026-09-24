"""The one definition of what a deal is worth (round 21C).

Owner, 23 ก.ย. 2569: the value a salesperson typed wins; the line items
are what a deal is worth when nobody typed one.

There were four implementations of this and two of them disagreed. The
pipeline card summed the line items first; the deal list and chat took the
typed amount first; and the AI report summed `deals.amount` alone, which
is NULL for every deal whose value lives in its lines — so it answered 0
(`docs/superpowers/specs/2026-09-23-ai-reports-diagnosis.md` §1).

The join to `deal_products` MULTIPLIES rows, so every user of this groups
by `Deal.id` and aggregates the subquery, never the joined rows: a count
taken over the join counts line items, not deals.
"""
from __future__ import annotations

from sqlalchemy import func, select

from ..models import Deal, DealProduct

#: The value of ONE deal, for a statement that has already outer-joined
#: `deal_products` and grouped by `Deal.id`. `amount` first, on purpose.
DEAL_VALUE = func.coalesce(
    Deal.amount, func.sum(DealProduct.quoted_unit_price * DealProduct.qty), 0,
)


def deal_value_subquery(*extra_columns):
    """One row per deal: `deal_id`, whatever else the caller asked for, and
    `value`. The caller adds its own WHERE and then aggregates."""
    return (
        select(Deal.id.label("deal_id"), *extra_columns, DEAL_VALUE.label("value"))
        .outerjoin(DealProduct, DealProduct.deal_id == Deal.id)
        .group_by(Deal.id, Deal.amount, *extra_columns)
    )
