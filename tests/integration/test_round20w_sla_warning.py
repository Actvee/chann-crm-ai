"""Round 20W — the overdue warning is claimed, not stamped, on a real DB.

The sweep hands back overdue rows unmarked; the caller that will do the
telling claims one (atomic — a second claim gets False), releases it if
the telling fails, and a parked conversation is never handed back.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402


def _customer(session):
    from chann_data.models import ChannIdentity

    row = ChannIdentity(
        chann_uid=f"CHN-C-{uuid.uuid4().hex[:8]}", line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="customer", display_name="สมชาย",
    )
    session.add(row)
    session.flush()
    return row


class TestTheWarningIsClaimed:
    def test_overdue_rows_come_back_until_claimed_and_never_after_parking(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            customer = _customer(session)
            repo = ChatSessionRepository(session)
            chat, _ = repo.open_session(scope, customer_chann_uid=customer.chann_uid)
            chat.sla_deadline = datetime.now(timezone.utc) - timedelta(minutes=5)
            session.flush()

            # Unmarked: the sweep can say it twice.
            assert chat.id in [r.id for r in repo.sla_overdue()]
            assert chat.id in [r.id for r in repo.sla_overdue()]
            assert repo.get(scope, chat.id).escalated_at is None

            # Claimed once; the second caller learns it is taken.
            assert repo.claim_escalation(scope, chat.id) is True
            assert repo.claim_escalation(scope, chat.id) is False
            assert chat.id not in [r.id for r in repo.sla_overdue()]

            # Released: back on the list for the next sweep.
            assert repo.release_escalation(scope, chat.id) is True
            assert repo.release_escalation(scope, chat.id) is False
            assert chat.id in [r.id for r in repo.sla_overdue()]

            # Claimed and parked: gone for good, and cannot be released.
            assert repo.claim_escalation(scope, chat.id) is True
            repo.close(scope, chat.id, status="unanswered")
            assert chat.id not in [r.id for r in repo.sla_overdue()]
            assert repo.release_escalation(scope, chat.id) is False
            session.rollback()

    def test_the_claim_is_tenant_scoped(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            other, _o2, _m2 = _phase2_tenant(session)
            repo = ChatSessionRepository(session)
            chat, _ = repo.open_session(TenantScope(lic.id), customer_chann_uid=_customer(session).chann_uid)
            chat.sla_deadline = datetime.now(timezone.utc) - timedelta(minutes=5)
            session.flush()
            assert repo.claim_escalation(TenantScope(other.id), chat.id) is False
            assert repo.claim_escalation(TenantScope(lic.id), chat.id) is True
            session.rollback()
