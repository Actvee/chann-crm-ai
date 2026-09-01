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


class TestQuoteTerms:
    """Expiry and discount — the two things a real quote has that this one
    did not, found by walking the flow against how commercial CRMs model
    the same object."""

    def test_a_new_quote_expires_by_default(self, deal_with_products):
        """A quote with no expiry is a price the shop is bound to
        indefinitely, and asking every salesperson to set one means most
        quotes will not have one."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            quote = QuoteRepository(session).create(scope, deal_id=deal_id)
            session.commit()
            assert quote.valid_until is not None

    def test_a_percentage_discount_reduces_the_total(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            repo.set_terms(scope, quote.id, discount_percent="10")
            totals = repo.totals(scope, quote.id)
            session.commit()

        # Two fans at 1500 plus a 500 fitting fee.
        assert totals["subtotal"] == Decimal("3500.00")
        assert totals["discount"] == Decimal("350.00")
        assert totals["total"] == Decimal("3150.00")

    def test_percent_and_amount_cannot_both_be_set(self, deal_with_products):
        """"10% and also 500 off" is ambiguous about which applies first,
        and the order changes the total."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            with pytest.raises(Phase10Conflict):
                repo.set_terms(
                    scope, quote.id, discount_percent="10", discount_amount="500",
                )

    def test_setting_one_kind_of_discount_clears_the_other(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            repo.set_terms(scope, quote.id, discount_percent="10")
            row = repo.set_terms(scope, quote.id, discount_amount="200")
            session.commit()
            assert row.discount_percent is None
            assert row.discount_amount == Decimal("200.00")

    def test_a_discount_larger_than_the_subtotal_is_clamped(self, deal_with_products):
        """Otherwise the shop owes the customer money."""
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            repo.set_terms(scope, quote.id, discount_amount="99999")
            totals = repo.totals(scope, quote.id)
            assert totals["total"] == Decimal("0.00")

    def test_an_issued_quote_cannot_have_its_terms_changed(self, deal_with_products):
        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            quote = repo.create(scope, deal_id=deal_id)
            session.flush()
            repo.transition_status(scope, quote.id, to_status="sent")
            session.commit()
            quote_id = quote.id
        with deal_with_products["session"]() as session:
            with pytest.raises(Phase10Conflict):
                QuoteRepository(session).set_terms(
                    scope, quote_id, discount_percent="5",
                )

    def test_only_sent_quotes_expire(self, deal_with_products):
        """A draft was never an offer, and an accepted quote is a
        commitment that a date does not undo."""
        from datetime import date, timedelta

        scope, deal_id = deal_with_products["scope"], deal_with_products["deal_id"]
        yesterday = date.today() - timedelta(days=1)

        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            draft = repo.create(scope, deal_id=deal_id)
            sent = repo.create(scope, deal_id=deal_id)
            session.flush()
            draft.valid_until = yesterday
            sent.valid_until = yesterday
            repo.transition_status(scope, sent.id, to_status="sent")
            session.commit()
            draft_id, sent_id = draft.id, sent.id

        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            assert repo.expire_overdue(scope) == 1
            session.commit()

        with deal_with_products["session"]() as session:
            repo = QuoteRepository(session)
            assert repo.get(scope, draft_id).status == "draft"
            assert repo.get(scope, sent_id).status == "expired"


class TestDealForecastAndLossReason:
    """Two fields every commercial CRM has: without a close date there is
    no forecast, and without a reason a shop loses the same way twice."""

    def _deal(self, migrated_db):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-FC-{suffix}", line_user_id=f"line-fc-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Forecast {suffix}",
                created_by_chann_uid=f"CHN-FC-{suffix}",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)

        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0800000000",
            )
            deal = DealRepository(session).create(scope, contact_id=customer.id)
            session.commit()
            return scope, deal.id, lambda: Session(migrated_db)

    def test_a_loss_reason_is_recorded_when_given(self, migrated_db):
        scope, deal_id, session_factory = self._deal(migrated_db)
        with session_factory() as session:
            deal = DealRepository(session).transition_stage(
                scope, deal_id, to_stage="lost", allow_reopen=False,
                lost_reason="ลูกค้าเลือกเจ้าอื่นเพราะราคาถูกกว่า",
            )
            session.commit()
            assert "ราคาถูกกว่า" in deal.lost_reason

    def test_closing_without_a_reason_still_works(self, migrated_db):
        """Demanding one gets a column full of "-", which looks answered
        and teaches nothing."""
        scope, deal_id, session_factory = self._deal(migrated_db)
        with session_factory() as session:
            deal = DealRepository(session).transition_stage(
                scope, deal_id, to_stage="lost", allow_reopen=False,
            )
            session.commit()
            assert deal.stage == "lost"
            assert deal.lost_reason is None

    def test_reopening_clears_the_reason(self, migrated_db):
        """Otherwise the deal explains why it was lost while sitting in
        "new"."""
        scope, deal_id, session_factory = self._deal(migrated_db)
        with session_factory() as session:
            repo = DealRepository(session)
            repo.transition_stage(
                scope, deal_id, to_stage="lost", allow_reopen=False,
                lost_reason="ราคาสูงไป",
            )
            session.commit()
        with session_factory() as session:
            deal = DealRepository(session).transition_stage(
                scope, deal_id, to_stage="new", allow_reopen=True,
            )
            session.commit()
            assert deal.lost_reason is None

    def test_an_expected_close_date_can_be_set(self, migrated_db):
        from datetime import date

        scope, deal_id, session_factory = self._deal(migrated_db)
        with session_factory() as session:
            deal = DealRepository(session).update(
                scope, deal_id, {"expected_close_date": date(2026, 10, 15)},
            )
            session.commit()
            assert deal.expected_close_date == date(2026, 10, 15)


