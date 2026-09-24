"""What a deal is worth, given a deal dict (round 21C).

The Data tier's `repositories/deal_value.py` is the same rule in SQL. This
one exists because the Application tier holds deals as dicts from the
DataClient, and three places were each deciding for themselves what a deal
was worth — two of them differently from the pipeline card.

`amount` is nullable. NULL and "" mean nobody typed a value; 0 means
somebody typed zero, and a typed zero is an answer.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def deal_value(deal: dict) -> Decimal:
    """The typed amount when there is one, otherwise the line items."""
    typed = deal.get("amount")
    if typed is not None and str(typed).strip() != "":
        return _decimal(typed)
    total = Decimal("0")
    for row in deal.get("products") or []:
        total += _decimal(row.get("quoted_unit_price")) * int(row.get("qty") or 0)
    return total
