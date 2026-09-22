"""Round 20X — the bills and the quotations of ONE deal, on a real database.

Owner, 22 ก.ย. 2569: an invoice always hangs off a deal, so the deal page
and the invoices page opened from it list by `deal_id`; the invoice form's
quote select lists one deal's quotations the same way. Both narrowings
are the repositories' own `_narrow`, so the page and the count agree.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_round20v_invoices import _create, _tenant  # noqa: E402

from chann_data.repositories.invoices import InvoiceRepository  # noqa: E402
from chann_data.repositories.phase9 import DealRepository  # noqa: E402
from chann_data.repositories.phase10 import QuoteRepository  # noqa: E402


class TestOneDealsRecords:
    def test_invoices_narrow_by_deal_and_count_the_same_rows(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            deals = DealRepository(session)
            # One open deal per customer (phase9's rule): the first is won
            # before the second is opened — a won deal is still billable.
            deals.transition_stage(t["scope"], t["deal_id"], to_stage="proposed", allow_reopen=False)
            deals.transition_stage(t["scope"], t["deal_id"], to_stage="won", allow_reopen=False)
            second = deals.create(t["scope"], contact_id=t["customer_id"])
            deals.add_product(
                t["scope"], second.id, product_id=None, product_name="พัดลม",
                quoted_unit_price="1500.00", qty=1,
            )
            repo = InvoiceRepository(session)
            from_quote = _create(repo, t)
            from_deal = repo.create(
                t["scope"], deal_id=second.id, subtotal="1500.00", vat_rate="0.07",
                vat_amount="105.00", total="1605.00", data_snapshot={"line_items": [{"line_no": 1}]},
            )
            session.commit()
            assert from_quote.deal_id == t["deal_id"] and from_deal.contact_id == t["customer_id"]
            assert [r.id for r in repo.list_for_license(t["scope"], deal_id=second.id)] == [from_deal.id]
            assert repo.count_for_license(t["scope"], deal_id=second.id) == 1
            assert repo.count_for_license(t["scope"], deal_id=t["deal_id"]) == 1
            # The customer filter still sees both; another deal's id sees none.
            assert repo.count_for_license(t["scope"], contact_id=t["customer_id"]) == 2
            other = _tenant(migrated_db)
            assert repo.count_for_license(t["scope"], deal_id=other["deal_id"]) == 0
            assert repo.list_for_license(other["scope"], deal_id=second.id) == []

    def test_quotes_narrow_by_deal_for_the_forms_select(self, migrated_db):
        t = _tenant(migrated_db)
        with t["session"]() as session:
            deals = DealRepository(session)
            # One open deal per customer (phase9's rule): the first is won
            # before the second is opened — a won deal is still billable.
            deals.transition_stage(t["scope"], t["deal_id"], to_stage="proposed", allow_reopen=False)
            deals.transition_stage(t["scope"], t["deal_id"], to_stage="won", allow_reopen=False)
            second = deals.create(t["scope"], contact_id=t["customer_id"])
            deals.add_product(
                t["scope"], second.id, product_id=None, product_name="พัดลม",
                quoted_unit_price="1500.00", qty=1,
            )
            quotes = QuoteRepository(session)
            other = quotes.create(t["scope"], deal_id=second.id)
            session.commit()
            assert [q.id for q in quotes.list_for_license(t["scope"], deal_id=second.id)] == [other.id]
            assert quotes.count_for_license(t["scope"], deal_id=second.id) == 1
            assert quotes.count_for_license(t["scope"], deal_id=t["deal_id"]) == 1
            assert quotes.count_for_license(t["scope"]) == 2
            assert quotes.count_for_license(t["scope"], deal_id=second.id, q="NOPE") == 0
