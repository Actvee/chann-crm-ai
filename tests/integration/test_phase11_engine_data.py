"""The assignment engine against the real Data tier (round 18, 14 Sep 2026).

tests/integration/test_phase11_assignment.py drives the PURE engine; these
drive the endpoint that feeds it — which is where every round-18 bug lived:
the candidate pool, the load count, the round-robin stamp, and the
persistence of a sales pick.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chann_data.models import (
    ChannIdentity, Customer, LicenseMember, SalesGroup, SalesGroupMember,
    ServiceTicket, TechnicianTeam, TechnicianTeamMember,
)
from chann_data.repositories.phase11 import AssignmentRuleRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository
from chann_data.repositories.tenant_scope import TenantScope



@pytest.fixture
def api(migrated_db, monkeypatch):
    """The real Data-tier app on the migrated test schema (the same shape as
    test_data_endpoints_smoke.api; copied because pytest does not import
    sibling test modules by name)."""
    from fastapi.testclient import TestClient
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


@pytest.fixture
def shop(migrated_db):
    """One shop: an owner, two technicians in team ทีมแอร์, two salespeople
    in group ขาย A, one salesperson outside any group."""
    tag = uuid.uuid4().hex[:6]
    uids = {k: f"CHN-P11{tag}-{k}" for k in ("owner", "t1", "t2", "s1", "s2", "s3")}
    with Session(migrated_db) as s:
        for k, uid in uids.items():
            s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="sales", display_name=k))
        s.commit()
    with Session(migrated_db) as s:
        lic = RegistrationRepository(s).create_license(company_name=f"Engine {tag}", created_by_chann_uid=uids["owner"])
        s.commit()
        license_id = lic.id
    scope = TenantScope(license_id=license_id)
    members: dict[str, uuid.UUID] = {}
    with Session(migrated_db) as s:
        for k, role, channel in (("t1", "technician", "technician"), ("t2", "technician", "technician"),
                                 ("s1", "sales", "sales"), ("s2", "sales", "sales"), ("s3", "sales", "sales")):
            m = LicenseMember(license_id=license_id, chann_uid=uids[k], role=role, channel=channel, status="active")
            s.add(m); s.flush(); members[k] = m.id
        team = TechnicianTeam(id=uuid.uuid4(), license_id=license_id, team_name="ทีมแอร์")
        s.add(team); s.flush()
        for k in ("t1", "t2"):
            s.add(TechnicianTeamMember(license_id=license_id, team_id=team.id, member_id=members[k], is_lead=(k == "t1")))
        group = SalesGroup(id=uuid.uuid4(), license_id=license_id, group_name="ขาย A")
        s.add(group); s.flush()
        for k in ("s1", "s2"):
            s.add(SalesGroupMember(id=uuid.uuid4(), license_id=license_id, group_id=group.id, member_id=members[k]))
        s.commit()
    return migrated_db, license_id, scope, members, uids


def _rule(scope_name: str, team: str, strategy: str = "round_robin", cap: int | None = None) -> dict:
    rule = {
        "version": 1, "scope": scope_name,
        "match_criteria": [{"field": "customer.stage" if scope_name == "sales" else "product.category",
                            "operator": "not_equals", "value": "", "assign_to_team": team}],
        "selection_strategy": strategy,
    }
    if cap:
        rule["capacity_constraint"] = {"max_per_day": cap, "mode": "hard_block"}
    return rule


class TestASalesRuleFindsSalespeople:
    def test_candidates_come_from_the_sales_group_and_the_pick_is_persisted(self, shop, api):
        engine, license_id, scope, members, uids = shop
        client, headers = api
        with Session(engine) as s:
            AssignmentRuleRepository(s).upsert_active(scope, rule_scope="sales", rules_json=_rule("sales", "ขาย A"))
            c = CustomerRepository(s).create(scope, first_name="ลูกค้า", last_name="ใหม่", phone="0811111111")
            s.commit()
            customer_id = c.id
        r = client.post(
            f"/internal/v1/licenses/{license_id}/assignment-rules/execute", headers=headers,
            json={"scope": "sales", "entity_type": "customer", "entity_id": str(customer_id),
                  "context": {"customer": {"stage": "lead", "source": "line"}}},
        )
        assert r.status_code == 200, r.text
        picked = r.json()["member_id"]
        assert picked in {str(members["s1"]), str(members["s2"])}, r.json()
        with Session(engine) as s:
            row = s.get(Customer, customer_id)
            assert str(row.owner_member_id) == picked
            stamped = s.get(LicenseMember, uuid.UUID(picked))
            assert stamped.last_assigned_at is not None

    def test_round_robin_takes_turns(self, shop, api):
        engine, license_id, scope, members, uids = shop
        client, headers = api
        with Session(engine) as s:
            AssignmentRuleRepository(s).upsert_active(scope, rule_scope="sales", rules_json=_rule("sales", "ขาย A"))
            ids = [CustomerRepository(s).create(scope, first_name=f"ลูกค้า{i}", last_name="ใหม่", phone=f"08122222{i:02d}").id for i in range(4)]
            s.commit()
        picks = []
        for cid in ids:
            r = client.post(
                f"/internal/v1/licenses/{license_id}/assignment-rules/execute", headers=headers,
                json={"scope": "sales", "entity_type": "customer", "entity_id": str(cid),
                      "context": {"customer": {"stage": "lead", "source": "csv"}}},
            )
            picks.append(r.json()["member_id"])
        # Before 0029 every pick was the lowest member id.
        assert picks[0] != picks[1] and picks[2] != picks[3], picks
        assert set(picks) == {str(members["s1"]), str(members["s2"])}


class TestATechnicianRuleInsideAChosenTeam:
    def test_team_name_forces_the_pool_and_tickets_count_as_load(self, shop, api):
        engine, license_id, scope, members, uids = shop
        client, headers = api
        with Session(engine) as s:
            AssignmentRuleRepository(s).upsert_active(
                scope, rule_scope="technician", rules_json=_rule("technician", "ทีมแอร์", strategy="least_load", cap=1),
            )
            c = CustomerRepository(s).create(scope, first_name="ลูกค้า", last_name="ซ่อม", phone="0813333333")
            s.flush()
            tickets = []
            for i in range(3):
                t = ServiceTicket(license_id=license_id, ticket_number=f"T-2026-9{i:03d}", contact_id=c.id,
                                  issue_description="แอร์ไม่เย็น", status="open")
                s.add(t); s.flush(); tickets.append(t.id)
            s.commit()
        picks = []
        for tid in tickets:
            r = client.post(
                f"/internal/v1/licenses/{license_id}/assignment-rules/execute", headers=headers,
                json={"scope": "technician", "entity_type": "service_ticket", "entity_id": str(tid),
                      "context": {"product": {"category": "AC"}}, "team_name": "ทีมแอร์"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            picks.append(body)
            # The engine does not dispatch tickets itself; mimic the caller so
            # the next pick sees today's load.
            if body["member_id"]:
                with Session(engine) as s:
                    t = s.get(ServiceTicket, tid)
                    t.status = "assigned"; t.assigned_target_type = "technician"
                    t.assigned_to_ref = uuid.UUID(body["member_id"]); t.scheduled_date = date.today()
                    s.commit()
        chosen = [p["member_id"] for p in picks]
        assert chosen[0] in {str(members["t1"]), str(members["t2"])}
        assert chosen[1] != chosen[0], "least_load with a cap of 1 must move to the other technician"
        # Third job: both at their cap — hard_block falls back rather than
        # exceeding it, and says so.
        assert picks[2]["used_fallback"] or picks[2]["warnings"], picks[2]

    def test_no_match_falls_back_to_the_same_channel_only(self, shop, api):
        engine, license_id, scope, members, uids = shop
        client, headers = api
        with Session(engine) as s:
            rule = _rule("technician", "ทีมแอร์")
            rule["match_criteria"][0] = {"field": "product.category", "operator": "equals", "value": "FRIDGE", "assign_to_team": "ทีมแอร์"}
            AssignmentRuleRepository(s).upsert_active(scope, rule_scope="technician", rules_json=rule)
            s.commit()
        seen = set()
        for _ in range(4):
            r = client.post(
                f"/internal/v1/licenses/{license_id}/assignment-rules/execute", headers=headers,
                json={"scope": "technician", "entity_type": "service_ticket", "entity_id": str(uuid.uuid4()),
                      "context": {"product": {"category": "AC"}}},
            )
            assert r.status_code == 200, r.text
            seen.add(r.json()["member_id"])
        assert seen <= {str(members["t1"]), str(members["t2"])}, "a salesperson was offered a repair job"
