"""Round 20R — a customer record with no name takes the name its person
registered.

Owner, 21 ก.ย. 2569: CHN-S-000002 of "dev company one" showed as a bare
id on the shop's chat page although the customer had typed their name
into their profile. The record had been attached by phone (a staff row
with a number and nothing else) and nothing ever carried the name
across. Three roads now do, and one migration does it for the rows
already there.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402


def _identity(session, *, first=None, last=None, display_name="LINE ชื่อ"):
    from chann_data.models import ChannIdentity

    row = ChannIdentity(
        chann_uid=f"CHN-C-{uuid.uuid4().hex[:8]}",
        line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="customer",
        display_name=display_name,
        first_name=first,
        last_name=last,
    )
    session.add(row)
    session.flush()
    return row


class TestTheNameReachesTheRecord:
    def test_a_phone_only_row_takes_the_name_when_the_person_links(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            person = _identity(session, first="สมชาย", last="ใจดี")
            repo = CustomerRepository(session)
            bare = repo.create(scope, phone="0812345678")  # a missed call, typed by staff
            assert not bare.first_name

            linked = repo.link_identity_by_phone(scope, phone="0812345678", customer_chann_uid=person.chann_uid)
            assert linked is not None and linked.id == bare.id
            assert (linked.first_name, linked.last_name) == ("สมชาย", "ใจดี")
            session.rollback()

    def test_a_name_the_shop_typed_is_kept(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            person = _identity(session, first="สมชาย", last="ใจดี")
            repo = CustomerRepository(session)
            repo.create(scope, first_name="คุณลูกค้า", phone="0812345678")
            linked = repo.link_identity_by_phone(scope, phone="0812345678", customer_chann_uid=person.chann_uid)
            assert linked.first_name == "คุณลูกค้า" and linked.last_name is None
            session.rollback()

    def test_a_name_registered_later_reaches_every_nameless_row(self, migrated_db):
        """The person linked first and typed their name afterwards — the
        order the owner suspected ("ลูกค้าบันทึกชื่อทีหลัง")."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.profile import ProfileRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic_a, _o, _m = _phase2_tenant(session)
            lic_b, _o2, _m2 = _phase2_tenant(session)
            person = _identity(session)  # no name yet
            repo = CustomerRepository(session)
            a = repo.create(TenantScope(lic_a.id), phone="0811111111", customer_chann_uid=person.chann_uid)
            b = repo.create(TenantScope(lic_b.id), first_name="ชื่อที่ร้านพิมพ์", phone="0811111111", customer_chann_uid=person.chann_uid)
            assert not a.first_name

            ProfileRepository(session).update_profile(person.chann_uid, {"first_name": "สมหญิง", "last_name": "รักดี"})
            session.flush()
            session.refresh(a); session.refresh(b)
            assert (a.first_name, a.last_name) == ("สมหญิง", "รักดี")
            assert b.first_name == "ชื่อที่ร้านพิมพ์", "the shop's own name is never overwritten"
            session.rollback()

    def test_the_chat_page_prefers_the_registered_name_to_the_line_name(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.tenant_scope import TenantScope
        from chann_data.routers.internal import _chat_sessions_out

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            person = _identity(session, first="สมศรี", last="มีสุข", display_name="🐱 nickname")
            # No customer record at all: the registered name still beats the LINE one.
            chat = ChatSessionRepository(session).open_session(scope, customer_chann_uid=person.chann_uid)[0]
            session.flush()
            (out,) = _chat_sessions_out(session, scope, [chat])
            assert out.customer_name == "สมศรี มีสุข"
            assert out.customer_record_id is None
            session.rollback()


class TestTheMigrationFillsTheRowsAlreadyThere:
    def test_the_backfill_statement_on_a_real_database(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        path = ROOT / "database" / "alembic" / "versions" / "0033_names_from_identity.py"
        spec = importlib.util.spec_from_file_location("mig_0033", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            named = _identity(session, first="สมชาย", last="ใจดี")
            nameless_person = _identity(session)
            repo = CustomerRepository(session)
            # Written straight to the row, bypassing the link road — the
            # state the existing rows are in.
            fill_me = repo.create(scope, phone="0810000001")
            fill_me.customer_chann_uid = named.chann_uid
            keep_me = repo.create(scope, first_name="ร้านพิมพ์เอง", phone="0810000002")
            keep_me.customer_chann_uid = named.chann_uid if False else None
            nothing_to_give = repo.create(scope, phone="0810000003")
            nothing_to_give.customer_chann_uid = nameless_person.chann_uid
            session.flush()

            session.execute(text(module.BACKFILL_SQL))
            session.flush()
            for row in (fill_me, keep_me, nothing_to_give):
                session.refresh(row)
            assert (fill_me.first_name, fill_me.last_name) == ("สมชาย", "ใจดี")
            assert keep_me.first_name == "ร้านพิมพ์เอง"
            assert nothing_to_give.first_name is None
            session.rollback()
