"""Every Data-tier GET endpoint actually executes.

Written because a missing `from sqlalchemy import select` in
`routers/internal.py` reached production. The module imported fine, the
app started fine, `phase2-source-verify.sh` passed, `/health` was green —
and the endpoint raised NameError the first time anything called it,
which happened to be the Cloud Scheduler reminder sweep in production.

Nothing in the suite called that endpoint at all. Neither did anything
call several of its neighbours. The value here is not deep assertions
about responses; it is that the code path *runs at all* against a real
database, so a name that does not exist is caught here rather than by a
user.

Deliberately broad and shallow: one call per endpoint, asserting only
that it did not blow up. Depth belongs in the per-feature tests; this is
the net underneath them.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://chann:chann@127.0.0.1:55432/chann_crm_ai_test"
)


@pytest.fixture
def api(migrated_db, monkeypatch):
    """The real Data-tier app against the real migrated schema.

    get_session is overridden rather than the settings patched: SessionLocal
    is bound at import time from DATABASE_URL, so changing the setting after
    the module is loaded has no effect — the app would quietly keep talking
    to whatever database the default pointed at.
    """
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


@pytest.fixture(scope="module")
def tenant(migrated_db):
    """One license with one customer and one deal, so list endpoints have
    something to serialise rather than trivially returning [].

    Module-scoped to match migrated_db: the schema is built once per module,
    so a function-scoped fixture would try to re-insert the same identity
    for every test and fail on the unique constraint after the first.
    """
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository
    from chann_data.repositories.phase9 import CustomerRepository, DealRepository
    from chann_data.repositories.tenant_scope import TenantScope

    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid="CHN-SMOKE-1", line_user_id="line-smoke-1", primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name="Smoke Co", created_by_chann_uid="CHN-SMOKE-1",
        )
        session.commit()
        license_id = lic.id
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as session:
        customer = CustomerRepository(session).create(
            scope, first_name="ก", last_name="ข", phone="0800000000",
        )
        deal = DealRepository(session).create(scope, contact_id=customer.id)
        session.commit()
        return {
            "license_id": str(license_id),
            "customer_id": str(customer.id),
            "deal_id": str(deal.id),
        }


class TestDataTierEndpointsExecute:
    def test_list_licenses_runs(self, api, tenant):
        """The exact endpoint whose missing `select` import reached
        production."""
        client, headers = api
        response = client.get("/internal/v1/licenses", headers=headers)
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_list_licenses_with_exclude_status_runs(self, api, tenant):
        client, headers = api
        response = client.get(
            "/internal/v1/licenses", headers=headers, params={"exclude_status": "suspended"},
        )
        assert response.status_code == 200, response.text
        # A new license defaults to "trial", so excluding "suspended" must
        # still return it — the filter bug that made the sweep see zero
        # tenants while reporting success.
        assert len(response.json()) >= 1

    def test_list_licenses_with_status_runs(self, api, tenant):
        client, headers = api
        response = client.get(
            "/internal/v1/licenses", headers=headers, params={"status": "trial"},
        )
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        "path",
        [
            "customers",
            "deals",
            "products",
            "quotes",
            "follow-ups",
            "document-templates",
        ],
    )
    def test_tenant_scoped_list_endpoints_run(self, api, tenant, path):
        client, headers = api
        response = client.get(
            f"/internal/v1/licenses/{tenant['license_id']}/{path}", headers=headers,
        )
        assert response.status_code == 200, f"{path}: {response.text}"

    def test_due_follow_ups_runs(self, api, tenant):
        """Called by the reminder sweep on a schedule, so a failure here is
        invisible until a cron job fails in production."""
        client, headers = api
        response = client.get(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/due",
            headers=headers, params={"days": 1},
        )
        assert response.status_code == 200, response.text

    def test_notes_list_runs(self, api, tenant):
        client, headers = api
        response = client.get(
            f"/internal/v1/licenses/{tenant['license_id']}/notes",
            headers=headers,
            params={"entity_type": "customer", "entity_id": tenant["customer_id"]},
        )
        assert response.status_code == 200, response.text

    def test_company_profile_runs(self, api, tenant):
        client, headers = api
        response = client.get(
            f"/internal/v1/licenses/{tenant['license_id']}/company-profile", headers=headers,
        )
        assert response.status_code == 200, response.text

    def test_audit_log_runs(self, api, tenant):
        client, headers = api
        response = client.get(
            f"/internal/v1/licenses/{tenant['license_id']}/audit-log", headers=headers,
        )
        assert response.status_code == 200, response.text

    def test_a_missing_internal_secret_is_still_rejected(self, api, tenant):
        """The smoke net must not accidentally prove the endpoints are
        reachable without the shared secret."""
        client, _ = api
        response = client.get("/internal/v1/licenses")
        assert response.status_code in (401, 403), response.text

    def test_an_unknown_license_does_not_500(self, api):
        """A nonexistent tenant is a 200-with-nothing or a 404, never a
        crash — the distinction matters because the Application tier turns
        a 500 into an opaque DataTierError."""
        client, headers = api
        response = client.get(
            f"/internal/v1/licenses/{uuid.uuid4()}/customers", headers=headers,
        )
        assert response.status_code != 500, response.text


class TestFollowUpOwnership:
    """A reminder has to reach a person.

    The sweep read `owner_chann_uid` off the follow-up — a field that never
    existed on FollowUpOut, which only carried `owner_member_id`. And chat
    created follow-ups without any owner at all. Together those meant a
    reminder could be set, found by the sweep as due, and then skipped for
    having nobody to tell: {"due": 1, "sent": 0, "skipped": 1} in
    production.
    """

    def test_the_creator_becomes_the_owner_when_none_is_given(self, api, tenant, migrated_db):
        """Chat sends no owner_member_id, only an actor header. Without the
        default, every reminder set through chat was ownerless."""
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember

        client, headers = api
        with Session(migrated_db) as session:
            member = session.execute(
                LicenseMember.__table__.select().where(
                    LicenseMember.license_id == tenant["license_id"]
                )
            ).first()
        assert member is not None, "fixture tenant should have an owner member"

        response = client.post(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups",
            headers={**headers, "X-Actor-Id": "CHN-SMOKE-1"},
            json={
                "entity_type": "deal",
                "entity_id": tenant["deal_id"],
                "due_date": "2026-09-01",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["owner_member_id"] is not None

    def test_due_follow_ups_resolve_the_owner_to_a_person(self, api, tenant):
        """owner_member_id is useless to anything that sends a message; the
        endpoint resolves it to the chann_uid the notifier actually needs."""
        client, headers = api
        client.post(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups",
            headers={**headers, "X-Actor-Id": "CHN-SMOKE-1"},
            json={
                "entity_type": "deal",
                "entity_id": tenant["deal_id"],
                "due_date": "2026-09-01",
            },
        )
        response = client.get(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/due",
            headers=headers, params={"days": 3650},
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert rows, "the follow-up just created should be due within 10 years"
        assert any(r.get("owner_chann_uid") == "CHN-SMOKE-1" for r in rows), (
            f"no row resolved an owner: {rows}"
        )

    def test_an_explicit_owner_is_not_overridden_by_the_actor(self, api, tenant, migrated_db):
        """The default only fills a gap — a caller that names an owner means
        it."""
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember

        client, headers = api
        with Session(migrated_db) as session:
            member_id = session.execute(
                LicenseMember.__table__.select().where(
                    LicenseMember.license_id == tenant["license_id"]
                )
            ).first()[0]

        response = client.post(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups",
            headers={**headers, "X-Actor-Id": "CHN-SOMEONE-ELSE"},
            json={
                "entity_type": "deal",
                "entity_id": tenant["deal_id"],
                "due_date": "2026-09-02",
                "owner_member_id": str(member_id),
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["owner_member_id"] == str(member_id)
