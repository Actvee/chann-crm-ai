"""Phase 15 data tier against a real Postgres: one live conversation per
(shop, customer), the SLA clock that runs only while the customer
waits, tenant isolation (15.5 test_multi_tenant_chat), and the sweeps.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity
from chann_data.repositories.phase15 import ChatSessionConflict, ChatSessionRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def two_shops(migrated_db):
    tag = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        for suffix, role in (("A", "sales"), ("B", "sales"), ("CUST", "customer")):
            session.add(ChannIdentity(
                chann_uid=f"CHN-P15{tag}-{suffix}", line_user_id=f"line-p15{tag}-{suffix}",
                primary_role=role, display_name="สมชาย" if suffix == "CUST" else None,
            ))
        session.commit()
    with Session(migrated_db) as session:
        reg = RegistrationRepository(session)
        lic_a = reg.create_license(company_name=f"Shop A {tag}", created_by_chann_uid=f"CHN-P15{tag}-A")
        lic_b = reg.create_license(company_name=f"Shop B {tag}", created_by_chann_uid=f"CHN-P15{tag}-B")
        ids = (lic_a.id, lic_b.id)
        session.commit()
    return migrated_db, TenantScope(license_id=ids[0]), TenantScope(license_id=ids[1]), f"CHN-P15{tag}-CUST"


class TestConversations:
    def test_one_live_conversation_per_customer_and_shop(self, two_shops):
        engine, scope_a, scope_b, customer = two_shops
        with Session(engine) as session:
            repo = ChatSessionRepository(session)
            first, created = repo.open_session(scope_a, customer_chann_uid=customer)
            again, created_again = repo.open_session(scope_a, customer_chann_uid=customer)
            other, created_other = repo.open_session(scope_b, customer_chann_uid=customer)
            session.commit()
            assert created and not created_again and created_other
            assert again.id == first.id and other.id != first.id
            # 15.5 multi-tenant: shop A's list never shows shop B's conversation.
            assert [r.id for r in repo.list_for_license(scope_a, status="live")] == [first.id]
            assert [r.id for r in repo.list_for_license(scope_b, status="live")] == [other.id]
            assert repo.get(scope_b, first.id) is None

    def test_the_sla_clock_runs_only_while_the_customer_waits(self, two_shops):
        engine, scope_a, _, customer = two_shops
        with Session(engine) as session:
            repo = ChatSessionRepository(session)
            row, _ = repo.open_session(scope_a, customer_chann_uid=customer, sla_minutes=10)
            assert row.sla_deadline is not None
            repo.add_message(scope_a, row.id, sender_type="agent", content="สวัสดีครับ",
                             sender_chann_uid="CHN-AGENT")
            assert row.sla_deadline is None and row.status == "assigned"
            repo.add_message(scope_a, row.id, sender_type="customer", content="ราคา?",
                             sender_chann_uid=customer, sla_minutes=10)
            first_deadline = row.sla_deadline
            assert first_deadline is not None
            repo.add_message(scope_a, row.id, sender_type="customer", content="ยังอยู่ไหม",
                             sender_chann_uid=customer, sla_minutes=10)
            assert row.sla_deadline == first_deadline  # a second nudge does not push it back
            summary = repo.summaries(scope_a, [row.id])[row.id]
            assert summary["unread_from_customer"] == 2 and summary["last_message"] == "ยังอยู่ไหม"
            assert repo.mark_read(scope_a, row.id, reader="agent") == 2
            session.commit()

    def test_closed_conversations_refuse_lines_and_history_stays(self, two_shops):
        engine, scope_a, _, customer = two_shops
        with Session(engine) as session:
            repo = ChatSessionRepository(session)
            row, _ = repo.open_session(scope_a, customer_chann_uid=customer)
            repo.close(scope_a, row.id)
            assert row.status == "closed" and row.closed_at is not None
            with pytest.raises(ChatSessionConflict):
                repo.add_message(scope_a, row.id, sender_type="customer", content="x")
            fresh, created = repo.open_session(scope_a, customer_chann_uid=customer)
            assert created and fresh.id != row.id
            assert len(repo.list_for_license(scope_a, status="all" if False else None)) == 2
            session.commit()


class TestSweeps:
    def test_overdue_is_handed_over_once_and_dead_ones_time_out(self, two_shops):
        engine, scope_a, scope_b, customer = two_shops
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        with Session(engine) as session:
            repo = ChatSessionRepository(session)
            late, _ = repo.open_session(scope_a, customer_chann_uid=customer)
            late.sla_deadline = past
            dead, _ = repo.open_session(scope_b, customer_chann_uid=customer)
            dead.timeout_at = past
            dead.sla_deadline = None
            session.commit()

            overdue = [r.id for r in repo.sla_overdue()]
            assert late.id in overdue and dead.id not in overdue
            for row in repo.sla_overdue():
                repo.mark_escalated(row)
            session.commit()
            assert late.id not in [r.id for r in repo.sla_overdue()]

            timed_out = [r.id for r in repo.time_out()]
            session.commit()
            assert dead.id in timed_out and late.id not in timed_out
            assert repo.get(scope_b, dead.id).status == "timeout"
