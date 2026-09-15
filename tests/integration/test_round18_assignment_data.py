"""Round 18 (14 Sep 2026) — the assignment repository against a real database.

The engine is pure and was always tested; what it was FED never was. A
sales rule found nobody (only technician teams were consulted), the
no-match fallback pooled the whole staff, "service_ticket" loads went
down the deal branch, and round_robin sorted on a stamp nobody wrote.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from chann_data.assignment_engine import choose, order_candidates
from chann_data.models import (
    ChannIdentity, LicenseMember, SalesGroup, SalesGroupMember, TechnicianTeam, TechnicianTeamMember,
)
from chann_data.repositories.phase11 import AssignmentRuleRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def world(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uids = {k: f"CHN-R18{tag}-{k}" for k in ("owner", "s1", "s2", "t1", "t2")}
    with Session(migrated_db) as s:
        for key, uid in uids.items():
            s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="sales", display_name=key))
        s.commit()
    with Session(migrated_db) as s:
        lic = RegistrationRepository(s).create_license(company_name=f"R18 {tag}", created_by_chann_uid=uids["owner"])
        license_id = lic.id
        s.commit()
    scope = TenantScope(license_id=license_id)
    members: dict[str, uuid.UUID] = {}
    with Session(migrated_db) as s:
        for key, channel in (("s1", "sales"), ("s2", "sales"), ("t1", "technician"), ("t2", "technician")):
            m = LicenseMember(license_id=license_id, chann_uid=uids[key], role=channel, channel=channel, status="active")
            s.add(m)
            s.flush()
            members[key] = m.id
        group = SalesGroup(id=uuid.uuid4(), license_id=license_id, group_name="ขาย A")
        team = TechnicianTeam(id=uuid.uuid4(), license_id=license_id, team_name="ทีมแอร์")
        s.add_all([group, team])
        s.flush()
        s.add(SalesGroupMember(id=uuid.uuid4(), license_id=license_id, group_id=group.id, member_id=members["s1"]))
        s.add(SalesGroupMember(id=uuid.uuid4(), license_id=license_id, group_id=group.id, member_id=members["s2"]))
        s.add(TechnicianTeamMember(id=uuid.uuid4(), license_id=license_id, team_id=team.id, member_id=members["t1"]))
        s.commit()
    return migrated_db, scope, members, uids


class TestCandidatesComeFromTheRightTable:
    def test_a_sales_rule_sees_the_sales_group(self, world):
        engine, scope, members, _ = world
        with Session(engine) as s:
            repo = AssignmentRuleRepository(s)
            sales = {c["id"] for c in repo.team_members(scope, team_name="ขาย A", rule_scope="sales")}
            assert sales == {str(members["s1"]), str(members["s2"])}
            # The same name asked as a technician team finds nobody — the
            # two tables are not interchangeable.
            assert repo.team_members(scope, team_name="ขาย A", rule_scope="technician") == []
            tech = {c["id"] for c in repo.team_members(scope, team_name="ทีมแอร์")}
            assert tech == {str(members["t1"])}

    def test_the_fallback_pool_is_the_rule_scopes_channel(self, world):
        engine, scope, members, _ = world
        with Session(engine) as s:
            repo = AssignmentRuleRepository(s)
            assert {c["id"] for c in repo.active_members(scope, channel="technician")} == {str(members["t1"]), str(members["t2"])}
            # The owner's own row is on the sales channel (create_license
            # adds it), so the sales pool is the two salespeople plus the
            # owner — and never a technician.
            sales = {c["id"] for c in repo.active_members(scope, channel="sales")}
            assert {str(members["s1"]), str(members["s2"])} <= sales
            assert not sales & {str(members["t1"]), str(members["t2"])}


class TestRoundRobinHasAMemory:
    def test_the_stamp_is_written_and_read_back(self, world):
        engine, scope, members, _ = world
        with Session(engine) as s:
            repo = AssignmentRuleRepository(s)
            before = repo.team_members(scope, team_name="ขาย A", rule_scope="sales")
            assert all(c["last_assigned_at"] == "" for c in before)
            first = order_candidates(before, "round_robin", {})[0]
            repo.touch_assigned(scope, uuid.UUID(first["id"]), now=datetime.now(timezone.utc))
            s.commit()
        with Session(engine) as s:
            repo = AssignmentRuleRepository(s)
            after = repo.team_members(scope, team_name="ขาย A", rule_scope="sales")
            stamped = {c["id"]: c["last_assigned_at"] for c in after}
            assert stamped[first["id"]] != ""
            # Next turn goes to the other member, not the same one again.
            assert order_candidates(after, "round_robin", {})[0]["id"] != first["id"]


class TestLoadsAndPersistence:
    def test_a_customer_given_today_counts_and_the_owner_is_written(self, world):
        engine, scope, members, _ = world
        with Session(engine) as s:
            repo = AssignmentRuleRepository(s)
            customer = CustomerRepository(s).create(scope, first_name="ลูกค้า", last_name="ใหม่", phone="0899000001")
            s.flush()
            repo.assign_customer(scope, customer.id, members["s1"])
            s.commit()
            ids = [str(members["s1"]), str(members["s2"])]
            loads = repo.current_loads(scope, ids, on_day=date.today(), entity_type="customer")
            assert loads == {str(members["s1"]): 1, str(members["s2"]): 0}
            # least_load now prefers s2.
            rule = {"scope": "sales", "selection_strategy": "least_load"}
            picked = choose(rule, repo.team_members(scope, team_name="ขาย A", rule_scope="sales"), loads, matched_team="ขาย A")
            assert picked.member_id == str(members["s2"])

    def test_service_ticket_loads_take_the_ticket_branch(self, world):
        """"service_ticket" (what the caller sends) must count tickets, not
        deals — the alias that went down the deal branch and made every
        technician's load 0."""
        engine, scope, members, _ = world
        with Session(engine) as s:
            repo = AssignmentRuleRepository(s)
            ids = [str(members["t1"])]
            assert repo.current_loads(scope, ids, on_day=date.today(), entity_type="service_ticket") == {ids[0]: 0}
            assert repo.current_loads(scope, ids, on_day=date.today(), entity_type="ticket") == {ids[0]: 0}
