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


def _phase2_tenant(session, *, suffix: str | None = None):
    from chann_data.models import ChannIdentity, CustomRole, License, LicenseMember, RolePermission
    from chann_data.permissions import DEFAULT_ROLE_TEMPLATES

    suffix = suffix or uuid.uuid4().hex[:8]
    license_row = License(
        id=uuid.uuid4(), license_code=f"P2{suffix}", company_name=f"Phase2 {suffix}"
    )
    owner_identity = ChannIdentity(
        chann_uid=f"CHN-S-{uuid.uuid4().hex[:8]}",
        line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="sales",
    )
    member_identity = ChannIdentity(
        chann_uid=f"CHN-S-{uuid.uuid4().hex[:8]}",
        line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="sales",
    )
    session.add_all([license_row, owner_identity, member_identity])
    session.flush()
    owner_member = LicenseMember(
        id=uuid.uuid4(),
        license_id=license_row.id,
        chann_uid=owner_identity.chann_uid,
        role="owner",
    )
    member = LicenseMember(
        id=uuid.uuid4(),
        license_id=license_row.id,
        chann_uid=member_identity.chann_uid,
        role="member",
    )
    session.add_all([owner_member, member])
    for role_name, permission_keys in DEFAULT_ROLE_TEMPLATES.items():
        session.add(
            CustomRole(
                id=uuid.uuid4(),
                license_id=license_row.id,
                role_name=role_name,
                is_owner=role_name == "owner",
            )
        )
        session.flush()
        if permission_keys is not None:
            session.add_all(
                RolePermission(
                    id=uuid.uuid4(),
                    license_id=license_row.id,
                    role=role_name,
                    permission_key=key,
                    allowed=True,
                )
                for key in permission_keys
            )
    session.flush()
    return license_row, owner_member, member


