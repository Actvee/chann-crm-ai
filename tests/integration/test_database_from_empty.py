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


# migrated_db now lives in conftest.py — a second integration file needed it,
# and duplicating a fixture that drops and rebuilds the schema would be two
# chances to disagree about what "a migrated database" means.


def _run_seed(env_overrides: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, **env_overrides}
    return subprocess.run(
        [sys.executable, str(ROOT / "database/scripts/seed_reference.py")],
        capture_output=True, text=True, env=env,
    )


class TestMigration:
    def test_percent_encoded_database_url_does_not_break_configparser(self):
        """Regression for a real deploy failure: a Cloud SQL Unix-socket
        DATABASE_URL (?host=%2Fcloudsql%2Fproject%3Aregion%3Ainstance) is
        full of "%" characters. env.py used to hand this straight to
        configparser via config.set_main_option("sqlalchemy.url", ...),
        which treats "%" as the start of a %(name)s interpolation
        reference and raised ValueError("invalid interpolation syntax")
        before a single query ran — on the actual deploy, not in any test,
        because no earlier migration ever ran against a URL with
        percent-encoding in it (direct host:port URLs don't need it).

        Doesn't need a real Unix socket to prove the fix: any DATABASE_URL
        containing literal "%" characters must get PAST env.py's own
        module-load step. A subsequent connection failure for an
        intentionally-bogus host is fine and expected here — only the
        configparser crash is what this guards against.
        """
        fake_socket_path = "/nonexistent/socket/path"
        percent_encoded_url = (
            f"{TEST_DATABASE_URL}?host=" + fake_socket_path.replace("/", "%2F")
        )
        env = {**os.environ, "DATABASE_URL": percent_encoded_url}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            cwd=str(ROOT / "database"), env=env, capture_output=True, text=True,
        )
        assert "invalid interpolation syntax" not in result.stderr, (
            "env.py is passing a % containing URL through configparser again "
            f"— full stderr:\n{result.stderr}"
        )

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


