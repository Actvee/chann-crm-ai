"""Round 20Q — the customer beside the conversation, on a real database.

Owner, 20 ก.ย. 2569: the chat page showed "CHN-C-…" for a person the shop
had saved as สมชาย ใจดี, and offered no way to their deals, jobs or notes.
The name now comes from the shop's own record first, the conversation
carries that record's id and code, and a ticket list can be narrowed to
one customer's jobs — page and count alike.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402

def _identity(session, *, display_name: str | None):
    from chann_data.models import ChannIdentity

    row = ChannIdentity(
        chann_uid=f"CHN-C-{uuid.uuid4().hex[:8]}",
        line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="customer",
        display_name=display_name,
    )
    session.add(row)
    session.flush()
    return row


class TestTheConversationNamesTheRecord:
    def test_the_saved_name_wins_and_the_record_rides_along(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.tenant_scope import TenantScope
        from chann_data.routers.internal import _chat_sessions_out

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            saved = _identity(session, display_name="🐱 nickname")
            unsaved = _identity(session, display_name="LINE Somying")
            nameless = _identity(session, display_name=None)
            customer = CustomerRepository(session).create(
                scope, first_name="สมชาย", last_name="ใจดี", phone="0812345678",
                customer_chann_uid=saved.chann_uid,
            )
            repo = ChatSessionRepository(session)
            rows = [
                repo.open_session(scope, customer_chann_uid=saved.chann_uid)[0],
                repo.open_session(scope, customer_chann_uid=unsaved.chann_uid)[0],
                repo.open_session(scope, customer_chann_uid=nameless.chann_uid)[0],
            ]
            session.flush()
            out = {o.customer_chann_uid: o for o in _chat_sessions_out(session, scope, rows)}

            # The record's name, never the LINE nickname, once there is one.
            assert out[saved.chann_uid].customer_name == "สมชาย ใจดี"
            assert out[saved.chann_uid].customer_record_id == customer.id
            assert out[saved.chann_uid].customer_code == customer.customer_id
            # No record: the LINE name still helps; no record and no name:
            # nothing, and the page falls back to the id.
            assert out[unsaved.chann_uid].customer_name == "LINE Somying"
            assert out[unsaved.chann_uid].customer_record_id is None
            assert out[nameless.chann_uid].customer_name is None
            session.rollback()

    def test_an_archived_record_does_not_name_the_conversation(self, migrated_db):
        """Archived customers leave every list; a conversation must not
        keep pointing at a record the customer page will not open."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.tenant_scope import TenantScope
        from chann_data.routers.internal import _chat_sessions_out

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            identity = _identity(session, display_name="LINE ชื่อ")
            customers = CustomerRepository(session)
            row = customers.create(scope, first_name="สมหญิง", phone="0899999999", customer_chann_uid=identity.chann_uid)
            customers.archive(scope, row.id)
            chat = ChatSessionRepository(session).open_session(scope, customer_chann_uid=identity.chann_uid)[0]
            session.flush()
            (out,) = _chat_sessions_out(session, scope, [chat])
            assert out.customer_record_id is None
            assert out.customer_name == "LINE ชื่อ"
            session.rollback()


class TestOneCustomersJobs:
    def test_the_page_and_the_count_are_narrowed_alike(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.phase12 import ServiceTicketRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            customers = CustomerRepository(session)
            a = customers.create(scope, first_name="ก", phone="0810000001")
            b = customers.create(scope, first_name="ข", phone="0810000002")
            tickets = ServiceTicketRepository(session)
            for i in range(3):
                tickets.create(scope, issue_description=f"แอร์ไม่เย็น {i}", contact_id=a.id, customer_name="ก")
            tickets.create(scope, issue_description="พัดลมไม่หมุน", contact_id=b.id, customer_name="ข")
            tickets.create(scope, issue_description="ไม่รู้ของใคร")
            session.flush()

            mine = tickets.list_for_license(scope, contact_id=a.id)
            assert {t.issue_description for t in mine} == {"แอร์ไม่เย็น 0", "แอร์ไม่เย็น 1", "แอร์ไม่เย็น 2"}
            assert tickets.count_for_license(scope, contact_id=a.id) == 3
            assert tickets.count_for_license(scope, contact_id=b.id) == 1
            # Without the filter the shop's whole queue is still the answer.
            assert tickets.count_for_license(scope) == 5
            session.rollback()
