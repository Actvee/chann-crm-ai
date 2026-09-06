"""Per-tenant serialisation for running numbers.

Every C-/D-/Q-/T-/W-/SR- number was "read every code, take max+1" with no
lock (review, 6 Sep 2026): two creates in the same second picked the same
number and the loser died on the unique constraint — a 409 carrying the
raw SQL for tickets, a 500 for customers, deals and quotes. A transaction-
scoped advisory lock keyed on (tenant, prefix) makes the allocation
serial without a counter table.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def serialise(session: Session, key: str) -> None:
    """Hold a transaction-level advisory lock for `key` until commit."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