class TestPhase2MigrationAndSeed:
    def test_phase2_tables_exist(self, migrated_db):
        from sqlalchemy import inspect

        tables = set(inspect(migrated_db).get_table_names())
        assert {
            "custom_roles",
            "role_permissions",
            "license_settings",
            "ownership_transfers",
        } <= tables

    def test_seed_creates_four_templates_per_existing_tenant_without_overwrite(self, migrated_db):
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from chann_data.models import CustomRole, License

        first = _run_seed({"APP_ENV": "dev"})
        second = _run_seed({"APP_ENV": "dev"})
        assert first.returncode == second.returncode == 0
        with Session(migrated_db) as session:
            licenses = list(session.execute(select(License)).scalars())
            for license_row in licenses:
                count = session.execute(
                    select(func.count()).select_from(CustomRole).where(
                        CustomRole.license_id == license_row.id
                    )
                ).scalar_one()
                assert count >= 4

    def test_default_cs_and_member_permissions_match_business_split(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase2 import RoleRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, _, _ = _phase2_tenant(session)
            scope = TenantScope(license_row.id)
            cs = RoleRepository(session).permission_keys(scope, "cs")
            member = RoleRepository(session).permission_keys(scope, "member")
            assert "ticket.assign" in cs
            assert "deal.create" not in cs
            assert "deal.create" in member
            assert "ticket.assign" not in member
            session.rollback()


class TestPhase2RoleAndSettingIsolation:
    def test_custom_role_is_tenant_scoped_and_owner_is_immutable(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase2 import Phase2Conflict, RoleRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_a, _, _ = _phase2_tenant(session)
            license_b, _, _ = _phase2_tenant(session)
            scope_a, scope_b = TenantScope(license_a.id), TenantScope(license_b.id)
            RoleRepository(session).create(scope_a, "หัวหน้าทีมขาย", {"deal.create"})
            session.flush()
            assert RoleRepository(session).get(scope_a, "หัวหน้าทีมขาย") is not None
            assert RoleRepository(session).get(scope_b, "หัวหน้าทีมขาย") is None
            with pytest.raises(Phase2Conflict):
                RoleRepository(session).delete(scope_a, "owner")
            session.rollback()

    def test_license_settings_are_isolated_and_upsert_by_business_key(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase2 import LicenseSettingRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_a, _, _ = _phase2_tenant(session)
            license_b, _, _ = _phase2_tenant(session)
            repo = LicenseSettingRepository(session)
            repo.upsert(TenantScope(license_a.id), "chat_sla", {"minutes": 15})
            repo.upsert(TenantScope(license_a.id), "chat_sla", {"minutes": 10})
            session.flush()
            rows_a = repo.list(TenantScope(license_a.id))
            rows_b = repo.list(TenantScope(license_b.id))
            assert [(row.setting_key, row.setting_value) for row in rows_a] == [
                ("chat_sla", {"minutes": 10})
            ]
            assert rows_b == []
            session.rollback()

    def test_duplicate_role_creation_race_has_one_winner(self, migrated_db):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase2 import RoleRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, _, _ = _phase2_tenant(session)
            license_id = license_row.id
            session.commit()

        role_name = f"race-{uuid.uuid4().hex[:8]}"
        barrier = Barrier(2)

        def create_once():
            with Session(migrated_db) as session:
                try:
                    barrier.wait(timeout=10)
                    RoleRepository(session).create(
                        TenantScope(license_id), role_name, {"deal.create"}
                    )
                    session.commit()
                    return "created"
                except IntegrityError:
                    session.rollback()
                    return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _value: create_once(), range(2)))
        assert sorted(results) == ["created", "duplicate"]

    def test_data_api_role_grant_is_used_by_authorization_context(
        self, migrated_db, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import Session

        from chann_data.config import settings
        from chann_data.db import get_session
        from chann_data.main import app

        with Session(migrated_db) as session:
            license_row, _, member = _phase2_tenant(session)
            license_id, member_uid = license_row.id, member.chann_uid
            session.commit()

        def override_session():
            with Session(migrated_db) as session:
                yield session

        monkeypatch.setattr(settings, "admin_secret", "integration-secret")
        app.dependency_overrides[get_session] = override_session
        headers = {"X-Internal-Secret": "integration-secret"}
        try:
            client = TestClient(app)
            create = client.post(
                f"/internal/v1/licenses/{license_id}/roles",
                headers=headers,
                json={
                    "role_name": "หัวหน้าทีมขาย",
                    "permission_keys": ["deal.create"],
                },
            )
            assert create.status_code == 201, create.text
            assign = client.patch(
                f"/internal/v1/licenses/{license_id}/members/{member_uid}/role",
                headers=headers,
                json={"role_name": "หัวหน้าทีมขาย"},
            )
            assert assign.status_code == 200, assign.text
            context = client.get(
                f"/internal/v1/licenses/{license_id}/authorization/{member_uid}",
                headers=headers,
            )
            assert context.status_code == 200, context.text
            assert context.json()["permission_keys"] == ["deal.create"]
        finally:
            app.dependency_overrides.clear()


class TestPhase2OwnershipTransfer:
    def test_two_party_transfer_requires_target_acceptance(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember
        from chann_data.repositories.phase2 import OwnershipTransferRepository, Phase2Conflict
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, owner, member = _phase2_tenant(session)
            license_id = license_row.id
            owner_uid, member_uid = owner.chann_uid, member.chann_uid
            owner_id, member_id = owner.id, member.id
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(license_id)
            transfer = OwnershipTransferRepository(session).request(scope, owner_uid, member_uid)
            with pytest.raises(Phase2Conflict):
                OwnershipTransferRepository(session).accept(scope, transfer.id, owner_uid)
            session.rollback()

        with Session(migrated_db) as session:
            # The rejected acceptance was rolled back with its request; create
            # a fresh authoritative request and let the nominated user accept.
            scope = TenantScope(license_id)
            transfer = OwnershipTransferRepository(session).request(scope, owner_uid, member_uid)
            OwnershipTransferRepository(session).accept(scope, transfer.id, member_uid)
            session.commit()
            assert session.get(LicenseMember, owner_id).role == "admin"
            assert session.get(LicenseMember, member_id).role == "owner"

    def test_break_glass_keeps_at_least_one_owner(self, migrated_db):
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember
        from chann_data.repositories.phase2 import OwnershipTransferRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, _, member = _phase2_tenant(session)
            license_id, target_uid = license_row.id, member.chann_uid
            OwnershipTransferRepository(session).force(TenantScope(license_id), target_uid)
            session.commit()
            owner_count = session.execute(
                select(func.count()).select_from(LicenseMember).where(
                    LicenseMember.license_id == license_id,
                    LicenseMember.role == "owner",
                )
            ).scalar_one()
            assert owner_count == 1

    def test_break_glass_cancels_stale_pending_transfer(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase2 import (
            OwnershipTransferRepository,
            Phase2Conflict,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, owner, member = _phase2_tenant(session)
            license_id = license_row.id
            scope = TenantScope(license_id)
            transfer = OwnershipTransferRepository(session).request(
                scope, owner.chann_uid, member.chann_uid
            )
            transfer_id = transfer.id
            target_uid = member.chann_uid
            OwnershipTransferRepository(session).force(scope, target_uid)
            session.commit()

        with Session(migrated_db) as session:
            with pytest.raises(Phase2Conflict):
                OwnershipTransferRepository(session).accept(
                    TenantScope(license_id), transfer_id, target_uid
                )

class TestTenantIsolationCrossProbe:
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
