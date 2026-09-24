"""Round 21D — the plan checks the Data tier makes itself (spec §4.1, §5.7,
§7.2, §7.3, §5.9). Each refusal is a PlanFeatureLocked, which every route
turns into 403 plan_required; nothing is written when it refuses."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chann_data.models import (
    ChannIdentity, CustomerLicenseLink, License, LicenseInvite, LicenseMember, ServiceReport,
)
from chann_data.plans import PlanFeatureLocked
from chann_data.repositories.phase14 import ApprovalRepository
from chann_data.repositories.phase2 import RoleRepository
from chann_data.repositories.phase65 import RegistrationRepository

from .test_phase14_approvals import _make_tenant, _submitted_report

SECRET = {"X-Internal-Secret": "test-internal-secret"}


def _set_plan(engine, license_id, code: str) -> None:
    with Session(engine) as s:
        s.get(License, license_id).plan_code = code
        s.commit()


@pytest.fixture
def shop(migrated_db):
    tag = uuid.uuid4().hex[:6]
    owner = f"CHN-21DF{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=owner, line_user_id=f"line-{owner}", primary_role="sales"))
        s.commit()
    with Session(migrated_db) as s:
        row = RegistrationRepository(s).create_license(company_name=f"ร้านแอร์ {tag}", created_by_chann_uid=owner)
        row.company_phone = "021234567"
        license_id, company_code = row.id, row.company_code
        s.commit()
    return migrated_db, license_id, company_code, tag


def _http(migrated_db, monkeypatch):
    from fastapi.testclient import TestClient

    from chann_data.config import settings
    from chann_data.db import get_session
    from chann_data.main import app

    monkeypatch.setattr(settings, "admin_secret", "test-internal-secret")
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setitem(app.dependency_overrides, get_session, override_session)
    return TestClient(app)


class TestTechnicianInvites:
    def test_starter_cannot_mint_one(self, shop):
        engine, license_id, _code, _tag = shop
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                RegistrationRepository(s).create_invite(license_id, role="technician")
            s.rollback()
        assert caught.value.detail()["feature"] == "feature.service"
        with Session(engine) as s:
            RegistrationRepository(s).create_invite(license_id, role="member")   # a sales invite is fine
            s.rollback()

    def test_a_code_made_before_a_downgrade_is_refused_and_not_consumed(self, shop):
        engine, license_id, _code, tag = shop
        tech = f"CHN-21DT{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=tech, line_user_id=f"line-{tech}", primary_role="technician"))
            code = RegistrationRepository(s).create_invite(license_id, role="technician").invite_code
            s.commit()
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                RegistrationRepository(s).redeem_invite(invite_code=code, chann_uid=tech, oa="technician")
            s.rollback()
        assert caught.value.detail()["company_name"].startswith("ร้านแอร์")
        with Session(engine) as s:
            invite = s.execute(select(LicenseInvite).where(LicenseInvite.invite_code == code)).scalar_one()
            assert invite.used_count == 0
            assert s.execute(select(LicenseMember).where(LicenseMember.chann_uid == tech)).first() is None
        _set_plan(engine, license_id, "pro")                  # works again after the upgrade
        with Session(engine) as s:
            member = RegistrationRepository(s).redeem_invite(invite_code=code, chann_uid=tech, oa="technician")
            assert member.channel == "technician"
            s.commit()


class TestCustomerLink:
    def test_starter_refuses_the_link_and_writes_nothing(self, shop):
        engine, license_id, company_code, tag = shop
        customer = f"CHN-21DC{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=customer, line_user_id=f"line-{customer}", primary_role="customer"))
            s.commit()
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                RegistrationRepository(s).link_customer(chann_uid=customer, company_code=company_code)
            s.rollback()
        body = caught.value.detail()
        assert body["feature"] == "feature.customer_line_link" and body["company_phone"] == "021234567"
        with Session(engine) as s:
            assert s.execute(select(CustomerLicenseLink).where(
                CustomerLicenseLink.chann_uid == customer)).first() is None


class TestCustomRoles:
    def test_assigning_a_custom_role_on_starter_is_403_and_a_standard_one_works(self, shop, monkeypatch):
        from chann_data.repositories.tenant_scope import TenantScope

        engine, license_id, _code, tag = shop
        http = _http(engine, monkeypatch)
        person = f"CHN-21DR{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=person, line_user_id=f"line-{person}", primary_role="sales"))
            s.flush()
            RoleRepository(s).create(TenantScope(license_id=license_id), "ช่างอาวุโส", {"customer.read"})
            s.add(LicenseMember(license_id=license_id, chann_uid=person, role="member", status="active"))
            s.commit()
        _set_plan(engine, license_id, "starter")
        out = http.patch(f"/internal/v1/licenses/{license_id}/members/{person}/role",
                         json={"role_name": "ช่างอาวุโส"}, headers=SECRET)
        assert out.status_code == 403 and out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.custom_roles"
        ok = http.patch(f"/internal/v1/licenses/{license_id}/members/{person}/role",
                        json={"role_name": "admin"}, headers=SECRET)
        assert ok.status_code == 200, ok.text


class TestTechnicianChannelGates:
    """Pre-flight S21 (spec §7.2): on a plan without feature.service, every
    road that puts or brings back a person on the Technician OA is gated
    at the Data tier — not only the invite/redeem road TestTechnicianInvites
    already covers. The seat check stays alongside each of these."""

    def test_reactivating_a_technician_row_on_starter_is_refused(self, shop):
        from chann_data.repositories.tenant_scope import MemberRepository, TenantScope

        engine, license_id, _code, tag = shop
        tech = f"CHN-21DZ{tag}"
        scope = TenantScope(license_id=license_id)
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=tech, line_user_id=f"line-{tech}", primary_role="technician"))
            s.add(LicenseMember(
                license_id=license_id, chann_uid=tech, role="technician",
                channel="technician", status="removed",
            ))
            s.commit()
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                MemberRepository(s).set_status(scope, tech, channel="technician", status="active")
            s.rollback()
        assert caught.value.detail()["feature"] == "feature.service"
        with Session(engine) as s:
            row = s.execute(select(LicenseMember).where(LicenseMember.chann_uid == tech)).scalar_one()
            assert row.status == "removed", "a refused reactivation must not be written"
        _set_plan(engine, license_id, "pro")
        with Session(engine) as s:
            member, _unassigned = MemberRepository(s).set_status(
                scope, tech, channel="technician", status="active",
            )
            assert member.status == "active"
            s.commit()

    def test_moving_a_technician_into_a_starter_shop_is_refused(self, migrated_db):
        from chann_data.repositories.phase18 import PlatformRepository
        from chann_data.repositories.tenant_scope import TenantScope

        tag = uuid.uuid4().hex[:6]
        source_owner, target_owner, tech = (
            f"CHN-21DMS{tag}", f"CHN-21DMT{tag}", f"CHN-21DMX{tag}",
        )
        with Session(migrated_db) as s:
            for uid in (source_owner, target_owner, tech):
                s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="sales"))
            s.commit()
        with Session(migrated_db) as s:
            source = RegistrationRepository(s).create_license(
                company_name=f"ต้นทาง {tag}", created_by_chann_uid=source_owner,
            )
            target = RegistrationRepository(s).create_license(
                company_name=f"ปลายทาง {tag}", created_by_chann_uid=target_owner,
            )
            source_id, target_id = source.id, target.id
            s.commit()
        source_scope = TenantScope(license_id=source_id)
        with Session(migrated_db) as s:
            s.add(LicenseMember(
                license_id=source_id, chann_uid=tech, role="technician",
                channel="technician", status="active",
            ))
            s.commit()
        _set_plan(migrated_db, target_id, "starter")
        with Session(migrated_db) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                PlatformRepository(s).move_member(
                    source_id, tech, target_license_id=target_id, role_name="technician",
                )
            s.rollback()
        assert caught.value.detail()["feature"] == "feature.service"
        with Session(migrated_db) as s:
            from chann_data.repositories.tenant_scope import MemberRepository

            still_source = MemberRepository(s).get(source_scope, tech, channel="technician")
            assert still_source is not None and still_source.status == "active", \
                "a refused move must leave the source membership untouched"
        _set_plan(migrated_db, target_id, "pro")
        with Session(migrated_db) as s:
            moved = PlatformRepository(s).move_member(
                source_id, tech, target_license_id=target_id, role_name="technician",
            )
            target_channel = moved["target"].channel
            s.commit()
        assert target_channel == "technician"

    def test_assigning_the_technician_role_on_starter_is_403(self, shop, monkeypatch):
        engine, license_id, _code, tag = shop
        http = _http(engine, monkeypatch)
        person = f"CHN-21DW{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=person, line_user_id=f"line-{person}", primary_role="technician"))
            s.add(LicenseMember(
                license_id=license_id, chann_uid=person, role="cs",
                channel="technician", status="active",
            ))
            s.commit()
        _set_plan(engine, license_id, "starter")
        out = http.patch(
            f"/internal/v1/licenses/{license_id}/members/{person}/role",
            json={"role_name": "technician", "channel": "technician"}, headers=SECRET,
        )
        assert out.status_code == 403 and out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.service"
        with Session(engine) as s:
            row = s.execute(select(LicenseMember).where(LicenseMember.chann_uid == person)).scalar_one()
            assert row.role == "cs", "a refused role assignment must not be written"
        _set_plan(engine, license_id, "pro")
        ok = http.patch(
            f"/internal/v1/licenses/{license_id}/members/{person}/role",
            json={"role_name": "technician", "channel": "technician"}, headers=SECRET,
        )
        assert ok.status_code == 200, ok.text


class TestTheThreeGatesOverHttp:
    """Final fix (Task 4): the three phase65 gates at the route, not only
    the repository — 403 plan_required on Starter, 201 on Pro."""

    def test_minting_a_technician_invite(self, shop, monkeypatch):
        engine, license_id, _code, _tag = shop
        http = _http(engine, monkeypatch)
        _set_plan(engine, license_id, "starter")
        out = http.post(f"/internal/v1/licenses/{license_id}/invites", json={"role": "technician"}, headers=SECRET)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.service"
        with Session(engine) as s:
            assert s.execute(select(LicenseInvite).where(
                LicenseInvite.license_id == license_id, LicenseInvite.role == "technician")).first() is None
        _set_plan(engine, license_id, "pro")
        ok = http.post(f"/internal/v1/licenses/{license_id}/invites", json={"role": "technician"}, headers=SECRET)
        assert ok.status_code == 201, ok.text
        assert ok.json()["channel"] == "technician"

    def test_redeeming_a_technician_invite(self, shop, monkeypatch):
        engine, license_id, _code, tag = shop
        http = _http(engine, monkeypatch)
        tech = f"CHN-21DH{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=tech, line_user_id=f"line-{tech}", primary_role="technician"))
            code = RegistrationRepository(s).create_invite(license_id, role="technician").invite_code
            s.commit()
        _set_plan(engine, license_id, "starter")
        body = {"invite_code": code, "chann_uid": tech, "oa": "technician"}
        out = http.post("/internal/v1/invites/redeem", json=body, headers=SECRET)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.service"
        with Session(engine) as s:
            assert s.execute(select(LicenseInvite).where(LicenseInvite.invite_code == code)).scalar_one().used_count == 0
        _set_plan(engine, license_id, "pro")
        ok = http.post("/internal/v1/invites/redeem", json=body, headers=SECRET)
        assert ok.status_code == 201, ok.text

    def test_linking_a_customer(self, shop, monkeypatch):
        engine, license_id, company_code, tag = shop
        http = _http(engine, monkeypatch)
        customer = f"CHN-21DK{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=customer, line_user_id=f"line-{customer}", primary_role="customer"))
            s.commit()
        _set_plan(engine, license_id, "starter")
        body = {"chann_uid": customer, "company_code": company_code}
        out = http.post("/internal/v1/customer-links", json=body, headers=SECRET)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.customer_line_link"
        assert out.json()["detail"]["company_phone"] == "021234567"
        _set_plan(engine, license_id, "pro")
        ok = http.post("/internal/v1/customer-links", json=body, headers=SECRET)
        assert ok.status_code == 201, ok.text

    def test_a_role_change_reads_the_plan_once(self, shop, monkeypatch):
        """A custom role on the technician channel needs two features; the
        route reads the shop's plan one time for both."""
        from chann_data.repositories.plan_repo import PlanRepository
        from chann_data.repositories.tenant_scope import TenantScope

        engine, license_id, _code, tag = shop
        http = _http(engine, monkeypatch)
        person = f"CHN-21DO{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=person, line_user_id=f"line-{person}", primary_role="technician"))
            s.flush()
            RoleRepository(s).create(TenantScope(license_id=license_id), "ช่างหัวหน้า", {"ticket.read"})
            s.add(LicenseMember(license_id=license_id, chann_uid=person, role="technician",
                                channel="technician", status="active"))
            s.commit()
        reads = []
        real = PlanRepository.plan_code

        def counted(self, lid):
            reads.append(lid)
            return real(self, lid)

        monkeypatch.setattr(PlanRepository, "plan_code", counted)
        out = http.patch(f"/internal/v1/licenses/{license_id}/members/{person}/role",
                         json={"role_name": "ช่างหัวหน้า", "channel": "technician"}, headers=SECRET)
        assert out.status_code == 200, out.text                         # Pro has both features
        assert len(reads) == 1, reads


