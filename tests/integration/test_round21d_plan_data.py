"""Round 21D on Postgres — the plan payload, the seat check and the plan
change (spec §3, §5.7, §11.2–11.3; owner decision Q2, 24 ก.ย. 2569)."""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chann_data.models import AuditLog, ChannIdentity, License, LicenseMember
from chann_data.plans import MemberLimitReached, PlanDowngradeRefused, UnknownPlan
from chann_data.repositories.phase18 import PlatformRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.plan_repo import PlanRepository
from chann_data.repositories.tenant_scope import MemberRepository, TenantScope

SECRET = {"X-Internal-Secret": "test-internal-secret"}


def _identity(s, uid: str) -> None:
    s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="sales"))


@pytest.fixture
def make_shop(migrated_db):
    """make_shop(plan_code, people) → (license_id, [chann_uid, ...]) with the
    owner first. Everyone joins through a real invite, so the seat check is
    on the road the fixture itself walks."""
    def make(plan_code: str, people: int):
        tag = uuid.uuid4().hex[:6]
        uids = [f"CHN-21D{tag}{i:03d}" for i in range(people)]
        with Session(migrated_db) as s:
            for uid in uids:
                _identity(s, uid)
            s.commit()
        with Session(migrated_db) as s:
            row = RegistrationRepository(s).create_license(
                company_name=f"Plan shop {tag}", created_by_chann_uid=uids[0])
            row.plan_code = plan_code
            license_id = row.id
            s.commit()
        for uid in uids[1:]:
            with Session(migrated_db) as s:
                reg = RegistrationRepository(s)
                invite = reg.create_invite(license_id, role="member")
                reg.redeem_invite(invite_code=invite.invite_code, chann_uid=uid)
                s.commit()
        return license_id, uids
    return make


def _join(engine, license_id, uid: str) -> None:
    with Session(engine) as s:
        _identity(s, uid)
        s.commit()
    with Session(engine) as s:
        reg = RegistrationRepository(s)
        invite = reg.create_invite(license_id, role="member")
        reg.redeem_invite(invite_code=invite.invite_code, chann_uid=uid)
        s.commit()