class TestPhase2RoleNameRegression:
    """Regression: license_members.role ไม่มี FK ไป custom_roles

    ก่อนแก้ การโอน ownership เขียน source.role = "admin" ตรงๆ ถ้า tenant
    เปลี่ยนชื่อหรือลบ role "admin" ไปแล้ว เจ้าของเดิมจะเหลือ role ที่ไม่มีแถว
    ใน custom_roles -> AuthorizationRepository.context คืน permission ศูนย์ตัว
    -> ถูกล็อกออกจากระบบตัวเอง กู้ได้แค่ผ่าน Platform Admin break-glass
    """

    def test_transfer_refuses_when_demotion_role_missing(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember
        from chann_data.repositories.phase2 import (
            OwnershipTransferRepository,
            Phase2Conflict,
            RoleRepository,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, owner, member = _phase2_tenant(session)
            license_id = license_row.id
            owner_uid, member_uid = owner.chann_uid, member.chann_uid
            owner_id, member_id = owner.id, member.id
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(license_id)
            # tenant เปลี่ยนชื่อ role admin -> ผู้จัดการ (ทำได้ ไม่มีอะไรห้าม)
            RoleRepository(session).update(
                scope,
                "admin",
                "ผู้จัดการ",
                RoleRepository(session).permission_keys(scope, "admin"),
            )
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(license_id)
            transfer = OwnershipTransferRepository(session).request(
                scope, owner_uid, member_uid
            )
            # ต้องปฏิเสธอย่างชัดเจน ไม่ใช่เขียนชื่อ role ลอยๆ ลงไปเงียบๆ
            with pytest.raises(Phase2Conflict):
                OwnershipTransferRepository(session).accept(
                    scope, transfer.id, member_uid
                )
            session.rollback()

        with Session(migrated_db) as session:
            # เจ้าของเดิมต้องยังเป็น owner อยู่ ไม่ถูกลดสิทธิ์ไปเป็นอะไรที่ไม่มีจริง
            assert session.get(LicenseMember, owner_id).role == "owner"
            assert session.get(LicenseMember, member_id).role != "owner"

    def test_break_glass_finds_owner_after_owner_role_renamed(self, migrated_db):
        """break-glass ต้องหา owner เจอผ่าน custom_roles.is_owner

        ถ้ายัง select ด้วย role == "owner" ตรงๆ วันที่ owner role ถูกตั้งชื่อ
        เป็นอย่างอื่น break-glass จะ raise 'tenant has no current owner'
        พอดีตอนที่จำเป็นต้องใช้มันที่สุด
        """
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from chann_data.models import CustomRole, LicenseMember
        from chann_data.repositories.phase2 import OwnershipTransferRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            license_row, owner, member = _phase2_tenant(session)
            license_id = license_row.id
            member_uid = member.chann_uid
            owner_id, member_id = owner.id, member.id
            session.commit()

        with Session(migrated_db) as session:
            # จำลองว่า owner role ถูกตั้งชื่อเป็นภาษาไทย (ผ่าน ORM ตรงๆ
            # เพราะ RoleRepository.update กัน owner ไว้อยู่แล้ว)
            role = session.execute(
                select(CustomRole).where(
                    CustomRole.license_id == license_id,
                    CustomRole.is_owner.is_(True),
                )
            ).scalars().first()
            assert role is not None
            renamed = "เจ้าของ"
            session.execute(
                LicenseMember.__table__.update()
                .where(
                    LicenseMember.license_id == license_id,
                    LicenseMember.role == role.role_name,
                )
                .values(role=renamed)
            )
            role.role_name = renamed
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(license_id)
            OwnershipTransferRepository(session).force(scope, member_uid)
            session.commit()
            assert session.get(LicenseMember, member_id).role == "เจ้าของ"
            assert session.get(LicenseMember, owner_id).role == "admin"


class TestPhase3AuditLog:
    """Master Spec 3.5 — mandatory audit emission + cross-tenant audit tests."""

    def test_audit_emission(self, migrated_db, monkeypatch):
        from sqlalchemy.orm import Session

        from chann_data.db import get_session
        from chann_data.main import app
        from chann_data.config import settings

        with Session(migrated_db) as session:
            license_row, owner, member = _phase2_tenant(session)
            license_id = license_row.id
            owner_uid = owner.chann_uid
            session.commit()

        def override_session():
            with Session(migrated_db) as session:
                yield session

        monkeypatch.setattr(settings, "admin_secret", "integration-secret")
        app.dependency_overrides[get_session] = override_session
        try:
            from fastapi.testclient import TestClient

            client = TestClient(app)
            headers = {
                "X-Internal-Secret": "integration-secret",
                "X-Actor-Id": owner_uid,
            }

            # create -> audit row with action=create
            resp = client.post(
                f"/internal/v1/licenses/{license_id}/roles",
                headers=headers,
                json={"role_name": "auditor", "permission_keys": ["ticket.read"]},
            )
            assert resp.status_code == 201, resp.text

            # update -> audit row with action=update, field_changes has old+new
            resp = client.patch(
                f"/internal/v1/licenses/{license_id}/roles/auditor",
                headers=headers,
                json={"role_name": "auditor", "permission_keys": ["ticket.read", "ticket.update"]},
            )
            assert resp.status_code == 200, resp.text

            # delete -> audit row with action=delete
            resp = client.delete(
                f"/internal/v1/licenses/{license_id}/roles/auditor", headers=headers
            )
            assert resp.status_code == 204, resp.text

            # read -> must NOT produce an audit row
            resp = client.get(f"/internal/v1/licenses/{license_id}/roles", headers=headers)
            assert resp.status_code == 200, resp.text

            log = client.get(
                f"/internal/v1/licenses/{license_id}/audit-log",
                headers=headers,
                params={"entity_type": "role"},
            )
            assert log.status_code == 200, log.text
            rows = log.json()
            actions = [r["action"] for r in rows]
            assert actions.count("create") == 1
            assert actions.count("update") == 1
            assert actions.count("delete") == 1
            # the list_roles GET must not have produced an extra row
            assert len(rows) == 3

            update_row = next(r for r in rows if r["action"] == "update")
            assert update_row["field_changes"]["permission_keys"]["old"] == ["ticket.read"]
            assert update_row["field_changes"]["permission_keys"]["new"] == [
                "ticket.read",
                "ticket.update",
            ]
            assert all(r["actor_id"] == owner_uid for r in rows)
            assert all(r["actor_type"] == "user" for r in rows)

            # AI actor -> ai_reasoning must be non-empty (Phase 4 will be the
            # real caller; exercised here directly through the generic write
            # endpoint since no AI actor exists yet in this phase)
            ai_resp = client.post(
                "/internal/v1/audit-log",
                headers=headers,
                json={
                    "license_id": str(license_id),
                    "entity_type": "customer",
                    "entity_id": str(uuid.uuid4()),
                    "actor_type": "ai",
                    "action": "create",
                    "ai_reasoning": "user typed 'add customer somchai' -> create customer intent",
                },
            )
            assert ai_resp.status_code == 201, ai_resp.text
            assert ai_resp.json()["ai_reasoning"]

            # ai_reasoning on a non-AI actor must be rejected (DB check constraint)
            bad_resp = client.post(
                "/internal/v1/audit-log",
                headers=headers,
                json={
                    "license_id": str(license_id),
                    "entity_type": "customer",
                    "entity_id": str(uuid.uuid4()),
                    "actor_type": "user",
                    "action": "create",
                    "ai_reasoning": "should not be allowed on a human actor",
                },
            )
            assert bad_resp.status_code >= 400
        finally:
            app.dependency_overrides.clear()

    def test_cross_tenant_audit(self, migrated_db, monkeypatch):
        from sqlalchemy.orm import Session

        from chann_data.db import get_session
        from chann_data.main import app
        from chann_data.config import settings

        with Session(migrated_db) as session:
            license_a, owner_a, _ = _phase2_tenant(session)
            license_b, _, member_b = _phase2_tenant(session)
            license_a_id, license_b_id = license_a.id, license_b.id
            owner_a_uid, member_b_uid = owner_a.chann_uid, member_b.chann_uid
            session.commit()

        def override_session():
            with Session(migrated_db) as session:
                yield session

        monkeypatch.setattr(settings, "admin_secret", "integration-secret")
        app.dependency_overrides[get_session] = override_session
        try:
            from fastapi.testclient import TestClient

            client = TestClient(app)
            headers = {"X-Internal-Secret": "integration-secret"}

            # cross-company lookup (refused) -> audit row, cross_tenant=true
            resp = client.get(
                f"/internal/v1/licenses/{license_a_id}/members/{member_b_uid}/cross-check",
                params={"target_license_id": str(license_b_id)},
                headers=headers,
            )
            assert resp.status_code == 403

            # break-glass force transfer -> audit row, cross_tenant=true
            resp = client.post(
                f"/internal/v1/platform/licenses/{license_a_id}/break-glass/transfer-owner",
                headers={**headers, "X-Actor-Id": "PA-000001"},
                json={"target_chann_uid": owner_a_uid},
            )
            assert resp.status_code == 200, resp.text

            # a same-tenant action for comparison -> cross_tenant must be false
            resp = client.get(f"/internal/v1/licenses/{license_a_id}/roles", headers=headers)
            assert resp.status_code == 200

            log = client.get(
                f"/internal/v1/licenses/{license_a_id}/audit-log", headers=headers
            )
            assert log.status_code == 200, log.text
            rows = log.json()

            cross_tenant_rows = [r for r in rows if r["cross_tenant"]]
            assert len(cross_tenant_rows) >= 2
            assert any(r["action"] == "cross_tenant_lookup" for r in cross_tenant_rows)
            assert any(r["action"] == "transfer" for r in cross_tenant_rows)

            same_tenant_rows = [r for r in rows if not r["cross_tenant"]]
            assert len(same_tenant_rows) == 0 or all(
                r["action"] != "cross_tenant_lookup" for r in same_tenant_rows
            )
        finally:
            app.dependency_overrides.clear()


class TestPhase6DataLayer:
    """Phase 6 round 1 — data layer only (Master Spec 6.3).

    The chat/notification-sending behaviour these tables support is round 2
    (Application tier). What is asserted here is the storage contract those
    handlers will rely on: tenant isolation, idempotency, and the state rules.
    """

    def test_message_entity_map_is_idempotent_and_tenant_scoped(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import (
            MessageEntityMapRepository,
            Phase6Conflict,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic_a, _, _ = _phase2_tenant(session)
            lic_b, _, _ = _phase2_tenant(session)
            a_id, b_id = lic_a.id, lic_b.id
            session.commit()

        entity = uuid.uuid4()
        with Session(migrated_db) as session:
            scope_a = TenantScope(a_id)
            repo = MessageEntityMapRepository(session)
            first = repo.record(
                scope_a, message_id="msg-1", entity_type="customer", entity_id=entity
            )
            # LINE redelivers webhooks — the same mapping twice must be a no-op
            again = repo.record(
                scope_a, message_id="msg-1", entity_type="customer", entity_id=entity
            )
            assert first.id == again.id
            session.commit()

        with Session(migrated_db) as session:
            repo = MessageEntityMapRepository(session)
            # same message_id, different entity -> real conflict, not a silent overwrite
            with pytest.raises(Phase6Conflict):
                repo.record(
                    TenantScope(a_id),
                    message_id="msg-1",
                    entity_type="deal",
                    entity_id=uuid.uuid4(),
                )
            session.rollback()

        with Session(migrated_db) as session:
            repo = MessageEntityMapRepository(session)
            assert repo.get(TenantScope(a_id), "msg-1") is not None
            # tenant B must not resolve tenant A's message id
            assert repo.get(TenantScope(b_id), "msg-1") is None
            assert repo.get(TenantScope(a_id), "no-such-message") is None

    def test_notification_read_and_unread_count(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import (
            NotificationRepository,
            Phase6NotFound,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, owner, member = _phase2_tenant(session)
            lic_id = lic.id
            owner_uid, member_uid = owner.chann_uid, member.chann_uid
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = NotificationRepository(session)
            n1 = repo.create(
                scope, target_chann_uid=owner_uid, type="followup_due",
                message="ครบกำหนดติดตาม", message_en="Follow-up due",
            )
            repo.create(
                scope, target_chann_uid=owner_uid, type="sla_warning",
                message="ใกล้ผิด SLA",
            )
            # goes to someone else — must not affect the owner's count
            repo.create(
                scope, target_chann_uid=member_uid, type="sla_warning",
                message="ของอีกคน",
            )
            # LINE-only: recorded, but must stay out of the dashboard list
            repo.create(
                scope, target_chann_uid=owner_uid, type="chat_session_new",
                message="เฉพาะ LINE", delivery_dashboard=False,
            )
            n1_id = n1.id
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = NotificationRepository(session)
            assert repo.unread_count(scope, owner_uid) == 2
            assert repo.unread_count(scope, member_uid) == 1
            assert len(repo.list_for_member(scope, owner_uid)) == 2

            repo.mark_read(scope, n1_id, owner_uid)
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = NotificationRepository(session)
            assert repo.unread_count(scope, owner_uid) == 1
            row = next(
                r for r in repo.list_for_member(scope, owner_uid) if r.id == n1_id
            )
            assert row.read_at is not None
            first_seen = row.read_at

            # another member must not be able to clear someone else's badge
            with pytest.raises(Phase6NotFound):
                repo.mark_read(scope, n1_id, member_uid)
            session.rollback()

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = NotificationRepository(session)
            repo.mark_read(scope, n1_id, owner_uid)   # idempotent
            session.commit()
        with Session(migrated_db) as session:
            row = NotificationRepository(session).mark_read(
                TenantScope(lic_id), n1_id, owner_uid
            )
            # re-reading must not move the timestamp
            assert row.read_at == first_seen

    def test_follow_up_lifecycle_and_due_scan(self, migrated_db):
        from datetime import date, timedelta

        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import (
            FollowUpRepository,
            Phase6Conflict,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _, _ = _phase2_tenant(session)
            lic_id = lic.id
            session.commit()

        today = date(2026, 8, 20)
        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = FollowUpRepository(session)
            due_tomorrow = repo.create(
                scope, entity_type="deal", entity_id=uuid.uuid4(),
                due_date=today + timedelta(days=1),
            )
            overdue = repo.create(
                scope, entity_type="ticket", entity_id=uuid.uuid4(),
                due_date=today - timedelta(days=3),
            )
            repo.create(
                scope, entity_type="customer", entity_id=uuid.uuid4(),
                due_date=today + timedelta(days=30),
            )
            tomorrow_id, overdue_id = due_tomorrow.id, overdue.id
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = FollowUpRepository(session)
            due = repo.due_within(scope, days=1, today=today)
            ids = {f.id for f in due}
            assert tomorrow_id in ids
            # overdue must be included — dropping it would lose it forever
            assert overdue_id in ids
            assert len(due) == 2
            # ordered soonest-first, so the most overdue is chased first
            assert due[0].id == overdue_id

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = FollowUpRepository(session)
            assert repo.set_status(scope, tomorrow_id, "completed").status == "completed"
            assert repo.set_status(scope, overdue_id, "cancelled").status == "cancelled"
            session.commit()

        with Session(migrated_db) as session:
            scope = TenantScope(lic_id)
            repo = FollowUpRepository(session)
            # settled rows drop out of the due scan
            assert repo.due_within(scope, days=1, today=today) == []
            # re-completing is a harmless no-op
            repo.set_status(scope, tomorrow_id, "completed")
            # but a settled row must not be flipped to a different terminal state
            with pytest.raises(Phase6Conflict):
                repo.set_status(scope, tomorrow_id, "cancelled")
            with pytest.raises(Phase6Conflict):
                repo.set_status(scope, tomorrow_id, "bogus-status")
            session.rollback()

    def test_follow_ups_do_not_leak_across_tenants(self, migrated_db):
        from datetime import date

        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import (
            FollowUpRepository,
            Phase6NotFound,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic_a, _, _ = _phase2_tenant(session)
            lic_b, _, _ = _phase2_tenant(session)
            a_id, b_id = lic_a.id, lic_b.id
            session.commit()

        with Session(migrated_db) as session:
            row = FollowUpRepository(session).create(
                TenantScope(a_id), entity_type="deal", entity_id=uuid.uuid4(),
                due_date=date(2026, 9, 1),
            )
            a_follow_up = row.id
            session.commit()

        with Session(migrated_db) as session:
            repo = FollowUpRepository(session)
            assert repo.get(TenantScope(a_id), a_follow_up) is not None
            assert repo.get(TenantScope(b_id), a_follow_up) is None
            assert repo.list_for_license(TenantScope(b_id)) == []
            with pytest.raises(Phase6NotFound):
                repo.set_status(TenantScope(b_id), a_follow_up, "cancelled")
            session.rollback()


class TestPhase65TenantRegistration:
    """Phase 6.5 — Master Spec 6.5.8 mandatory tests."""

    def test_license_self_registration(self, migrated_db):
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, CustomRole, License, LicenseMember
        from chann_data.repositories.phase65 import (
            OWNER_ROLE_NAME,
            RegistrationConflict,
            RegistrationRepository,
        )

        with Session(migrated_db) as session:
            session.add(
                ChannIdentity(
                    chann_uid="CHN-REG-0001",
                    line_user_id="line-reg-0001",
                    primary_role="sales",
                )
            )
            session.commit()

        with Session(migrated_db) as session:
            row = RegistrationRepository(session).create_license(
                company_name="บริษัท ทดสอบ จำกัด",
                created_by_chann_uid="CHN-REG-0001",
                display_name="ผู้ก่อตั้ง",
            )
            license_id = row.id
            assert row.status == "trial"
            assert row.trial_expires_at is not None
            assert row.company_code and len(row.company_code) == 8
            # must be typeable off a phone screen / over the phone
            assert not set(row.company_code) & set("01OIL")
            session.commit()

        with Session(migrated_db) as session:
            # owner membership created
            member = session.execute(
                select(LicenseMember).where(
                    LicenseMember.license_id == license_id,
                    LicenseMember.chann_uid == "CHN-REG-0001",
                )
            ).scalars().one()
            assert member.role == OWNER_ROLE_NAME

            # all default role templates seeded, owner flagged — 5 as of
            # "technician" joining owner/admin/member/cs in
            # DEFAULT_ROLE_TEMPLATES
            roles = list(
                session.execute(
                    select(CustomRole).where(CustomRole.license_id == license_id)
                ).scalars()
            )
            assert len(roles) == 5
            assert sum(1 for r in roles if r.is_owner) == 1

            # trial deadline ~30 days out
            lic = session.get(License, license_id)
            delta = lic.trial_expires_at - lic.created_at
            assert 29 <= delta.days <= 30

        # one LINE identity, one company
        with Session(migrated_db) as session:
            with pytest.raises(RegistrationConflict):
                RegistrationRepository(session).create_license(
                    company_name="บริษัทที่สอง",
                    created_by_chann_uid="CHN-REG-0001",
                )
            session.rollback()

    def test_one_company_limit_holds_under_concurrency(self, migrated_db):
        """Two webhook deliveries racing must still yield one company.

        The application-level check alone cannot guarantee this — both
        sessions can pass it before either commits — so the partial unique
        index is what actually holds. This asserts the index exists and bites.
        """
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, License

        with Session(migrated_db) as session:
            session.add(
                ChannIdentity(
                    chann_uid="CHN-RACE-0001",
                    line_user_id="line-race-0001",
                    primary_role="sales",
                )
            )
            session.commit()

        # Bypass the repository's pre-check and hit the DB constraint directly,
        # which is exactly what a lost race looks like.
        with Session(migrated_db) as s1, Session(migrated_db) as s2:
            for s in (s1, s2):
                s.add(
                    License(
                        id=uuid.uuid4(),
                        license_code=f"RACE{uuid.uuid4().hex[:6].upper()}",
                        company_name="race",
                        company_code=uuid.uuid4().hex[:8].upper(),
                        status="trial",
                        created_by_chann_uid="CHN-RACE-0001",
                    )
                )
            s1.commit()
            with pytest.raises(IntegrityError):
                s2.commit()
            s2.rollback()

        with Session(migrated_db) as session:
            rows = list(
                session.execute(
                    select(License).where(
                        License.created_by_chann_uid == "CHN-RACE-0001"
                    )
                ).scalars()
            )
            assert len(rows) == 1

    def test_invite_redeem(self, migrated_db):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import (
            RegistrationConflict,
            RegistrationNotFound,
            RegistrationRepository,
        )

        with Session(migrated_db) as session:
            for n in range(1, 5):
                session.add(
                    ChannIdentity(
                        chann_uid=f"CHN-INV-000{n}",
                        line_user_id=f"line-inv-000{n}",
                        primary_role="sales",
                    )
                )
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            lic = repo.create_license(
                company_name="Invite Co", created_by_chann_uid="CHN-INV-0001"
            )
            license_id = lic.id
            good = repo.create_invite(license_id, role="member", max_uses=2)
            expired = repo.create_invite(license_id, role="member", expires_in_days=None)
            expired.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            revoked = repo.create_invite(license_id, role="member")
            good_code, expired_code, revoked_code = (
                good.invite_code, expired.invite_code, revoked.invite_code
            )
            repo.revoke_invite(license_id, revoked.id)
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            member = repo.redeem_invite(
                invite_code=good_code, chann_uid="CHN-INV-0002"
            )
            assert member.role == "member"
            assert member.license_id == license_id
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            # re-redeeming by the same person must not burn a use
            again = repo.redeem_invite(invite_code=good_code, chann_uid="CHN-INV-0002")
            assert again.id is not None
            invites = {i.invite_code: i for i in repo.list_invites(license_id)}
            assert invites[good_code].used_count == 1
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            with pytest.raises(RegistrationConflict):
                repo.redeem_invite(invite_code=expired_code, chann_uid="CHN-INV-0003")
            session.rollback()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            with pytest.raises(RegistrationConflict):
                repo.redeem_invite(invite_code=revoked_code, chann_uid="CHN-INV-0003")
            session.rollback()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            with pytest.raises(RegistrationNotFound):
                repo.redeem_invite(invite_code="NOSUCHCODE", chann_uid="CHN-INV-0003")
            session.rollback()

        # exhaust the remaining use, then the next person is refused
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            repo.redeem_invite(invite_code=good_code, chann_uid="CHN-INV-0003")
            session.commit()
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            with pytest.raises(RegistrationConflict):
                repo.redeem_invite(invite_code=good_code, chann_uid="CHN-INV-0004")
            session.rollback()

    def test_invite_cannot_grant_ownership(self, migrated_db):
        """Owner must only change hands through Phase 2's two-party transfer."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import (
            OWNER_ROLE_NAME,
            RegistrationConflict,
            RegistrationRepository,
        )

        with Session(migrated_db) as session:
            session.add(
                ChannIdentity(
                    chann_uid="CHN-OWN-0001",
                    line_user_id="line-own-0001",
                    primary_role="sales",
                )
            )
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            lic = repo.create_license(
                company_name="Owner Co", created_by_chann_uid="CHN-OWN-0001"
            )
            with pytest.raises(RegistrationConflict):
                repo.create_invite(lic.id, role=OWNER_ROLE_NAME)
            with pytest.raises(RegistrationConflict):
                repo.create_invite(lic.id, role="no-such-role")
            session.rollback()

    def test_technician_invite_self_heals_missing_role(self, migrated_db):
        """"technician" was added to DEFAULT_ROLE_TEMPLATES after some
        tenants already existed. create_invite must provision the CustomRole
        + RolePermission rows on first use rather than rejecting — no
        migration should be required for a tenant created before this."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, CustomRole, RolePermission
        from chann_data.permissions import DEFAULT_ROLE_TEMPLATES
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-TECH-0001", line_user_id="line-tech-0001",
                primary_role="sales",
            ))
            session.add(ChannIdentity(
                chann_uid="CHN-TECH-0002", line_user_id="line-tech-0002",
                primary_role="technician",
            ))
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            lic = repo.create_license(
                company_name="Repair Co", created_by_chann_uid="CHN-TECH-0001",
            )
            license_id = lic.id
            # Simulate a pre-existing tenant: delete the role this same
            # _seed_role_templates call just wrote, so create_invite has to
            # provision it from scratch, exactly like a tenant that existed
            # before "technician" was added to DEFAULT_ROLE_TEMPLATES.
            session.execute(
                RolePermission.__table__.delete().where(
                    RolePermission.license_id == license_id,
                    RolePermission.role == "technician",
                )
            )
            session.execute(
                CustomRole.__table__.delete().where(
                    CustomRole.license_id == license_id,
                    CustomRole.role_name == "technician",
                )
            )
            session.commit()

        with Session(migrated_db) as session:
            assert session.execute(
                select(CustomRole).where(
                    CustomRole.license_id == license_id,
                    CustomRole.role_name == "technician",
                )
            ).scalars().first() is None

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            invite = repo.create_invite(license_id, role="technician")
            session.commit()
            invite_code = invite.invite_code

        with Session(migrated_db) as session:
            role_row = session.execute(
                select(CustomRole).where(
                    CustomRole.license_id == license_id,
                    CustomRole.role_name == "technician",
                )
            ).scalars().one()
            assert role_row.is_owner is False
            granted = {
                r.permission_key for r in session.execute(
                    select(RolePermission).where(
                        RolePermission.license_id == license_id,
                        RolePermission.role == "technician",
                    )
                ).scalars()
            }
            assert granted == DEFAULT_ROLE_TEMPLATES["technician"]

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            member = repo.redeem_invite(
                invite_code=invite_code, chann_uid="CHN-TECH-0002",
            )
            assert member.role == "technician"
            session.commit()

    def test_memberships_of_is_oa_scoped(self, migrated_db):
        """A Sales-side membership must not, by itself, satisfy Technician
        OA — and a technician-role membership must not satisfy Sales OA.
        Directly reported: an account already registered as Sales staff
        could message the Technician OA and be treated as already belonging
        to that company there too.

        Uses two identities, not one: redeem_invite treats an existing
        license_members row at that license as already-a-member and returns
        it UNCHANGED rather than adding a second role (see redeem_invite's
        "do NOT burn a use" branch) — one chann_uid holds exactly one role
        per license, by design. A technician joining a company is a
        different person from its owner in practice anyway.
        """
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import MemberRepository

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-OASCOPE-0001", line_user_id="line-oascope-0001",
                primary_role="sales",
            ))
            session.add(ChannIdentity(
                chann_uid="CHN-OASCOPE-0002", line_user_id="line-oascope-0002",
                primary_role="technician",
            ))
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            lic = repo.create_license(
                company_name="Scoped Co", created_by_chann_uid="CHN-OASCOPE-0001",
            )
            license_id = lic.id
            # A different person entirely redeems a technician invite at the
            # same company — the owner's own membership is untouched.
            tech_invite = repo.create_invite(license_id, role="technician")
            session.commit()
            tech_code = tech_invite.invite_code

        with Session(migrated_db) as session:
            member_repo = MemberRepository(session)
            # OWNER-APPROVED CHANGE. This used to require role ==
            # "technician" exactly, which was right about the risk — a
            # salesperson must not silently become a technician — and
            # wrong about who actually works: in a small shop the owner
            # goes out on jobs, and the rule told them they were "not
            # linked to any company as a technician" at their own company.
            #
            # The gate is ticket.read now, which is the same capability
            # the channel already uses to decide what may be done once
            # someone is inside it.
            owner_as_tech = member_repo.memberships_of("CHN-OASCOPE-0001", oa="technician")
            assert len(owner_as_tech) == 1, (
                "an owner holds ticket.read and does field work in a small shop"
            )
            owner_as_sales = member_repo.memberships_of("CHN-OASCOPE-0001", oa="sales")
            assert len(owner_as_sales) == 1 and owner_as_sales[0].role == "owner"

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            repo.redeem_invite(invite_code=tech_code, chann_uid="CHN-OASCOPE-0002")
            session.commit()

        with Session(migrated_db) as session:
            member_repo = MemberRepository(session)
            tech_ok = member_repo.memberships_of("CHN-OASCOPE-0002", oa="technician")
            assert len(tech_ok) == 1 and tech_ok[0].role == "technician"
            # The technician must not show up as a valid Sales OA member at
            # the same company.
            tech_as_sales = member_repo.memberships_of("CHN-OASCOPE-0002", oa="sales")
            assert tech_as_sales == []
            # And the owner's own scoping is unaffected by a second person
            # joining the same license with a different role.
            owner_as_sales = member_repo.memberships_of("CHN-OASCOPE-0001", oa="sales")
            assert {m.role for m in owner_as_sales} == {"owner"}
            unscoped = member_repo.memberships_of("CHN-OASCOPE-0002")
            assert {m.role for m in unscoped} == {"technician"}

    def test_customer_license_link_is_separate_from_staff_membership(self, migrated_db):
        """The other half of the same gap: a Sales/Owner account must not be
        treated as an automatic customer of its own company. Only a real
        customer_license_links row (via company code) counts."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-CUSTSCOPE-0001", line_user_id="line-custscope-0001",
                primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            lic = repo.create_license(
                company_name="Custscope Co", created_by_chann_uid="CHN-CUSTSCOPE-0001",
            )
            license_id = lic.id
            company_code = lic.company_code
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            before = repo.my_shops("CHN-CUSTSCOPE-0001")
            assert before == [], (
                "the owner's own account must not already appear as a "
                "customer of the company it owns"
            )
            repo.link_customer(chann_uid="CHN-CUSTSCOPE-0001", company_code=company_code)
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            after = repo.my_shops("CHN-CUSTSCOPE-0001")
            assert len(after) == 1 and after[0].id == license_id

    def test_customer_license_link(self, migrated_db):
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, LicenseMember
        from chann_data.repositories.phase65 import (
            RegistrationNotFound,
            RegistrationRepository,
        )

        with Session(migrated_db) as session:
            for uid in ("CHN-SHOP-0001", "CHN-SHOP-0002", "CHN-CUST-0001"):
                session.add(
                    ChannIdentity(
                        chann_uid=uid, line_user_id=f"line-{uid}", primary_role="customer"
                    )
                )
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            a = repo.create_license(company_name="ร้าน ก", created_by_chann_uid="CHN-SHOP-0001")
            b = repo.create_license(company_name="ร้าน ข", created_by_chann_uid="CHN-SHOP-0002")
            code_a, code_b = a.company_code, b.company_code
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            repo.link_customer(chann_uid="CHN-CUST-0001", company_code=code_a)
            repo.link_customer(chann_uid="CHN-CUST-0001", company_code=code_b)
            # idempotent
            repo.link_customer(chann_uid="CHN-CUST-0001", company_code=code_a)
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            shops = repo.my_shops("CHN-CUST-0001")
            assert {s.company_name for s in shops} == {"ร้าน ก", "ร้าน ข"}

            # the crucial one: linking grants NO membership anywhere
            memberships = list(
                session.execute(
                    select(LicenseMember).where(
                        LicenseMember.chann_uid == "CHN-CUST-0001"
                    )
                ).scalars()
            )
            assert memberships == []

            with pytest.raises(RegistrationNotFound):
                repo.link_customer(chann_uid="CHN-CUST-0001", company_code="BADCODE1")

    def test_public_shop_search(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            for n in (1, 2):
                session.add(
                    ChannIdentity(
                        chann_uid=f"CHN-SEARCH-000{n}",
                        line_user_id=f"line-search-000{n}",
                        primary_role="sales",
                    )
                )
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            visible = repo.create_license(
                company_name="ร้านมองเห็นได้", created_by_chann_uid="CHN-SEARCH-0001"
            )
            hidden = repo.create_license(
                company_name="ร้านถูกระงับ", created_by_chann_uid="CHN-SEARCH-0002"
            )
            visible_code = visible.company_code
            repo.set_status(hidden.id, "suspended")
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            names = {s.company_name for s in repo.find_shops("ร้าน")}
            assert "ร้านมองเห็นได้" in names
            # suspended shops must not be findable
            assert "ร้านถูกระงับ" not in names

            # exact company code also resolves
            assert any(
                s.company_code == visible_code for s in repo.find_shops(visible_code)
            )

            # too-short queries are refused rather than returning the table
            assert repo.find_shops("ร") == []
            assert repo.find_shops("") == []

    def test_trial_expiry_suspends_without_deleting(self, migrated_db):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, CustomRole, License, LicenseMember
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            session.add(
                ChannIdentity(
                    chann_uid="CHN-TRIAL-0001",
                    line_user_id="line-trial-0001",
                    primary_role="sales",
                )
            )
            session.commit()

        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            lic = repo.create_license(
                company_name="Trial Co", created_by_chann_uid="CHN-TRIAL-0001"
            )
            license_id = lic.id
            # backdate the deadline rather than sleeping 30 days
            lic.trial_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            session.commit()

        with Session(migrated_db) as session:
            expired = RegistrationRepository(session).expire_due_trials()
            assert [r.id for r in expired] == [license_id]
            session.commit()

        with Session(migrated_db) as session:
            lic = session.get(License, license_id)
            assert lic.status == "suspended"
            # nothing was deleted — data must survive non-payment
            assert lic.company_name == "Trial Co"
            # 5 default roles as of "technician" joining
            # owner/admin/member/cs in DEFAULT_ROLE_TEMPLATES
            assert len(list(session.execute(
                select(CustomRole).where(CustomRole.license_id == license_id)
            ).scalars())) == 5
            assert len(list(session.execute(
                select(LicenseMember).where(LicenseMember.license_id == license_id)
            ).scalars())) == 1

            # sweeping again is a no-op — already-suspended rows are not re-swept
            assert RegistrationRepository(session).expire_due_trials() == []


class TestPhase7MasterData:
    """Phase 7 — Master Spec 7.5 mandatory tests."""

    def _tenant(self, session, suffix):
        from chann_data.models import ChannIdentity, License, LicenseMember

        lic = License(
            id=uuid.uuid4(),
            license_code=f"P7{suffix}",
            company_name=f"P7 Co {suffix}",
        )
        session.add(lic)
        ident = ChannIdentity(
            chann_uid=f"CHN-P7-{suffix}",
            line_user_id=f"line-p7-{suffix}",
            primary_role="sales",
        )
        session.add(ident)
        session.flush()
        member = LicenseMember(
            id=uuid.uuid4(),
            license_id=lic.id,
            chann_uid=ident.chann_uid,
            role="member",
            status="active",
        )
        session.add(member)
        session.flush()
        return lic, member

    def test_product_crud(self, migrated_db):
        from decimal import Decimal

        from sqlalchemy.orm import Session

        from chann_data.repositories.phase7 import ProductRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _ = self._tenant(session, "CRUD")
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id)

        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            p = repo.upsert(
                scope, product_id="P001", product_name="แอร์ LG 12000 BTU",
                sku="AC-LG-12K", category="AIR_CONDITIONER", unit_price="25,000",
            )
            assert p.unit_price == Decimal("25000")
            session.commit()

        # duplicate product_id upserts rather than erroring
        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            again = repo.upsert(
                scope, product_id="P001", product_name="แอร์ LG 12000 BTU (ใหม่)",
                unit_price="26000",
            )
            assert again.product_name.endswith("(ใหม่)")
            assert again.unit_price == Decimal("26000")
            assert len(repo.list(scope)) == 1
            session.commit()

        # CSV import
        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            result = repo.upsert_csv(scope, (
                "product_id,product_name,sku,category,unit_price,description\n"
                "P002,ฟิลเตอร์ HEPA,FILTER-01,AIR_FILTER,1500,ฟิลเตอร์\n"
                "P003,ท่อทองแดง,PIPE-01,PARTS,\"2,300\",\n"
            ))
            assert result["imported"] == 2
            assert result["errors"] == []
            session.commit()

        # a bad row must not sink the whole file
        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            result = repo.upsert_csv(scope, (
                "product_id,product_name,unit_price\n"
                "P004,ดีอยู่,100\n"
                "P005,ราคาพัง,ไม่ใช่ตัวเลข\n"
                ",ไม่มีรหัส,50\n"
            ))
            assert result["imported"] == 1
            assert len(result["errors"]) == 2
            # the reported line number must match what the user sees in Excel
            assert {e["line"] for e in result["errors"]} == {3, 4}
            session.commit()

        # archive, not hard delete
        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            repo.archive(scope, "P001")
            session.commit()
        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            assert repo.get(scope, "P001") is not None          # row still there
            assert "P001" not in {p.product_id for p in repo.list(scope)}
            assert "P001" in {
                p.product_id for p in repo.list(scope, include_archived=True)
            }
            # re-adding an archived product brings it back
            repo.upsert(scope, product_id="P001", product_name="กลับมาแล้ว")
            assert "P001" in {p.product_id for p in repo.list(scope)}
            session.commit()

    def test_multi_tenant_product(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase7 import ProductRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            a, _ = self._tenant(session, "MTA")
            b, _ = self._tenant(session, "MTB")
            a_id, b_id = a.id, b.id
            session.commit()

        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            # the same product_id in two tenants must be allowed
            repo.upsert(TenantScope(a_id), product_id="P001", product_name="ของ A")
            repo.upsert(TenantScope(b_id), product_id="P001", product_name="ของ B")
            session.commit()

        with Session(migrated_db) as session:
            repo = ProductRepository(session)
            assert repo.get(TenantScope(a_id), "P001").product_name == "ของ A"
            assert repo.get(TenantScope(b_id), "P001").product_name == "ของ B"
            assert [p.product_name for p in repo.list(TenantScope(a_id))] == ["ของ A"]

    def test_sales_group(self, migrated_db):
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember
        from chann_data.repositories.phase7 import (
            MasterDataConflict,
            SalesGroupRepository,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, member = self._tenant(session, "SG")
            license_id, member_id = lic.id, member.id
            session.commit()

        scope = TenantScope(license_id)

        with Session(migrated_db) as session:
            repo = SalesGroupRepository(session)
            north = repo.create(scope, "ภาคเหนือ")
            south = repo.create(scope, "ภาคใต้")
            north_id, south_id = north.id, south.id
            with pytest.raises(MasterDataConflict):
                repo.create(scope, "ภาคเหนือ")       # duplicate name
            session.rollback()

        with Session(migrated_db) as session:
            repo = SalesGroupRepository(session)
            north = repo.create(scope, "ภาคเหนือ")
            south = repo.create(scope, "ภาคใต้")
            north_id, south_id = north.id, south.id
            # one salesperson, several groups
            repo.add_member(scope, north_id, member_id)
            repo.add_member(scope, south_id, member_id)
            repo.add_member(scope, north_id, member_id)   # idempotent
            session.commit()

        with Session(migrated_db) as session:
            repo = SalesGroupRepository(session)
            groups = repo.groups_for_member(scope, member_id)
            assert {g.group_name for g in groups} == {"ภาคเหนือ", "ภาคใต้"}
            assert len(repo.members(scope, north_id)) == 1

        # deleting a group must not delete the person
        with Session(migrated_db) as session:
            SalesGroupRepository(session).delete(scope, north_id)
            session.commit()
        with Session(migrated_db) as session:
            repo = SalesGroupRepository(session)
            assert {g.group_name for g in repo.list(scope)} == {"ภาคใต้"}
            still_there = session.execute(
                select(LicenseMember).where(LicenseMember.id == member_id)
            ).scalars().first()
            assert still_there is not None
            # and they keep their other group
            assert len(repo.groups_for_member(scope, member_id)) == 1

    def test_technician_team(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, LicenseMember
        from chann_data.repositories.phase7 import TechnicianTeamRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, tech1 = self._tenant(session, "TT")
            license_id, tech1_id = lic.id, tech1.id
            ident2 = ChannIdentity(
                chann_uid="CHN-P7-TT2", line_user_id="line-p7-tt2",
                primary_role="technician",
            )
            session.add(ident2)
            session.flush()
            tech2 = LicenseMember(
                id=uuid.uuid4(), license_id=license_id,
                chann_uid="CHN-P7-TT2", role="member", status="active",
            )
            session.add(tech2)
            session.flush()
            tech2_id = tech2.id
            session.commit()

        scope = TenantScope(license_id)

        with Session(migrated_db) as session:
            repo = TechnicianTeamRepository(session)
            a = repo.create(scope, "ทีม A")
            b = repo.create(scope, "ทีม B")
            a_id, b_id = a.id, b.id
            # one technician, several teams
            repo.add_member(scope, a_id, tech1_id, is_lead=True)
            repo.add_member(scope, b_id, tech1_id)
            # a team may have more than one lead
            repo.add_member(scope, a_id, tech2_id, is_lead=True)
            session.commit()

        with Session(migrated_db) as session:
            repo = TechnicianTeamRepository(session)
            assert {t.team_name for t in repo.teams_for_member(scope, tech1_id)} == {
                "ทีม A", "ทีม B"
            }
            leads = [m for m in repo.members(scope, a_id) if m.is_lead]
            assert len(leads) == 2, "a team must be able to have several leads"

            # re-adding updates is_lead rather than erroring
            repo.add_member(scope, b_id, tech1_id, is_lead=True)
            session.commit()
        with Session(migrated_db) as session:
            repo = TechnicianTeamRepository(session)
            b_members = repo.members(scope, b_id)
            assert len(b_members) == 1 and b_members[0].is_lead is True

            repo.set_lead(scope, b_id, tech1_id, False)
            session.commit()
        with Session(migrated_db) as session:
            repo = TechnicianTeamRepository(session)
            assert repo.members(scope, b_id)[0].is_lead is False


class TestPhase8Profiles:
    """Phase 8 — Master Spec 8.5 mandatory tests."""

    def test_profile_self_edit(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.profile import (
            ProfileConflict,
            ProfileNotFound,
            ProfileRepository,
        )

        with Session(migrated_db) as session:
            session.add(
                ChannIdentity(
                    chann_uid="CHN-P8-0001", line_user_id="line-p8-0001",
                    primary_role="technician",
                )
            )
            session.commit()

        with Session(migrated_db) as session:
            repo = ProfileRepository(session)
            row = repo.update_profile(
                "CHN-P8-0001",
                {"first_name": "สมชาย", "phone": "081-234-5678", "email": "somchai@test.com"},
            )
            assert row.first_name == "สมชาย"
            assert row.phone == "0812345678"          # normalised, hyphens stripped
            assert row.email == "somchai@test.com"
            assert row.registered is True              # first real edit marks it registered
            assert row.registered_at is not None
            session.commit()

        with Session(migrated_db) as session:
            repo = ProfileRepository(session)
            # editing again must not re-stamp registered_at
            first_registered_at = repo.get("CHN-P8-0001").registered_at
            repo.update_profile("CHN-P8-0001", {"last_name": "ใจดี"})
            session.commit()
        with Session(migrated_db) as session:
            row = ProfileRepository(session).get("CHN-P8-0001")
            assert row.last_name == "ใจดี"
            assert row.registered_at == first_registered_at

        # invalid values are refused, not silently dropped or half-applied
        with Session(migrated_db) as session:
            repo = ProfileRepository(session)
            with pytest.raises(ProfileConflict):
                repo.update_profile("CHN-P8-0001", {"phone": "not-a-phone"})
            with pytest.raises(ProfileConflict):
                repo.update_profile("CHN-P8-0001", {"email": "not-an-email"})
            with pytest.raises(ProfileConflict):
                repo.update_profile("CHN-P8-0001", {"role": "owner"})  # not editable
            session.rollback()

        with Session(migrated_db) as session:
            with pytest.raises(ProfileNotFound):
                ProfileRepository(session).update_profile(
                    "CHN-NOSUCH", {"first_name": "x"}
                )

    def test_profile_edit_on_behalf_requires_a_real_relationship(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.profile import ProfileRepository

        with Session(migrated_db) as session:
            for uid, role in (
                ("CHN-P8-SALES1", "sales"),
                ("CHN-P8-CUST1", "customer"),
                ("CHN-P8-STRANGER", "customer"),
            ):
                session.add(
                    ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role=role)
                )
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Profile Co", created_by_chann_uid="CHN-P8-SALES1"
            )
            license_id = lic.id
            RegistrationRepository(session).link_customer(
                chann_uid="CHN-P8-CUST1", company_code=lic.company_code
            )
            session.commit()

        with Session(migrated_db) as session:
            repo = ProfileRepository(session)
            # self-edit: always allowed, no relationship needed
            assert repo.may_edit_on_behalf(
                actor_chann_uid="CHN-P8-CUST1", target_chann_uid="CHN-P8-CUST1",
                license_id=license_id,
            ) is True
            # sales editing a customer actually linked to their tenant: allowed
            assert repo.may_edit_on_behalf(
                actor_chann_uid="CHN-P8-SALES1", target_chann_uid="CHN-P8-CUST1",
                license_id=license_id,
            ) is True
            # sales editing a total stranger with no link to this tenant: refused
            assert repo.may_edit_on_behalf(
                actor_chann_uid="CHN-P8-SALES1", target_chann_uid="CHN-P8-STRANGER",
                license_id=license_id,
            ) is False

    def test_profile_chat_vs_liff_use_the_same_domain_function(self, migrated_db):
        """Spec 8.5: chat and LIFF must produce identical results because both
        call update_profile — proven here by calling it twice the same way
        two different callers would, and getting the same state either time."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.profile import ProfileRepository

        with Session(migrated_db) as session:
            session.add(
                ChannIdentity(
                    chann_uid="CHN-P8-BOTH", line_user_id="line-p8-both",
                    primary_role="customer",
                )
            )
            session.commit()

        # "via chat"
        with Session(migrated_db) as session:
            ProfileRepository(session).update_profile(
                "CHN-P8-BOTH", {"phone": "0899999999"}
            )
            session.commit()
        # "via LIFF" — same function, same effect
        with Session(migrated_db) as session:
            row = ProfileRepository(session).update_profile(
                "CHN-P8-BOTH", {"address": "123 ถนนสุขุมวิท"}
            )
            session.commit()
            assert row.phone == "0899999999"           # earlier edit preserved
            assert row.address == "123 ถนนสุขุมวิท"


class TestPhase9CRMCore:
    """Master Spec 9.7's mandatory automated tests."""

    def test_customer_crud(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, Phase9Conflict
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P9C-0001", line_user_id="line-p9c-0001", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Customer Co", created_by_chann_uid="CHN-P9C-0001",
            )
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        # สร้าง customer ผ่านแชท (ตัวแทนด้วยการเรียก repository ตรง — chat
        # layer เป็นแค่ AI parse + field validation ที่อยู่หน้าเรียกนี้)
        with Session(migrated_db) as session:
            row = CustomerRepository(session).create(
                scope, first_name="สมชาย", phone="0812345678",
            )
            customer_id = row.id
            assert row.stage == "lead"
            session.commit()

        # ต้องการอย่างน้อยหนึ่งอย่าง — สร้างเปล่าไม่ได้
        with Session(migrated_db) as session:
            with pytest.raises(Phase9Conflict):
                CustomerRepository(session).create(scope)
            session.rollback()

        # 1 customer ต่อ tenant (UNIQUE constraint บน customer_chann_uid)
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P9C-CUST", line_user_id="line-p9c-cust",
                primary_role="customer",
            ))
            session.commit()
        with Session(migrated_db) as session:
            CustomerRepository(session).create(
                scope, customer_chann_uid="CHN-P9C-CUST", first_name="วิชัย",
            )
            session.commit()
        with Session(migrated_db) as session:
            with pytest.raises(Phase9Conflict):
                CustomerRepository(session).create(
                    scope, customer_chann_uid="CHN-P9C-CUST", first_name="วิชัย อีกครั้ง",
                )
            session.rollback()

        # Lead -> Contact promotion
        with Session(migrated_db) as session:
            promoted = CustomerRepository(session).promote_to_contact(scope, customer_id)
            assert promoted.stage == "contact"
            session.commit()
        # idempotent — promoting an already-Contact record is a no-op success
        with Session(migrated_db) as session:
            promoted_again = CustomerRepository(session).promote_to_contact(scope, customer_id)
            assert promoted_again.stage == "contact"

    def test_deal_crud(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import (
            CustomerRepository,
            DealRepository,
            Phase9Conflict,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P9D-0001", line_user_id="line-p9d-0001", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Deal Co", created_by_chann_uid="CHN-P9D-0001",
            )
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        with Session(migrated_db) as session:
            contact = CustomerRepository(session).create(scope, first_name="สมหญิง")
            contact_id = contact.id
            session.commit()

        # สร้าง deal ผ่านแชท
        with Session(migrated_db) as session:
            deal = DealRepository(session).create(scope, contact_id=contact_id)
            deal_id = deal.id
            assert deal.deal_id.startswith("D-")
            assert deal.stage == "new"
            session.commit()

        # เพิ่ม product ใน deal
        with Session(migrated_db) as session:
            DealRepository(session).add_product(
                scope, deal_id, product_id=None, product_name="เครื่องซักผ้า",
                quoted_unit_price="12000", qty=2,
            )
            session.commit()
        with Session(migrated_db) as session:
            products = DealRepository(session).products_of(deal_id)
            assert len(products) == 1
            assert products[0].qty == 2

        # stage transition: new -> proposed -> won
        with Session(migrated_db) as session:
            repo = DealRepository(session)
            d = repo.transition_stage(scope, deal_id, to_stage="proposed", allow_reopen=False)
            assert d.stage == "proposed"
            d = repo.transition_stage(scope, deal_id, to_stage="won", allow_reopen=False)
            assert d.stage == "won"
            session.commit()

        # reopen: won -> new ต้องมี deal.reopen (allow_reopen=False ต้องถูกปฏิเสธ)
        with Session(migrated_db) as session:
            with pytest.raises(Phase9Conflict):
                DealRepository(session).transition_stage(
                    scope, deal_id, to_stage="new", allow_reopen=False,
                )
            session.rollback()
        with Session(migrated_db) as session:
            d = DealRepository(session).transition_stage(
                scope, deal_id, to_stage="new", allow_reopen=True,
            )
            assert d.stage == "new"
            session.commit()

        # illegal transition (new -> won directly) refused
        with Session(migrated_db) as session:
            with pytest.raises(Phase9Conflict):
                DealRepository(session).transition_stage(
                    scope, deal_id, to_stage="won", allow_reopen=True,
                )
            session.rollback()

    def test_storefront_cross_tenant(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase7 import ProductRepository
        from chann_data.repositories.phase9 import StorefrontRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P9SF-A", line_user_id="line-p9sf-a", primary_role="sales",
            ))
            session.add(ChannIdentity(
                chann_uid="CHN-P9SF-B", line_user_id="line-p9sf-b", primary_role="sales",
            ))
            session.add(ChannIdentity(
                chann_uid="CHN-P9SF-CUST", line_user_id="line-p9sf-cust",
                primary_role="customer",
            ))
            session.commit()

        with Session(migrated_db) as session:
            reg = RegistrationRepository(session)
            lic_a = reg.create_license(company_name="Shop A", created_by_chann_uid="CHN-P9SF-A")
            lic_b = reg.create_license(company_name="Shop B", created_by_chann_uid="CHN-P9SF-B")
            license_a, license_b = lic_a.id, lic_b.id
            session.commit()

        scope_a = TenantScope(license_id=license_a)
        scope_b = TenantScope(license_id=license_b)

        with Session(migrated_db) as session:
            ProductRepository(session).upsert(
                scope_a, product_id="P-A-1", product_name="พัดลมไอเย็น A",
                unit_price="3500",
            )
            ProductRepository(session).upsert(
                scope_b, product_id="P-B-1", product_name="พัดลมไอเย็น B",
                unit_price="3900",
            )
            session.commit()

        # ค้นสินค้า -> เห็นสินค้าจากหลาย tenant
        with Session(migrated_db) as session:
            results = StorefrontRepository(session).search_products("พัดลมไอเย็น")
            assert len(results) == 2
            companies = {r["company_name"] for r in results}
            assert companies == {"Shop A", "Shop B"}

        # เลือกร้าน -> สร้าง Lead ใน tenant นั้น (เฉพาะ Shop A)
        with Session(migrated_db) as session:
            lead = StorefrontRepository(session).record_interest(
                chann_uid="CHN-P9SF-CUST", license_id=license_a,
                product_name="พัดลมไอเย็น A",
            )
            assert lead.license_id == license_a
            session.commit()

        # ร้าน A ไม่เห็นว่าลูกค้าคนนี้สนใจสินค้าร้าน B — ร้าน B ไม่มี Lead เลย
        with Session(migrated_db) as session:
            from chann_data.repositories.phase9 import CustomerRepository
            leads_in_a = CustomerRepository(session).list_for_license(scope_a)
            leads_in_b = CustomerRepository(session).list_for_license(scope_b)
            assert len(leads_in_a) == 1
            assert leads_in_a[0].customer_chann_uid == "CHN-P9SF-CUST"
            assert leads_in_b == []

    def test_multi_tenant_customer_and_deal_isolation(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P9MT-A", line_user_id="line-p9mt-a", primary_role="sales",
            ))
            session.add(ChannIdentity(
                chann_uid="CHN-P9MT-B", line_user_id="line-p9mt-b", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            reg = RegistrationRepository(session)
            lic_a = reg.create_license(company_name="Iso A", created_by_chann_uid="CHN-P9MT-A")
            lic_b = reg.create_license(company_name="Iso B", created_by_chann_uid="CHN-P9MT-B")
            license_a, license_b = lic_a.id, lic_b.id
            session.commit()

        scope_a = TenantScope(license_id=license_a)
        scope_b = TenantScope(license_id=license_b)

        with Session(migrated_db) as session:
            cust_a = CustomerRepository(session).create(scope_a, first_name="ลูกค้า A")
            cust_a_id = cust_a.id
            deal_a = DealRepository(session).create(scope_a, contact_id=cust_a_id)
            deal_a_id = deal_a.id
            session.commit()

        # customer ใน tenant A ไม่ปรากฏใน tenant B
        with Session(migrated_db) as session:
            assert CustomerRepository(session).get(scope_b, cust_a_id) is None
            assert CustomerRepository(session).get(scope_a, cust_a_id) is not None

        # deal ใน tenant A ไม่ปรากฏใน tenant B
        with Session(migrated_db) as session:
            assert DealRepository(session).get(scope_b, deal_a_id) is None
            assert DealRepository(session).get(scope_a, deal_a_id) is not None


class TestPhase10QuoteAndTemplateEngine:
    """Master Spec 10.7's mandatory automated tests — the subset that
    doesn't need a real SmartBrowz render (see phase10.py's module
    docstring: the DOCX-authoring/AI-mapping/SmartBrowz-render pipeline
    itself isn't built in this patch, only the quote and template-version
    workflow it will eventually sit behind)."""

    def test_quote_create(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.phase10 import Phase10NotFound, QuoteRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10Q-0001", line_user_id="line-p10q-0001", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            reg = RegistrationRepository(session)
            lic = reg.create_license(company_name="Quote Co", created_by_chann_uid="CHN-P10Q-0001")
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        with Session(migrated_db) as session:
            cust = CustomerRepository(session).create(scope, first_name="ลูกค้า", phone="0812345678")
            deal_repo = DealRepository(session)
            deal = deal_repo.create(scope, contact_id=cust.id)
            # A quote needs something to quote. An empty one renders a
            # document saying the customer owes zero, and the first person
            # to notice is the customer.
            deal_repo.add_product(
                scope, deal.id, product_id=None, product_name="สินค้าทดสอบ",
                quoted_unit_price="1000.00", qty=1,
            )
            deal_id = deal.id
            session.commit()

        # create -> success, quote number increments per tenant
        with Session(migrated_db) as session:
            quote_repo = QuoteRepository(session)
            q1 = quote_repo.create(scope, deal_id=deal_id)
            q2 = quote_repo.create(scope, deal_id=deal_id)
            assert q1.quote_id != q2.quote_id
            assert q1.quote_id.startswith("Q-")
            n1 = int(q1.quote_id.rsplit("-", 1)[1])
            n2 = int(q2.quote_id.rsplit("-", 1)[1])
            assert n2 == n1 + 1
            assert q1.status == "draft"
            session.commit()
            q1_id = q1.id

        # a quote against a deal that doesn't exist in this tenant fails
        with Session(migrated_db) as session:
            quote_repo = QuoteRepository(session)
            try:
                quote_repo.create(scope, deal_id=uuid.uuid4())
                raise AssertionError("expected Phase10NotFound")
            except Phase10NotFound:
                pass

        # status lifecycle: draft -> sent -> accepted, illegal transitions refused
        with Session(migrated_db) as session:
            from chann_data.repositories.phase10 import Phase10Conflict
            quote_repo = QuoteRepository(session)
            q = quote_repo.transition_status(scope, q1_id, to_status="sent")
            assert q.status == "sent"
            q = quote_repo.transition_status(scope, q1_id, to_status="accepted")
            assert q.status == "accepted"
            try:
                quote_repo.transition_status(scope, q1_id, to_status="draft")
                raise AssertionError("expected Phase10Conflict for accepted->draft")
            except Phase10Conflict:
                pass
            session.commit()

    def test_template_versioning(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase10 import (
            DocumentTemplateRepository,
            GeneratedDocumentRepository,
            Phase10Conflict,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10T-0001", line_user_id="line-p10t-0001", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            reg = RegistrationRepository(session)
            lic = reg.create_license(company_name="Template Co", created_by_chann_uid="CHN-P10T-0001")
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            tmpl = tmpl_repo.create_template(
                scope, document_type="quote", template_code="STD",
                template_name="Standard Quote Template",
            )
            tmpl_id = tmpl.id
            session.commit()

        # preview does not publish
        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            v1 = tmpl_repo.create_draft_version(
                scope, tmpl_id, source_docx_path="gs://b/v1.docx",
                intermediate_model={"fields": []}, mapping_schema={},
                compiled_template_path="gs://b/v1.html",
            )
            assert v1.version == 1
            v1 = tmpl_repo.mark_previewed(scope, v1.id)
            assert v1.status == "previewed"
            session.commit()
            v1_id = v1.id

        # publish requires explicit approval
        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            v1 = tmpl_repo.get_version(scope, v1_id)
            assert v1.status == "previewed"  # not auto-published by preview
            v1 = tmpl_repo.publish_version(scope, v1_id)
            assert v1.status == "published"
            assert v1.published_at is not None
            session.commit()

        # published version is immutable — re-publishing / re-previewing refused
        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            try:
                tmpl_repo.publish_version(scope, v1_id)
                raise AssertionError("expected Phase10Conflict re-publishing")
            except Phase10Conflict:
                pass
            try:
                tmpl_repo.mark_previewed(scope, v1_id)
                raise AssertionError("expected Phase10Conflict re-previewing a published version")
            except Phase10Conflict:
                pass

        # editing published version creates N+1 draft, doesn't touch v1
        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            v2 = tmpl_repo.create_draft_version(
                scope, tmpl_id, source_docx_path="gs://b/v2.docx",
                intermediate_model={"fields": ["new_field"]}, mapping_schema={},
                compiled_template_path="gs://b/v2.html",
            )
            assert v2.version == 2
            assert v2.status == "draft"
            v1_reloaded = tmpl_repo.get_version(scope, v1_id)
            assert v1_reloaded.status == "published"  # unchanged by v2 existing
            session.commit()

        # old generated document still references old (published) version
        with Session(migrated_db) as session:
            doc_repo = GeneratedDocumentRepository(session)
            gen = doc_repo.record(
                scope, document_type="quote", source_entity_type="quote",
                source_entity_id=uuid.uuid4(), template_version_id=v1_id,
                data_snapshot={"example": True}, output_path="gs://b/out.pdf",
                sha256="a" * 64,
            )
            session.commit()
            gen_id = gen.id

        with Session(migrated_db) as session:
            doc_repo = GeneratedDocumentRepository(session)
            reloaded = doc_repo.get(scope, gen_id)
            assert reloaded.template_version_id == v1_id  # still v1, unaffected by v2

    def test_multi_tenant_quote(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.phase10 import DocumentTemplateRepository, QuoteRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10MT-A", line_user_id="line-p10mt-a", primary_role="sales",
            ))
            session.add(ChannIdentity(
                chann_uid="CHN-P10MT-B", line_user_id="line-p10mt-b", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            reg = RegistrationRepository(session)
            lic_a = reg.create_license(company_name="Quote Iso A", created_by_chann_uid="CHN-P10MT-A")
            lic_b = reg.create_license(company_name="Quote Iso B", created_by_chann_uid="CHN-P10MT-B")
            license_a, license_b = lic_a.id, lic_b.id
            session.commit()

        scope_a = TenantScope(license_id=license_a)
        scope_b = TenantScope(license_id=license_b)

        with Session(migrated_db) as session:
            cust_a = CustomerRepository(session).create(scope_a, first_name="ลูกค้า A")
            deal_repo = DealRepository(session)
            deal_a = deal_repo.create(scope_a, contact_id=cust_a.id)
            # A quote needs a line item — see QuoteRepository.create.
            deal_repo.add_product(
                scope_a, deal_a.id, product_id=None, product_name="สินค้า A",
                quoted_unit_price="1000.00", qty=1,
            )
            deal_a_id = deal_a.id
            session.commit()

        with Session(migrated_db) as session:
            quote_repo = QuoteRepository(session)
            quote_a = quote_repo.create(scope_a, deal_id=deal_a_id)
            quote_a_id = quote_a.id
            session.commit()

        # quote in tenant A is invisible in tenant B
        with Session(migrated_db) as session:
            quote_repo = QuoteRepository(session)
            assert quote_repo.get(scope_b, quote_a_id) is None
            assert quote_repo.get(scope_a, quote_a_id) is not None

        # tenant A cannot use tenant B's deal to create a quote — a deal
        # id that exists, but not in this tenant, must behave exactly like
        # one that doesn't exist at all (no cross-tenant leakage of
        # existence).
        with Session(migrated_db) as session:
            from chann_data.repositories.phase10 import Phase10NotFound
            quote_repo = QuoteRepository(session)
            try:
                quote_repo.create(scope_b, deal_id=deal_a_id)
                raise AssertionError("expected Phase10NotFound using another tenant's deal")
            except Phase10NotFound:
                pass

        # tenant A cannot use tenant B's template/version
        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            tmpl_b = tmpl_repo.create_template(
                scope_b, document_type="quote", template_code="B-STD",
                template_name="B's Standard Template",
            )
            tmpl_b_id = tmpl_b.id
            session.commit()

        with Session(migrated_db) as session:
            tmpl_repo = DocumentTemplateRepository(session)
            assert tmpl_repo.get_template(scope_a, tmpl_b_id) is None
            assert tmpl_repo.get_template(scope_b, tmpl_b_id) is not None


class TestPhase10CompanyProfile:
    """Phase 10 — the issuing company's legal identity on documents.

    A Thai quote/invoice must carry the issuing company's tax ID and
    address. `licenses` carried neither before migration 0010, so a
    document rendered from it would have been legally incomplete no
    matter how good the template was.
    """

    def test_company_profile_update_and_readiness(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from decimal import Decimal

        from chann_data.repositories.phase10 import CompanyProfileRepository
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10CP-001", line_user_id="line-p10cp-001", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Doc Co", created_by_chann_uid="CHN-P10CP-001",
            )
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        # A freshly registered tenant is NOT document-ready — this is the
        # state every existing tenant is in after migration 0010, and the
        # renderer has to refuse rather than emit a blank tax ID.
        with Session(migrated_db) as session:
            repo = CompanyProfileRepository(session)
            row = repo.get(scope)
            assert repo.missing_for_documents(row) == ["tax_id", "company_address"]
            assert repo.is_document_ready(row) is False

        with Session(migrated_db) as session:
            repo = CompanyProfileRepository(session)
            row = repo.update(scope, {
                "legal_name": "Doc Co., Ltd.",
                "tax_id": "0105558123456",
                "company_address": "99/1 ถนนสุขุมวิท กรุงเทพฯ 10110",
                "vat_rate": Decimal("0.07"),
            })
            assert repo.is_document_ready(row) is True
            session.commit()

        # company_name is untouched by a document-identity update: the shop's
        # chat/storefront display name and its registered legal name are
        # different things and must not overwrite each other.
        with Session(migrated_db) as session:
            row = CompanyProfileRepository(session).get(scope)
            assert row.company_name == "Doc Co"
            assert row.legal_name == "Doc Co., Ltd."
            assert row.vat_rate == Decimal("0.0700")

    def test_partial_update_leaves_omitted_fields_alone(self, migrated_db):
        """An omitted key must not be treated as null. Sending only a phone
        number should never silently wipe the tax ID."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from decimal import Decimal

        from chann_data.repositories.phase10 import CompanyProfileRepository
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10CP-002", line_user_id="line-p10cp-002", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Partial Co", created_by_chann_uid="CHN-P10CP-002",
            )
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        with Session(migrated_db) as session:
            CompanyProfileRepository(session).update(scope, {
                "tax_id": "0105558123456", "company_address": "123 Road",
            })
            session.commit()

        with Session(migrated_db) as session:
            repo = CompanyProfileRepository(session)
            row = repo.update(scope, {"company_phone": "021234567"})
            assert row.tax_id == "0105558123456"
            assert row.company_address == "123 Road"
            assert row.company_phone == "021234567"
            session.commit()

    def test_invalid_tax_id_and_vat_rate_are_refused(self, migrated_db):
        """Both refusals matter for the same reason: a malformed value that
        reaches a rendered document is worse than a failed update, because
        the document goes to a customer and looks authoritative."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from decimal import Decimal

        from chann_data.repositories.phase10 import CompanyProfileRepository
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10CP-003", line_user_id="line-p10cp-003", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Invalid Co", created_by_chann_uid="CHN-P10CP-003",
            )
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        for bad_tax_id in ("12345", "010555812345X", "0105558123456789"):
            with Session(migrated_db) as session:
                with pytest.raises(ValueError, match="13 digits"):
                    CompanyProfileRepository(session).update(scope, {"tax_id": bad_tax_id})
                session.rollback()

        # 7 instead of 0.07 — the mistake that would silently render a 700%
        # VAT line rather than fail.
        with Session(migrated_db) as session:
            with pytest.raises(ValueError, match="fraction"):
                CompanyProfileRepository(session).update(scope, {"vat_rate": Decimal("7")})
            session.rollback()

    def test_blank_and_whitespace_are_treated_as_missing(self, migrated_db):
        """A tax_id of "   " is not a tax ID. If whitespace counted as
        present, a document could claim to be complete while showing an
        empty box where the TIN belongs."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from decimal import Decimal

        from chann_data.repositories.phase10 import CompanyProfileRepository
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-P10CP-004", line_user_id="line-p10cp-004", primary_role="sales",
            ))
            session.commit()

        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="Blank Co", created_by_chann_uid="CHN-P10CP-004",
            )
            license_id = lic.id
            session.commit()

        scope = TenantScope(license_id=license_id)

        with Session(migrated_db) as session:
            repo = CompanyProfileRepository(session)
            # "   " is stripped to None by update(), so it never even reaches
            # the 13-digit check — it is an absent value, not a malformed one.
            row = repo.update(scope, {"tax_id": "   ", "company_address": "  "})
            assert row.tax_id is None
            assert repo.missing_for_documents(row) == ["tax_id", "company_address"]
            session.commit()

    def test_multi_tenant_company_profile_isolation(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from decimal import Decimal

        from chann_data.repositories.phase10 import CompanyProfileRepository
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add_all([
                ChannIdentity(
                    chann_uid="CHN-P10CP-A", line_user_id="line-p10cp-a", primary_role="sales",
                ),
                ChannIdentity(
                    chann_uid="CHN-P10CP-B", line_user_id="line-p10cp-b", primary_role="sales",
                ),
            ])
            session.commit()

        with Session(migrated_db) as session:
            reg = RegistrationRepository(session)
            a = reg.create_license(company_name="Iso A", created_by_chann_uid="CHN-P10CP-A")
            b = reg.create_license(company_name="Iso B", created_by_chann_uid="CHN-P10CP-B")
            scope_a, scope_b = TenantScope(license_id=a.id), TenantScope(license_id=b.id)
            session.commit()

        with Session(migrated_db) as session:
            CompanyProfileRepository(session).update(scope_a, {"tax_id": "0105558123456"})
            session.commit()

        with Session(migrated_db) as session:
            repo = CompanyProfileRepository(session)
            assert repo.get(scope_a).tax_id == "0105558123456"
            assert repo.get(scope_b).tax_id is None


class TestPhase10QuoteDocumentLink:
    """A quote must be able to name the document that was issued for it.

    generated_document_id existed on the schema from the start but nothing
    ever wrote it, so an issued quote had no way back to its own evidence.
    """

    def _tenant(self, migrated_db, suffix):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-QDL-{suffix}", line_user_id=f"line-qdl-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Doc Link {suffix}", created_by_chann_uid=f"CHN-QDL-{suffix}",
            )
            session.commit()
            return lic.id

    def _quote_with_document(self, migrated_db, license_id, session_factory):
        from chann_data.repositories.phase10 import (
            DocumentTemplateRepository, GeneratedDocumentRepository, QuoteRepository,
        )
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        scope = TenantScope(license_id=license_id)
        with session_factory() as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0800000000",
            )
            deal_repo = DealRepository(session)
            deal = deal_repo.create(scope, contact_id=customer.id)
            # A quote needs a line item — see QuoteRepository.create.
            deal_repo.add_product(
                scope, deal.id, product_id=None, product_name="สินค้าทดสอบ",
                quoted_unit_price="1000.00", qty=1,
            )
            quote = QuoteRepository(session).create(scope, deal_id=deal.id)
            template = DocumentTemplateRepository(session).create_template(
                scope, document_type="quote", template_code="T1", template_name="T1",
            )
            version = DocumentTemplateRepository(session).create_draft_version(
                scope, template.id, source_docx_path="builtin://none",
                intermediate_model={}, mapping_schema={},
                compiled_template_path="builtin://quote/v1",
            )
            session.commit()
            quote_id, version_id = quote.id, version.id

        with session_factory() as session:
            document = GeneratedDocumentRepository(session).record(
                scope, document_type="quote", source_entity_type="quote",
                source_entity_id=quote_id, template_version_id=version_id,
                data_snapshot={"totals": {"grand_total": "1.00"}},
                output_path="gs://bucket/documents/x.pdf", sha256="a" * 64,
            )
            session.commit()
            return quote_id, document.id

    def test_link_document_points_the_quote_at_its_evidence(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase10 import QuoteRepository
        from chann_data.repositories.tenant_scope import TenantScope

        license_id = self._tenant(migrated_db, "A")
        factory = lambda: Session(migrated_db)  # noqa: E731
        quote_id, document_id = self._quote_with_document(migrated_db, license_id, factory)
        scope = TenantScope(license_id=license_id)

        with Session(migrated_db) as session:
            QuoteRepository(session).link_document(scope, quote_id, document_id)
            session.commit()

        with Session(migrated_db) as session:
            assert QuoteRepository(session).get(scope, quote_id).generated_document_id == document_id

    def test_a_document_from_another_tenant_cannot_be_attached(self, migrated_db):
        """Both sides are scoped: without the document-side check a caller
        could point its own quote at someone else's evidence."""
        import pytest as _pytest
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase10 import Phase10NotFound, QuoteRepository
        from chann_data.repositories.tenant_scope import TenantScope

        a = self._tenant(migrated_db, "B")
        b = self._tenant(migrated_db, "C")
        factory = lambda: Session(migrated_db)  # noqa: E731
        quote_a, _ = self._quote_with_document(migrated_db, a, factory)
        _, document_b = self._quote_with_document(migrated_db, b, factory)

        with Session(migrated_db) as session:
            with _pytest.raises(Phase10NotFound):
                QuoteRepository(session).link_document(
                    TenantScope(license_id=a), quote_a, document_b,
                )


class TestPhase9DealEditing:
    """Editing a deal and removing a line item — the writes the dashboard
    needs and chat had no way to express either."""

    def _deal(self, migrated_db, suffix):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-DE-{suffix}", line_user_id=f"line-de-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Edit {suffix}", created_by_chann_uid=f"CHN-DE-{suffix}",
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
            return scope, deal.id

    def test_update_changes_notes_but_never_the_stage(self, migrated_db):
        """Stage has its own transition method with the state machine and
        the reopen permission behind it. A generic patch that could set it
        would make that machine advisory rather than enforced."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import DealRepository

        scope, deal_id = self._deal(migrated_db, "A")
        with Session(migrated_db) as session:
            DealRepository(session).update(
                scope, deal_id, {"notes": "ลูกค้าขอส่วนลด", "stage": "won"},
            )
            session.commit()
        with Session(migrated_db) as session:
            row = DealRepository(session).get(scope, deal_id)
            assert row.notes == "ลูกค้าขอส่วนลด"
            assert row.stage == "new", "stage must not be settable through update()"

    def test_remove_product_takes_the_line_off_the_deal(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import DealRepository

        scope, deal_id = self._deal(migrated_db, "B")
        with Session(migrated_db) as session:
            repo = DealRepository(session)
            keep = repo.add_product(
                scope, deal_id, product_id=None, product_name="พัดลม",
                quoted_unit_price="1250", qty=2,
            )
            drop = repo.add_product(
                scope, deal_id, product_id=None, product_name="ใส่ผิด",
                quoted_unit_price="1", qty=1,
            )
            session.commit()
            keep_id, drop_id = keep.id, drop.id

        with Session(migrated_db) as session:
            removed = DealRepository(session).remove_product(scope, deal_id, drop_id)
            # Returned so the caller can name it: the row is gone afterwards,
            # so the audit entry is the only record of what was removed.
            assert removed.product_name == "ใส่ผิด"
            session.commit()

        with Session(migrated_db) as session:
            remaining = DealRepository(session).products_of(deal_id)
            assert [p.id for p in remaining] == [keep_id]

    def test_another_tenants_deal_cannot_be_edited_or_stripped(self, migrated_db):
        import pytest as _pytest
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import DealRepository, Phase9NotFound

        scope_a, deal_a = self._deal(migrated_db, "C")
        scope_b, _ = self._deal(migrated_db, "D")

        with Session(migrated_db) as session:
            with _pytest.raises(Phase9NotFound):
                DealRepository(session).update(scope_b, deal_a, {"notes": "x"})
            with _pytest.raises(Phase9NotFound):
                DealRepository(session).remove_product(scope_b, deal_a, deal_a)


class TestPhase9CustomerCode:
    """Customers now carry a human-facing code, like deals and quotes.

    They did not before, which made them the one entity that could be
    listed but never referred to afterwards: a list row's button had
    nothing to put in the message and sent the literal string "None".
    """

    def _tenant(self, migrated_db, suffix):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-CC-{suffix}", line_user_id=f"line-cc-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Code {suffix}", created_by_chann_uid=f"CHN-CC-{suffix}",
            )
            session.commit()
            return TenantScope(license_id=lic.id)

    def test_codes_are_assigned_and_increment(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository

        scope = self._tenant(migrated_db, "A")
        codes = []
        for index in range(3):
            with Session(migrated_db) as session:
                row = CustomerRepository(session).create(
                    scope, first_name=f"ก{index}", last_name="ข", phone=f"08000000{index:02d}",
                )
                codes.append(row.customer_id)
                session.commit()

        assert all(code.startswith("C-") for code in codes)
        assert codes == sorted(codes), "codes should increase with creation order"
        assert len(set(codes)) == 3

    def test_each_tenant_numbers_from_one(self, migrated_db):
        """Per-license, unlike deal_id which is unique platform-wide. Global
        numbering would give a new tenant's first customer a code like
        C-2026-0847 — which looks broken, and quietly discloses how much the
        whole platform is being used."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository

        a = self._tenant(migrated_db, "B")
        b = self._tenant(migrated_db, "C")

        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            for index in range(4):
                repo.create(scope := a, first_name=f"A{index}", last_name="x",
                            phone=f"08100000{index:02d}")
            session.commit()

        with Session(migrated_db) as session:
            first_of_b = CustomerRepository(session).create(
                b, first_name="B0", last_name="x", phone="0820000000",
            )
            session.commit()
            assert first_of_b.customer_id.endswith("-0001"), (
                f"second tenant started at {first_of_b.customer_id}"
            )

    def test_the_same_code_may_exist_in_two_tenants(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository

        a = self._tenant(migrated_db, "D")
        b = self._tenant(migrated_db, "E")
        with Session(migrated_db) as session:
            repo = CustomerRepository(session)
            one = repo.create(a, first_name="A", last_name="x", phone="0830000000")
            two = repo.create(b, first_name="B", last_name="x", phone="0840000000")
            session.commit()
            assert one.customer_id == two.customer_id


class TestPhase9DealCodeIsPerTenant:
    """Deal codes are now numbered per tenant, like quotes and customers.

    An owner-approved departure from the Master Spec, which marks deal_id
    plainly UNIQUE while giving quote_id an explicit "per company"
    qualifier. Global numbering meant a newly registered tenant's first
    deal was called something like D-2026-0847 — visibly broken to that
    tenant, and a quiet disclosure of platform-wide volume.
    """

    def _tenant_with_customer(self, migrated_db, suffix):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-DC-{suffix}", line_user_id=f"line-dc-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"DealCode {suffix}", created_by_chann_uid=f"CHN-DC-{suffix}",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)
        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone=f"09{ord(suffix):08d}",
            )
            session.commit()
            return scope, customer.id

    def test_each_tenant_numbers_deals_from_one(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import DealRepository

        a, customer_a = self._tenant_with_customer(migrated_db, "A")
        b, customer_b = self._tenant_with_customer(migrated_db, "B")

        with Session(migrated_db) as session:
            # A customer holds only one OPEN deal, so numbering has to be
            # exercised across several — which is what a real tenant looks
            # like anyway.
            from chann_data.repositories.phase9 import CustomerRepository

            repo = DealRepository(session)
            customers = CustomerRepository(session)
            for index in range(3):
                extra = customers.create(
                    a, first_name=f"นับ{index}", last_name="ก",
                    phone=f"08220000{index:02d}",
                )
                repo.create(a, contact_id=extra.id)
            session.commit()

        with Session(migrated_db) as session:
            first_of_b = DealRepository(session).create(b, contact_id=customer_b)
            session.commit()
            assert first_of_b.deal_id.endswith("-0001"), (
                f"second tenant started at {first_of_b.deal_id}, so numbering "
                "is still global"
            )

    def test_the_same_deal_code_may_exist_in_two_tenants(self, migrated_db):
        """The point of the change: what used to be a uniqueness violation
        is now the expected outcome."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import DealRepository

        a, customer_a = self._tenant_with_customer(migrated_db, "C")
        b, customer_b = self._tenant_with_customer(migrated_db, "D")

        with Session(migrated_db) as session:
            repo = DealRepository(session)
            one = repo.create(a, contact_id=customer_a)
            two = repo.create(b, contact_id=customer_b)
            session.commit()
            assert one.deal_id == two.deal_id

    def test_a_deal_code_is_still_unique_inside_one_tenant(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import DealRepository

        scope, customer = self._tenant_with_customer(migrated_db, "E")
        with Session(migrated_db) as session:
            from chann_data.repositories.phase9 import CustomerRepository

            repo = DealRepository(session)
            customers = CustomerRepository(session)
            codes = []
            for index in range(5):
                extra = customers.create(
                    scope, first_name=f"รหัส{index}", last_name="ก",
                    phone=f"08330000{index:02d}",
                )
                codes.append(repo.create(scope, contact_id=extra.id).deal_id)
            session.commit()
        assert len(set(codes)) == 5
        assert codes == sorted(codes)


class TestNotesAndAppointments:
    """Master Spec 6.3/6.7 — notes as rows, and follow-ups with a time.

    ACTION_PERMISSIONS promised note.* since Phase 6 while no notes table
    existed; what there was is a single overwritable TEXT column per record,
    with no author, no timestamp and no history.
    """

    def _tenant(self, migrated_db, suffix):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-NT-{suffix}", line_user_id=f"line-nt-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Notes {suffix}", created_by_chann_uid=f"CHN-NT-{suffix}",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)
        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone=f"07{ord(suffix):08d}",
            )
            session.commit()
            return scope, customer.id

    def test_many_notes_accumulate_newest_first(self, migrated_db):
        """The whole point of a table over a column: a record keeps every
        note, so "what did we agree in March" is answerable."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import NoteRepository

        scope, customer_id = self._tenant(migrated_db, "A")
        for text in ("ลูกค้าขอส่วนลด", "ตกลงราคาแล้ว", "รอเซ็นสัญญา"):
            with Session(migrated_db) as session:
                NoteRepository(session).create(
                    scope, entity_type="customer", entity_id=customer_id,
                    body=text, author_chann_uid="CHN-NT-A",
                )
                session.commit()

        with Session(migrated_db) as session:
            notes = NoteRepository(session).list_for_entity(
                scope, entity_type="customer", entity_id=customer_id,
            )
            assert len(notes) == 3
            assert notes[0].body == "รอเซ็นสัญญา", "newest first"
            assert all(n.author_chann_uid == "CHN-NT-A" for n in notes)

    def test_an_empty_note_is_refused(self, migrated_db):
        """A blank row in a history is worse than a refusal someone can act
        on."""
        import pytest as _pytest
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import NoteRepository, Phase6Conflict

        scope, customer_id = self._tenant(migrated_db, "B")
        with Session(migrated_db) as session:
            with _pytest.raises(Phase6Conflict):
                NoteRepository(session).create(
                    scope, entity_type="customer", entity_id=customer_id, body="   ",
                )

    def test_notes_do_not_leak_across_tenants(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import NoteRepository

        a, customer_a = self._tenant(migrated_db, "C")
        b, _ = self._tenant(migrated_db, "D")
        with Session(migrated_db) as session:
            NoteRepository(session).create(
                scope=a, entity_type="customer", entity_id=customer_a, body="ความลับ",
            )
            session.commit()

        with Session(migrated_db) as session:
            # Same entity id, other tenant's scope: must see nothing.
            assert NoteRepository(session).list_for_entity(
                b, entity_type="customer", entity_id=customer_a,
            ) == []

    def test_a_follow_up_can_carry_a_time_or_not(self, migrated_db):
        """NULL keeps the original whole-day meaning exactly; a value turns
        the same row into an appointment."""
        from datetime import date, time

        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import FollowUpRepository

        scope, customer_id = self._tenant(migrated_db, "E")
        with Session(migrated_db) as session:
            repo = FollowUpRepository(session)
            whole_day = repo.create(
                scope, entity_type="customer", entity_id=customer_id,
                due_date=date(2026, 9, 4),
            )
            appointment = repo.create(
                scope, entity_type="customer", entity_id=customer_id,
                due_date=date(2026, 9, 4), due_time=time(14, 0),
            )
            session.commit()
            assert whole_day.due_time is None
            assert appointment.due_time == time(14, 0)


class TestDealCanBeLostBeforeItIsQuoted:
    """OWNER-APPROVED DEPARTURE FROM MASTER SPEC 9.6.

    The spec has no new → lost, which makes a deal that dies before anyone
    quotes it impossible to close — and that is the most ordinary way for
    a deal to end: the customer changes their mind, buys elsewhere, or
    stops replying, all before a quote exists.

    The alternatives were leaving it open forever, or moving it to
    proposed — inventing a quote that was never made — and then losing it.
    Both corrupt the pipeline numbers the stage exists to produce.
    """

    def test_a_new_deal_can_be_marked_lost(self, migrated_db):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-LOST-{suffix}", line_user_id=f"line-lost-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Lost {suffix}",
                created_by_chann_uid=f"CHN-LOST-{suffix}",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)

        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0800000000",
            )
            deals = DealRepository(session)
            deal = deals.create(scope, contact_id=customer.id)
            session.flush()
            assert deal.stage == "new"

            updated = deals.transition_stage(
                scope, deal.id, to_stage="lost", allow_reopen=False,
            )
            session.commit()
            assert updated.stage == "lost"

    def test_a_new_deal_still_cannot_jump_to_won(self, migrated_db):
        """A deal nobody ever quoted was not won — it was never worked."""
        import uuid

        import pytest
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.phase9 import (
            CustomerRepository, DealRepository, Phase9Conflict,
        )
        from chann_data.repositories.tenant_scope import TenantScope

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-WON-{suffix}", line_user_id=f"line-won-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Won {suffix}", created_by_chann_uid=f"CHN-WON-{suffix}",
            )
            session.commit()
            scope = TenantScope(license_id=lic.id)

        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0800000001",
            )
            deals = DealRepository(session)
            deal = deals.create(scope, contact_id=customer.id)
            session.flush()
            with pytest.raises(Phase9Conflict):
                deals.transition_stage(
                    scope, deal.id, to_stage="won", allow_reopen=False,
                )


class TestTechnicianChannelIsCapabilityGated:
    """Who may use the Technician OA, at the owner's direction: anyone
    whose role grants ticket.read.

    The old rule was role == "technician" exactly. It protected the right
    thing — a salesperson should not silently become a technician — but a
    small shop's owner goes out on jobs, and it left them told they were
    "not linked to any company as a technician" at their own company.
    """

    def _license_with_member(self, migrated_db, role: str, suffix: str):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity, LicenseMember
        from chann_data.repositories.phase65 import RegistrationRepository

        tag = f"{suffix}-{uuid.uuid4().hex[:4]}"
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-{tag}", line_user_id=f"line-{tag}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Cap {tag}", created_by_chann_uid=f"CHN-{tag}",
            )
            session.commit()
            license_id = lic.id

        if role != "owner":
            with Session(migrated_db) as session:
                member = session.query(LicenseMember).filter_by(
                    license_id=license_id, chann_uid=f"CHN-{tag}",
                ).one()
                member.role = role
                session.commit()

        return f"CHN-{tag}", license_id

    @pytest.mark.parametrize("role", ["owner", "admin", "cs", "technician"])
    def test_roles_that_do_field_work_get_in(self, migrated_db, role):
        from sqlalchemy.orm import Session

        from chann_data.repositories.tenant_scope import MemberRepository

        chann_uid, _ = self._license_with_member(migrated_db, role, "IN")
        with Session(migrated_db) as session:
            found = MemberRepository(session).memberships_of(
                chann_uid, oa="technician",
            )
        assert len(found) == 1, f"{role} holds ticket.read and should get in"

    def test_a_role_without_ticket_read_is_refused(self, migrated_db):
        """The protection that mattered, kept."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.tenant_scope import MemberRepository

        chann_uid, _ = self._license_with_member(migrated_db, "member", "OUT")
        with Session(migrated_db) as session:
            found = MemberRepository(session).memberships_of(
                chann_uid, oa="technician",
            )
        assert found == []

    def test_an_unknown_role_name_is_refused(self, migrated_db):
        """A typo, or a role deleted after members were assigned to it,
        must not open a channel."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.tenant_scope import MemberRepository

        chann_uid, _ = self._license_with_member(migrated_db, "ผู้ช่วย", "UNK")
        with Session(migrated_db) as session:
            found = MemberRepository(session).memberships_of(
                chann_uid, oa="technician",
            )
        assert found == []

    def test_a_tenant_can_revoke_it_from_a_role(self, migrated_db):
        """A shop that removed ticket.read from cs has said cs does not do
        field work, and that must be honoured over the template."""
        from sqlalchemy.orm import Session

        import uuid

        from chann_data.models import RolePermission
        from chann_data.repositories.tenant_scope import MemberRepository

        chann_uid, license_id = self._license_with_member(migrated_db, "cs", "REV")
        with Session(migrated_db) as session:
            # The grant already exists — creating a licence seeds the
            # default roles into role_permissions — so revoking means
            # flipping the row, not inserting a second one.
            existing = session.query(RolePermission).filter_by(
                license_id=license_id, role="cs", permission_key="ticket.read",
            ).one_or_none()
            if existing is None:
                session.add(RolePermission(
                    id=uuid.uuid4(), license_id=license_id, role="cs",
                    permission_key="ticket.read", allowed=False,
                ))
            else:
                existing.allowed = False
            session.commit()

        with Session(migrated_db) as session:
            found = MemberRepository(session).memberships_of(
                chann_uid, oa="technician",
            )
        assert found == []

    def test_sales_oa_still_excludes_technicians(self, migrated_db):
        """The other direction is unchanged: a technician has no business
        in the Sales channel."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.tenant_scope import MemberRepository

        chann_uid, _ = self._license_with_member(migrated_db, "technician", "SAL")
        with Session(migrated_db) as session:
            found = MemberRepository(session).memberships_of(chann_uid, oa="sales")
        assert found == []
