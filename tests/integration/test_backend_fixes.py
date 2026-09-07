"""Backend review fixes (6 Sep 2026, section E) against Postgres."""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase9 import CustomerRepository, Phase9Duplicate  # noqa: E402
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402


@pytest.fixture
def tenant(migrated_db):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(chann_uid=f"CHN-BF-{suffix}", line_user_id=f"line-bf-{suffix}", primary_role="sales"))
        for n in (1, 2):
            session.add(ChannIdentity(chann_uid=f"CHN-BF-{suffix}-C{n}", line_user_id=f"line-bf-{suffix}-c{n}", primary_role="customer"))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(company_name=f"Fixes {suffix}", created_by_chann_uid=f"CHN-BF-{suffix}")
        session.commit()
        license_id = lic.id
    return {
        "scope": TenantScope(license_id=license_id), "license_id": license_id, "suffix": suffix,
        "owner_uid": f"CHN-BF-{suffix}", "c1": f"CHN-BF-{suffix}-C1", "c2": f"CHN-BF-{suffix}-C2",
        "session": lambda: Session(migrated_db),
    }


class TestE8LinkedCustomerAttachesToStaffRow:
    def test_create_with_identity_and_a_known_phone_returns_that_row_linked(self, tenant):
        with tenant["session"]() as session:
            staff_row = CustomerRepository(session).create(
                tenant["scope"], first_name="สมชาย", phone="081-234-5678",
            )
            session.commit()
            staff_id = staff_row.id
        with tenant["session"]() as session:
            row = CustomerRepository(session).create(
                tenant["scope"], first_name="Somchai", phone="0812345678",
                customer_chann_uid=tenant["c1"],
            )
            session.commit()
            assert row.id == staff_id
            assert row.customer_chann_uid == tenant["c1"]
            assert row.first_name == "สมชาย", "the shop's own spelling is kept"

    def test_link_identity_by_phone_attaches_without_creating(self, tenant):
        with tenant["session"]() as session:
            CustomerRepository(session).create(tenant["scope"], first_name="A", phone="0899990001")
            session.commit()
        with tenant["session"]() as session:
            repo = CustomerRepository(session)
            row = repo.link_identity_by_phone(tenant["scope"], phone="0899990001", customer_chann_uid=tenant["c1"])
            session.commit()
            assert row is not None and row.customer_chann_uid == tenant["c1"]
            assert repo.find_by_chann_uid(tenant["scope"], tenant["c1"]).id == row.id
            assert len(repo.list_for_license(tenant["scope"])) == 1

    def test_no_row_with_the_phone_means_none(self, tenant):
        with tenant["session"]() as session:
            assert CustomerRepository(session).link_identity_by_phone(
                tenant["scope"], phone="0800000000", customer_chann_uid=tenant["c1"],
            ) is None

    def test_a_number_held_by_another_identity_is_refused(self, tenant):
        with tenant["session"]() as session:
            CustomerRepository(session).create(
                tenant["scope"], first_name="B", phone="0899990002", customer_chann_uid=tenant["c2"],
            )
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(Phase9Duplicate):
                CustomerRepository(session).link_identity_by_phone(
                    tenant["scope"], phone="0899990002", customer_chann_uid=tenant["c1"],
                )
        with tenant["session"]() as session:
            with pytest.raises(Phase9Duplicate):
                CustomerRepository(session).create(
                    tenant["scope"], first_name="X", phone="0899990002", customer_chann_uid=tenant["c1"],
                )

    def test_reattaching_ones_own_row_is_a_no_op(self, tenant):
        with tenant["session"]() as session:
            CustomerRepository(session).create(
                tenant["scope"], first_name="C", phone="0899990003", customer_chann_uid=tenant["c1"],
            )
            session.commit()
        with tenant["session"]() as session:
            row = CustomerRepository(session).link_identity_by_phone(
                tenant["scope"], phone="0899990003", customer_chann_uid=tenant["c1"],
            )
            assert row is not None and row.customer_chann_uid == tenant["c1"]


class TestE3TrialsExpiringOnABangkokDay:
    def test_the_deadline_is_read_on_the_bangkok_calendar(self, tenant):
        from chann_data.models import License
        from chann_data.repositories.phase65 import RegistrationRepository

        # 17:30 UTC on the 10th is 00:30 on the 11th in Bangkok.
        moment = datetime(2026, 9, 10, 17, 30, tzinfo=timezone.utc)
        with tenant["session"]() as session:
            row = session.get(License, tenant["license_id"])
            row.trial_expires_at = moment
            session.commit()
        with tenant["session"]() as session:
            repo = RegistrationRepository(session)
            on_11 = [r for r in repo.trials_expiring_on(date(2026, 9, 11)) if r["id"] == tenant["license_id"]]
            on_10 = [r for r in repo.trials_expiring_on(date(2026, 9, 10)) if r["id"] == tenant["license_id"]]
            assert len(on_11) == 1 and not on_10
            assert on_11[0]["owner_chann_uid"] == tenant["owner_uid"]
            assert on_11[0]["company_name"].startswith("Fixes")

    def test_a_suspended_or_active_license_is_not_listed(self, tenant):
        from chann_data.models import License
        from chann_data.repositories.phase65 import RegistrationRepository

        with tenant["session"]() as session:
            row = session.get(License, tenant["license_id"])
            row.trial_expires_at = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)
            row.status = "active"
            session.commit()
        with tenant["session"]() as session:
            assert not [r for r in RegistrationRepository(session).trials_expiring_on(date(2026, 9, 12)) if r["id"] == tenant["license_id"]]


