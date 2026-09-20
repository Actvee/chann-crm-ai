"""The counts that a screen prints, checked against Postgres.

Round 20O's claims are all about numbers a person reads and acts on: how
many notes this customer has, how many entries the compliance trail holds,
how many conversations are in this tab, which categories the catalogue
uses. Every one of them is a database question, so a fake answering it
would be answering for itself.
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

NOTES = 30
PAGE = 20
BASE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _at(n: int) -> datetime:
    return BASE + timedelta(minutes=n)


@pytest.fixture(scope="module")
def shop(migrated_db):
    from chann_data.models import ChannIdentity, Customer, Product
    from chann_data.repositories.phase65 import RegistrationRepository

    with Session(migrated_db) as session:
        if session.get(ChannIdentity, "CHN-20O-OWNER") is None:
            session.add(ChannIdentity(
                chann_uid="CHN-20O-OWNER", line_user_id="line-20o", primary_role="sales",
            ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name="ร้านนับให้ถูก", created_by_chann_uid="CHN-20O-OWNER",
        )
        session.commit()
        license_id = lic.id

    with Session(migrated_db) as session:
        customer = Customer(
            license_id=license_id, customer_id="C-20O-0001",
            first_name="สมชาย", last_name="มีบันทึกเยอะ", phone="0811111111", stage="contact",
        )
        session.add(customer)
        session.commit()
        customer_id = customer.id

    from chann_data.models import Note

    with Session(migrated_db) as session:
        for i in range(NOTES):
            session.add(Note(
                license_id=license_id, entity_type="customer", entity_id=customer_id,
                body=f"บันทึกที่ {i + 1}", created_at=_at(i),
            ))
        session.commit()

    # A catalogue whose last category only appears on a late row.
    with Session(migrated_db) as session:
        for i in range(40):
            session.add(Product(
                license_id=license_id, product_id=f"P-20O-{i:04d}",
                product_name=f"สินค้า{i:04d}", category="แอร์" if i < 39 else "พัดลม",
                created_at=_at(i),
            ))
        session.commit()

    return license_id, customer_id


def _scope(license_id):
    from chann_data.repositories.tenant_scope import TenantScope

    return TenantScope(license_id=license_id)


class TestANotesCountIsTheRecordsNotThePages:
    def test_the_page_really_is_capped(self, migrated_db, shop):
        from chann_data.repositories.phase6 import NoteRepository

        license_id, customer_id = shop
        with Session(migrated_db) as session:
            rows = NoteRepository(session).list_for_entity(
                _scope(license_id), entity_type="customer", entity_id=customer_id, limit=PAGE,
            )
        assert len(rows) == PAGE

    def test_but_the_count_is_all_of_them(self, migrated_db, shop):
        """The card said "บันทึก (20)" for a customer with thirty."""
        from chann_data.repositories.phase6 import NoteRepository

        license_id, customer_id = shop
        with Session(migrated_db) as session:
            total = NoteRepository(session).count_for_entity(
                _scope(license_id), entity_type="customer", entity_id=customer_id,
            )
        assert total == NOTES

    def test_another_record_is_not_counted_in(self, migrated_db, shop):
        from chann_data.repositories.phase6 import NoteRepository

        license_id, customer_id = shop
        with Session(migrated_db) as session:
            assert NoteRepository(session).count_for_entity(
                _scope(license_id), entity_type="deal", entity_id=customer_id,
            ) == 0

    def test_paging_walks_every_note_once(self, migrated_db, shop):
        from chann_data.repositories.phase6 import NoteRepository

        license_id, customer_id = shop
        seen = []
        with Session(migrated_db) as session:
            repo = NoteRepository(session)
            for offset in range(0, NOTES, 7):
                seen += [
                    r.id for r in repo.list_for_entity(
                        _scope(license_id), entity_type="customer", entity_id=customer_id,
                        limit=7, offset=offset,
                    )
                ]
        assert len(seen) == NOTES
        assert len(set(seen)) == NOTES


class TestTheCategoriesAreTheCatalogues:
    def test_a_category_only_a_late_row_uses_is_still_offered(self, migrated_db, shop):
        """The filter was built from the loaded rows, so "พัดลม" — used by
        product 40 of 40 — was missing from the filter that would have
        found it."""
        from chann_data.repositories.phase7 import ProductRepository

        license_id, _ = shop
        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            first_page = repo.list(_scope(license_id), limit=PAGE)
            found = repo.categories(_scope(license_id))
        assert "พัดลม" not in {p.category for p in first_page}
        assert found == ["พัดลม", "แอร์"] or set(found) == {"พัดลม", "แอร์"}

    def test_an_archived_product_does_not_haunt_the_filter(self, migrated_db, shop):
        from chann_data.models import Product
        from chann_data.repositories.phase7 import ProductRepository

        license_id, _ = shop
        with Session(migrated_db) as session:
            session.add(Product(
                license_id=license_id, product_id="P-20O-GONE",
                product_name="เลิกขายแล้ว", category="ตู้เย็น",
                archived_at=datetime.now(timezone.utc),
            ))
            session.commit()
        with Session(migrated_db) as session:
            assert "ตู้เย็น" not in ProductRepository(session).categories(_scope(license_id))


class TestTheTrailSaysHowLongItIs:
    def test_the_count_matches_what_paging_walks(self, migrated_db, shop):
        from chann_data.repositories.audit import AuditRepository

        license_id, customer_id = shop
        with Session(migrated_db) as session:
            repo = AuditRepository(session)
            for i in range(25):
                repo.write(
                    license_id=license_id, entity_type="customer", entity_id=customer_id,
                    actor_type="user", actor_id="CHN-20O-OWNER", action="update",
                    field_changes={"n": {"before": i, "after": i + 1}},
                )
            session.commit()

        seen = []
        with Session(migrated_db) as session:
            repo = AuditRepository(session)
            total = repo.count_for_license(license_id, entity_type="customer")
            for offset in range(0, total, 10):
                seen += [
                    r.id for r in repo.list_for_license(
                        license_id, entity_type="customer", limit=10, offset=offset,
                    )
                ]
        assert total == 25
        assert len(seen) == 25 and len(set(seen)) == 25

    def test_a_filter_narrows_the_count_too(self, migrated_db, shop):
        from chann_data.repositories.audit import AuditRepository

        license_id, _ = shop
        with Session(migrated_db) as session:
            repo = AuditRepository(session)
            assert repo.count_for_license(license_id, entity_type="deal") == 0
            assert repo.count_for_license(license_id, entity_type="customer") == 25
