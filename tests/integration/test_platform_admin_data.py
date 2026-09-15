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
            names = {m["chann_uid"]: m["display_name"] for m in detail["members_detail"]}
            assert names == {uids["a"]: "เจ้าของ A", uids["staff"]: "พนักงาน"}
            assert detail["members"] == 2  # the count survives beside the list
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


class TestSubscriptionRound18:
    """Round 18 — NOT RUN in the authoring session (no Postgres in Cloud
    Shell; TEST_DATABASE_URL unset). Written against the repository
    signatures; the first run with a database is the verification."""

    def test_extend_moves_the_deadline_and_reopens_a_suspended_tenant(self, world):
        from datetime import datetime, timedelta, timezone

        from chann_data.models import License

        engine, ids, _, _ = world
        now = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
        with Session(engine) as s:
            repo = PlatformRepository(s)
            lic = s.get(License, ids[0])
            lic.expires_at = now - timedelta(days=5)   # already past
            lic.status = "suspended"
            s.flush()
            before, row = repo.extend(ids[0], 30, now=now)
            s.commit()
            assert before["status"] == "suspended" and row.status == "active"
            assert row.expires_at == now + timedelta(days=30)   # from now, not from the past date
            _, row = repo.extend(ids[0], 365, now=now)
            s.commit()
            assert row.expires_at == now + timedelta(days=395)  # from the current expiry
            with pytest.raises(ValueError):
                repo.extend(ids[0], 0)
            with pytest.raises(PlatformNotFound):
                repo.extend(uuid.uuid4(), 30)

    def test_the_sweep_covers_active_licenses_and_says_what_they_were(self, world):
        from datetime import datetime, timedelta, timezone

        from chann_data.models import License

        engine, ids, _, _ = world
        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            a, b = s.get(License, ids[0]), s.get(License, ids[1])
            a.status, a.expires_at = "active", now - timedelta(minutes=1)
            b.status, b.expires_at = "trial", now + timedelta(days=3)
            s.commit()
        with Session(engine) as s:
            reg = RegistrationRepository(s)
            expiring = {r["id"]: r for r in reg.licenses_expiring_on((now + timedelta(days=3)).astimezone(timezone(timedelta(hours=7))).date())}
            assert ids[1] in expiring and expiring[ids[1]]["status"] == "trial"
            pairs = reg.expire_due_licenses(now=now)
            s.commit()
            assert [(row.id, before) for row, before in pairs if row.id in ids] == [(ids[0], "active")]
            assert s.get(License, ids[0]).status == "suspended"

    def test_soft_delete_hides_the_tenant_and_removes_members_then_restore(self, world):
        from chann_data.models import License

        engine, ids, uids, tag = world
        with Session(engine) as s:
            repo = PlatformRepository(s)
            before, row, removed = repo.soft_delete(ids[0])
            s.commit()
            assert row.status == "deleted" and row.deleted_at is not None
            assert {m.chann_uid for m in removed} == {uids["a"], uids["staff"]}
            assert [r["id"] for r in repo.tenants(q=tag)] == [ids[1]]
            assert [r["id"] for r in repo.tenants(q=tag, status="deleted")] == [ids[0]]
            # Reversal through the ordinary status edit clears the stamp.
            RegistrationRepository(s).set_status(ids[0], "active")
            s.commit()
            lic = s.get(License, ids[0])
            assert lic.status == "active" and lic.deleted_at is None
            assert set(r["id"] for r in repo.tenants(q=tag)) == set(ids)

    def test_purge_removes_every_row_and_detaches_the_audit_trail(self, world):
        from chann_data.models import Customer, License, ServiceTicket

        engine, ids, uids, _ = world
        with Session(engine) as s:
            AuditRepository(s).write(license_id=ids[0], entity_type="customer", entity_id=uuid.uuid4(),
                                     actor_type="user", actor_id=uids["a"], action="create", field_changes={})
            s.commit()
        with Session(engine) as s:
            summary = PlatformRepository(s).purge(ids[0])
            AuditRepository(s).write(license_id=None, entity_type="license", entity_id=ids[0],
                                     actor_type="platform_admin", actor_id="admin-1", action="delete",
                                     field_changes={"purge": True, **summary}, cross_tenant=True)
            s.commit()
        with Session(engine) as s:
            assert s.get(License, ids[0]) is None
            assert s.get(License, ids[1]) is not None
            assert s.execute(select(Customer).where(Customer.license_id == ids[0])).scalars().all() == []
            assert s.execute(select(ServiceTicket).where(ServiceTicket.license_id == ids[0])).scalars().all() == []
            assert s.execute(select(LicenseMember).where(LicenseMember.license_id == ids[0])).scalars().all() == []
            assert summary["rows"]["customers"] == 1 and summary["rows"]["service_tickets"] == 2
            assert summary["audit_log_detached"] >= 1
            detached = s.execute(select(AuditLog).where(AuditLog.license_id.is_(None), AuditLog.actor_id == uids["a"])).scalars().all()
            assert detached, "the tenant's own audit rows survive the purge, detached"
            purge_row = s.execute(select(AuditLog).where(AuditLog.entity_id == ids[0], AuditLog.action == "delete")).scalars().one()
            assert purge_row.cross_tenant and purge_row.field_changes["purge"] is True

    def test_member_role_status_and_move(self, world):
        from chann_data.repositories.phase18 import PlatformConflict

        engine, ids, uids, _ = world
        with Session(engine) as s:
            repo = PlatformRepository(s)
            # role: a staff member becomes admin; the owner is refused
            before, member = repo.set_member_role(ids[0], uids["staff"], "admin")
            assert before["role"] == "technician" and member.role == "admin"
            with pytest.raises(PlatformConflict):
                repo.set_member_role(ids[0], uids["a"], "admin")
            with pytest.raises(PlatformConflict):
                repo.set_member_role(ids[0], uids["staff"], "owner")
            with pytest.raises(PlatformNotFound):
                repo.set_member_role(ids[0], uids["staff"], "no-such-role")
            # status: the owner row is refused; staff can be removed and reactivated
            with pytest.raises(PlatformConflict):
                repo.set_member_status(ids[0], uids["a"], "removed")
            _, member, _ = repo.set_member_status(ids[0], uids["staff"], "removed")
            assert member.status == "removed"
            _, member, _ = repo.set_member_status(ids[0], uids["staff"], "active")
            assert member.status == "active"
            s.commit()
        with Session(engine) as s:
            repo = PlatformRepository(s)
            with pytest.raises(PlatformConflict):
                repo.move_member(ids[0], uids["a"], target_license_id=ids[1], role_name="admin")   # owner
            with pytest.raises(PlatformConflict):
                repo.move_member(ids[0], uids["staff"], target_license_id=ids[0], role_name="admin")  # same company
            with pytest.raises(PlatformConflict):
                repo.move_member(ids[0], uids["staff"], target_license_id=ids[1], role_name="owner")
            moved = repo.move_member(ids[0], uids["staff"], target_license_id=ids[1], role_name="admin")
            s.commit()
            assert moved["source"].status == "removed" and moved["source"].license_id == ids[0]
            assert moved["target"].status == "active" and moved["target"].role == "admin" and moved["target"].license_id == ids[1]
            # already a member of the target now
            with pytest.raises(PlatformConflict):
                repo.move_member(ids[1], uids["staff"], target_license_id=ids[1], role_name="admin")
            rows = {(m.license_id, m.status) for m in s.execute(select(LicenseMember).where(LicenseMember.chann_uid == uids["staff"])).scalars()}
            assert rows == {(ids[0], "removed"), (ids[1], "active")}
