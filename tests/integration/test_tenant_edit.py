"""7 Sep 2026 — the platform operator edits a tenant: status, trial
deadline and the shop's details, audited as a cross-tenant row."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from fastapi.testclient import TestClient  # noqa: E402

from chann_data.repositories.phase18 import PlatformNotFound, PlatformRepository  # noqa: E402


@pytest.fixture
def data_client(migrated_db, monkeypatch):
    """The real Data-tier app on the migrated schema (same shape as
    test_data_endpoints_smoke.api); the internal secret rides on every call."""
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
    client = TestClient(app)
    client.headers.update({"X-Internal-Secret": "test-internal-secret"})
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def license_id(migrated_db):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(chann_uid=f"CHN-TE-{suffix}", line_user_id=f"line-te-{suffix}", primary_role="sales"))
        session.commit()
        lic = RegistrationRepository(session).create_license(company_name=f"Edit {suffix}", created_by_chann_uid=f"CHN-TE-{suffix}")
        session.commit()
        return lic.id


class TestPlatformRepositoryUpdate:
    def test_only_sent_fields_move_and_before_values_come_back(self, migrated_db, license_id):
        from sqlalchemy.orm import Session

        deadline = datetime(2026, 9, 30, 16, 59, 59, tzinfo=timezone.utc)
        with Session(migrated_db) as session:
            before, row = PlatformRepository(session).update(
                license_id, {"company_phone": " 021234567 ", "trial_expires_at": deadline, "status": "active"},
            )
            session.commit()
            assert before["company_phone"] is None and before["status"] == "trial"
            assert row.company_phone == "021234567" and row.status == "active"
            assert row.trial_expires_at == deadline
        with Session(migrated_db) as session:
            data = PlatformRepository(session).tenant(license_id)
            assert data["company_phone"] == "021234567" and data["status"] == "active"
            assert data["company_address"] is None and data["tax_id"] is None

    def test_bad_input_is_refused_not_written(self, migrated_db, license_id):
        from sqlalchemy.orm import Session

        with Session(migrated_db) as session:
            repo = PlatformRepository(session)
            with pytest.raises(ValueError):
                repo.update(license_id, {"status": "deleted"})
            with pytest.raises(ValueError):
                repo.update(license_id, {"company_name": "   "})
            with pytest.raises(ValueError):
                repo.update(license_id, {"license_code": "HACK"})
            with pytest.raises(PlatformNotFound):
                repo.update(uuid.uuid4(), {"company_name": "x"})


class TestDataRoute:
    def test_the_detail_get_validates(self, migrated_db, license_id, data_client):
        # Regression: the member list overwrote the member count and the
        # route answered 500 for every tenant.
        res = data_client.get(f"/internal/v1/platform/tenants/{license_id}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["members"] == 1 and len(body["members_detail"]) == 1
        assert body["members_detail"][0]["role"] == "owner"

    def test_patch_audits_a_cross_tenant_row_and_returns_the_tenant(self, migrated_db, license_id, data_client):
        res = data_client.patch(
            f"/internal/v1/platform/tenants/{license_id}",
            json={"company_name": "Edited Shop", "trial_expires_at": "2026-10-15T23:59:59+07:00"},
            headers={"X-Actor-Id": "admin-1"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["company_name"] == "Edited Shop" and body["trial_expires_at"].startswith("2026-10-15T")
        assert "members_detail" in body
        audit = data_client.get(f"/internal/v1/platform/audit?license_id={license_id}&cross_tenant=true").json()
        row = [r for r in audit if r["action"] == "update" and r["entity_type"] == "license"][0]
        assert row["actor_type"] == "platform_admin" and row["actor_id"] == "admin-1"
        assert row["field_changes"]["company_name"]["new"] == "Edited Shop"
        assert "trial_expires_at" in row["field_changes"]

    def test_clearing_the_deadline_and_empty_bodies(self, migrated_db, license_id, data_client):
        res = data_client.patch(f"/internal/v1/platform/tenants/{license_id}", json={"clear_trial_expires_at": True})
        assert res.status_code == 200 and res.json()["trial_expires_at"] is None
        assert data_client.patch(f"/internal/v1/platform/tenants/{license_id}", json={}).status_code == 422
        assert data_client.patch(f"/internal/v1/platform/tenants/{license_id}", json={"status": "gone"}).status_code == 422
        assert data_client.patch(f"/internal/v1/platform/tenants/{uuid.uuid4()}", json={"company_name": "x"}).status_code == 404
