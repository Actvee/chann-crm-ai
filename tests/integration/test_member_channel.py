"""Per-OA persona separation (owner, 8 Sep 2026) — migration 0026.

One LINE account is the same chann_uid on every OA, and the OAs are
separate registrations that share only the person: a license_members
row is per (license, chann_uid, channel). The owner of company A holds
a sales row; they are a technician there only after redeeming a
technician invite, which makes a second row with its own id and role.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def data_client(migrated_db, monkeypatch):
    """The real Data-tier app on the migrated schema (same shape as
    test_tenant_edit.data_client); the internal secret rides on every call."""
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


def _identity(engine, uid: str, primary_role: str = "sales") -> str:
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity

    with Session(engine) as session:
        session.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role=primary_role,
                                  display_name=f"name {uid}"))
        session.commit()
    return uid


@pytest.fixture
def shop(migrated_db):
    """A license whose owner is CHN-PC-OWNER-<tag>, plus a technician
    invite and a cs invite for it."""
    from sqlalchemy.orm import Session

    from chann_data.repositories.phase65 import RegistrationRepository

    tag = uuid.uuid4().hex[:6]
    owner = _identity(migrated_db, f"CHN-PC-OWNER-{tag}")
    with Session(migrated_db) as session:
        repo = RegistrationRepository(session)
        lic = repo.create_license(company_name=f"Persona {tag}", created_by_chann_uid=owner)
        session.commit()
        tech_code = repo.create_invite(lic.id, role="technician", max_uses=3).invite_code
        cs_code = repo.create_invite(lic.id, role="cs", max_uses=3).invite_code
        session.commit()
        return {"license_id": lic.id, "owner": owner, "tech_code": tech_code, "cs_code": cs_code, "tag": tag}


class TestMembershipsArePerChannel:
    def test_the_owner_is_a_stranger_to_the_technician_oa_until_they_redeem(self, migrated_db, shop):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import MemberRepository, TenantScope

        scope = TenantScope(license_id=shop["license_id"])
        with Session(migrated_db) as session:
            repo = MemberRepository(session)
            assert repo.memberships_of(shop["owner"], oa="technician") == []
            sales = repo.memberships_of(shop["owner"], oa="sales")
            assert [m.channel for m in sales] == ["sales"]
            sales_id = sales[0].id

        with Session(migrated_db) as session:
            member = RegistrationRepository(session).redeem_invite(
                invite_code=shop["tech_code"], chann_uid=shop["owner"], oa="technician",
            )
            session.commit()
            assert member.channel == "technician" and member.role == "technician"
            assert member.id != sales_id, "a second row, not the owner's row re-labelled"

        with Session(migrated_db) as session:
            repo = MemberRepository(session)
            tech = repo.memberships_of(shop["owner"], oa="technician")
            assert len(tech) == 1 and tech[0].role == "technician"
            # The sales side is untouched: still the owner, still one row.
            sales = repo.memberships_of(shop["owner"], oa="sales")
            assert len(sales) == 1 and sales[0].role == "owner"
            # get() answers per channel with different ids.
            assert repo.get(scope, shop["owner"], channel="sales").id == sales_id
            assert repo.get(scope, shop["owner"], channel="technician").id == tech[0].id
            # Unspecified: the sales row.
            assert repo.get(scope, shop["owner"]).id == sales_id

    def test_permissions_come_from_the_channel_in_use(self, migrated_db, shop):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase2 import AuthorizationRepository
        from chann_data.repositories.phase65 import RegistrationRepository
        from chann_data.repositories.tenant_scope import TenantScope

        scope = TenantScope(license_id=shop["license_id"])
        with Session(migrated_db) as session:
            RegistrationRepository(session).redeem_invite(
                invite_code=shop["tech_code"], chann_uid=shop["owner"], oa="technician",
            )
            session.commit()
        with Session(migrated_db) as session:
            auth = AuthorizationRepository(session)
            as_sales = auth.context(scope, shop["owner"], channel="sales")
            as_tech = auth.context(scope, shop["owner"], channel="technician")
        assert as_sales["is_owner"] is True and as_sales["channel"] == "sales"
        assert as_tech["is_owner"] is False and as_tech["role"] == "technician"
        assert "member.manage" not in as_tech["permission_keys"]
        assert "ticket.read" in as_tech["permission_keys"]

    def test_a_code_for_the_other_oa_is_refused(self, migrated_db, shop):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase65 import RegistrationConflict, RegistrationRepository

        stranger = _identity(migrated_db, f"CHN-PC-X-{shop['tag']}")
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            with pytest.raises(RegistrationConflict, match="technician OA"):
                repo.redeem_invite(invite_code=shop["tech_code"], chann_uid=stranger, oa="sales")
            with pytest.raises(RegistrationConflict, match="sales OA"):
                repo.redeem_invite(invite_code=shop["cs_code"], chann_uid=stranger, oa="technician")
            session.rollback()
        with Session(migrated_db) as session:
            # Nothing was written and no use was burnt.
            from chann_data.repositories.tenant_scope import MemberRepository

            assert MemberRepository(session).memberships_of(stranger) == []
            codes = {i.invite_code: i.used_count for i in RegistrationRepository(session).list_invites(shop["license_id"])}
            assert codes[shop["tech_code"]] == 0 and codes[shop["cs_code"]] == 0

    def test_redeeming_twice_on_the_same_channel_is_idempotent(self, migrated_db, shop):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase65 import RegistrationRepository

        tech = _identity(migrated_db, f"CHN-PC-T-{shop['tag']}", "technician")
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            first = repo.redeem_invite(invite_code=shop["tech_code"], chann_uid=tech, oa="technician")
            again = repo.redeem_invite(invite_code=shop["tech_code"], chann_uid=tech, oa="technician")
            session.commit()
            assert first.id == again.id
            used = {i.invite_code: i.used_count for i in repo.list_invites(shop["license_id"])}
            assert used[shop["tech_code"]] == 1


class TestMemberManagementRoutes:
    def _redeem(self, data_client, code, uid, oa):
        response = data_client.post("/internal/v1/invites/redeem", json={"invite_code": code, "chann_uid": uid, "oa": oa})
        assert response.status_code == 201, response.text
        return response.json()

    def test_redeem_answers_the_full_member_with_the_company_name(self, data_client, migrated_db, shop):
        body = self._redeem(data_client, shop["tech_code"], shop["owner"], "technician")
        assert body["channel"] == "technician" and body["role"] == "technician"
        assert body["company_name"].startswith("Persona") and body["id"] and body["is_owner"] is False
        wrong = data_client.post("/internal/v1/invites/redeem", json={"invite_code": shop["cs_code"], "chann_uid": shop["owner"], "oa": "technician"})
        assert wrong.status_code == 409 and "sales OA" in wrong.text

    def test_the_list_carries_channel_owner_and_name(self, data_client, migrated_db, shop):
        self._redeem(data_client, shop["tech_code"], shop["owner"], "technician")
        rows = data_client.get(f"/internal/v1/licenses/{shop['license_id']}/members").json()
        by_channel = {r["channel"]: r for r in rows if r["chann_uid"] == shop["owner"]}
        assert set(by_channel) == {"sales", "technician"}
        assert by_channel["sales"]["is_owner"] is True and by_channel["technician"]["is_owner"] is False
        assert by_channel["sales"]["display_name"] == f"name {shop['owner']}"
        assert by_channel["sales"]["joined_at"]
        invites = data_client.get(f"/internal/v1/licenses/{shop['license_id']}/invites").json()
        assert {i["role"]: i["channel"] for i in invites} == {"technician": "technician", "cs": "sales"}

    def test_get_member_and_authorization_per_channel(self, data_client, migrated_db, shop):
        self._redeem(data_client, shop["tech_code"], shop["owner"], "technician")
        base = f"/internal/v1/licenses/{shop['license_id']}"
        sales = data_client.get(f"{base}/members/{shop['owner']}", params={"channel": "sales"}).json()
        tech = data_client.get(f"{base}/members/{shop['owner']}", params={"channel": "technician"}).json()
        assert sales["role"] == "owner" and tech["role"] == "technician" and sales["id"] != tech["id"]
        assert data_client.get(f"{base}/members/{shop['owner']}").json()["id"] == sales["id"]
        assert data_client.get(f"{base}/members/{shop['owner']}", params={"channel": "cs"}).status_code == 422
        auth_t = data_client.get(f"{base}/authorization/{shop['owner']}", params={"channel": "technician"}).json()
        auth_s = data_client.get(f"{base}/authorization/{shop['owner']}").json()
        assert auth_t["is_owner"] is False and auth_s["is_owner"] is True

    def test_the_owner_row_cannot_be_removed(self, data_client, migrated_db, shop):
        response = data_client.patch(
            f"/internal/v1/licenses/{shop['license_id']}/members/{shop['owner']}/status",
            json={"status": "removed", "channel": "sales"},
        )
        assert response.status_code == 409 and "owner cannot be removed" in response.text
        # ...but the owner's technician row is an ordinary technician.
        self._redeem(data_client, shop["tech_code"], shop["owner"], "technician")
        response = data_client.patch(
            f"/internal/v1/licenses/{shop['license_id']}/members/{shop['owner']}/status",
            json={"status": "removed", "channel": "technician"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "removed"
        assert data_client.get(f"/internal/v1/identities/{shop['owner']}/memberships", params={"oa": "technician"}).json() == []
        assert len(data_client.get(f"/internal/v1/identities/{shop['owner']}/memberships", params={"oa": "sales"}).json()) == 1

    def test_removing_a_technician_frees_their_teams_and_jobs(self, data_client, migrated_db, shop):
        from sqlalchemy.orm import Session

        from chann_data.models import ServiceTicket, TechnicianTeamMember
        from chann_data.repositories.phase12 import ServiceTicketRepository
        from chann_data.repositories.phase7 import TechnicianTeamRepository
        from chann_data.repositories.tenant_scope import TenantScope

        tech = _identity(migrated_db, f"CHN-PC-T2-{shop['tag']}", "technician")
        member = self._redeem(data_client, shop["tech_code"], tech, "technician")
        member_id = uuid.UUID(member["id"])
        scope = TenantScope(license_id=shop["license_id"])
        with Session(migrated_db) as session:
            team = TechnicianTeamRepository(session).create(scope, f"Team {shop['tag']}")
            TechnicianTeamRepository(session).add_member(scope, team.id, member_id, is_lead=True)
            tickets = ServiceTicketRepository(session)
            open_job = tickets.create(scope, issue_description="แอร์ไม่เย็น")
            done_job = tickets.create(scope, issue_description="เสร็จแล้ว")
            for job, status in ((open_job, "assigned"), (done_job, "completed")):
                job.assigned_target_type = "technician"
                job.assigned_to_ref = member_id
                job.status = status
            session.commit()
            open_id, done_id = open_job.id, done_job.id

        response = data_client.patch(
            f"/internal/v1/licenses/{shop['license_id']}/members/{tech}/status",
            json={"status": "removed", "channel": "technician"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "removed"
        assert [t["id"] for t in body["unassigned_tickets"]] == [str(open_id)]

        with Session(migrated_db) as session:
            assert session.query(TechnicianTeamMember).filter_by(member_id=member_id).count() == 0
            freed = session.get(ServiceTicket, open_id)
            assert freed.assigned_to_ref is None and freed.status == "open" and freed.accept_status == "pending"
            kept = session.get(ServiceTicket, done_id)
            assert kept.assigned_to_ref == member_id and kept.status == "completed"

        # Reactivating brings the row back as it was, without its old jobs.
        back = data_client.patch(
            f"/internal/v1/licenses/{shop['license_id']}/members/{tech}/status",
            json={"status": "active", "channel": "technician"},
        )
        assert back.status_code == 200 and back.json()["status"] == "active" and back.json()["unassigned_tickets"] == []
        assert back.json()["id"] == member["id"]

    def test_status_and_reset_answer_404_for_the_wrong_channel(self, data_client, migrated_db, shop):
        base = f"/internal/v1/licenses/{shop['license_id']}/members/{shop['owner']}"
        assert data_client.patch(f"{base}/status", json={"status": "removed", "channel": "technician"}).status_code == 404
        assert data_client.post(f"{base}/reset", json={"channel": "technician"}).status_code == 404
        reset = data_client.post(f"{base}/reset", json={"channel": "sales"})
        assert reset.status_code == 200 and reset.json()["chann_uid"] == shop["owner"]

    def test_role_changes_address_one_channel(self, data_client, migrated_db, shop):
        self._redeem(data_client, shop["tech_code"], shop["owner"], "technician")
        base = f"/internal/v1/licenses/{shop['license_id']}/members/{shop['owner']}/role"
        # The owner's sales row is protected by the transfer rule as before.
        assert data_client.patch(base, json={"role_name": "cs", "channel": "sales"}).status_code == 409
        # The technician row of the same person is an ordinary row.
        changed = data_client.patch(base, json={"role_name": "cs", "channel": "technician"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["role"] == "cs" and changed.json()["channel"] == "technician"
        sales = data_client.get(f"/internal/v1/licenses/{shop['license_id']}/members/{shop['owner']}", params={"channel": "sales"}).json()
        assert sales["role"] == "owner"

    def test_member_changes_are_audited(self, data_client, migrated_db, shop):
        tech = _identity(migrated_db, f"CHN-PC-T3-{shop['tag']}", "technician")
        self._redeem(data_client, shop["tech_code"], tech, "technician")
        base = f"/internal/v1/licenses/{shop['license_id']}"
        data_client.patch(f"{base}/members/{tech}/status", json={"status": "removed", "channel": "technician"},
                          headers={"X-Actor-Id": shop["owner"]})
        data_client.post(f"{base}/members/{tech}/reset", json={"channel": "technician"}, headers={"X-Actor-Id": shop["owner"]})
        rows = data_client.get(f"{base}/audit-log", params={"entity_type": "license_member"}).json()
        actions = [r["action"] for r in rows if r.get("actor_id") == shop["owner"]]
        assert "status" in actions and "update" in actions
