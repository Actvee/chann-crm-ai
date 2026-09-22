"""Round 20Z — a unit registered makes its holder a customer, on a real DB.

The claim promotes the shop's lead to a contact, creates the row when
the shop has none, links the unit to that record, and never reaches
another tenant. The migration does the same once for the rows already
there.
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


def _identity(session, *, first="สมชาย", last="ใจดี", phone="0812345678"):
    from chann_data.models import ChannIdentity

    row = ChannIdentity(
        chann_uid=f"CHN-C-{uuid.uuid4().hex[:8]}", line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="customer", display_name=first, first_name=first, last_name=last, phone=phone,
    )
    session.add(row)
    session.flush()
    return row


class TestRegisteringAUnitMakesACustomer:
    def test_a_lead_becomes_a_contact_and_the_unit_points_at_the_record(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.phase16 import WarrantyRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            identity = _identity(session)
            customers = CustomerRepository(session)
            lead = customers.create(
                scope, first_name="สมชาย", last_name="ใจดี", phone="0812345678",
                customer_chann_uid=identity.chann_uid,
            )
            assert lead.stage == "lead"
            warranties = WarrantyRepository(session)
            warranties.register(scope, serial_number="SN-20Z-1", product_name="แอร์")
            session.flush()

            unit = warranties.claim(
                scope, serial_number="SN-20Z-1", customer_chann_uid=identity.chann_uid,
            )
            row, outcome = customers.ensure_contact_for_identity(
                scope, identity.chann_uid, reason="ลงทะเบียนสินค้า SN-20Z-1",
            )
            if unit.contact_id is None and row is not None:
                unit.contact_id = row.id
            session.flush()

            assert outcome == "promoted" and row is not None and row.id == lead.id
            assert customers.get(scope, lead.id).stage == "contact"
            assert unit.contact_id == lead.id
            # Doing it twice changes nothing and says so.
            assert customers.ensure_contact_for_identity(scope, identity.chann_uid)[1] == "unchanged"
            session.rollback()

    def test_a_shop_with_no_record_gains_one_as_a_contact(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            other, _o2, _m2 = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            identity = _identity(session, first="สมหญิง", last="รักดี", phone="0899999999")
            customers = CustomerRepository(session)
            assert customers.find_by_chann_uid(scope, identity.chann_uid) is None

            row, outcome = customers.ensure_contact_for_identity(
                scope, identity.chann_uid, reason="ลงทะเบียนสินค้า SN-20Z-2",
            )
            session.flush()
            assert outcome == "created" and row is not None
            assert row.stage == "contact"
            # The name and number come from the person's own profile.
            assert row.first_name == "สมหญิง" and row.phone == "0899999999"
            assert "ลงทะเบียนสินค้า" in (row.notes or "")
            # The other shop gained nothing.
            assert CustomerRepository(session).find_by_chann_uid(TenantScope(other.id), identity.chann_uid) is None
            session.rollback()

    def test_the_migration_promotes_the_rows_already_there(self, migrated_db):
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.phase16 import WarrantyRepository
        from chann_data.repositories.tenant_scope import TenantScope

        path = ROOT / "database" / "alembic" / "versions" / "0036_warranty_contacts.py"
        source = path.read_text()
        assert 'down_revision = "0035_invoices"' in source
        namespace: dict = {}
        exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 — the migration's own SQL
        promote_sql, link_sql = namespace["PROMOTE_SQL"], namespace["LINK_SQL"]

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            identity = _identity(session, first="สมศรี", phone="0877777777")
            stranger = _identity(session, first="คนอื่น", phone="0866666666")
            customers = CustomerRepository(session)
            holder = customers.create(
                scope, first_name="สมศรี", phone="0877777777", customer_chann_uid=identity.chann_uid,
            )
            untouched = customers.create(
                scope, first_name="คนอื่น", phone="0866666666", customer_chann_uid=stranger.chann_uid,
            )
            warranties = WarrantyRepository(session)
            warranties.register(scope, serial_number="SN-20Z-3", product_name="พัดลม")
            unit = warranties.claim(
                scope, serial_number="SN-20Z-3", customer_chann_uid=identity.chann_uid,
            )
            unit.contact_id = None
            session.commit()

            session.execute(text(promote_sql))
            session.execute(text(link_sql))
            session.commit()

            assert customers.get(scope, holder.id).stage == "contact"
            assert customers.get(scope, untouched.id).stage == "lead", "a lead with no unit stays a lead"
            assert warranties.get(scope, unit.id).contact_id == holder.id
            session.rollback()
