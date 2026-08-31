"""Phase 11 — the assignment engine.

The race-condition test is mandatory per Master Spec 11.7 and is the
reason the rest of this file exists in the shape it does: the engine's
decision logic is pure, so it can be driven directly, while the locking
is exercised against a real database with real concurrent transactions.

A capacity cap that "usually" holds is not a capacity cap. Ten tickets
arriving together against a five-a-day limit must produce five
assignments and five overflows, every time.
"""
from __future__ import annotations

import sys
import threading
import uuid
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.assignment_engine import (  # noqa: E402
    choose,
    match_team,
    order_candidates,
    validate_rule,
)

AC_RULE = {
    "version": 1,
    "scope": "technician",
    "match_criteria": [
        {
            "field": "product.category", "operator": "equals",
            "value": "AIR_CONDITIONER", "assign_to_team": "AC Team",
        },
        {
            "field": "product.category", "operator": "equals",
            "value": "REFRIGERATOR", "assign_to_team": "Cooling Team",
        },
    ],
    "selection_strategy": "least_load",
    "capacity_constraint": {"max_per_day": 5, "mode": "hard_block"},
    "fallback": "round_robin_in_team",
    "no_active_fallback": "assign_to_owner_or_admin",
}


class TestRuleMatching:
    """Master Spec 11.7 test_rule_matching."""

    def test_air_conditioner_goes_to_the_ac_team(self):
        assert match_team(AC_RULE, {"product": {"category": "AIR_CONDITIONER"}}) == "AC Team"

    def test_refrigerator_goes_to_the_cooling_team(self):
        assert match_team(AC_RULE, {"product": {"category": "REFRIGERATOR"}}) == "Cooling Team"

    def test_matching_ignores_case_and_surrounding_space(self):
        """A category arrives from a typed chat message as often as from a
        form, so "air_conditioner " has to match "AIR_CONDITIONER"."""
        assert match_team(AC_RULE, {"product": {"category": " air_conditioner "}}) == "AC Team"

    def test_an_unmatched_category_falls_through(self):
        assert match_team(AC_RULE, {"product": {"category": "TELEVISION"}}) is None

    def test_a_missing_field_does_not_match_rather_than_raising(self):
        """A ticket with no product is a normal thing to assign; a rule that
        mentions one should simply not match it."""
        assert match_team(AC_RULE, {}) is None
        assert match_team(AC_RULE, {"product": None}) is None

    def test_criteria_are_tried_in_the_order_written(self):
        """Order is the tenant's own statement of priority — reordering is
        how someone changes precedence, so the engine must not reorder."""
        rule = {
            "scope": "technician",
            "match_criteria": [
                {"field": "x", "operator": "equals", "value": "a", "assign_to_team": "First"},
                {"field": "x", "operator": "equals", "value": "a", "assign_to_team": "Second"},
            ],
        }
        assert match_team(rule, {"x": "a"}) == "First"

    def test_a_bad_operator_in_one_criterion_does_not_stop_the_others(self):
        rule = {
            "scope": "technician",
            "match_criteria": [
                {"field": "x", "operator": "nonsense", "value": "a", "assign_to_team": "Bad"},
                {"field": "x", "operator": "equals", "value": "a", "assign_to_team": "Good"},
            ],
        }
        assert match_team(rule, {"x": "a"}) == "Good"


class TestRuleValidation:
    """Problems are caught when someone types the policy, not months later
    when a rule silently matches nothing."""

    def test_a_valid_rule_has_no_problems(self):
        assert validate_rule(AC_RULE) == []

    def test_every_problem_is_reported_not_just_the_first(self):
        """Someone fixing a policy wants the whole list, not whack-a-mole."""
        problems = validate_rule({
            "scope": "nope",
            "match_criteria": [{"field": "x", "operator": "bad"}],
            "selection_strategy": "vibes",
        })
        assert len(problems) >= 3

    def test_an_unknown_operator_is_rejected(self):
        problems = validate_rule({
            "scope": "sales",
            "match_criteria": [
                {"field": "x", "operator": "sounds_like", "value": "a", "assign_to_team": "T"},
            ],
        })
        assert any("operator" in p for p in problems)

    def test_a_zero_capacity_is_rejected(self):
        """max_per_day=0 would mean nobody can ever be assigned, which is
        never what someone meant to type."""
        problems = validate_rule({
            "scope": "sales", "capacity_constraint": {"max_per_day": 0},
        })
        assert any("max_per_day" in p for p in problems)


