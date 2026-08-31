"""Quote line items, and the independence that is the whole point of them.

A quote used to be a pointer at a deal. Its contents were whatever the
deal held when someone looked, which meant two quotes on one deal were
necessarily identical, and editing the deal silently rewrote every draft
quote already sent out for discussion.

The deal records what a customer is buying. A quote records what they were
OFFERED — a different thing, which outlives the negotiation and must not
change under them.
"""
from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase10 import (  # noqa: E402
    Phase10Conflict,
    Phase10NotFound,
    QuoteRepository,
)
from chann_data.repositories.phase9 import CustomerRepository, DealRepository  # noqa: E402
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402


@pytest.fixture
def deal_with_products(migrated_db):
    """A tenant with one deal carrying two line items."""
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-QP-{suffix}", line_user_id=f"line-qp-{suffix}",
            primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"Quote lines {suffix}", created_by_chann_uid=f"CHN-QP-{suffix}",
        )
        session.commit()
        license_id = lic.id

    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as session:
        customer = CustomerRepository(session).create(
            scope, first_name="นาคี", last_name="มีทรัพย์", phone="0465316666",
        )
        deals = DealRepository(session)
        deal = deals.create(scope, contact_id=customer.id)
        deals.add_product(
            scope, deal.id, product_id=None, product_name="พัดลมตั้งพื้น 16 นิ้ว",
            quoted_unit_price="1500.00", qty=2,
        )
        deals.add_product(
            scope, deal.id, product_id=None, product_name="ค่าติดตั้ง",
            quoted_unit_price="500.00", qty=1,
        )
        session.commit()
        deal_id = deal.id

    return {
        "scope": scope,
        "deal_id": deal_id,
        "session": lambda: Session(migrated_db),
    }


class TestQuotesCopyTheirLines:
    def test_creating_a_quote_copies_the_deal_lines(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.commit()
            lines = repo.list_products(scope, quote.id)

        assert [line.product_name for line in lines] == [
            "พัดลมตั้งพื้น 16 นิ้ว", "ค่าติดตั้ง",
        ]
        assert lines[0].qty == 2
        assert lines[0].quoted_unit_price == Decimal("1500.00")
        # Order is part of the document, not whatever the database returns.
        assert [line.position for line in lines] == [0, 1]

    def test_editing_a_quote_does_not_touch_the_deal(self, deal_with_products):
        """The case that made this table necessary: a customer negotiates a
        discount on ONE offer."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            line = repo.list_products(scope, quote.id)[0]
            repo.update_product(
                scope, quote.id, line.id, {"quoted_unit_price": "1400.00"},
            )
            session.commit()
            quote_id = quote.id

        with deal_with_products["session"]() as session:
            quoted = QuoteRepository(session).list_products(scope, quote_id)
            deal_lines = DealRepository(session).products_of(deal_id)

        assert quoted[0].quoted_unit_price == Decimal("1400.00")
        assert deal_lines[0].quoted_unit_price == Decimal("1500.00"), (
            "the deal was rewritten by a discount that applied to one quote"
        )

    def test_editing_the_deal_does_not_touch_an_existing_quote(self, deal_with_products):
        """A quote already under discussion must not change under the
        customer's feet."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            quote = QuoteRepository(session).create(scope, deal_id=deal_id)
            session.commit()
            quote_id = quote.id

        with deal_with_products["session"]() as session:
            deals = DealRepository(session)
            deals.add_product(
                scope, deal_id, product_id=None, product_name="สินค้าที่เพิ่มทีหลัง",
                quoted_unit_price="9999.00", qty=1,
            )
            session.commit()

        with deal_with_products["session"]() as session:
            lines = QuoteRepository(session).list_products(scope, quote_id)
        assert len(lines) == 2, "a later change to the deal leaked into a sent quote"

    def test_two_quotes_on_one_deal_can_differ(self, deal_with_products):
        """"Here is the three-item version and here is the two-item one" —
        impossible while quotes read the deal."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            first = repo.create(scope, deal_id=deal_id)
            second = repo.create(scope, deal_id=deal_id)
            session.flush()
            drop = repo.list_products(scope, second.id)[1]
            repo.remove_product(scope, second.id, drop.id)
            session.commit()
            first_id, second_id = first.id, second.id

        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            assert len(repo.list_products(scope, first_id)) == 2
            assert len(repo.list_products(scope, second_id)) == 1


class TestEditingLimits:
    def test_an_issued_quote_can_no_longer_be_edited(self, deal_with_products):
        """It is a document the customer is holding. Changing what it says
        after the fact is how two people end up quoting different numbers
        from the same reference."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            line = repo.list_products(scope, quote.id)[0]
            repo.transition_status(scope, quote.id, to_status="sent")
            session.commit()
            quote_id, line_id = quote.id, line.id

        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            with pytest.raises(Phase10Conflict):
                repo.update_product(scope, quote_id, line_id, {"qty": 5})
            with pytest.raises(Phase10Conflict):
                repo.remove_product(scope, quote_id, line_id)
            with pytest.raises(Phase10Conflict):
                repo.add_product(
                    scope, quote_id, product_name="ของแถม", quoted_unit_price="0",
                )

    def test_a_negative_price_is_refused(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            with pytest.raises(Phase10Conflict):
                repo.add_product(
                    scope, quote.id, product_name="ส่วนลด", quoted_unit_price="-100",
                )

    def test_an_unparseable_price_is_refused(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            with pytest.raises(Phase10Conflict):
                repo.add_product(
                    scope, quote.id, product_name="อะไรสักอย่าง",
                    quoted_unit_price="แพงมาก",
                )

    def test_a_line_from_another_quote_cannot_be_edited(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            first = repo.create(scope, deal_id=deal_id)
            second = repo.create(scope, deal_id=deal_id)
            session.flush()
            other_line = repo.list_products(scope, first.id)[0]
            with pytest.raises(Phase10NotFound):
                repo.update_product(scope, second.id, other_line.id, {"qty": 9})


class TestTenantIsolation:
    def test_quote_lines_are_invisible_to_another_tenant(
        self, deal_with_products, migrated_db,
    ):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            quote = QuoteRepository(session).create(scope, deal_id=deal_id)
            session.commit()
            quote_id = quote.id

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-QO-{suffix}", line_user_id=f"line-qo-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            other = RegistrationRepository(session).create_license(
                company_name=f"Other {suffix}", created_by_chann_uid=f"CHN-QO-{suffix}",
            )
            session.commit()
            other_scope = TenantScope(license_id=other.id)

        with Session(migrated_db) as session:
            assert QuoteRepository(session).list_products(other_scope, quote_id) == []
