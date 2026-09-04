"""Phase 17 against Postgres: the spec becomes one tenant-filtered,
parameterised statement; counts, groups and date ranges are right; a
neighbouring tenant's rows never leak in.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, Deal, LicenseMember
from chann_data.repositories.phase17 import ReportQueryRepository, ReportSpecInvalid, date_window
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase12 import ServiceTicketRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def world(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uids = {"a": f"CHN-P17{tag}-A", "b": f"CHN-P17{tag}-B"}
    with Session(migrated_db) as s:
        for key in uids:
            s.add(ChannIdentity(chann_uid=uids[key], line_user_id=f"line-{uids[key]}", primary_role="sales",
                                display_name="สมชาย" if key == "a" else "สมหญิง"))
        s.commit()
    with Session(migrated_db) as s:
        reg = RegistrationRepository(s)
        a = reg.create_license(company_name=f"Report A {tag}", created_by_chann_uid=uids["a"])
        b = reg.create_license(company_name=f"Report B {tag}", created_by_chann_uid=uids["b"])
        ids = (a.id, b.id)
        s.commit()
    scope_a, scope_b = TenantScope(license_id=ids[0]), TenantScope(license_id=ids[1])
    with Session(migrated_db) as s:
        owner_a = s.execute(select(LicenseMember).where(LicenseMember.license_id == ids[0])).scalars().first()
        ca = CustomerRepository(s).create(scope_a, first_name="ลูกค้า", last_name="เอ", phone="0811111111")
        cb = CustomerRepository(s).create(scope_b, first_name="ลูกค้า", last_name="บี", phone="0822222222")
        s.flush()
        # One open deal per customer at a time (Phase 9 duplicate guard), so
        # each deal is closed before the next is opened.
        for stage in ("won", "won", "lost"):
            deal = DealRepository(s).create(scope_a, contact_id=ca.id, owner_member_id=owner_a.id)
            s.flush()
            deal.stage = stage
            s.flush()
        DealRepository(s).create(scope_a, contact_id=ca.id, owner_member_id=owner_a.id)  # stays "new"
        s.flush()
        ca_old = CustomerRepository(s).create(scope_a, first_name="ลูกค้า", last_name="เก่า", phone="0833333333")
        s.flush()
        old = DealRepository(s).create(scope_a, contact_id=ca_old.id)
        s.flush()
        old.stage = "won"
        old.created_at = datetime.now(timezone.utc) - timedelta(days=400)
        other = DealRepository(s).create(scope_b, contact_id=cb.id)
        s.flush()
        other.stage = "won"
        for status in ("open", "open", "completed"):
            ticket = ServiceTicketRepository(s).create(scope_a, issue_description="งาน", contact_id=ca.id)
            s.flush()
            ticket.status = status
            if status == "open":
                ticket.assigned_target_type = "member"
                ticket.assigned_to_ref = owner_a.id
        s.commit()
    return migrated_db, scope_a, scope_b, uids


class TestStatement:
    def test_always_filters_the_tenant_and_binds_parameters(self, world):
        engine, scope_a, _, _ = world
        with Session(engine) as s:
            stmt, spec = ReportQueryRepository(s).build_statement(scope_a, {"entity": "deals", "filter": {"stage": "won"}, "date_range": "this_year"})
            sql = str(stmt.compile())
            assert "deals.license_id = :license_id_1" in sql
            assert "deals.stage = :stage_1" in sql
            assert "won" not in sql and str(scope_a.license_id) not in sql
            params = stmt.compile().params
            assert params["license_id_1"] == scope_a.license_id and params["stage_1"] == "won"

    def test_outside_the_whitelist_never_becomes_sql(self, world):
        engine, scope_a, _, _ = world
        with Session(engine) as s:
            repo = ReportQueryRepository(s)
            for bad in ({"entity": "audit_log"}, {"entity": "deals", "filter": {"notes": "x"}}, {"entity": "deals", "group_by": "notes"},
                        {"entity": "deals", "filter": {"stage": "won' OR 1=1"}}, {"entity": "deals", "date_range": "forever"}):
                with pytest.raises(ReportSpecInvalid):
                    repo.build_statement(scope_a, bad)


class TestResults:
    def test_count_with_filter_and_isolation(self, world):
        engine, scope_a, scope_b, _ = world
        with Session(engine) as s:
            repo = ReportQueryRepository(s)
            assert repo.run(scope_a, {"entity": "deals", "filter": {"stage": "won"}})["total"] == 3
            assert repo.run(scope_a, {"entity": "deals", "filter": {"stage": "won"}, "date_range": "this_year"})["total"] == 2
            assert repo.run(scope_b, {"entity": "deals", "filter": {"stage": "won"}})["total"] == 1
            assert repo.run(scope_b, {"entity": "tickets"})["total"] == 0

    def test_group_by_with_labels(self, world):
        engine, scope_a, _, uids = world
        with Session(engine) as s:
            repo = ReportQueryRepository(s)
            by_stage = repo.run(scope_a, {"entity": "deals", "group_by": "stage"})
            assert {r["key"]: r["value"] for r in by_stage["rows"]} == {"won": 3, "lost": 1, "new": 1}
            assert by_stage["total"] == 5
            by_tech = repo.run(scope_a, {"entity": "tickets", "filter": {"status": "open"}, "group_by": "assigned_to"})
            assert by_tech["rows"] == [{"key": by_tech["rows"][0]["key"], "label": "สมชาย", "value": 2}]

    def test_date_windows_are_bangkok_days(self):
        start, end = date_window("today", today=date(2026, 9, 4))
        assert start.isoformat() == "2026-09-03T17:00:00+00:00" and end.isoformat() == "2026-09-04T17:00:00+00:00"
        start, end = date_window("last_month", today=date(2026, 9, 4))
        assert start.astimezone(timezone(timedelta(hours=7))).date() == date(2026, 8, 1)
        assert end.astimezone(timezone(timedelta(hours=7))).date() == date(2026, 9, 1)
        start, _ = date_window("last_3_months", today=date(2026, 9, 4))
        assert start.astimezone(timezone(timedelta(hours=7))).date() == date(2026, 7, 1)
