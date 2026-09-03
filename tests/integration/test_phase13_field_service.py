"""Phase 13 — field service execution.

The check-out gate is what this file is really about. A technician who
leaves without recording what they found leaves nobody able to answer the
customer's next question, invoice the work, or approve it — and by the
time anyone notices, they are three jobs away and do not remember.

Multi-tenant isolation is tested per Master Spec 13.6, because a service
report contains a customer's address, their fault history, and photographs
of the inside of their house.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase12 import ServiceTicketRepository  # noqa: E402
from chann_data.repositories.phase13 import (  # noqa: E402
    CheckoutBlocked,
    FieldServiceRepository,
    ReportConflict,
    ReportNotFound,
)

COMPLETE_TICKET = {
    "customer_name": "จุใจ มาติกา",
    "customer_phone": "0659635642",
    "service_address": "99/1 ถนนสุขุมวิท",
    "scheduled_date": date(2026, 9, 4),
    "scheduled_time": time(14, 0),
}

GOOD_REPORT = {
    "found_issue": "คอมเพรสเซอร์รั่ว",
    "work_done": "เปลี่ยนคอมเพรสเซอร์และเติมน้ำยา",
    "parts_changed": "คอมเพรสเซอร์ 1 ตัว",
    "notes": "แนะนำล้างแอร์ทุก 6 เดือน",
}


def _make_tenant(migrated_db, suffix):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity, LicenseMember
    from chann_data.repositories.phase65 import RegistrationRepository
    from chann_data.repositories.tenant_scope import TenantScope

    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-FS-{suffix}", line_user_id=f"line-fs-{suffix}",
            primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"Field {suffix}", created_by_chann_uid=f"CHN-FS-{suffix}",
        )
        session.commit()
        license_id = lic.id

    members = []
    with Session(migrated_db) as session:
        for index in range(2):
            identity = ChannIdentity(
                chann_uid=f"CHN-FT-{suffix}{index}",
                line_user_id=f"line-ft-{suffix}{index}",
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
            members.append(member.id)
        session.commit()

    return {
        "scope": TenantScope(license_id=license_id),
        "members": members,
        "session": lambda: Session(migrated_db),
    }


@pytest.fixture
def tenant(migrated_db):
    return _make_tenant(migrated_db, uuid.uuid4().hex[:6])


@pytest.fixture
def assigned_ticket(tenant):
    """A ticket dispatched to members[0] and accepted by them."""
    with tenant["session"]() as session:
        repo = ServiceTicketRepository(session)
        row = repo.create(
            tenant["scope"], issue_description="แอร์ไม่เย็น", **COMPLETE_TICKET,
        )
        session.flush()
        repo.assign(
            tenant["scope"], row.id,
            target_type="technician", target_ref=tenant["members"][0],
        )
        session.commit()
        return row.id


@pytest.fixture
def in_progress_ticket(tenant, assigned_ticket):
    """The same job after the technician arrived — check-out closes a
    visit that check-in opened (13.4), so every check-out test starts
    here rather than on a merely assigned ticket."""
    with tenant["session"]() as session:
        FieldServiceRepository(session).check_in(
            tenant["scope"], assigned_ticket, member_id=tenant["members"][0],
            gps_lat=13.7563309, gps_lng=100.5017651,
        )
        session.commit()
    return assigned_ticket


class TestCheckIn:
    def test_checking_in_records_gps_and_moves_the_ticket(self, tenant, assigned_ticket):
        with tenant["session"]() as session:
            row = FieldServiceRepository(session).check_in(
                tenant["scope"], assigned_ticket,
                member_id=tenant["members"][0],
                gps_lat=13.7563309, gps_lng=100.5017651,
            )
            session.commit()
            assert row.status == "in_progress"

        with tenant["session"]() as session:
            photos = FieldServiceRepository(session).list_photos(
                tenant["scope"], assigned_ticket, photo_type="checkin",
            )
            assert len(photos) == 1
            # Seven decimals survives: NUMERIC, not float, because a
            # location that may be evidence should not carry binary
            # rounding nobody can account for.
            assert photos[0].gps_lat == Decimal("13.7563309")

    def test_someone_else_cannot_check_in_to_your_ticket(self, tenant, assigned_ticket):
        """Their check-in would put a location and a timestamp against a
        visit they did not make."""
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).check_in(
                    tenant["scope"], assigned_ticket,
                    member_id=tenant["members"][1], gps_lat=13.0, gps_lng=100.0,
                )

    def test_a_completed_ticket_cannot_be_checked_in_to(self, tenant, assigned_ticket):
        with tenant["session"]() as session:
            ServiceTicketRepository(session).set_status(
                tenant["scope"], assigned_ticket, status="completed",
            )
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).check_in(
                    tenant["scope"], assigned_ticket, member_id=tenant["members"][0],
                )


class TestCheckOutFromTheSofa:
    def test_a_job_not_checked_in_to_cannot_be_checked_out_of(self, tenant, assigned_ticket):
        """Claimed and never arrived at: the visit has not started, so it
        cannot end. This is what let the technician home offer "ปิดงาน"
        straight after "รับงาน" (owner, 3 Sep)."""
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).check_out(
                    tenant["scope"], assigned_ticket, member_id=tenant["members"][0],
                    report_data={"found_issue": "ก", "work_done": "ข"},
                )


class TestCheckOutGate:
    """Master Spec 13.6 test_check_in_out."""

    def test_checking_out_with_no_report_is_refused(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            with pytest.raises(CheckoutBlocked) as caught:
                FieldServiceRepository(session).check_out(
                    tenant["scope"], in_progress_ticket,
                    member_id=tenant["members"][0], report_data={},
                )
            assert "ปัญหาที่พบ" in caught.value.missing
            assert "สิ่งที่แก้ไข" in caught.value.missing

    def test_a_partly_filled_report_names_only_what_is_missing(
        self, tenant, in_progress_ticket,
    ):
        """The technician is standing in a customer's house while they read
        this — "cannot check out" alone makes them guess."""
        with tenant["session"]() as session:
            with pytest.raises(CheckoutBlocked) as caught:
                FieldServiceRepository(session).check_out(
                    tenant["scope"], in_progress_ticket,
                    member_id=tenant["members"][0],
                    report_data={"found_issue": "คอมรั่ว"},
                )
            assert caught.value.missing == ["สิ่งที่แก้ไข"]

    def test_whitespace_does_not_count_as_filled_in(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            with pytest.raises(CheckoutBlocked):
                FieldServiceRepository(session).check_out(
                    tenant["scope"], in_progress_ticket,
                    member_id=tenant["members"][0],
                    report_data={"found_issue": "   ", "work_done": "\\n"},
                )

    def test_a_complete_report_closes_the_visit(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            report = FieldServiceRepository(session).check_out(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], report_data=GOOD_REPORT,
                gps_lat=13.7563309, gps_lng=100.5017651,
            )
            session.commit()
            assert report.report_id.startswith("SR-")
            # submitted, NOT approved: Phase 14 decides whether the work
            # was acceptable, and a technician approving their own visit
            # would make that step meaningless.
            assert report.status == "submitted"

        with tenant["session"]() as session:
            ticket = ServiceTicketRepository(session).get(tenant["scope"], in_progress_ticket)
            assert ticket.status == "completed"

    def test_the_gate_can_be_checked_without_attempting_a_checkout(self, tenant):
        with tenant["session"]() as session:
            repo = FieldServiceRepository(session)
            assert repo.report_blockers(GOOD_REPORT) == []
            assert repo.report_blockers({"found_issue": "x"}) == ["สิ่งที่แก้ไข"]

    def test_only_the_assignee_may_check_out(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).check_out(
                    tenant["scope"], in_progress_ticket,
                    member_id=tenant["members"][1], report_data=GOOD_REPORT,
                )

    def test_a_ticket_cannot_be_checked_out_of_twice(self, tenant, in_progress_ticket):
        """A second report would make "the report for this job" ambiguous at
        exactly the moment it is being approved."""
        with tenant["session"]() as session:
            FieldServiceRepository(session).check_out(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], report_data=GOOD_REPORT,
            )
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).check_out(
                    tenant["scope"], in_progress_ticket,
                    member_id=tenant["members"][0], report_data=GOOD_REPORT,
                )


class TestPhotos:
    """Master Spec 13.6 test_photo_upload."""

    def test_a_photo_is_tied_to_its_ticket_with_gps(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            row = FieldServiceRepository(session).add_photo(
                tenant["scope"], ticket_id=in_progress_ticket,
                photo_url="gs://bucket/evidence/1.jpg",
                photo_type="evidence", gps_lat=13.75, gps_lng=100.5,
                uploaded_by=tenant["members"][0],
            )
            session.commit()
            assert row.ticket_id == in_progress_ticket
            assert row.gps_lat == Decimal("13.75")

    def test_an_unknown_photo_type_is_refused(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).add_photo(
                    tenant["scope"], ticket_id=in_progress_ticket,
                    photo_url="gs://bucket/x.jpg", photo_type="selfie",
                )

    def test_a_photo_cannot_be_attached_to_another_tenants_ticket(
        self, tenant, migrated_db,
    ):
        other = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        with tenant["session"]() as session:
            ticket = ServiceTicketRepository(session).create(
                tenant["scope"], issue_description="งาน",
            )
            session.commit()
            ticket_id = ticket.id

        with other["session"]() as session:
            with pytest.raises(ReportNotFound):
                FieldServiceRepository(session).add_photo(
                    other["scope"], ticket_id=ticket_id,
                    photo_url="gs://bucket/x.jpg",
                )

    def test_checkin_and_checkout_photos_are_distinguishable(
        self, tenant, assigned_ticket,
    ):
        in_progress_ticket = assigned_ticket  # this test performs the check-in itself
        """One pair of GPS columns on the ticket could record only one of
        them — which is the one that matters when a customer disputes
        whether anyone turned up."""
        with tenant["session"]() as session:
            repo = FieldServiceRepository(session)
            repo.check_in(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], gps_lat=13.1, gps_lng=100.1,
            )
            repo.check_out(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], report_data=GOOD_REPORT,
                gps_lat=13.2, gps_lng=100.2,
            )
            session.commit()

        with tenant["session"]() as session:
            repo = FieldServiceRepository(session)
            check_in = repo.list_photos(
                tenant["scope"], in_progress_ticket, photo_type="checkin",
            )
            check_out = repo.list_photos(
                tenant["scope"], in_progress_ticket, photo_type="checkout",
            )
            assert check_in[0].gps_lat == Decimal("13.1")
            assert check_out[0].gps_lat == Decimal("13.2")


class TestReportLifecycle:
    def _report(self, tenant, in_progress_ticket):
        with tenant["session"]() as session:
            report = FieldServiceRepository(session).check_out(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], report_data=GOOD_REPORT,
            )
            session.commit()
            return report.id

    def test_a_pdf_can_be_attached_after_the_fact(self, tenant, in_progress_ticket):
        """Rendering can fail while the report itself is perfectly valid —
        losing the technician's work over a renderer outage would be
        absurd, so the report exists first and the PDF is linked once it
        renders."""
        from chann_data.models import GeneratedDocument
        from chann_data.repositories.phase10 import DocumentTemplateRepository

        report_id = self._report(tenant, in_progress_ticket)

        # Built through the real repositories rather than by hand: the
        # tables have NOT NULL columns this test has no business knowing
        # about, and hand-rolling rows means rediscovering each one.
        with tenant["session"]() as session:
            templates = DocumentTemplateRepository(session)
            template = templates.create_template(
                tenant["scope"], document_type="service_report",
                template_code="SR-TPL", template_name="Service report",
            )
            version = templates.create_draft_version(
                tenant["scope"], template_id=template.id,
                source_docx_path="gs://bucket/tpl/sr.docx",
                intermediate_model={}, mapping_schema={},
                compiled_template_path="gs://bucket/tpl/sr.html",
            )
            session.flush()

            document = GeneratedDocument(
                id=uuid.uuid4(), license_id=tenant["scope"].license_id,
                document_type="service_report", source_entity_type="service_report",
                source_entity_id=report_id, template_version_id=version.id,
                data_snapshot={}, output_path="gs://bucket/sr/1.pdf", sha256="x" * 64,
            )
            session.add(document)
            session.flush()

            row = FieldServiceRepository(session).attach_document(
                tenant["scope"], report_id,
                document_id=document.id, pdf_path="gs://bucket/sr/1.pdf",
            )
            session.commit()
            assert row.pdf_path == "gs://bucket/sr/1.pdf"
            assert row.generated_document_id == document.id

    def test_an_approved_report_cannot_be_un_approved(self, tenant, in_progress_ticket):
        """It has been acted on — invoiced, closed, reported to the
        customer. Reversing it rewrites a decision other things depend on."""
        report_id = self._report(tenant, in_progress_ticket)
        with tenant["session"]() as session:
            FieldServiceRepository(session).set_report_status(
                tenant["scope"], report_id, status="approved",
            )
            session.commit()
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).set_report_status(
                    tenant["scope"], report_id, status="rejected",
                )

    def test_an_unknown_status_is_refused(self, tenant, in_progress_ticket):
        report_id = self._report(tenant, in_progress_ticket)
        with tenant["session"]() as session:
            with pytest.raises(ReportConflict):
                FieldServiceRepository(session).set_report_status(
                    tenant["scope"], report_id, status="เสร็จมั้ง",
                )


class TestMultiTenantIsolation:
    """Master Spec 13.6 test_multi_tenant_service_report.

    A service report holds a customer's address, their fault history, and
    photographs of the inside of their house.
    """

    def test_a_report_is_invisible_to_another_tenant(
        self, tenant, in_progress_ticket, migrated_db,
    ):
        other = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        with tenant["session"]() as session:
            report = FieldServiceRepository(session).check_out(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], report_data=GOOD_REPORT,
            )
            session.commit()
            report_id = report.id

        with other["session"]() as session:
            repo = FieldServiceRepository(session)
            assert repo.get_report(other["scope"], report_id) is None
            assert repo.list_reports(other["scope"]) == []

    def test_photos_are_invisible_to_another_tenant(
        self, tenant, in_progress_ticket, migrated_db,
    ):
        other = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        with tenant["session"]() as session:
            FieldServiceRepository(session).add_photo(
                tenant["scope"], ticket_id=in_progress_ticket,
                photo_url="gs://bucket/private.jpg", gps_lat=13.0, gps_lng=100.0,
            )
            session.commit()

        with other["session"]() as session:
            assert FieldServiceRepository(session).list_photos(
                other["scope"], in_progress_ticket,
            ) == []

    def test_report_numbering_is_per_tenant(self, tenant, in_progress_ticket, migrated_db):
        """A new tenant's first report must be SR-YYYY-0001, not whatever
        the platform-wide count happens to be — same reasoning as customer,
        deal, quote and ticket codes."""
        other = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        with tenant["session"]() as session:
            first = FieldServiceRepository(session).check_out(
                tenant["scope"], in_progress_ticket,
                member_id=tenant["members"][0], report_data=GOOD_REPORT,
            )
            session.commit()
            assert first.report_id.endswith("0001")

        with other["session"]() as session:
            ticket = ServiceTicketRepository(session).create(
                other["scope"], issue_description="งาน", **COMPLETE_TICKET,
            )
            session.flush()
            ServiceTicketRepository(session).assign(
                other["scope"], ticket.id,
                target_type="technician", target_ref=other["members"][0],
            )
            session.commit()
            ticket_id = ticket.id

        with other["session"]() as session:
            FieldServiceRepository(session).check_in(
                other["scope"], ticket_id, member_id=other["members"][0],
                gps_lat=13.0, gps_lng=100.0,
            )
            session.commit()
        with other["session"]() as session:
            second = FieldServiceRepository(session).check_out(
                other["scope"], ticket_id,
                member_id=other["members"][0], report_data=GOOD_REPORT,
            )
            session.commit()
            assert second.report_id.endswith("0001")
