"""Phase 18 against Postgres: the tenant list with its counts and search,
one tenant's detail, suspend/reopen, the cross-tenant audit filters, and
break-glass leaving the old owner demoted with an audit row.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chann_data.models import AuditLog, ChannIdentity, CustomRole, LicenseMember
from chann_data.repositories.audit import AuditRepository
from chann_data.repositories.phase12 import ServiceTicketRepository
from chann_data.repositories.phase18 import PlatformNotFound, PlatformRepository
from chann_data.repositories.phase2 import OwnershipTransferRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def world(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uids = {"a": f"CHN-P18{tag}-A", "b": f"CHN-P18{tag}-B", "staff": f"CHN-P18{tag}-S"}
    with Session(migrated_db) as s:
        for key in uids:
            s.add(ChannIdentity(chann_uid=uids[key], line_user_id=f"line-{uids[key]}", primary_role="sales",
                                display_name={"a": "เจ้าของ A", "b": "เจ้าของ B", "staff": "พนักงาน"}[key]))
        s.commit()
    with Session(migrated_db) as s:
        reg = RegistrationRepository(s)
        lic_a = reg.create_license(company_name=f"Cool Air {tag}", created_by_chann_uid=uids["a"])
        lic_b = reg.create_license(company_name=f"Warm Home {tag}", created_by_chann_uid=uids["b"])
        ids = (lic_a.id, lic_b.id)
        s.commit()
    scope_a = TenantScope(license_id=ids[0])
    with Session(migrated_db) as s:
        s.add(LicenseMember(license_id=ids[0], chann_uid=uids["staff"], role="technician", status="active"))
        c = CustomerRepository(s).create(scope_a, first_name="ลูกค้า", last_name="หนึ่ง", phone="0811111111")
        s.flush()
        ServiceTicketRepository(s).create(scope_a, issue_description="แอร์ไม่เย็น", contact_id=c.id)
        done = ServiceTicketRepository(s).create(scope_a, issue_description="ล้างแอร์", contact_id=c.id)
        s.flush()
        done.status = "completed"
        s.commit()
    return migrated_db, ids, uids, tag


class TestTenantList:
    def test_counts_owner_and_search(self, world):
        engine, ids, uids, tag = world
        with Session(engine) as s:
            repo = PlatformRepository(s)
            rows = {r["id"]: r for r in repo.tenants(q=tag)}
            assert set(rows) == set(ids)
            a = rows[ids[0]]
            assert a["members"] == 2 and a["customers"] == 1 and a["tickets"] == 2 and a["open_tickets"] == 1
            assert a["owner_chann_uid"] == uids["a"] and a["owner_name"] == "เจ้าของ A"
            assert a["last_activity_at"] is not None
            assert [r["id"] for r in repo.tenants(q=f"Cool Air {tag}")] == [ids[0]]
            assert repo.tenants(q=f"nothing-{tag}") == []

    def test_detail_lists_members_with_names(self, world):
        engine, ids, uids, _ = world
        with Session(engine) as s:
            detail = PlatformRepository(s).tenant(ids[0])
            names = {m["chann_uid"]: m["display_name"] for m in detail["members"]}
            assert names == {uids["a"]: "เจ้าของ A", uids["staff"]: "พนักงาน"}
            with pytest.raises(PlatformNotFound):
                PlatformRepository(s).tenant(uuid.uuid4())


class TestSuspend:
    def test_suspend_then_reopen_shows_in_the_list(self, world):
        engine, ids, _, tag = world
        with Session(engine) as s:
            RegistrationRepository(s).set_status(ids[1], "suspended")
            s.commit()
            repo = PlatformRepository(s)
            assert [r["id"] for r in repo.tenants(q=tag, status="suspended")] == [ids[1]]
            RegistrationRepository(s).set_status(ids[1], "active")
            s.commit()
            assert repo.tenants(q=tag, status="suspended") == []


class TestCrossTenantAudit:
    def test_filters(self, world):
        engine, ids, uids, _ = world
        with Session(engine) as s:
            audit = AuditRepository(s)
            audit.write(license_id=ids[0], entity_type="license", entity_id=ids[0], actor_type="platform_admin",
                        actor_id="admin-1", action="update", field_changes={"status": ["active", "suspended"]}, cross_tenant=True)
            audit.write(license_id=ids[0], entity_type="customer", entity_id=uuid.uuid4(), actor_type="user",
                        actor_id=uids["a"], action="create", field_changes={})
            audit.write(license_id=ids[1], entity_type="license", entity_id=ids[1], actor_type="platform_admin",
                        actor_id="admin-1", action="update", field_changes={}, cross_tenant=True)
            s.commit()
            cross = audit.list_platform(cross_tenant=True, limit=500)
            assert {r.license_id for r in cross} >= set(ids) and all(r.cross_tenant for r in cross)
            only_a = audit.list_platform(license_id=ids[0], limit=500)
            assert {r.actor_type for r in only_a} == {"platform_admin", "user"}
            admins = audit.list_platform(license_id=ids[0], actor_type="platform_admin", limit=500)
            assert all(r.actor_type == "platform_admin" for r in admins) and admins


class TestBreakGlass:
    def test_new_owner_old_owner_demoted_and_audited(self, world):
        engine, ids, uids, _ = world
        scope = TenantScope(license_id=ids[0])
        with Session(engine) as s:
            member = OwnershipTransferRepository(s).force(scope, uids["staff"])
            AuditRepository(s).write(license_id=ids[0], entity_type="license_member", entity_id=member.id,
                                     actor_type="platform_admin", actor_id="admin-1", action="transfer",
                                     field_changes={"role": ["technician", member.role]}, cross_tenant=True)
            s.commit()
            owner_role = s.execute(select(CustomRole).where(CustomRole.license_id == ids[0], CustomRole.is_owner.is_(True))).scalars().one()
            roles = {m.chann_uid: m.role for m in s.execute(select(LicenseMember).where(LicenseMember.license_id == ids[0])).scalars()}
            assert roles[uids["staff"]] == owner_role.role_name
            assert roles[uids["a"]] != owner_role.role_name
            detail = PlatformRepository(s).tenant(ids[0])
            assert detail["owner_chann_uid"] == uids["staff"]
            rows = s.execute(select(AuditLog).where(AuditLog.license_id == ids[0], AuditLog.action == "transfer")).scalars().all()
            assert rows and all(r.cross_tenant and r.actor_type == "platform_admin" for r in rows)