class TestOnePhoneOneCustomer:
    """A repair shop's customer calls in months apart. Two records for the
    same person split their service history, so the technician arriving
    sees no previous visit and the shop cannot tell a repeat customer from
    a new one."""

    def _scope(self, migrated_db):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-DUP-{suffix}", line_user_id=f"line-dup-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Dup {suffix}", created_by_chann_uid=f"CHN-DUP-{suffix}",
            )
            session.commit()
            return TenantScope(license_id=lic.id), lambda: Session(migrated_db)

    def test_the_same_number_twice_is_refused(self, migrated_db):
        from chann_data.repositories.phase9 import Phase9Duplicate

        scope, session_factory = self._scope(migrated_db)
        with session_factory() as session:
            CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0812345678",
            )
            session.commit()
        with session_factory() as session:
            with pytest.raises(Phase9Duplicate) as caught:
                CustomerRepository(session).create(
                    scope, first_name="ค", last_name="ง", phone="0812345678",
                )
            # It says WHICH record, so the caller can point at it rather
            # than leaving the person to go and search.
            assert caught.value.existing_code.startswith("C-")

    @pytest.mark.parametrize(
        "written", ["081-234-5678", "081 234 5678", "+66812345678", "66812345678"],
    )
    def test_formatting_differences_are_the_same_number(self, migrated_db, written):
        """A Thai shop saves the same number both ways depending on where
        it was copied from."""
        from chann_data.repositories.phase9 import Phase9Duplicate

        scope, session_factory = self._scope(migrated_db)
        with session_factory() as session:
            CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0812345678",
            )
            session.commit()
        with session_factory() as session:
            with pytest.raises(Phase9Duplicate):
                CustomerRepository(session).create(
                    scope, first_name="ค", last_name="ง", phone=written,
                )

    def test_another_tenant_may_hold_the_same_number(self, migrated_db):
        """Two shops can both have the same customer; the rule is about one
        shop's records, not the platform's."""
        scope_a, factory = self._scope(migrated_db)
        scope_b, _ = self._scope(migrated_db)
        with factory() as session:
            repo = CustomerRepository(session)
            repo.create(scope_a, first_name="ก", last_name="ข", phone="0899999999")
            repo.create(scope_b, first_name="ก", last_name="ข", phone="0899999999")
            session.commit()

    def test_a_customer_with_no_phone_is_still_allowed(self, migrated_db):
        """Someone who walks in and gives only a name is a real customer."""
        scope, factory = self._scope(migrated_db)
        with factory() as session:
            repo = CustomerRepository(session)
            repo.create(scope, first_name="ไม่มี", last_name="เบอร์")
            repo.create(scope, first_name="ก็ไม่มี", last_name="เหมือนกัน")
            session.commit()


class TestOneOpenDealPerCustomer:
    """Two live deals for one person means two salespeople quoting them
    different numbers and neither knowing about the other."""

    def _customer(self, migrated_db):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-1D-{suffix}", line_user_id=f"line-1d-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"One deal {suffix}",
                created_by_chann_uid=f"CHN-1D-{suffix}",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)
        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0855555555",
            )
            session.commit()
            return scope, customer.id, lambda: Session(migrated_db)

    def test_a_second_open_deal_is_refused(self, migrated_db):
        from chann_data.repositories.phase9 import Phase9Duplicate

        scope, customer_id, factory = self._customer(migrated_db)
        with factory() as session:
            DealRepository(session).create(scope, contact_id=customer_id)
            session.commit()
        with factory() as session:
            with pytest.raises(Phase9Duplicate) as caught:
                DealRepository(session).create(scope, contact_id=customer_id)
            assert caught.value.existing_code.startswith("D-")

    def test_closing_the_first_frees_the_customer(self, migrated_db):
        """A customer who bought last year and comes back is the reason for
        keeping the record at all."""
        scope, customer_id, factory = self._customer(migrated_db)
        with factory() as session:
            repo = DealRepository(session)
            first = repo.create(scope, contact_id=customer_id)
            session.flush()
            repo.transition_stage(
                scope, first.id, to_stage="proposed", allow_reopen=False,
            )
            repo.transition_stage(scope, first.id, to_stage="won", allow_reopen=False)
            session.commit()

        with factory() as session:
            second = DealRepository(session).create(scope, contact_id=customer_id)
            session.commit()
            assert second.deal_id.endswith("0002")

    def test_a_lost_deal_also_frees_the_customer(self, migrated_db):
        scope, customer_id, factory = self._customer(migrated_db)
        with factory() as session:
            repo = DealRepository(session)
            first = repo.create(scope, contact_id=customer_id)
            session.flush()
            repo.transition_stage(
                scope, first.id, to_stage="lost", allow_reopen=False,
                lost_reason="เปลี่ยนใจ",
            )
            session.commit()
        with factory() as session:
            DealRepository(session).create(scope, contact_id=customer_id)
            session.commit()


