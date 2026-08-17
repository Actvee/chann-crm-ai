"""Database integration — migrate from an EMPTY database, then seed.

This is the gate that the Chann1 project learned the hard way: schema
migration succeeding does not mean the environment is usable. Deal creation
failed there because reference data was absent even though the migration had
passed (02_..._PLAYBOOK section 9). So this test asserts the whole mandated
order, not just the migration:

    migration -> idempotent reference seed -> (fixture) -> usable

Requires a real PostgreSQL. Set TEST_DATABASE_URL to run it. When unset the
tests skip and the capability stays NOT_VERIFIED rather than being quietly
reported as passing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set — database integration is NOT_VERIFIED in this run",
)


@pytest.fixture(scope="module")
def migrated_db():
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DATABASE_URL, future=True)

    # Start genuinely empty; a migration that only works on a warm database
    # is not a migration you can deploy to a new environment.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))

    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT / "database"), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return engine


def _run_seed(env_overrides: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, **env_overrides}
    return subprocess.run(
        [sys.executable, str(ROOT / "database/scripts/seed_reference.py")],
        capture_output=True, text=True, env=env,
    )


class TestMigration:
    def test_all_phase1_tables_exist(self, migrated_db):
        from sqlalchemy import inspect

        tables = set(inspect(migrated_db).get_table_names())
        assert {"chann_identities", "platform_admins", "licenses", "license_members"} <= tables

    def test_membership_uniqueness_is_enforced_by_the_database(self, migrated_db):
        """Isolation must not depend on application code remembering to check.
        A duplicate membership row would create a second role path into the
        same tenant."""
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, License, LicenseMember

        with Session(migrated_db) as s:
            lic = License(id=uuid.uuid4(), license_code=f"UQ{uuid.uuid4().hex[:6]}", company_name="Uq Co")
            ident = ChannIdentity(
                chann_uid=f"CHN-S-{uuid.uuid4().hex[:6]}",
                line_user_id=f"U{uuid.uuid4().hex}",
                primary_role="sales",
            )
            s.add_all([lic, ident])
            s.flush()
            s.add(LicenseMember(id=uuid.uuid4(), license_id=lic.id, chann_uid=ident.chann_uid))
            s.commit()

            s.add(LicenseMember(id=uuid.uuid4(), license_id=lic.id, chann_uid=ident.chann_uid))
            with pytest.raises(IntegrityError):
                s.commit()

    def test_identity_line_user_id_is_unique(self, migrated_db):
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity

        line_user_id = f"U{uuid.uuid4().hex}"
        with Session(migrated_db) as s:
            s.add(ChannIdentity(chann_uid=f"CHN-C-{uuid.uuid4().hex[:6]}",
                                line_user_id=line_user_id, primary_role="customer"))
            s.commit()
            s.add(ChannIdentity(chann_uid=f"CHN-C-{uuid.uuid4().hex[:6]}",
                                line_user_id=line_user_id, primary_role="customer"))
            with pytest.raises(IntegrityError):
                s.commit()


class TestReferenceSeed:
    def test_seed_is_idempotent(self, migrated_db):
        """Run it twice; the second run must change nothing and still exit 0."""
        from sqlalchemy import text

        first = _run_seed({"APP_ENV": "dev"})
        assert first.returncode == 0, first.stderr

        with migrated_db.connect() as conn:
            after_first = conn.execute(text("SELECT count(*) FROM platform_admins")).scalar_one()

        second = _run_seed({"APP_ENV": "dev"})
        assert second.returncode == 0, second.stderr
        assert "exists — unchanged" in second.stdout

        with migrated_db.connect() as conn:
            after_second = conn.execute(text("SELECT count(*) FROM platform_admins")).scalar_one()

        assert after_first == after_second

    def test_seed_refuses_a_known_password_outside_dev(self, migrated_db):
        """A Stage or Production database that comes up with a publicly known
        break-glass password is worse than one that fails to come up."""
        result = _run_seed({"APP_ENV": "production", "PLATFORM_ADMIN_BOOTSTRAP_PASSWORD": ""})
        assert result.returncode != 0
        assert "PLATFORM_ADMIN_BOOTSTRAP_PASSWORD" in (result.stdout + result.stderr)

    def test_seed_succeeds_outside_dev_when_a_password_is_supplied(self, migrated_db):
        result = _run_seed({
            "APP_ENV": "stage",
            "PLATFORM_ADMIN_BOOTSTRAP_PASSWORD": "a-real-bootstrap-password",
            "PLATFORM_ADMIN_USERNAME": f"admin_{uuid.uuid4().hex[:6]}",
        })
        assert result.returncode == 0, result.stderr


class TestTenantIsolationThroughDataApi:
    def test_identity_is_created_once_and_reused(self, migrated_db, monkeypatch):
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import Session

        from chann_data.config import settings
        from chann_data.db import get_session
        from chann_data.main import app

        def override_session():
            with Session(migrated_db) as session:
                yield session

        monkeypatch.setattr(settings, "admin_secret", "integration-secret")
        app.dependency_overrides[get_session] = override_session
        try:
            client = TestClient(app)
            headers = {"X-Internal-Secret": "integration-secret"}
            line_user_id = f"U{uuid.uuid4().hex}"
            payload = {"line_user_id": line_user_id, "primary_role": "customer"}
            first = client.post(
                "/internal/v1/identities/resolve", json=payload, headers=headers
            )
            second = client.post(
                "/internal/v1/identities/resolve", json=payload, headers=headers
            )
            assert first.status_code == second.status_code == 200
            assert first.json()["chann_uid"] == second.json()["chann_uid"]
            assert first.json()["chann_uid"].startswith("CHN-C-")
        finally:
            app.dependency_overrides.clear()

    def test_license_a_cannot_list_or_probe_license_b(self, migrated_db, monkeypatch):
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import Session

        from chann_data.config import settings
        from chann_data.db import get_session
        from chann_data.main import app
        from chann_data.models import ChannIdentity, License, LicenseMember

        license_a = License(
            id=uuid.uuid4(), license_code=f"ISOA{uuid.uuid4().hex[:6]}", company_name="A"
        )
        license_b = License(
            id=uuid.uuid4(), license_code=f"ISOB{uuid.uuid4().hex[:6]}", company_name="B"
        )
        identity_a = ChannIdentity(
            chann_uid=f"CHN-C-{uuid.uuid4().hex[:6]}",
            line_user_id=f"U{uuid.uuid4().hex}",
            primary_role="customer",
        )
        identity_b = ChannIdentity(
            chann_uid=f"CHN-C-{uuid.uuid4().hex[:6]}",
            line_user_id=f"U{uuid.uuid4().hex}",
            primary_role="customer",
        )
        # Keep primitive identifiers before commit. SQLAlchemy's plain
        # Session expires ORM attributes on commit; reading the objects after
        # the context closes would test detached-instance behaviour instead
        # of tenant isolation.
        license_a_id = license_a.id
        license_b_id = license_b.id
        identity_a_uid = identity_a.chann_uid
        identity_b_uid = identity_b.chann_uid
        with Session(migrated_db) as session:
            session.add_all([license_a, license_b, identity_a, identity_b])
            session.flush()
            session.add_all([
                LicenseMember(
                    id=uuid.uuid4(), license_id=license_a.id, chann_uid=identity_a.chann_uid
                ),
                LicenseMember(
                    id=uuid.uuid4(), license_id=license_b.id, chann_uid=identity_b.chann_uid
                ),
            ])
            session.commit()

        def override_session():
            with Session(migrated_db) as session:
                yield session

        monkeypatch.setattr(settings, "admin_secret", "integration-secret")
        app.dependency_overrides[get_session] = override_session
        try:
            client = TestClient(app)
            headers = {"X-Internal-Secret": "integration-secret"}
            response = client.get(f"/internal/v1/licenses/{license_a_id}/members", headers=headers)
            assert response.status_code == 200
            assert [row["chann_uid"] for row in response.json()] == [identity_a_uid]

            cross = client.get(
                f"/internal/v1/licenses/{license_a_id}/members/{identity_b_uid}/cross-check",
                params={"target_license_id": str(license_b_id)},
                headers=headers,
            )
            assert cross.status_code == 403
        finally:
            app.dependency_overrides.clear()