class TestTheSeat:
    def test_starter_takes_its_fifth_person_and_refuses_the_sixth(self, migrated_db, make_shop):
        license_id, _ = make_shop("starter", 4)
        _join(migrated_db, license_id, f"CHN-21DX{uuid.uuid4().hex[:6]}")      # the 5th: limit reached, not passed
        with pytest.raises(MemberLimitReached) as caught:
            _join(migrated_db, license_id, f"CHN-21DY{uuid.uuid4().hex[:6]}")
        body = caught.value.detail()
        assert body["error"] == "member_limit_reached" and body["limit"] == 5 and body["plan"] == "starter"
        assert body["license_id"] == str(license_id) and body["owner_chann_uid"]
        with Session(migrated_db) as s:
            assert PlanRepository(s).active_people(license_id) == 5

    def test_a_removed_row_frees_its_seat_and_reactivating_takes_one(self, migrated_db, make_shop):
        license_id, uids = make_shop("starter", 5)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            MemberRepository(s).set_status(scope, uids[4], channel="sales", status="removed")
            s.commit()
        _join(migrated_db, license_id, f"CHN-21DZ{uuid.uuid4().hex[:6]}")      # back to 5
        with Session(migrated_db) as s:
            with pytest.raises(MemberLimitReached):
                MemberRepository(s).set_status(scope, uids[4], channel="sales", status="active")
            s.rollback()

    def test_one_person_on_two_channels_is_one_seat(self, migrated_db, make_shop):
        license_id, uids = make_shop("pro", 15)
        with Session(migrated_db) as s:
            # The owner redeeming a technician invite adds a ROW, not a person.
            PlanRepository(s).require_seat(license_id, uids[0])
            assert PlanRepository(s).active_people(license_id) == 15
            s.rollback()

    def test_enterprise_plus_has_no_cap(self, migrated_db, make_shop):
        license_id, _ = make_shop("enterprise_plus", 1)
        tag = uuid.uuid4().hex[:5]
        uids = [f"CHN-21DP{tag}{i:03d}" for i in range(500)]
        with Session(migrated_db) as s:
            for uid in uids:
                _identity(s, uid)
            s.commit()                     # identities first: license_members has an FK to them
        with Session(migrated_db) as s:
            s.add_all(LicenseMember(license_id=license_id, chann_uid=uid, role="member", status="active")
                      for uid in uids)
            s.commit()
        with Session(migrated_db) as s:
            PlanRepository(s).require_seat(license_id, f"CHN-21DQ{tag}")
            assert PlanRepository(s).active_people(license_id) == 501
            s.rollback()

    def test_two_people_racing_for_the_last_seat_one_wins(self, migrated_db, make_shop):
        license_id, _ = make_shop("starter", 4)
        racers = [f"CHN-21DR{uuid.uuid4().hex[:6]}" for _ in range(2)]
        codes = []
        with Session(migrated_db) as s:
            for uid in racers:
                _identity(s, uid)
                codes.append(RegistrationRepository(s).create_invite(license_id, role="member").invite_code)
            s.commit()
        barrier, outcomes = threading.Barrier(2), []

        def race(uid, code):
            with sessionmaker(bind=migrated_db)() as s:
                barrier.wait()
                try:
                    RegistrationRepository(s).redeem_invite(invite_code=code, chann_uid=uid)
                    s.commit()
                    outcomes.append("joined")
                except MemberLimitReached:
                    s.rollback()
                    outcomes.append("refused")

        threads = [threading.Thread(target=race, args=pair) for pair in zip(racers, codes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(outcomes) == ["joined", "refused"]

    def test_moving_someone_into_a_full_shop_is_refused(self, migrated_db, make_shop):
        full, _ = make_shop("starter", 5)
        source, uids = make_shop("pro", 2)
        with Session(migrated_db) as s:
            with pytest.raises(MemberLimitReached):
                PlatformRepository(s).move_member(source, uids[1], target_license_id=full, role_name="member")
            s.rollback()


class TestThePlanChange:
    def test_a_downgrade_over_the_limit_is_refused_with_how_many_to_inactivate(self, migrated_db, make_shop):
        license_id, uids = make_shop("pro", 9)
        with Session(migrated_db) as s:
            with pytest.raises(PlanDowngradeRefused) as caught:
                PlatformRepository(s).update(license_id, {"plan_code": "starter"})
            s.rollback()
        assert caught.value.detail()["message"] == "ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter"
        with Session(migrated_db) as s:
            assert s.get(License, license_id).plan_code == "pro"          # unchanged
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            for uid in uids[5:]:                                          # inactivate four
                MemberRepository(s).set_status(scope, uid, channel="sales", status="removed")
            s.commit()
        with Session(migrated_db) as s:
            before, row = PlatformRepository(s).update(license_id, {"plan_code": "starter"})
            assert before == {"plan_code": "pro"} and row.plan_code == "starter"
            s.commit()

    def test_an_unknown_plan_is_refused(self, migrated_db, make_shop):
        license_id, _ = make_shop("pro", 1)
        with Session(migrated_db) as s:
            with pytest.raises(UnknownPlan):
                PlatformRepository(s).update(license_id, {"plan_code": "gold"})
            s.rollback()

    def test_the_payload_and_usage(self, migrated_db, make_shop):
        license_id, _ = make_shop("starter", 3)
        with Session(migrated_db) as s:
            repo = PlanRepository(s)
            assert repo.payload(license_id)["code"] == "starter"
            assert repo.usage(license_id, month="2026-09") == {
                "members": 3, "members_limit": 5, "ai_reports_used": 0,
                "ai_reports_allowance": 0, "ai_reports_month": "2026-09",
            }

    def test_the_preview_counts_what_would_lock(self, migrated_db, make_shop):
        license_id, _ = make_shop("pro", 9)
        with Session(migrated_db) as s:
            out = PlanRepository(s).preview(license_id)
        assert out["current"] == "pro" and set(out["previews"]) == {"starter", "enterprise", "enterprise_plus"}
        starter = out["previews"]["starter"]
        assert {lock["feature"] for lock in starter["locks"]} == {
            "feature.customer_line_link", "feature.live_chat", "feature.service",
            "feature.warranty", "feature.custom_documents", "feature.custom_roles"}
        assert starter["refused"] is True and starter["inactivate"] == 4
        assert out["previews"]["enterprise"]["locks"] == [] and out["previews"]["enterprise"]["refused"] is False

    def test_the_preview_reads_the_deepest_active_chain(self, migrated_db, make_shop):
        """Not whichever workflow the query returns first (final-fix, Task 3)."""
        from chann_data.models import ApprovalWorkflow

        license_id, _ = make_shop("enterprise", 1)
        with Session(migrated_db) as s:
            s.add_all([
                ApprovalWorkflow(license_id=license_id, entity_type="quote",
                                 rules_json={"steps": [{"role": "a"}]}),
                ApprovalWorkflow(license_id=license_id, entity_type="service_report",
                                 rules_json={"steps": [{"role": "a"}, {"role": "b"}, {"role": "c"}]}),
                ApprovalWorkflow(license_id=license_id, entity_type="invoice", is_active=False,
                                 rules_json={"steps": [{"role": x} for x in "abcdefg"]}),
            ])
            s.commit()
        with Session(migrated_db) as s:
            out = PlanRepository(s).preview(license_id)
        locks = {lock["feature"]: lock["counts"] for lock in out["previews"]["pro"]["locks"]}
        assert locks["feature.multi_level_approval"] == {"steps": 3}

    def test_the_same_plan_is_not_a_downgrade(self, migrated_db, make_shop):
        """A shop already over its own plan's limit (a backfill edge) can
        still be saved on that plan: nothing changes, nothing to refuse."""
        license_id, _ = make_shop("starter", 5)
        extra = f"CHN-21DS{uuid.uuid4().hex[:6]}"
        with Session(migrated_db) as s:
            _identity(s, extra)
            s.commit()
        with Session(migrated_db) as s:
            s.add(LicenseMember(license_id=license_id, chann_uid=extra, role="member", status="active"))
            s.commit()
        with Session(migrated_db) as s:
            assert PlanRepository(s).active_people(license_id) == 6
            before, row = PlatformRepository(s).update(license_id, {"plan_code": "starter"})
            assert before == {"plan_code": "starter"} and row.plan_code == "starter"
            s.rollback()


class TestTheInternalRoutes:
    def _http(self, migrated_db, monkeypatch):
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

    def test_the_membership_carries_the_plan_and_a_change_is_seen_at_once(
        self, migrated_db, monkeypatch, make_shop, memory_cache,
    ):
        http = self._http(migrated_db, monkeypatch)
        license_id, uids = make_shop("pro", 2)
        rows = http.get(f"/internal/v1/identities/{uids[1]}/memberships?oa=sales", headers=SECRET).json()
        assert rows[0]["plan"]["code"] == "pro"                    # now cached under license_plan:<id>
        out = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "enterprise"},
                         headers={**SECRET, "X-Actor-Id": "00000000-0000-0000-0000-000000000001"})
        assert out.status_code == 200, out.text
        assert out.json()["plan_code"] == "enterprise" and out.json()["plan"]["code"] == "enterprise"
        rows = http.get(f"/internal/v1/identities/{uids[1]}/memberships?oa=sales", headers=SECRET).json()
        assert rows[0]["plan"]["code"] == "enterprise"             # k_license_plan was invalidated
        with Session(migrated_db) as s:
            audit = s.execute(select(AuditLog).where(
                AuditLog.entity_id == license_id, AuditLog.action == "update",
            ).order_by(AuditLog.created_at.desc())).scalars().first()
            assert audit.field_changes["plan_code"] == {"old": "pro", "new": "enterprise"}

    def test_the_route_refuses_a_downgrade_with_409_and_the_sentence(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("pro", 7)
        out = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "starter"}, headers=SECRET)
        assert out.status_code == 409
        assert out.json()["detail"] == {
            "error": "plan_member_limit", "members": 7, "limit": 5, "inactivate": 2,
            "plan": "starter", "plan_label": "Starter",
            "message": "ต้องปิดใช้งาน (inactivate) สมาชิก 2 คนก่อนลดแพ็กเกจเป็น Starter",
        }
        bad = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "gold"}, headers=SECRET)
        assert bad.status_code == 422 and bad.json()["detail"]["error"] == "unknown_plan"

    def test_a_redeem_past_the_limit_is_409_with_the_limit(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("starter", 5)
        uid = f"CHN-21DH{uuid.uuid4().hex[:6]}"
        with Session(migrated_db) as s:
            _identity(s, uid)
            code = RegistrationRepository(s).create_invite(license_id, role="member").invite_code
            s.commit()
        out = http.post("/internal/v1/invites/redeem", json={"invite_code": code, "chann_uid": uid}, headers=SECRET)
        assert out.status_code == 409 and out.json()["detail"]["error"] == "member_limit_reached"

    def test_the_plan_route_and_the_list_filter(self, migrated_db, monkeypatch, make_shop, memory_cache):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("starter", 2)
        out = http.get(f"/internal/v1/licenses/{license_id}/plan", headers=SECRET).json()
        assert out["plan"]["code"] == "starter" and out["usage"]["members"] == 2
        listed = http.get("/internal/v1/platform/tenants?plan=starter", headers=SECRET).json()
        assert str(license_id) in {row["id"] for row in listed}
        assert {row["plan_code"] for row in listed} == {"starter"}

    def test_an_override_reaches_the_payload_at_once(self, migrated_db, monkeypatch, make_shop, memory_cache):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("pro", 1)
        assert http.get(f"/internal/v1/licenses/{license_id}/plan", headers=SECRET).json()["plan"]["limits"]["ai_reports_per_month"] == 30
        put = http.put(f"/internal/v1/licenses/{license_id}/settings/ai_chart_quota",
                       json={"setting_value": 45}, headers=SECRET)
        assert put.status_code == 200, put.text
        assert http.get(f"/internal/v1/licenses/{license_id}/plan", headers=SECRET).json()["plan"]["limits"]["ai_reports_per_month"] == 45

    def test_the_api_key_resolution_carries_the_plan(self, migrated_db, monkeypatch, make_shop):
        from chann_data.repositories.api_keys import ApiKeyRepository, hash_key
        from chann_data.routers import internal

        http = self._http(migrated_db, monkeypatch)
        monkeypatch.setattr(internal, "rate_window_remaining", lambda *a, **k: 599)
        license_id, uids = make_shop("enterprise", 1)
        with Session(migrated_db) as s:
            _row, raw = ApiKeyRepository(s).create(TenantScope(license_id=license_id), name="ERP", created_by_chann_uid=uids[0])
            s.commit()
        out = http.post("/internal/v1/api-keys/resolve", json={"key_hash": hash_key(raw)}, headers=SECRET).json()
        assert out["plan"]["code"] == "enterprise"

    def test_reactivating_past_the_limit_on_the_tenant_route_is_409(self, migrated_db, monkeypatch, make_shop):
        """The branch set_member_status gained: a refusal, not a 500."""
        http = self._http(migrated_db, monkeypatch)
        license_id, uids = make_shop("starter", 5)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            MemberRepository(s).set_status(scope, uids[4], channel="sales", status="removed")
            s.commit()
        _join(migrated_db, license_id, f"CHN-21DS{uuid.uuid4().hex[:6]}")
        out = http.patch(f"/internal/v1/licenses/{license_id}/members/{uids[4]}/status",
                         json={"channel": "sales", "status": "active"}, headers=SECRET)
        assert out.status_code == 409, out.text
        assert out.json()["detail"]["error"] == "member_limit_reached"
        assert out.json()["detail"]["owner_chann_uid"] == uids[0]

    def test_the_preview_route(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("pro", 6)
        out = http.get(f"/internal/v1/platform/tenants/{license_id}/plan-preview", headers=SECRET)
        assert out.status_code == 200, out.text
        starter = out.json()["previews"]["starter"]
        assert starter["refused"] is True and starter["inactivate"] == 1 and starter["limit"] == 5
        missing = http.get(f"/internal/v1/platform/tenants/{uuid.uuid4()}/plan-preview", headers=SECRET)
        assert missing.status_code == 404
        # Both round-21D routes answer a missing shop with the admin routes' body.
        assert missing.json() == {"detail": {"error": "tenant_not_found"}}
        gone = http.get(f"/internal/v1/licenses/{uuid.uuid4()}/plan", headers=SECRET)
        assert gone.status_code == 404 and gone.json() == missing.json()

    def test_the_same_plan_patch_is_200_on_a_shop_over_its_limit(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("starter", 5)
        extra = f"CHN-21DT{uuid.uuid4().hex[:6]}"
        with Session(migrated_db) as s:
            _identity(s, extra)
            s.commit()
        with Session(migrated_db) as s:
            s.add(LicenseMember(license_id=license_id, chann_uid=extra, role="member", status="active"))
            s.commit()
        out = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "starter"}, headers=SECRET)
        assert out.status_code == 200, out.text
        assert out.json()["plan_code"] == "starter"

    def test_the_tenant_page_carries_the_plan_and_usage(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("enterprise", 3)
        out = http.get(f"/internal/v1/platform/tenants/{license_id}", headers=SECRET).json()
        assert out["plan_code"] == "enterprise" and out["plan"]["code"] == "enterprise"
        assert out["usage"]["members"] == 3 and out["usage"]["members_limit"] == out["plan"]["limits"]["members"]

    def test_the_summary_and_usage_count_the_same_people(self, migrated_db, monkeypatch, make_shop):
        """One person on both OAs is one member in the list, on the tenant
        page and in usage — the plan's user limit counts people, not rows."""
        http = self._http(migrated_db, monkeypatch)
        license_id, uids = make_shop("pro", 3)
        with Session(migrated_db) as s:
            s.add(LicenseMember(license_id=license_id, chann_uid=uids[0], role="technician",
                                channel="technician", status="active"))
            s.commit()
        page = http.get(f"/internal/v1/platform/tenants/{license_id}", headers=SECRET).json()
        assert page["members"] == page["usage"]["members"] == 3
        listed = [row for row in http.get("/internal/v1/platform/tenants", headers=SECRET).json()
                  if row["id"] == str(license_id)]
        assert listed[0]["members"] == 3


class TestTheCreditAllowance:
    """Owner decision Q3 (24 ก.ย. 2569): Enterprise Plus starts at 100; the
    override is honoured on Pro and up; values already set keep working;
    Starter is 0 — locked, never "used up"."""

    @pytest.mark.parametrize("code,override,expected", [
        ("pro", None, 30), ("pro", 45, 45), ("pro", 0, 0), ("enterprise", None, 100),
        ("enterprise_plus", None, 100), ("enterprise_plus", 400, 400), ("starter", 45, 0),
    ])
    def test_allowance(self, migrated_db, make_shop, code, override, expected):
        from chann_data.repositories.phase2 import LicenseSettingRepository

        license_id, _ = make_shop(code, 1)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            if override is not None:
                LicenseSettingRepository(s).upsert(scope, "ai_chart_quota", override)
                s.commit()
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).ai_chart_allowance(scope) == expected

    def test_the_last_credit_and_the_one_after(self, migrated_db, make_shop):
        from chann_data.repositories.phase2 import LicenseSettingRepository

        license_id, _ = make_shop("pro", 1)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            LicenseSettingRepository(s).upsert(scope, "ai_chart_quota", 1)
            s.commit()
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).consume_ai_chart(scope, month="2026-09") == (True, 1, 1)
            s.commit()
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).consume_ai_chart(scope, month="2026-09") == (False, 1, 1)

    def test_starter_spends_nothing(self, migrated_db, make_shop):
        from chann_data.repositories.phase2 import LicenseSettingRepository

        license_id, _ = make_shop("starter", 1)
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).consume_ai_chart(
                TenantScope(license_id=license_id), month="2026-09") == (False, 0, 0)