class TestPipelineSummary:
    """Counting deals by stage is not a forecast: a stage says where a deal
    is, not when or whether it lands."""

    def _tenant(self, migrated_db):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-PL-{suffix}", line_user_id=f"line-pl-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Pipeline {suffix}",
                created_by_chann_uid=f"CHN-PL-{suffix}",
            )
            session.commit()
            return TenantScope(license_id=lic.id), lambda: Session(migrated_db)

    def _deal_worth(self, session, scope, *, phone, amount, close_date=None):
        customer = CustomerRepository(session).create(
            scope, first_name="ก", last_name="ข", phone=phone,
        )
        deals = DealRepository(session)
        deal = deals.create(scope, contact_id=customer.id)
        deals.add_product(
            scope, deal.id, product_id=None, product_name="สินค้า",
            quoted_unit_price=str(amount), qty=1,
        )
        if close_date:
            deals.update(scope, deal.id, {"expected_close_date": close_date})
        return deal

    def test_value_comes_from_the_line_items(self, migrated_db):
        scope, factory = self._tenant(migrated_db)
        with factory() as session:
            self._deal_worth(session, scope, phone="0810000001", amount=1500)
            self._deal_worth(session, scope, phone="0810000002", amount=2500)
            session.commit()

        with factory() as session:
            summary = DealRepository(session).pipeline_summary(scope)
        assert summary["by_stage"]["new"]["count"] == 2
        assert Decimal(summary["open_value"]) == Decimal("4000")

    def test_only_deals_closing_this_month_are_forecast(self, migrated_db):
        from datetime import date, timedelta

        scope, factory = self._tenant(migrated_db)
        today = date.today()
        next_month = (today.replace(day=28) + timedelta(days=10)).replace(day=1)

        with factory() as session:
            self._deal_worth(
                session, scope, phone="0820000001", amount=1000, close_date=today,
            )
            self._deal_worth(
                session, scope, phone="0820000002", amount=9000,
                close_date=next_month,
            )
            session.commit()

        with factory() as session:
            summary = DealRepository(session).pipeline_summary(scope)
        assert Decimal(summary["closing_this_month"]) == Decimal("1000")

    def test_overdue_deals_are_counted_separately_from_the_forecast(self, migrated_db):
        """A deal whose close date has passed and is still open is not a
        forecast, it is a deal nobody has touched."""
        from datetime import date, timedelta

        scope, factory = self._tenant(migrated_db)
        with factory() as session:
            self._deal_worth(
                session, scope, phone="0830000001", amount=5000,
                close_date=date.today() - timedelta(days=30),
            )
            session.commit()

        with factory() as session:
            summary = DealRepository(session).pipeline_summary(scope)
        assert summary["overdue_count"] == 1
        assert Decimal(summary["closing_this_month"]) == Decimal("0")

    def test_deals_with_no_date_are_counted_not_hidden(self, migrated_db):
        """A pipeline where half the deals have no date has a forecast that
        means very little, and the reader should be able to see that."""
        scope, factory = self._tenant(migrated_db)
        with factory() as session:
            self._deal_worth(session, scope, phone="0840000001", amount=100)
            session.commit()

        with factory() as session:
            summary = DealRepository(session).pipeline_summary(scope)
        assert summary["undated_open_count"] == 1

    def test_closed_deals_leave_the_open_value(self, migrated_db):
        scope, factory = self._tenant(migrated_db)
        with factory() as session:
            deal = self._deal_worth(session, scope, phone="0850000001", amount=7000)
            repo = DealRepository(session)
            repo.transition_stage(
                scope, deal.id, to_stage="proposed", allow_reopen=False,
            )
            repo.transition_stage(scope, deal.id, to_stage="won", allow_reopen=False)
            session.commit()

        with factory() as session:
            summary = DealRepository(session).pipeline_summary(scope)
        assert Decimal(summary["open_value"]) == Decimal("0")
        assert summary["by_stage"]["won"]["count"] == 1

    def test_another_tenant_is_not_counted(self, migrated_db):
        scope_a, factory = self._tenant(migrated_db)
        scope_b, _ = self._tenant(migrated_db)
        with factory() as session:
            self._deal_worth(session, scope_a, phone="0860000001", amount=1234)
            session.commit()

        with factory() as session:
            summary = DealRepository(session).pipeline_summary(scope_b)
        assert Decimal(summary["open_value"]) == Decimal("0")
