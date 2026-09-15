"""A registered unit names the customer it was sold to (round 18b, 15 Sep 2026).

The row carries contact_id; the API used to send only the LINE claim
(customer_chann_uid), so the book said "no customer" about a unit
registered for a named customer.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity
from chann_data.repositories.phase16 import WarrantyRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository
from chann_data.repositories.tenant_scope import TenantScope
from chann_data.routers.internal import _warranty_contacts, _warranty_out


def test_the_list_row_carries_the_contact_name_and_code(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-W{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        lic = RegistrationRepository(s).create_license(company_name=f"Warranty {tag}", created_by_chann_uid=uid)
        s.commit()
        license_id = lic.id
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as s:
        customer = CustomerRepository(s).create(scope, first_name="มิ", last_name="เกียร", phone="0811111111")
        s.flush()
        attached = WarrantyRepository(s).register(scope, serial_number=f"SN{tag}A", contact_id=customer.id)
        alone = WarrantyRepository(s).register(scope, serial_number=f"SN{tag}B")
        s.commit()
        rows = WarrantyRepository(s).list_for_license(scope)
        contacts = _warranty_contacts(s, scope, rows)
        out = {r.serial_number: _warranty_out(r, contacts.get(r.contact_id)) for r in rows}
    with_contact = out[f"SN{tag}A"]
    assert with_contact["contact_name"] == "มิ เกียร" and with_contact["contact_code"] == customer.customer_id
    assert with_contact["contact_id"] == str(customer.id) and with_contact["customer_chann_uid"] is None
    assert out[f"SN{tag}B"]["contact_name"] is None and out[f"SN{tag}B"]["contact_id"] is None