class TestE4E5WarrantyStatusAndBangkokDay:
    def test_a_stale_active_row_reads_expired_after_its_end_date(self, tenant):
        from chann_data.repositories.phase16 import WarrantyRepository

        with tenant["session"]() as session:
            row = WarrantyRepository(session).register(
                tenant["scope"], serial_number="OLD-BF", warranty_start=date(2020, 1, 1), warranty_months=12,
            )
            session.commit()
            assert row.status == "active", "the column is stale until the sweep"
            assert WarrantyRepository.effective_status(row) == "expired"
            assert WarrantyRepository.effective_status(row, on_day=date(2020, 6, 1)) == "active"

    def test_expire_overdue_uses_the_bangkok_day(self, tenant, monkeypatch):
        from chann_data.repositories import phase16

        with tenant["session"]() as session:
            phase16.WarrantyRepository(session).register(
                tenant["scope"], serial_number="EDGE-BF", warranty_start=date(2026, 1, 1), warranty_months=8,
            )
            session.commit()
        # Cover ends 2026-09-01. On the UTC clock it is still 31 Aug for the
        # first seven hours of 1 Sep in Bangkok: the sweep must see Bangkok.
        monkeypatch.setattr(phase16, "bangkok_today", lambda: date(2026, 9, 1))
        with tenant["session"]() as session:
            assert phase16.WarrantyRepository(session).expire_overdue(tenant["scope"]) == 0
        monkeypatch.setattr(phase16, "bangkok_today", lambda: date(2026, 9, 2))
        with tenant["session"]() as session:
            assert phase16.WarrantyRepository(session).expire_overdue(tenant["scope"]) == 1
            session.commit()

    def test_quote_expiry_uses_the_bangkok_day(self, tenant, monkeypatch):
        from chann_data.repositories import localtime
        from chann_data.repositories.phase10 import QuoteRepository
        from chann_data.repositories.phase9 import DealRepository

        with tenant["session"]() as session:
            contact = CustomerRepository(session).create(tenant["scope"], first_name="Q", phone="0877770001")
            session.flush()
            deals = DealRepository(session)
            deal = deals.create(tenant["scope"], contact_id=contact.id)
            deals.add_product(
                tenant["scope"], deal.id, product_id=None, product_name="พัดลม",
                quoted_unit_price="1500.00", qty=1,
            )
            session.flush()
            repo = QuoteRepository(session)
            quote = repo.create(tenant["scope"], deal_id=deal.id)
            session.flush()
            quote.valid_until = date(2026, 9, 5)
            repo.transition_status(tenant["scope"], quote.id, to_status="sent")
            session.commit()
        monkeypatch.setattr(localtime, "bangkok_today", lambda: date(2026, 9, 5))
        with tenant["session"]() as session:
            assert QuoteRepository(session).expire_overdue(tenant["scope"]) == 0
        monkeypatch.setattr(localtime, "bangkok_today", lambda: date(2026, 9, 6))
        with tenant["session"]() as session:
            assert QuoteRepository(session).expire_overdue(tenant["scope"]) == 1
            session.commit()


@pytest.fixture
def api(migrated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from chann_data import config as config_module
    from chann_data.db import get_session
    from chann_data.main import app

    monkeypatch.setattr(config_module.settings, "admin_secret", "test-internal-secret")
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), {"X-Internal-Secret": "test-internal-secret"}
    finally:
        app.dependency_overrides.pop(get_session, None)


class TestDataRoutes:
    def test_warranty_list_derives_status_and_the_expiry_route_fixes_the_column(self, tenant, api):
        from chann_data.repositories.phase16 import WarrantyRepository

        client, headers = api
        with tenant["session"]() as session:
            WarrantyRepository(session).register(
                tenant["scope"], serial_number="ROUTE-BF", warranty_start=date(2020, 1, 1), warranty_months=12,
            )
            session.commit()
        lid = tenant["license_id"]
        listed = client.get(f"/internal/v1/licenses/{lid}/warranties", params={"serial_number": "ROUTE-BF"}, headers=headers)
        assert listed.status_code == 200 and listed.json()[0]["status"] == "expired"
        swept = client.post(f"/internal/v1/licenses/{lid}/warranties/expire-overdue", headers=headers)
        assert swept.status_code == 200 and swept.json()["expired"] >= 1
        with tenant["session"]() as session:
            assert WarrantyRepository(session).by_serial(tenant["scope"], "ROUTE-BF").status == "expired"

    def test_trials_expiring_route(self, tenant, api):
        from chann_data.models import License

        client, headers = api
        with tenant["session"]() as session:
            row = session.get(License, tenant["license_id"])
            row.status = "trial"
            row.trial_expires_at = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
            session.commit()
        response = client.get("/internal/v1/platform/trials/expiring", params={"on_day": "2026-10-01"}, headers=headers)
        assert response.status_code == 200
        mine = [r for r in response.json() if r["id"] == str(tenant["license_id"])]
        assert mine and mine[0]["owner_chann_uid"] == tenant["owner_uid"]

    def test_link_identity_route(self, tenant, api):
        client, headers = api
        lid = tenant["license_id"]
        created = client.post(f"/internal/v1/licenses/{lid}/customers", json={"first_name": "R", "phone": "0866660001"}, headers=headers)
        assert created.status_code == 201
        missing = client.post(f"/internal/v1/licenses/{lid}/customers/link-identity", json={"phone": "0800000009", "customer_chann_uid": tenant["c2"]}, headers=headers)
        assert missing.status_code == 404
        linked = client.post(f"/internal/v1/licenses/{lid}/customers/link-identity", json={"phone": "086-666-0001", "customer_chann_uid": tenant["c2"]}, headers=headers)
        assert linked.status_code == 200 and linked.json()["customer_chann_uid"] == tenant["c2"]
        assert linked.json()["id"] == created.json()["id"]
