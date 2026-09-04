"""User review fixes against Postgres: email duplicates, deal amount and
closing date persisted, and the inactive-lead sweep touching only what it
should."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, Customer, Deal
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository, Phase9Duplicate
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def scope(migrated_db):
    tag = uuid.uuid4().hex[:6]
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=f"CHN-UR{tag}", line_user_id=f"line-UR{tag}", primary_role="sales"))
        s.commit()
        lic = RegistrationRepository(s).create_license(company_name=f"Review {tag}", created_by_chann_uid=f"CHN-UR{tag}")
        s.commit()
        return migrated_db, TenantScope(license_id=lic.id)


class TestDuplicates:
    def test_email_duplicate_is_refused_with_the_existing_code(self, scope):
        engine, sc = scope
        with Session(engine) as s:
            repo = CustomerRepository(s)
            first = repo.create(sc, first_name="สมชาย", phone="0811111111", email="Somchai@Example.com")
            s.commit()
            with pytest.raises(Phase9Duplicate) as exc:
                repo.create(sc, first_name="อื่น", phone="0822222222", email=" somchai@example.com ")
            assert exc.value.existing_code == first.customer_id and exc.value.field == "email"
            with pytest.raises(Phase9Duplicate) as exc:
                repo.create(sc, first_name="อื่น", phone="081-111-1111")
            assert exc.value.field == "phone"
            # unique phone and email: created
            assert repo.create(sc, first_name="ใหม่", phone="0833333333", email="new@example.com").id


class TestDealValue:
    def test_amount_currency_and_close_date_persist(self, scope):
        engine, sc = scope
        with Session(engine) as s:
            customer = CustomerRepository(s).create(sc, first_name="อาทิตย์", phone="0844444444")
            s.flush()
            deal = DealRepository(s).create(
                sc, contact_id=customer.id, amount=Decimal("250000"), currency="THB",
                expected_close_date=datetime(2026, 9, 30).date(),
            )
            s.commit()
            row = s.get(Deal, deal.id)
            assert row.amount == Decimal("250000.00") and row.currency == "THB"
            assert row.expected_close_date.isoformat() == "2026-09-30"
            DealRepository(s).update(sc, deal.id, {"amount": Decimal("1200000"), "currency": "USD"})
            s.commit()
            assert s.get(Deal, deal.id).amount == Decimal("1200000.00") and s.get(Deal, deal.id).currency == "USD"


class TestInactiveLeadSweep:
    def test_only_stale_leads_are_archived(self, scope):
        engine, sc = scope
        with Session(engine) as s:
            repo = CustomerRepository(s)
            stale = repo.create(sc, first_name="เก่า", phone="0851111111")
            fresh = repo.create(sc, first_name="ใหม่", phone="0852222222")
            contact = repo.create(sc, first_name="ลูกค้าจริง", phone="0853333333", stage="contact")
            active = repo.create(sc, first_name="มีดีล", phone="0854444444")
            s.flush()
            DealRepository(s).create(sc, contact_id=active.id)
            s.commit()
            old = datetime.now(timezone.utc) - timedelta(days=120)
            s.execute(update(Customer).where(Customer.id.in_([stale.id, contact.id, active.id])).values(updated_at=old))
            s.commit()
            archived = repo.archive_inactive_leads(sc, days=90)
            s.commit()
            assert {r.id for r in archived} == {stale.id}
            assert s.get(Customer, fresh.id).archived_at is None
            assert s.get(Customer, contact.id).archived_at is None   # never a contact
            assert s.get(Customer, active.id).archived_at is None    # its deal is recent activity
            assert s.get(Customer, stale.id).archived_at is not None
            # idempotent
            assert repo.archive_inactive_leads(sc, days=90) == []