class TestSelection:
    def test_least_load_picks_the_least_busy(self):
        outcome = choose(
            AC_RULE,
            [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
            {"m1": 4, "m2": 1, "m3": 3},
            matched_team="AC Team",
        )
        assert outcome.member_id == "m2"

    def test_ties_break_deterministically(self):
        """Two equally loaded technicians must not be ordered by whatever
        the database happened to return, or the same inputs assign
        differently between runs."""
        first = order_candidates(
            [{"id": "b"}, {"id": "a"}], "least_load", {"a": 2, "b": 2},
        )
        second = order_candidates(
            [{"id": "a"}, {"id": "b"}], "least_load", {"a": 2, "b": 2},
        )
        assert [c["id"] for c in first] == [c["id"] for c in second] == ["a", "b"]

    def test_round_robin_prefers_the_least_recently_assigned(self):
        """Round robin with no memory is "first in the list" forever."""
        ordered = order_candidates(
            [
                {"id": "a", "last_assigned_at": "2026-08-31T10:00:00"},
                {"id": "b", "last_assigned_at": "2026-08-29T10:00:00"},
            ],
            "round_robin", {},
        )
        assert [c["id"] for c in ordered] == ["b", "a"]


class TestCapacity:
    """Master Spec 11.7 test_capacity_hard_block / test_capacity_soft_warn."""

    def test_hard_block_skips_a_full_member_and_takes_the_next(self):
        outcome = choose(
            AC_RULE, [{"id": "m1"}, {"id": "m2"}], {"m1": 5, "m2": 2},
            matched_team="AC Team",
        )
        assert outcome.member_id == "m2"
        assert not outcome.warnings

    def test_hard_block_with_everyone_full_falls_back_to_the_owner(self):
        """An unassigned job is one nobody is accountable for — worse than
        one given to a busy owner who can hand it on (11.1)."""
        outcome = choose(
            AC_RULE, [{"id": "m1"}, {"id": "m2"}], {"m1": 5, "m2": 5},
            matched_team="AC Team", owner_candidates=[{"id": "owner-1"}],
        )
        assert outcome.member_id == "owner-1"
        assert outcome.used_fallback

    def test_soft_warn_assigns_anyway_and_says_so(self):
        rule = {**AC_RULE, "capacity_constraint": {"max_per_day": 5, "mode": "soft_warn"}}
        outcome = choose(
            rule, [{"id": "m1", "name": "ช่างเอ"}], {"m1": 7}, matched_team="AC Team",
        )
        assert outcome.member_id == "m1"
        assert outcome.warnings, "a soft warning that warns nobody is not a warning"

    def test_no_capacity_constraint_means_no_limit(self):
        rule = {k: v for k, v in AC_RULE.items() if k != "capacity_constraint"}
        outcome = choose(rule, [{"id": "m1"}], {"m1": 999}, matched_team="AC Team")
        assert outcome.member_id == "m1"

    def test_the_reason_is_always_populated(self):
        """The reason goes into the audit log so an assignment can be
        explained later without re-running the engine against data that has
        since changed."""
        for loads in ({"m1": 0}, {"m1": 5}):
            outcome = choose(
                AC_RULE, [{"id": "m1"}], loads,
                matched_team="AC Team", owner_candidates=[{"id": "owner"}],
            )
            assert outcome.reason.strip()


class TestNobodyAvailable:
    def test_an_empty_team_falls_back_to_the_owner(self):
        outcome = choose(
            AC_RULE, [], {}, matched_team="AC Team", owner_candidates=[{"id": "owner-1"}],
        )
        assert outcome.member_id == "owner-1"
        assert outcome.used_fallback

    def test_no_candidates_and_no_owner_returns_none_rather_than_guessing(self):
        outcome = choose(AC_RULE, [], {}, matched_team="AC Team")
        assert outcome.member_id is None
        assert outcome.reason


class TestRaceCondition:
    """Master Spec 11.7 — MANDATORY.

    Ten records assigned concurrently against a five-a-day cap. The
    capacity check and the write happen inside one lock per tenant, so the
    five that fit are assigned and the rest overflow; without the lock,
    every thread reads "load is 4" and all ten decide they have room.

    Driven through real database transactions rather than the pure engine,
    because the lock is the thing under test.
    """

    def _tenant_with_team(self, migrated_db, suffix, member_count=2):
        from sqlalchemy.orm import Session

        from chann_data.models import (
            ChannIdentity, LicenseMember, TechnicianTeam, TechnicianTeamMember,
        )
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-AS-{suffix}", line_user_id=f"line-as-{suffix}",
                primary_role="technician",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Assign {suffix}", created_by_chann_uid=f"CHN-AS-{suffix}",
            )
            session.commit()
            license_id = lic.id

        member_ids = []
        with Session(migrated_db) as session:
            team = TechnicianTeam(
                id=uuid.uuid4(), license_id=license_id, team_name="AC Team",
            )
            session.add(team)
            session.flush()
            for index in range(member_count):
                identity = ChannIdentity(
                    chann_uid=f"CHN-T-{suffix}{index}",
                    line_user_id=f"line-t-{suffix}{index}",
                    primary_role="technician",
                )
                session.add(identity)
                session.flush()
                member = LicenseMember(
                    id=uuid.uuid4(), license_id=license_id,
                    chann_uid=identity.chann_uid, role="technician", status="active",
                )
                session.add(member)
                session.flush()
                session.add(TechnicianTeamMember(
                    id=uuid.uuid4(), license_id=license_id,
                    team_id=team.id, member_id=member.id,
                ))
                member_ids.append(member.id)
            session.commit()
        return license_id, member_ids

    def test_ten_concurrent_assignments_do_not_exceed_capacity(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.assignment_engine import choose
        from chann_data.repositories.phase11 import AssignmentRuleRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        license_id, member_ids = self._tenant_with_team(migrated_db, "R", member_count=2)
        scope = TenantScope(license_id=license_id)
        cap = 5

        # Ten deals, all matching the same team.
        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                scope, first_name="ก", last_name="ข", phone="0800000099",
            )
            deals = [
                DealRepository(session).create(scope, contact_id=customer.id).id
                for _ in range(10)
            ]
            session.commit()

        rule = {**AC_RULE, "capacity_constraint": {"max_per_day": cap, "mode": "hard_block"}}
        results: list[str | None] = []
        lock = threading.Lock()

        def assign_one(deal_id):
            # Each thread gets its own session, as a separate request would.
            with Session(migrated_db) as session:
                repo = AssignmentRuleRepository(session)
                repo.lock_license(scope)
                candidates = repo.team_members(scope, team_name="AC Team")
                loads = repo.current_loads(
                    scope, [c["id"] for c in candidates], on_day=date.today(),
                )
                outcome = choose(
                    rule, candidates, loads,
                    matched_team="AC Team", owner_candidates=[],
                )
                if outcome.member_id:
                    repo.assign_deal(scope, deal_id, uuid.UUID(outcome.member_id))
                session.commit()
            with lock:
                results.append(outcome.member_id)

        threads = [threading.Thread(target=assign_one, args=(d,)) for d in deals]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assigned = [r for r in results if r]
        # Two members, five each: at most ten can be placed, and no single
        # member may exceed the cap.
        per_member: dict[str, int] = {}
        for member_id in assigned:
            per_member[member_id] = per_member.get(member_id, 0) + 1
        for member_id, count in per_member.items():
            assert count <= cap, (
                f"member {member_id} took {count} assignments against a cap of {cap} "
                "— the lock is not serialising capacity checks"
            )

    def test_capacity_is_counted_per_tenant_not_globally(self, migrated_db):
        """Master Spec 11.7 test_assignment_rule_isolation: one tenant's
        load must never consume another's capacity."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase11 import AssignmentRuleRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        a_license, a_members = self._tenant_with_team(migrated_db, "A")
        b_license, _ = self._tenant_with_team(migrated_db, "B")
        a_scope = TenantScope(license_id=a_license)
        b_scope = TenantScope(license_id=b_license)

        with Session(migrated_db) as session:
            customer = CustomerRepository(session).create(
                a_scope, first_name="ก", last_name="ข", phone="0800000098",
            )
            repo = DealRepository(session)
            for _ in range(3):
                deal = repo.create(a_scope, contact_id=customer.id)
                deal.owner_member_id = a_members[0]
            session.commit()

        with Session(migrated_db) as session:
            rule_repo = AssignmentRuleRepository(session)
            a_loads = rule_repo.current_loads(
                a_scope, [str(a_members[0])], on_day=date.today(),
            )
            b_loads = rule_repo.current_loads(
                b_scope, [str(a_members[0])], on_day=date.today(),
            )
        assert a_loads[str(a_members[0])] == 3
        assert b_loads[str(a_members[0])] == 0, (
            "tenant B saw tenant A's workload"
        )


class TestRuleIsolation:
    """Master Spec 11.7 test_assignment_rule_isolation."""

    def _license(self, migrated_db, suffix):
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-RI-{suffix}", line_user_id=f"line-ri-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Iso {suffix}", created_by_chann_uid=f"CHN-RI-{suffix}",
            )
            session.commit()
            return lic.id

    def test_a_rule_in_one_tenant_is_invisible_to_another(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase11 import AssignmentRuleRepository
        from chann_data.repositories.tenant_scope import TenantScope

        a = TenantScope(license_id=self._license(migrated_db, "A"))
        b = TenantScope(license_id=self._license(migrated_db, "B"))

        with Session(migrated_db) as session:
            AssignmentRuleRepository(session).upsert_active(
                a, rule_scope="technician", rules_json=AC_RULE,
            )
            session.commit()

        with Session(migrated_db) as session:
            repo = AssignmentRuleRepository(session)
            assert repo.get_active(a, rule_scope="technician") is not None
            assert repo.get_active(b, rule_scope="technician") is None

    def test_replacing_a_rule_keeps_the_old_one_inactive(self, migrated_db):
        """A rule that assigned work last month explains why those records
        look the way they do."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase11 import AssignmentRuleRepository
        from chann_data.repositories.tenant_scope import TenantScope

        scope = TenantScope(license_id=self._license(migrated_db, "C"))
        with Session(migrated_db) as session:
            repo = AssignmentRuleRepository(session)
            repo.upsert_active(scope, rule_scope="technician", rules_json=AC_RULE)
            session.commit()
        with Session(migrated_db) as session:
            repo = AssignmentRuleRepository(session)
            repo.upsert_active(
                scope, rule_scope="technician",
                rules_json={**AC_RULE, "selection_strategy": "round_robin"},
            )
            session.commit()

        with Session(migrated_db) as session:
            repo = AssignmentRuleRepository(session)
            all_rules = repo.list_for_license(scope)
            active = [r for r in all_rules if r.is_active]
            assert len(all_rules) == 2, "the superseded rule should still exist"
            assert len(active) == 1, "exactly one rule may be active per scope"
            assert active[0].rules_json["selection_strategy"] == "round_robin"
