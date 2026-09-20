"""Searching finds a row that is NOT on the first page.

The one claim this round has to prove, against a real database.

Every list screen searched the rows it had already fetched. That is fine
while a shop is small and becomes a data-loss bug the moment a list is
capped: round 20j put a ceiling of 500 on customers and deals, so a shop
with 800 customers could type the 600th name, be told "ไม่พบลูกค้า", and
believe it. The cap did not slow the page down — it hid records
(20 ก.ย. 2569).

These run against Postgres because that is where the claim lives: ILIKE,
the ordering, OFFSET, and the count taken through the same filter are all
the database's behaviour, and a fake that answered them would be
answering for itself.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set — database integration is NOT_VERIFIED in this run",
)

PAGE = 20
ROWS = 60
#: Deliberately last in `created_at DESC`, so it is on NO first page.
NEEDLE = "เข็มหมุด"
#: Written explicitly rather than left to the default. Postgres' `now()`
#: is transaction-scoped, so rows inserted together share a created_at and
#: the id tie-break — a random UUID — decides the order. The needle then
#: lands on page one about a third of the time and the premise this file
#: rests on is a coin toss (20 ก.ย. 2569).
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _at(n: int) -> datetime:
    return BASE + timedelta(minutes=n)


@pytest.fixture(scope="module")
def shop(migrated_db):
    """A shop with more rows than one page, and one to go looking for."""
    from chann_data.models import ChannIdentity, Customer, Product, ServiceTicket
    from chann_data.repositories.phase65 import RegistrationRepository

    with Session(migrated_db) as session:
        for uid, role in (("CHN-20N-OWNER", "sales"), ("CHN-20N-TECH", "technician")):
            if session.get(ChannIdentity, uid) is None:
                session.add(ChannIdentity(
                    chann_uid=uid, line_user_id=f"line-{uid.lower()}", primary_role=role,
                ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name="ร้านค้นให้เจอ", created_by_chann_uid="CHN-20N-OWNER",
        )
        session.commit()
        license_id = lic.id

    with Session(migrated_db) as session:
        # The needle FIRST, so it is the oldest row and therefore last in
        # `created_at DESC` — the position a page-local search can't reach.
        session.add(Customer(
            license_id=license_id, customer_id="C-20N-0000", created_at=_at(0),
            first_name=NEEDLE, last_name="ท้ายสุด", phone="0899999999", stage="contact",
        ))
        session.flush()
        for i in range(1, ROWS + 1):
            session.add(Customer(
                license_id=license_id, customer_id=f"C-20N-{i:04d}", created_at=_at(i),
                first_name=f"ลูกค้า{i}", last_name="ทดสอบ",
                phone=f"08{i:08d}", stage="contact",
            ))
            session.flush()
        session.commit()

    with Session(migrated_db) as session:
        session.add(Product(
            license_id=license_id, product_id="P-20N-0000", created_at=_at(0),
            product_name=f"{NEEDLE} รุ่นพิเศษ", sku="SKU-0000",
        ))
        session.flush()
        for i in range(1, ROWS + 1):
            session.add(Product(
                license_id=license_id, product_id=f"P-20N-{i:04d}", created_at=_at(i),
                product_name=f"สินค้า{i:04d}", sku=f"SKU-{i:04d}",
            ))
            session.flush()
        session.commit()

    with Session(migrated_db) as session:
        session.add(ServiceTicket(
            license_id=license_id, ticket_number="T-20N-0000", created_at=_at(0),
            issue_description=f"{NEEDLE} เสีย", status="open", visibility="public",
        ))
        session.flush()
        for i in range(1, ROWS + 1):
            session.add(ServiceTicket(
                license_id=license_id, ticket_number=f"T-20N-{i:04d}", created_at=_at(i),
                issue_description=f"งาน{i}", status="open", visibility="public",
            ))
            session.flush()
        session.commit()

    return license_id


def _scope(license_id):
    from chann_data.repositories.tenant_scope import TenantScope

    return TenantScope(license_id=license_id)


class TestTheNeedleIsNotOnThePage:
    def test_a_page_really_does_stop_short(self, migrated_db, shop):
        """The premise. Without this the rest proves nothing."""
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            rows = CustomerRepository(session).list_for_license(_scope(shop), limit=PAGE)
        assert len(rows) == PAGE
        assert not any(NEEDLE in (r.first_name or "") for r in rows)

    def test_but_searching_finds_it(self, migrated_db, shop):
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            rows = CustomerRepository(session).list_for_license(
                _scope(shop), q=NEEDLE, limit=PAGE,
            )
        assert [r.customer_id for r in rows] == ["C-20N-0000"]

    def test_the_same_for_a_product(self, migrated_db, shop):
        from chann_data.repositories.phase7 import ProductRepository

        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            page_one = repo.list(_scope(shop), limit=PAGE)
            found = repo.list(_scope(shop), q=NEEDLE, limit=PAGE)
        assert not any(NEEDLE in r.product_name for r in page_one)
        assert [r.product_id for r in found] == ["P-20N-0000"]

    def test_the_same_for_a_ticket(self, migrated_db, shop):
        from chann_data.repositories.phase12 import ServiceTicketRepository

        with Session(migrated_db) as session:
            repo = ServiceTicketRepository(session)
            page_one = repo.list_for_license(_scope(shop), limit=PAGE)
            found = repo.list_for_license(_scope(shop), q=NEEDLE, limit=PAGE)
        assert not any(NEEDLE in (r.issue_description or "") for r in page_one)
        assert [r.ticket_number for r in found] == ["T-20N-0000"]


class TestTheTotalDescribesTheSearch:
    def test_a_search_is_counted_by_what_it_matched(self, migrated_db, shop):
        """"แสดง 20 จาก 61" for a search that matched one is a lie the
        screen prints as the truth."""
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            assert repo.count_for_license(_scope(shop)) == ROWS + 1
            assert repo.count_for_license(_scope(shop), q=NEEDLE) == 1

    def test_a_miss_counts_zero_rather_than_everything(self, migrated_db, shop):
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            assert repo.count_for_license(_scope(shop), q="ไม่มีใครชื่อนี้") == 0
            assert repo.list_for_license(_scope(shop), q="ไม่มีใครชื่อนี้") == []


class TestPagingWalksTheWholeList:
    def test_every_row_is_seen_once(self, migrated_db, shop):
        """Offset paging with `created_at DESC, id DESC`: no row twice, none
        missed. Rows created inside one transaction share a created_at, so
        without the id tie-break a page boundary repeats or skips."""
        from chann_data.repositories.phase9 import CustomerRepository

        seen: list[str] = []
        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            total = repo.count_for_license(_scope(shop))
            for offset in range(0, total, PAGE):
                seen += [
                    r.customer_id for r in
                    repo.list_for_license(_scope(shop), limit=PAGE, offset=offset)
                ]
        assert len(seen) == total
        assert len(set(seen)) == total

    def test_the_search_pages_too(self, migrated_db, shop):
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            first = repo.list_for_license(_scope(shop), q="ลูกค้า", limit=5)
            second = repo.list_for_license(_scope(shop), q="ลูกค้า", limit=5, offset=5)
        assert len(first) == 5 and len(second) == 5
        assert not ({r.id for r in first} & {r.id for r in second})


class TestWhatAWildcardMeans:
    def test_a_percent_the_person_typed_is_a_character_not_a_wildcard(
        self, migrated_db, shop,
    ):
        """Searching "%" must not return the whole shop."""
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            assert CustomerRepository(session).list_for_license(_scope(shop), q="%") == []

    def test_an_underscore_likewise(self, migrated_db, shop):
        from chann_data.repositories.phase9 import CustomerRepository

        with Session(migrated_db) as session:
            assert CustomerRepository(session).list_for_license(_scope(shop), q="_") == []


class TestATechniciansCountIsTheirOwn:
    def test_the_total_is_taken_through_the_visibility_predicate(self, migrated_db, shop):
        """A count without it tells a technician how many jobs exist that
        they are not allowed to see — the number itself is the leak."""
        from chann_data.models import LicenseMember, ServiceTicket
        from chann_data.repositories.phase12 import ServiceTicketRepository

        with Session(migrated_db) as session:
            member = LicenseMember(
                license_id=shop, chann_uid="CHN-20N-TECH",
                role="technician", channel="technician", status="active",
            )
            session.add(member)
            session.commit()
            member_id = member.id

        with Session(migrated_db) as session:
            session.add(ServiceTicket(
                license_id=shop, ticket_number="T-20N-PRIV", created_at=_at(ROWS + 1),
                issue_description="ของช่างคนอื่น", status="open", visibility="private",
            ))
            session.commit()

        with Session(migrated_db) as session:
            repo = ServiceTicketRepository(session)
            everything = repo.count_for_license(_scope(shop))
            theirs = repo.count_for_license(_scope(shop), member_id=member_id)
            rows = repo.list_visible_to(_scope(shop), member_id=member_id, limit=500)
        assert everything > theirs, "the private job is counted for everyone"
        assert theirs == len(rows), "the count and the page must agree"
        assert not any(r.ticket_number == "T-20N-PRIV" for r in rows)
