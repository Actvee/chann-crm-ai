"""Round 21A — the bills of one quote, on a real database.

The quote page asks "have I been billed?"; the filter has to answer
from the row's own quote_id, not from the deal's, or a second quote on
the same deal would claim the first one's invoice.
"""
from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402


class TestOneQuotesBills:
    def test_each_quote_sees_only_its_own(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.invoices import InvoiceRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.phase10 import QuoteRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            customer = CustomerRepository(session).create(
                scope, first_name="สมชาย", phone=f"08{uuid.uuid4().int % 10**8:08d}",
            )
            deals = DealRepository(session)
            deal = deals.create(scope, contact_id=customer.id)
            # A quote needs a line: the Data Tier refuses one without.
            deals.add_product(
                scope, deal.id, product_id=None, product_name="แอร์",
                quoted_unit_price=Decimal("100"), qty=1,
            )
            session.flush()
            quotes = QuoteRepository(session)
            first = quotes.create(scope, deal_id=deal.id)
            second = quotes.create(scope, deal_id=deal.id)
            session.flush()

            invoices = InvoiceRepository(session)
            one = invoices.create(scope, quote_id=first.id, contact_id=customer.id, total=Decimal("100"))
            two = invoices.create(scope, quote_id=second.id, contact_id=customer.id, total=Decimal("200"))
            session.flush()

            by_first = invoices.list_for_license(scope, quote_id=first.id)
            assert [row.id for row in by_first] == [one.id]
            assert invoices.count_for_license(scope, quote_id=first.id) == 1
            # The deal's own filter still sees both — a deal may be billed twice.
            assert {row.id for row in invoices.list_for_license(scope, deal_id=deal.id)} == {one.id, two.id}
            assert invoices.count_for_license(scope, quote_id=uuid.uuid4()) == 0
            session.rollback()
