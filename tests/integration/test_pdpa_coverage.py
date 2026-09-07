"""Review D10/E9 (6 Sep 2026) against Postgres: erasure reaches the frozen
document snapshots, the notes and follow-ups on the person's customer
record, the deal notes, the audit values that quoted them, and hands back
every object (PDFs, export pages) for the Application tier to delete;
export covers the same tables. Plus the two Data-tier seams the admin
console relies on: a locked account answers 423 with locked_until, and a
suspend/reopen lands in the cross-tenant audit view.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chann_data.models import (
    AuditLog, ChannIdentity, Customer, CustomerLicenseLink, Deal, FollowUp, GeneratedDocument, Note,
    ServiceReport, ServiceTicket,
)
from chann_data.repositories.audit import AuditRepository, diff_fields
from chann_data.repositories.phase6 import FollowUpRepository, NoteRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase10 import DocumentTemplateRepository, GeneratedDocumentRepository, QuoteRepository
from chann_data.repositories.phase12 import ServiceTicketRepository
from chann_data.repositories.phase165 import ANON_CUSTOMER, ANON_PHOTO, ANON_TEXT, PdpaRepository, export_object_path
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

NAME, LAST, PHONE, ADDRESS = "ประยุทธ์", "จันทร์โอชา", "0861234567", "77/9 ซอยลาดพร้าว 101"
PERSONAL = (NAME, LAST, PHONE, ADDRESS)


@pytest.fixture
def world(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uid, owner = f"CHN-P10{tag}-C", f"CHN-P10{tag}-O"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=owner, line_user_id=f"line-{owner}", primary_role="sales"))
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="customer",
                            display_name=NAME, first_name=NAME, last_name=LAST, phone=PHONE, address=ADDRESS))
        s.commit()
    with Session(migrated_db) as s:
        lic = RegistrationRepository(s).create_license(company_name=f"Erase Co {tag}", created_by_chann_uid=owner)
        s.commit()
        scope = TenantScope(license_id=lic.id)
    ids: dict = {}
    with Session(migrated_db) as s:
        s.add(CustomerLicenseLink(chann_uid=uid, license_id=scope.license_id))
        customer = CustomerRepository(s).create(scope, first_name=NAME, last_name=LAST, phone=PHONE, address=ADDRESS,
                                                customer_chann_uid=uid)
        s.flush()
        ids["customer"] = customer.id
        NoteRepository(s).create(scope, entity_type="customer", entity_id=customer.id,
                                 body=f"โทรหา {NAME} แล้ว บอกว่าอยู่ {ADDRESS}", author_chann_uid=owner)
        FollowUpRepository(s).create(scope, entity_type="customer", entity_id=customer.id,
                                     due_date=date(2026, 9, 10), notes=f"นัด {NAME} โทร {PHONE}")
        deal = DealRepository(s).create(scope, contact_id=customer.id, notes=f"{NAME} ขอส่วนลด")
        s.flush()
        ids["deal"] = deal.id
        DealRepository(s).add_product(scope, deal.id, product_id=None, product_name="แอร์ 12000 BTU", quoted_unit_price=15900)
        quote = QuoteRepository(s).create(scope, deal_id=deal.id)
        s.flush()
        ids["quote"] = quote.id
        ticket = ServiceTicketRepository(s).create(
            scope, customer_name=f"{NAME} {LAST}", customer_phone=PHONE, issue_description="แอร์ไม่เย็น",
            service_address=ADDRESS, customer_chann_uid=uid,
        )
        s.flush()
        ids["ticket"] = ticket.id
        report = ServiceReport(id=uuid.uuid4(), license_id=scope.license_id, report_id=f"SR-{tag}", ticket_id=ticket.id,
                               report_data={"found_issue": "รั่ว", "work_done": "เปลี่ยน"}, status="approved",
                               pdf_path=f"documents/{scope.license_id}/service-reports/SR-{tag}.pdf")
        s.add(report)
        s.flush()
        ids["report"] = report.id
        templates = DocumentTemplateRepository(s)
        template = templates.create_template(scope, document_type="quote", template_code=f"T{tag}", template_name="t")
        version = templates.create_draft_version(
            scope, template.id, source_docx_path="builtin://none", intermediate_model={}, mapping_schema={},
            compiled_template_path="builtin://quote/v1",
        )
        docs = GeneratedDocumentRepository(s)
        ids["quote_doc"] = docs.record(
            scope, document_type="quote", source_entity_type="quote", source_entity_id=quote.id,
            template_version_id=version.id, output_path=f"documents/{scope.license_id}/quotes/Q-{tag}.pdf", sha256="a" * 64,
            data_snapshot={"company": {"name": "Erase Co"}, "quote": {"quote_id": f"Q-{tag}"},
                           "customer": {"name": f"{NAME} {LAST}", "phone": PHONE, "address": ADDRESS},
                           "line_items": [{"description": f"ติดตั้งที่บ้าน {NAME}"}]},
        ).id
        ids["report_doc"] = docs.record(
            scope, document_type="service_report", source_entity_type="service_report", source_entity_id=report.id,
            template_version_id=version.id, output_path=report.pdf_path, sha256="b" * 64,
            data_snapshot={"report": {"report_id": f"SR-{tag}"},
                           "ticket": {"customer_name": f"{NAME} {LAST}", "customer_phone": PHONE, "service_address": ADDRESS},
                           "technician": {"name": "ช่างเอก"}},
        ).id
        AuditRepository(s).write(
            license_id=scope.license_id, entity_type="customer", entity_id=customer.id, actor_type="user",
            actor_id=owner, action="update", field_changes=diff_fields({"phone": "0800000000"}, {"phone": PHONE}),
        )
        AuditRepository(s).write(
            license_id=scope.license_id, entity_type="service_ticket", entity_id=ticket.id, actor_type="user",
            actor_id=owner, action="create", field_changes={"customer_name": f"{NAME} {LAST}", "ticket_number": "T-1"},
        )
        # An export the person asked for earlier: its page is theirs too.
        earlier = PdpaRepository(s).create_request(chann_uid=uid, request_type="export", requested_via="chat")
        PdpaRepository(s).export(earlier.id)
        ids["export_request"] = earlier.id
        s.commit()
    return migrated_db, scope, uid, ids, tag


def _personal_in(text: str) -> list[str]:
    return [p for p in PERSONAL if p in text]


class TestErasureCoverage:
    def test_nothing_personal_remains_and_every_object_is_handed_back(self, world):
        engine, scope, uid, ids, tag = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            request = repo.create_request(chann_uid=uid, request_type="erasure", requested_via="liff")
            result = repo.erase(request.id)
            s.commit()
        assert result["notes"] == 1 and result["follow_ups"] == 1 and result["deals"] == 1 and result["documents"] == 2
        assert result["audit_rows"] >= 2
        paths = set(result["storage_paths"])
        assert f"documents/{scope.license_id}/quotes/Q-{tag}.pdf" in paths
        assert f"documents/{scope.license_id}/service-reports/SR-{tag}.pdf" in paths
        assert export_object_path(uid, ids["export_request"]) in paths

        with Session(engine) as s:
            customer = s.get(Customer, ids["customer"])
            assert customer.first_name == ANON_CUSTOMER and customer.phone is None and customer.address is None
            note = s.execute(select(Note).where(Note.entity_id == ids["customer"])).scalar_one()
            assert note.body == ANON_TEXT
            follow_up = s.execute(select(FollowUp).where(FollowUp.entity_id == ids["customer"])).scalar_one()
            assert follow_up.notes == ANON_TEXT
            assert s.get(Deal, ids["deal"]).notes == ANON_TEXT
            ticket = s.get(ServiceTicket, ids["ticket"])
            assert ticket.customer_name == ANON_CUSTOMER and ticket.service_address == ANON_TEXT
            report = s.get(ServiceReport, ids["report"])
            assert report.pdf_path == ANON_PHOTO and report.report_data["found_issue"] == "รั่ว"
            for key in ("quote_doc", "report_doc"):
                document = s.get(GeneratedDocument, ids[key])
                assert document.output_path == ANON_PHOTO
                assert not _personal_in(str(document.data_snapshot)), document.data_snapshot
            quote_doc = s.get(GeneratedDocument, ids["quote_doc"])
            # Structure survives — the shop's own words stay, the person's go.
            assert quote_doc.data_snapshot["company"]["name"] == "Erase Co"
            assert quote_doc.data_snapshot["quote"]["quote_id"] == f"Q-{tag}"
            assert quote_doc.data_snapshot["customer"]["name"] == ANON_TEXT
            assert quote_doc.data_snapshot["line_items"][0]["description"] == ANON_TEXT
            report_doc = s.get(GeneratedDocument, ids["report_doc"])
            assert report_doc.data_snapshot["technician"]["name"] == "ช่างเอก"
            for row in s.execute(select(AuditLog).where(AuditLog.license_id == scope.license_id)).scalars():
                assert not _personal_in(str(row.field_changes or {})), (row.action, row.field_changes)
            # The row that quoted the phone still records that a phone changed.
            changed = s.execute(select(AuditLog).where(AuditLog.entity_id == ids["customer"], AuditLog.action == "update")).scalar_one()
            assert "phone" in changed.field_changes
            assert s.get(ChannIdentity, uid).first_name is None

    def test_export_covers_the_same_tables(self, world):
        engine, scope, uid, ids, tag = world
        with Session(engine) as s:
            repo = PdpaRepository(s)
            bundle = repo.export(repo.create_request(chann_uid=uid, request_type="export", requested_via="liff").id)
            s.commit()
        (company,) = bundle["companies"]
        assert [n["body"] for n in company["notes"]] == [f"โทรหา {NAME} แล้ว บอกว่าอยู่ {ADDRESS}"]
        assert company["follow_ups"][0]["notes"] == f"นัด {NAME} โทร {PHONE}"
        assert company["deals"][0]["notes"] == f"{NAME} ขอส่วนลด"
        assert {d["document_type"] for d in company["documents"]} == {"quote", "service_report"}
        assert any(d["data_snapshot"]["customer"]["phone"] == PHONE for d in company["documents"] if d["document_type"] == "quote")


@pytest.fixture
def api(migrated_db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from chann_data import config as config_module
    from chann_data.db import get_session
    from chann_data.main import app

    monkeypatch.setattr(config_module.settings, "admin_secret", "test-internal-secret")
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), {"X-Internal-Secret": "test-internal-secret"}
    finally:
        app.dependency_overrides.pop(get_session, None)


class TestAdminSeams:
    def test_a_locked_account_answers_423_with_locked_until(self, api, migrated_db):
        from argon2 import PasswordHasher

        from chann_data.models import PlatformAdmin

        http, headers = api
        name = f"admin-{uuid.uuid4().hex[:6]}"
        with Session(migrated_db) as s:
            s.add(PlatformAdmin(id=uuid.uuid4(), username=name, password_hash=PasswordHasher().hash("right")))
            s.commit()
        for _ in range(4):
            assert http.post("/internal/v1/platform-admins/authenticate", headers=headers,
                             json={"username": name, "password": "wrong"}).status_code == 401
        tripped = http.post("/internal/v1/platform-admins/authenticate", headers=headers,
                            json={"username": name, "password": "wrong"})
        assert tripped.status_code == 423, tripped.text
        assert tripped.json()["detail"]["error"] == "locked" and tripped.json()["detail"]["locked_until"]
        again = http.post("/internal/v1/platform-admins/authenticate", headers=headers,
                          json={"username": name, "password": "right"})
        assert again.status_code == 423 and again.json()["detail"]["locked_until"] == tripped.json()["detail"]["locked_until"]

    def test_suspend_and_reopen_are_cross_tenant_audit_rows(self, api, world):
        http, headers = api
        engine, scope, uid, ids, tag = world
        for status in ("suspended", "active"):
            res = http.patch(f"/internal/v1/licenses/{scope.license_id}/status", headers={**headers, "X-Actor-Id": "admin-1"},
                             json={"status": status})
            assert res.status_code == 200, res.text
        with Session(engine) as s:
            rows = list(s.execute(select(AuditLog).where(
                AuditLog.entity_type == "license", AuditLog.entity_id == scope.license_id, AuditLog.actor_type == "platform_admin",
            )).scalars())
        assert len(rows) == 2 and all(r.cross_tenant for r in rows)
