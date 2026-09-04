"""Phase 16.5 against a real Postgres — Master Spec 16.5.6, all six:
erasure anonymises and never deletes, crosses every tenant, touches no
one else; consent is recorded; export is complete and leaks nothing;
every request leaves its rows and audit trail.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chann_data.models import AuditLog, ChannIdentity, Customer, CustomerLicenseLink, ServiceTicket, TicketPhoto
from chann_data.repositories.phase12 import ServiceTicketRepository
from chann_data.repositories.phase13 import FieldServiceRepository
from chann_data.repositories.phase165 import ANON_CUSTOMER, ANON_NAME, ANON_PHOTO, PdpaRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def world(migrated_db):
    """Two shops; สมชาย is a customer of both with a job in each; สมหญิง is
    a customer of shop A only — the person who must not be touched."""
    tag = uuid.uuid4().hex[:6]
    uids = {"a": f"CHN-P165{tag}-A", "b": f"CHN-P165{tag}-B", "somchai": f"CHN-P165{tag}-SC", "somying": f"CHN-P165{tag}-SY"}
    with Session(migrated_db) as s:
        for key, role in (("a", "sales"), ("b", "sales"), ("somchai", "customer"), ("somying", "customer")):
            s.add(ChannIdentity(chann_uid=uids[key], line_user_id=f"line-{uids[key]}", primary_role=role,
                                display_name="สมชาย" if key == "somchai" else ("สมหญิง" if key == "somying" else None),
                                phone="0812345678" if key == "somchai" else None,
                                signature_url=f"gs://b/signatures/{uids['somchai']}/s.png" if key == "somchai" else None))
        s.commit()
    with Session(migrated_db) as s:
        reg = RegistrationRepository(s)
        lic_a = reg.create_license(company_name=f"Shop A {tag}", created_by_chann_uid=uids["a"])
        lic_b = reg.create_license(company_name=f"Shop B {tag}", created_by_chann_uid=uids["b"])
        ids = (lic_a.id, lic_b.id)
        s.commit()
    scope_a, scope_b = TenantScope(license_id=ids[0]), TenantScope(license_id=ids[1])
    tickets = {}
    with Session(migrated_db) as s:
        for scope, uid, name in ((scope_a, uids["somchai"], "สมชาย ใจดี"), (scope_b, uids["somchai"], "สมชาย ใจดี"), (scope_a, uids["somying"], "สมหญิง ดีใจ")):
            s.add(CustomerLicenseLink(chann_uid=uid, license_id=scope.license_id))
            customer = CustomerRepository(s).create(scope, first_name=name.split()[0], last_name=name.split()[1],
                                                    phone="0812345678" if uid == uids["somchai"] else "0899999999",
                                                    customer_chann_uid=uid)
            s.flush()
            if uid == uids["somchai"]:
                DealRepository(s).create(scope, contact_id=customer.id, notes="ซื้อแอร์")
            ticket = ServiceTicketRepository(s).create(
                scope, customer_name=name, customer_phone=customer.phone, issue_description="แอร์ไม่เย็น",
                service_address="99/1 ถ.สุขุมวิท", customer_chann_uid=uid,
            )
            s.flush()
            tickets[(scope.license_id, uid)] = ticket.id
            if uid == uids["somchai"]:
                FieldServiceRepository(s).add_photo(scope, ticket_id=ticket.id, photo_url=f"gs://b/documents/{scope.license_id}/p.jpg", photo_type="evidence")
        s.commit()
    return migrated_db, scope_a, scope_b, uids, tickets


class TestErasure:
    def test_anonymizes_not_deletes_and_keeps_fks(self, world):
        engine, scope_a, scope_b, uids, tickets = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            request = repo.create_request(chann_uid=uids["somchai"], request_type="erasure", requested_via="chat")
            result = repo.erase(request.id)
            s.commit()
            assert result["tenants"] == 2 and result["customers"] == 2 and result["tickets"] == 2 and result["photos"] == 2
            assert len(result["storage_paths"]) == 3  # two photos + the signature
        with Session(engine) as s:
            identity = s.get(ChannIdentity, uids["somchai"])
            assert identity is not None and identity.anonymized_at is not None
            assert identity.display_name == ANON_NAME and identity.phone is None and identity.signature_url is None
            assert identity.line_user_id == f"line-{uids['somchai']}"  # kept for dedup
            assert identity.consent_accepted_at is None
            customers = list(s.execute(select(Customer).where(Customer.customer_chann_uid == uids["somchai"])).scalars())
            assert len(customers) == 2 and all(c.first_name == ANON_CUSTOMER and c.phone is None for c in customers)
            # The deal behind the customer still points at the row.
            from chann_data.models import Deal

            assert s.execute(select(Deal).where(Deal.contact_id == customers[0].id)).scalars().first() is not None
            for (license_id, uid), ticket_id in tickets.items():
                ticket = s.get(ServiceTicket, ticket_id)
                assert ticket is not None
                if uid == uids["somchai"]:
                    assert ticket.customer_name == ANON_CUSTOMER and ticket.customer_phone is None
            photos = list(s.execute(select(TicketPhoto).where(TicketPhoto.photo_url == ANON_PHOTO)).scalars())
            assert len(photos) == 2

    def test_cross_tenant_audit_rows(self, world):
        engine, scope_a, scope_b, uids, _ = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            request = repo.create_request(chann_uid=uids["somchai"], request_type="erasure", requested_via="platform_admin")
            repo.erase(request.id)
            s.commit()
            rows = list(s.execute(select(AuditLog).where(AuditLog.action == "pdpa_erasure", AuditLog.entity_id == request.id)).scalars())
            assert {r.license_id for r in rows} == {scope_a.license_id, scope_b.license_id}
            assert all(r.cross_tenant for r in rows)
            assert s.get(type(request), request.id).status == "completed"

    def test_isolation(self, world):
        engine, scope_a, scope_b, uids, _ = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            repo.erase(repo.create_request(chann_uid=uids["somchai"], request_type="erasure", requested_via="chat").id)
            s.commit()
            other = s.execute(select(Customer).where(Customer.customer_chann_uid == uids["somying"])).scalars().first()
            assert other.first_name == "สมหญิง" and other.phone == "0899999999"
            assert s.get(ChannIdentity, uids["somying"]).anonymized_at is None


class TestConsentAndExport:
    def test_consent_is_recorded(self, world):
        engine, _, _, uids, _ = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            assert repo.consent_of(uids["somying"])["consent_accepted_at"] is None
            repo.record_consent(uids["somying"], version="2026-09-04")
            s.commit()
            row = repo.consent_of(uids["somying"])
            assert row["consent_accepted_at"] is not None and row["consent_version"] == "2026-09-04"

    def test_export_is_complete_and_leaks_nothing(self, world):
        engine, scope_a, scope_b, uids, _ = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            request = repo.create_request(chann_uid=uids["somchai"], request_type="export", requested_via="liff")
            bundle = repo.export(request.id)
            s.commit()
            assert bundle["identity"]["phone"] == "0812345678"
            assert {c["license_id"] for c in bundle["companies"]} == {str(scope_a.license_id), str(scope_b.license_id)}
            for company in bundle["companies"]:
                assert company["customer"]["first_name"] == "สมชาย"
                assert [t["issue_description"] for t in company["tickets"]] == ["แอร์ไม่เย็น"]
            text = str(bundle)
            assert "สมหญิง" not in text and "0899999999" not in text
            audits = list(s.execute(select(AuditLog).where(AuditLog.action == "pdpa_export", AuditLog.entity_id == request.id)).scalars())
            assert len(audits) == 2 and all(a.cross_tenant for a in audits)

    def test_every_request_leaves_its_row(self, world):
        engine, _, _, uids, _ = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            e = repo.create_request(chann_uid=uids["somchai"], request_type="export", requested_via="chat")
            x = repo.create_request(chann_uid=uids["somchai"], request_type="erasure", requested_via="chat")
            repo.reject(x.id, reason="identity disputed", processed_by=None)
            s.commit()
            rows = repo.list_requests(chann_uid=uids["somchai"])
            assert {r.id for r in rows} == {e.id, x.id}
            assert repo.get_request(x.id).status == "rejected"