class TestAssignmentRulesAtTheDataTier:
    """Final fix (Task 11): Ruling 26 at the tier seam — the Data route
    refuses a technician-scope rule on a plan without service, whoever
    calls it; a sales rule stays open on every plan."""

    def test_the_route(self, shop, monkeypatch):
        from chann_data.models import AssignmentRule

        engine, license_id, _code, _tag = shop
        http = _http(engine, monkeypatch)
        url = f"/internal/v1/licenses/{license_id}/assignment-rules"
        rule = {"rules": [], "max_per_day": 5}
        _set_plan(engine, license_id, "starter")
        out = http.put(url, json={"scope": "technician", "rules_json": rule}, headers=SECRET)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.service"
        with Session(engine) as s:
            assert s.execute(select(AssignmentRule).where(
                AssignmentRule.license_id == license_id, AssignmentRule.scope == "technician")).first() is None
        sales = http.put(url, json={"scope": "sales", "rules_json": rule}, headers=SECRET)
        assert sales.status_code == 200, sales.text
        _set_plan(engine, license_id, "pro")
        ok = http.put(url, json={"scope": "technician", "rules_json": rule}, headers=SECRET)
        assert ok.status_code == 200, ok.text


class TestMultiLevelApproval:
    TWO_STEPS = {"steps": [
        {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
        {"order": 2, "approver_type": "role", "approver_ref": "admin"},
    ]}

    def test_saving_two_steps_needs_enterprise(self, migrated_db, monkeypatch):
        tenant = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        license_id = tenant["scope"].license_id
        http = _http(migrated_db, monkeypatch)
        out = http.put(f"/internal/v1/licenses/{license_id}/approval-workflows/service_report",
                       json={"rules_json": self.TWO_STEPS}, headers=SECRET)
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "feature.multi_level_approval"
        _set_plan(migrated_db, license_id, "enterprise")
        out = http.put(f"/internal/v1/licenses/{license_id}/approval-workflows/service_report",
                       json={"rules_json": self.TWO_STEPS}, headers=SECRET)
        assert out.status_code == 200, out.text

    def test_on_pro_a_saved_chain_opens_step_one_only_and_a_report_mid_flow_keeps_its_steps(
        self, migrated_db, monkeypatch,
    ):
        tenant = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        license_id = tenant["scope"].license_id
        http = _http(migrated_db, monkeypatch)
        _set_plan(migrated_db, license_id, "enterprise")
        assert http.put(f"/internal/v1/licenses/{license_id}/approval-workflows/service_report",
                        json={"rules_json": self.TWO_STEPS}, headers=SECRET).status_code == 200
        _, before = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        opened = http.post(f"/internal/v1/licenses/{license_id}/service-reports/{before}/approval-steps",
                           headers=SECRET).json()
        assert len(opened) == 2
        _set_plan(migrated_db, license_id, "pro")
        _, after = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        opened = http.post(f"/internal/v1/licenses/{license_id}/service-reports/{after}/approval-steps",
                           headers=SECRET).json()
        assert [s["step_order"] for s in opened] == [1]
        with tenant["session"]() as s:
            assert len(ApprovalRepository(s).steps_for_entity(tenant["scope"], "service_report", before)) == 2


class TestTheRoundTrip:
    """Spec §11.3: Pro → Starter → Pro leaves every row byte-identical —
    a plan locks, it never deletes (spec §1)."""

    TABLES = ("service_tickets", "custom_roles", "role_permissions", "license_members", "approval_workflows",
              "document_templates", "chat_sessions", "warranties", "customer_license_links", "api_keys")

    def test_nothing_changes_on_the_way_down_and_back(self, shop):
        from sqlalchemy import text

        from chann_data.repositories.phase12 import ServiceTicketRepository
        from chann_data.repositories.phase18 import PlatformRepository
        from chann_data.repositories.tenant_scope import TenantScope

        engine, license_id, _code, _tag = shop
        scope = TenantScope(license_id=license_id)
        with Session(engine) as s:
            ServiceTicketRepository(s).create(scope, issue_description="แอร์ไม่เย็น")
            RoleRepository(s).create(scope, "ผู้ช่วยขาย", {"customer.read"})
            s.commit()

        def snapshot():
            with engine.connect() as conn:
                return {t: sorted(map(str, conn.execute(
                    text(f"SELECT * FROM {t} WHERE license_id = :id"), {"id": license_id}).all()))
                    for t in self.TABLES}

        before = snapshot()
        for code in ("starter", "pro"):
            with Session(engine) as s:
                PlatformRepository(s).update(license_id, {"plan_code": code})
                s.commit()
        assert snapshot() == before


class TestTheChatSweep:
    def test_a_starter_shop_is_skipped(self, shop):
        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.plan_repo import PlanRepository

        engine, license_id, _code, _tag = shop
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            skip = PlanRepository(s).licences_without("feature.live_chat")
            assert license_id in skip
            assert all(row.license_id != license_id
                       for row in ChatSessionRepository(s).sla_overdue(skip_licenses=skip))
