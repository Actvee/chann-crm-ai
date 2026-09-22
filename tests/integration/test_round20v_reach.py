"""Round 20V — the two new Data-tier questions, asked of a real PostgreSQL.

The survey summary is a join across surveys, tickets, members and
identities with a time window; the rule deactivation must respect the
partial unique index that only one rule per scope is active. Neither is
provable against a fake.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase11 import AssignmentRuleRepository  # noqa: E402
from chann_data.repositories.phase12 import ServiceTicketRepository  # noqa: E402
from chann_data.repositories.phase14 import ApprovalRepository  # noqa: E402
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set — database integration is NOT_VERIFIED in this run",
)


def _make_tenant(migrated_db, suffix):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity, LicenseMember
    from chann_data.repositories.phase65 import RegistrationRepository

    with Session(migrated_db) as session:
        session.add(ChannIdentity(chann_uid=f"CHN-RV-{suffix}", line_user_id=f"line-rv-{suffix}", primary_role="sales"))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(company_name=f"Reach {suffix}", created_by_chann_uid=f"CHN-RV-{suffix}")
        session.commit()
        license_id = lic.id
    members = {}
    with Session(migrated_db) as session:
        for name, role in (("สมศักดิ์", "technician"), ("วิชัย", "technician")):
            identity = ChannIdentity(
                chann_uid=f"CHN-RT-{suffix}-{name}", line_user_id=f"line-rt-{suffix}-{name}",
                primary_role="technician", first_name=name, last_name="ช่าง",
            )
            session.add(identity)
            session.flush()
            member = LicenseMember(id=uuid.uuid4(), license_id=license_id, chann_uid=identity.chann_uid,
                                   role=role, status="active", channel="technician")
            session.add(member)
            session.flush()
            members[name] = member.id
        session.commit()
    return {"scope": TenantScope(license_id=license_id), "members": members, "session": lambda: Session(migrated_db)}


@pytest.fixture
def tenant(migrated_db):
    return _make_tenant(migrated_db, uuid.uuid4().hex[:6])


def _answered_survey(tenant, *, technician: str | None, score: int | None, when: datetime, comment: str | None = None):
    """A ticket assigned to `technician`, its survey opened and — when a
    score is given — answered at `when`."""
    from chann_data.models import SatisfactionSurvey

    with tenant["session"]() as session:
        repo = ServiceTicketRepository(session)
        ticket = repo.create(
            tenant["scope"], issue_description="แอร์ไม่เย็น", customer_name="จุใจ", customer_phone="0812345678",
            service_address="99/1", scheduled_date=date(2026, 9, 4), scheduled_time=time(14, 0),
        )
        session.flush()
        if technician:
            repo.assign(tenant["scope"], ticket.id, target_type="technician", target_ref=tenant["members"][technician])
        survey = ApprovalRepository(session).open_survey(tenant["scope"], ticket.id)
        session.flush()
        row = session.get(SatisfactionSurvey, survey.id)
        row.created_at = when
        if score is not None:
            row.score = score
            row.submitted_at = when
            row.comment = comment
        session.commit()
        return ticket.ticket_number


class TestSurveySummary:
    def test_counts_average_spread_per_technician_and_the_latest(self, tenant):
        now = datetime.now(timezone.utc)
        t1 = _answered_survey(tenant, technician="สมศักดิ์", score=3, when=now - timedelta(days=2), comment="ช่างมาเร็ว")
        _answered_survey(tenant, technician="สมศักดิ์", score=2, when=now - timedelta(days=10))
        _answered_survey(tenant, technician="วิชัย", score=1, when=now - timedelta(days=5))
        _answered_survey(tenant, technician=None, score=3, when=now - timedelta(days=1))
        _answered_survey(tenant, technician="วิชัย", score=None, when=now - timedelta(days=3))   # pending
        _answered_survey(tenant, technician="สมศักดิ์", score=3, when=now - timedelta(days=60))  # outside 30 days
        with tenant["session"]() as session:
            out = ApprovalRepository(session).survey_summary(tenant["scope"], days=30)
        assert out["answered"] == 4 and out["pending"] == 1
        assert out["average"] == 2.25
        assert out["distribution"] == {"1": 1, "2": 1, "3": 2}
        assert out["response_rate"] == 0.8
        by_name = {t["display_name"]: t for t in out["technicians"]}
        assert by_name["สมศักดิ์ ช่าง"]["answered"] == 2 and by_name["สมศักดิ์ ช่าง"]["average"] == 2.5
        assert by_name["วิชัย ช่าง"]["answered"] == 1 and by_name["วิชัย ช่าง"]["average"] == 1.0
        assert out["technicians"][0]["display_name"] == "สมศักดิ์ ช่าง"  # best first
        # Newest answer first: the unassigned job (1 day ago) precedes t1
        # (2 days ago), and an unassigned job names no technician.
        assert [r["technician_name"] for r in out["recent"][:2]] == [None, "สมศักดิ์ ช่าง"]
        mine = next(r for r in out["recent"] if r["ticket_number"] == t1)
        assert mine["score"] == 3 and mine["comment"] == "ช่างมาเร็ว" and mine["score_label"] == "ดีเยี่ยม"
        assert all(r["score"] is not None for r in out["recent"])  # pending rows never appear here

    def test_a_wider_window_takes_the_older_answer_in(self, tenant):
        now = datetime.now(timezone.utc)
        _answered_survey(tenant, technician="สมศักดิ์", score=3, when=now - timedelta(days=60))
        with tenant["session"]() as session:
            repo = ApprovalRepository(session)
            assert repo.survey_summary(tenant["scope"], days=30)["answered"] == 0
            assert repo.survey_summary(tenant["scope"], days=90)["answered"] == 1

    def test_another_tenants_answers_are_invisible(self, tenant, migrated_db):
        other = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        _answered_survey(other, technician="สมศักดิ์", score=1, when=datetime.now(timezone.utc))
        with tenant["session"]() as session:
            out = ApprovalRepository(session).survey_summary(tenant["scope"], days=365)
        assert out["answered"] == 0 and out["technicians"] == [] and out["recent"] == []


class TestRuleDeactivation:
    def test_switching_off_keeps_the_row_and_a_new_rule_can_follow(self, tenant):
        rule = {"version": 1, "scope": "technician", "match_criteria": [], "selection_strategy": "round_robin"}
        with tenant["session"]() as session:
            repo = AssignmentRuleRepository(session)
            first = repo.upsert_active(tenant["scope"], rule_scope="technician", rules_json=rule)
            session.commit()
            first_id = first.id
        with tenant["session"]() as session:
            repo = AssignmentRuleRepository(session)
            off = repo.deactivate_active(tenant["scope"], rule_scope="technician")
            session.commit()
            assert off is not None and off.id == first_id and off.is_active is False
        with tenant["session"]() as session:
            repo = AssignmentRuleRepository(session)
            assert repo.get_active(tenant["scope"], rule_scope="technician") is None
            assert repo.deactivate_active(tenant["scope"], rule_scope="technician") is None
            # The history keeps the old rule; a fresh one activates cleanly
            # under the partial unique index.
            rows = repo.list_for_license(tenant["scope"])
            assert [r.is_active for r in rows] == [False]
            again = repo.upsert_active(tenant["scope"], rule_scope="technician", rules_json=rule)
            session.commit()
            assert again.is_active and again.id != first_id

    def test_only_the_named_scope_is_switched_off(self, tenant):
        with tenant["session"]() as session:
            repo = AssignmentRuleRepository(session)
            repo.upsert_active(tenant["scope"], rule_scope="technician", rules_json={"version": 1, "scope": "technician", "match_criteria": [], "selection_strategy": "round_robin"})
            repo.upsert_active(tenant["scope"], rule_scope="sales", rules_json={"version": 1, "scope": "sales", "match_criteria": [], "selection_strategy": "round_robin"})
            session.commit()
            repo.deactivate_active(tenant["scope"], rule_scope="sales")
            session.commit()
            assert repo.get_active(tenant["scope"], rule_scope="sales") is None
            assert repo.get_active(tenant["scope"], rule_scope="technician") is not None
