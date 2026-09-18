"""Round 20h — the by-number route, against the real schema.

Route ORDER is the thing a unit test cannot check: this route lives above
`/tickets/{ticket_id}`, which takes a UUID, and registered the other way
round every by-number path would be a 422 instead of a lookup. So would
the visibility narrowing be, if the route dropped it.

Why it exists at all: the Application tier had no way to ask for a ticket
by its number, so every lookup fetched a page of the queue and scanned it
— and that page is the newest hundred. Measured on 3,000 tickets
(17 ก.ย. 2569) the oldest was NOT FOUND at the default hundred and NOT
FOUND at the 500 cap; here it is one indexed query.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://chann:chann@127.0.0.1:55432/chann_crm_ai_test"
)


@pytest.fixture
def api(migrated_db, monkeypatch):
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


@pytest.fixture(scope="module")
def shop(migrated_db):
    """A shop with a member, a team-less technician, and 3 tickets: two
    public and one private to someone else."""
    from chann_data.models import ChannIdentity, ServiceTicket
    from chann_data.repositories.phase65 import RegistrationRepository

    with Session(migrated_db) as session:
        for uid, role in (("CHN-20H-OWNER", "sales"), ("CHN-20H-TECH", "technician")):
            if session.get(ChannIdentity, uid) is None:
                session.add(ChannIdentity(
                    chann_uid=uid, line_user_id=f"line-{uid.lower()}", primary_role=role,
                ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name="ร้านหาใบงาน", created_by_chann_uid="CHN-20H-OWNER",
        )
        session.commit()
        license_id = lic.id

    from chann_data.models import LicenseMember

    with Session(migrated_db) as session:
        tech = LicenseMember(
            license_id=license_id, chann_uid="CHN-20H-TECH",
            role="technician", channel="technician", status="active",
        )
        session.add(tech)
        session.commit()
        tech_member_id = tech.id

    with Session(migrated_db) as session:
        rows = [
            ServiceTicket(
                license_id=license_id, ticket_number="T-2026-0001",
                issue_description="เก่าที่สุด", status="open", visibility="public",
            ),
            ServiceTicket(
                license_id=license_id, ticket_number="T-2026-0002",
                issue_description="ของคนอื่น", status="open", visibility="private",
                assigned_to_ref=None,
            ),
        ]
        session.add_all(rows)
        session.commit()
    return str(license_id), str(tech_member_id)


@pytest.mark.usefixtures("migrated_db")
class TestTheByNumberRoute:
    def test_it_finds_a_ticket_by_the_number_a_person_types(self, api, shop):
        client, headers = api
        license_id, _tech = shop
        r = client.get(
            f"/internal/v1/licenses/{license_id}/tickets/by-number/T-2026-0001",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["ticket_number"] == "T-2026-0001"

    def test_it_is_matched_before_the_uuid_route(self, api, shop):
        """A 422 here means the path fell through to /tickets/{ticket_id}."""
        client, headers = api
        license_id, _tech = shop
        r = client.get(
            f"/internal/v1/licenses/{license_id}/tickets/by-number/T-2026-0001",
            headers=headers,
        )
        assert r.status_code != 422, r.text

    def test_a_number_this_shop_never_issued_is_a_404(self, api, shop):
        client, headers = api
        license_id, _tech = shop
        r = client.get(
            f"/internal/v1/licenses/{license_id}/tickets/by-number/T-2026-9999",
            headers=headers,
        )
        assert r.status_code == 404, r.text

    def test_a_technician_is_refused_a_private_job_that_is_not_theirs(self, api, shop):
        """The whole reason the narrowing is a parameter: without it, any
        technician could read any private job's address by guessing."""
        client, headers = api
        license_id, tech = shop
        open_to_all = client.get(
            f"/internal/v1/licenses/{license_id}/tickets/by-number/T-2026-0001",
            headers=headers, params={"visible_to": tech},
        )
        assert open_to_all.status_code == 200, open_to_all.text
        someone_elses = client.get(
            f"/internal/v1/licenses/{license_id}/tickets/by-number/T-2026-0002",
            headers=headers, params={"visible_to": tech},
        )
        assert someone_elses.status_code == 404, someone_elses.text

    def test_without_the_narrowing_the_shop_sees_its_own_private_job(self, api, shop):
        """Sales and CS dispatch; the narrowing is the technician's rule,
        not the shop's."""
        client, headers = api
        license_id, _tech = shop
        r = client.get(
            f"/internal/v1/licenses/{license_id}/tickets/by-number/T-2026-0002",
            headers=headers,
        )
        assert r.status_code == 200, r.text


@pytest.mark.usefixtures("migrated_db")
class TestDealLinesComeBackInOneQuery:
    def test_every_deal_gets_its_own_lines(self, migrated_db):
        """Batching is only correct if each deal still gets ITS lines."""
        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            if session.get(ChannIdentity, "CHN-20H-DEALS") is None:
                session.add(ChannIdentity(
                    chann_uid="CHN-20H-DEALS", line_user_id="line-20h-deals",
                    primary_role="sales",
                ))
                session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="ร้านดีล", created_by_chann_uid="CHN-20H-DEALS",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)

        # One customer each: a shop may hold only one OPEN deal per person,
        # which is exactly the rule that makes a deal list worth batching.
        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            contacts = [
                repo.create(
                    scope, first_name="ก", last_name=f"ข{n}", phone=f"08000000{n:02d}",
                ).id
                for n in range(3)
            ]
            session.commit()

        with Session(migrated_db) as session:
            repo = DealRepository(session)
            made = []
            for n, contact_id in enumerate(contacts):
                deal = repo.create(scope, contact_id=contact_id, amount=1000 + n)
                session.flush()
                repo.add_product(
                    scope, deal.id, product_id=None,
                    product_name=f"สินค้า {n}", quoted_unit_price=100 + n, qty=1,
                )
                made.append(deal.id)
            session.commit()

        with Session(migrated_db) as session:
            repo = DealRepository(session)
            batched = repo.products_for(made)
            assert set(batched) >= set(made)
            for deal_id in made:
                one_at_a_time = repo.products_of(deal_id)
                assert [p.product_name for p in batched[deal_id]] == \
                       [p.product_name for p in one_at_a_time]

    def test_no_deal_ids_is_not_a_query(self, migrated_db):
        from chann_data.repositories.phase9 import DealRepository

        with Session(migrated_db) as session:
            assert DealRepository(session).products_for([]) == {}
