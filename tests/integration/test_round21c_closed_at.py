"""Round 21C — a deal records WHEN it closed.

Until now "ปิดสำเร็จเดือนนี้" could only be approximated from
`expected_close_date ?? created_at` — a forecast date, not a close date
(diagnosis §1, "two more defects").
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, Deal
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def shop(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CC{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        row = RegistrationRepository(s).create_license(
            company_name=f"Closed at {tag}", created_by_chann_uid=uid)
        license_id = row.id
        s.commit()
    return migrated_db, TenantScope(license_id=license_id)


class TestTheColumn:
    def test_migration_adds_closed_at_and_its_index(self, migrated_db):
        columns = {c["name"] for c in inspect(migrated_db).get_columns("deals")}
        assert "closed_at" in columns
        indexes = {i["name"] for i in inspect(migrated_db).get_indexes("deals")}
        assert "ix_deals_closed_at" in indexes

    def test_the_head_the_data_image_expects_is_this_one(self, migrated_db):
        from chann_data.main import EXPECTED_MIGRATION_HEAD

        with migrated_db.begin() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == EXPECTED_MIGRATION_HEAD == "0038_deal_closed_at"


class TestWhenItIsStamped:
    def test_won_stamps_it_and_reopening_clears_it(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="ปิด", phone="0820000001")
            s.flush()
            deals = DealRepository(s)
            deal = deals.create(scope, contact_id=customer.id)
            s.flush()
            assert deal.closed_at is None
            deals.transition_stage(scope, deal.id, to_stage="proposed", allow_reopen=False)
            assert deal.closed_at is None
            deals.transition_stage(scope, deal.id, to_stage="won", allow_reopen=False)
            assert deal.closed_at is not None
            deals.transition_stage(scope, deal.id, to_stage="new", allow_reopen=True)
            assert deal.closed_at is None

    def test_lost_stamps_it_too(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="แพ้", phone="0820000002")
            s.flush()
            deals = DealRepository(s)
            deal = deals.create(scope, contact_id=customer.id)
            s.flush()
            deals.transition_stage(scope, deal.id, to_stage="lost", allow_reopen=False,
                                   lost_reason="ราคาสูงกว่าคู่แข่ง")
            assert deal.closed_at is not None


class TestTheBackfill:
    def test_a_deal_closed_before_the_column_existed_gets_its_updated_at(self, shop):
        """The migration's UPDATE, re-run against a row that looks like an
        old one: closed, with no closed_at. It is an approximation, and the
        report says so in words (spec §4)."""
        engine, scope = shop
        stamp = datetime.now(timezone.utc) - timedelta(days=40)
        with Session(engine) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="เก่า", phone="0820000003")
            s.flush()
            deal = DealRepository(s).create(scope, contact_id=customer.id)
            s.flush()
            deal.stage = "won"
            deal.closed_at = None
            deal.updated_at = stamp
            s.commit()
            deal_id = deal.id
        with Session(engine) as s:
            s.execute(text(
                "UPDATE deals SET closed_at = updated_at "
                "WHERE stage IN ('won','lost') AND closed_at IS NULL"))
            s.commit()
        with Session(engine) as s:
            assert s.get(Deal, deal_id).closed_at is not None
