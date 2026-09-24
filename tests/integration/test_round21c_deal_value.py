"""Round 21C — one definition of what a deal is worth, on a real database.

Owner, 23 ก.ย. 2569: the typed `amount` wins; the line items are the
fallback. The pipeline card and the AI report must return the SAME number,
which nothing has ever asserted (diagnosis §1f).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase17 import ReportQueryRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def shop(migrated_db):
    """Three deals: lines only (30,000) · amount only (250,000) · both,
    where the typed 99,000 must beat the 1,000 of lines."""
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CV{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        row = RegistrationRepository(s).create_license(
            company_name=f"Deal value {tag}", created_by_chann_uid=uid)
        license_id = row.id
        s.commit()
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as s:
        customers, deals = CustomerRepository(s), DealRepository(s)
        # One open deal per customer (the Phase 9 duplicate guard), so each
        # case gets its own customer rather than closing the one before.
        a = customers.create(scope, first_name="ลูกค้า", last_name="ก", phone="0810000001")
        s.flush()
        lines_only = deals.create(scope, contact_id=a.id)
        s.flush()
        deals.add_product(scope, lines_only.id, product_id=None, product_name="แอร์ 12000 BTU",
                          quoted_unit_price=Decimal("15000.00"), qty=2)
        b = customers.create(scope, first_name="ลูกค้า", last_name="ข", phone="0810000002")
        s.flush()
        deals.create(scope, contact_id=b.id, amount=Decimal("250000.00"))
        s.flush()
        c = customers.create(scope, first_name="ลูกค้า", last_name="ค", phone="0810000003")
        s.flush()
        both = deals.create(scope, contact_id=c.id, amount=Decimal("99000.00"))
        s.flush()
        deals.add_product(scope, both.id, product_id=None, product_name="ค่าบริการติดตั้ง",
                          quoted_unit_price=Decimal("1000.00"), qty=1)
        s.commit()
    return migrated_db, scope


class TestOneDefinition:
    def test_the_typed_amount_wins_and_the_lines_are_the_fallback(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            summary = DealRepository(s).pipeline_summary(scope)
        # 30,000 + 250,000 + 99,000 — NOT 281,000, which is what summing the
        # line items first gives.
        assert Decimal(summary["by_stage"]["new"]["value"]) == Decimal("379000")
        assert Decimal(summary["open_value"]) == Decimal("379000")

    def test_the_ai_report_returns_the_same_number_as_the_pipeline_card(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            card = DealRepository(s).pipeline_summary(scope)
            report = ReportQueryRepository(s).run(
                scope, {"entity": "deals", "metric": "sum", "field": "amount"})
        assert Decimal(str(report["total"])) == Decimal(card["by_stage"]["new"]["value"])
        assert Decimal(str(report["total"])) == Decimal("379000")

    def test_grouping_by_stage_still_counts_deals_not_line_items(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            counted = ReportQueryRepository(s).run(
                scope, {"entity": "deals", "metric": "count", "group_by": "stage"})
        assert {row["key"]: row["value"] for row in counted["rows"]} == {"new": 3}
