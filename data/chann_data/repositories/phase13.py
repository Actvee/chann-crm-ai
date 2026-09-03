"""Phase 13 — check-in, evidence, and the check-out gate.

The gate (13.4) is the point of the phase. A technician who leaves without
writing down what they found leaves nobody able to answer the customer's
next question, invoice the work, or approve it — and by the time anyone
notices, the technician is three jobs away and does not remember.

Enforced here, next to the write it guards, for the same reason the
dispatch gate is: a check that lives one tier up leaves the endpoint that
actually sets the status reachable without it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ServiceReport, ServiceTicket, TicketPhoto
from .tenant_scope import TenantScope

PHOTO_TYPES = frozenset({"checkin", "checkout", "evidence"})
REPORT_STATUSES = frozenset({"draft", "submitted", "approved", "rejected"})

# What a report must say before anyone can leave. Labels rather than field
# names because the list is read back to the technician in chat, and
# "found_issue" is not something they typed or would recognise.
REPORT_REQUIRED = (
    ("found_issue", "ปัญหาที่พบ"),
    ("work_done", "สิ่งที่แก้ไข"),
)


class ReportNotFound(Exception):
    pass


class ReportConflict(Exception):
    pass


class CheckoutBlocked(Exception):
    """The service report is not complete enough to close the visit."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(", ".join(missing))


class FieldServiceRepository:
    def __init__(self, session: Session):
        self._s = session

    # ------------------------------------------------------------ photos

    def add_photo(
        self,
        scope: TenantScope,
        *,
        ticket_id: uuid.UUID,
        photo_url: str,
        photo_type: str = "evidence",
        gps_lat: Decimal | float | str | None = None,
        gps_lng: Decimal | float | str | None = None,
        taken_at: datetime | None = None,
        uploaded_by: uuid.UUID | None = None,
    ) -> TicketPhoto:
        if photo_type not in PHOTO_TYPES:
            raise ReportConflict(f"unknown photo type: {photo_type!r}")

        ticket = self._s.execute(
            select(ServiceTicket).where(
                ServiceTicket.id == ticket_id,
                ServiceTicket.license_id == scope.license_id,
            )
        ).scalars().first()
        if ticket is None:
            raise ReportNotFound("ticket not found in this tenant")

        row = TicketPhoto(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            ticket_id=ticket_id,
            photo_url=photo_url,
            photo_type=photo_type,
            taken_at=taken_at or datetime.now(timezone.utc),
            # Converted through str so a float that arrived over JSON does
            # not carry its binary rounding into a NUMERIC column.
            gps_lat=Decimal(str(gps_lat)) if gps_lat is not None else None,
            gps_lng=Decimal(str(gps_lng)) if gps_lng is not None else None,
            uploaded_by=uploaded_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def list_photos(
        self, scope: TenantScope, ticket_id: uuid.UUID, *, photo_type: str | None = None,
    ) -> list[TicketPhoto]:
        query = select(TicketPhoto).where(
            TicketPhoto.license_id == scope.license_id,
            TicketPhoto.ticket_id == ticket_id,
        )
        if photo_type:
            query = query.where(TicketPhoto.photo_type == photo_type)
        return list(
            self._s.execute(query.order_by(TicketPhoto.created_at)).scalars()
        )

    # --------------------------------------------------------- check-in

    def check_in(
        self,
        scope: TenantScope,
        ticket_id: uuid.UUID,
        *,
        member_id: uuid.UUID,
        gps_lat=None,
        gps_lng=None,
        photo_url: str | None = None,
    ) -> ServiceTicket:
        """Mark a technician as on site.

        Requires the ticket to be theirs. Someone else's check-in would
        put a location and a timestamp against a visit they did not make,
        which is the kind of record that only matters when it is wrong.
        """
        ticket = self._s.execute(
            select(ServiceTicket).where(
                ServiceTicket.id == ticket_id,
                ServiceTicket.license_id == scope.license_id,
            )
        ).scalars().first()
        if ticket is None:
            raise ReportNotFound("ticket not found in this tenant")
        if ticket.status in ("completed", "cancelled"):
            raise ReportConflict(f"a {ticket.status} ticket cannot be checked in to")
        if ticket.assigned_to_ref != member_id:
            raise ReportConflict("this ticket is not assigned to you")

        if photo_url or gps_lat is not None:
            self.add_photo(
                scope, ticket_id=ticket_id,
                photo_url=photo_url or "",
                photo_type="checkin",
                gps_lat=gps_lat, gps_lng=gps_lng, uploaded_by=member_id,
            )

        ticket.status = "in_progress"
        self._s.flush()
        return ticket

    # ---------------------------------------------------- report + gate

    def report_blockers(self, report_data: dict) -> list[str]:
        """What the report still has to say (13.4).

        Checked against the DATA rather than the row so a form can show the
        gaps while the technician is still typing, not only when they try
        to leave.
        """
        return [
            label for field, label in REPORT_REQUIRED
            if not str((report_data or {}).get(field) or "").strip()
        ]

    def _next_report_id(self, scope: TenantScope) -> str:
        year = datetime.now(timezone.utc).year
        prefix = f"SR-{year}-"
        existing = self._s.execute(
            select(ServiceReport.report_id).where(
                ServiceReport.license_id == scope.license_id,
                ServiceReport.report_id.like(f"{prefix}%"),
            )
        ).scalars().all()
        used = {
            int(code.rsplit("-", 1)[1])
            for code in existing
            if code.rsplit("-", 1)[1].isdigit()
        }
        return f"{prefix}{(max(used) + 1) if used else 1:04d}"

    def check_out(
        self,
        scope: TenantScope,
        ticket_id: uuid.UUID,
        *,
        member_id: uuid.UUID,
        report_data: dict,
        gps_lat=None,
        gps_lng=None,
        photo_url: str | None = None,
    ) -> ServiceReport:
        """Close a visit, if there is a report to close it with.

        The report is created here rather than separately: a check-out
        without one is the thing the gate exists to prevent, and two
        endpoints would mean two orders they could happen in.
        """
        ticket = self._s.execute(
            select(ServiceTicket).where(
                ServiceTicket.id == ticket_id,
                ServiceTicket.license_id == scope.license_id,
            )
        ).scalars().first()
        if ticket is None:
            raise ReportNotFound("ticket not found in this tenant")
        if ticket.assigned_to_ref != member_id:
            raise ReportConflict("this ticket is not assigned to you")
        if ticket.status == "completed":
            raise ReportConflict("this ticket is already completed")
        if ticket.status == "cancelled":
            raise ReportConflict("a cancelled ticket cannot be checked out of")
        if ticket.status != "in_progress":
            # 13.4: check-out closes a visit that check-in opened. Without
            # this a job could be "finished" from the sofa.
            raise ReportConflict("check in first — this ticket is not in progress")

        blockers = self.report_blockers(report_data)
        if blockers:
            raise CheckoutBlocked(blockers)

        existing = self._s.execute(
            select(ServiceReport).where(
                ServiceReport.license_id == scope.license_id,
                ServiceReport.ticket_id == ticket_id,
                ServiceReport.status != "rejected",
            )
        ).scalars().first()
        if existing is not None:
            raise ReportConflict("this ticket already has a report")

        if photo_url or gps_lat is not None:
            self.add_photo(
                scope, ticket_id=ticket_id,
                photo_url=photo_url or "",
                photo_type="checkout",
                gps_lat=gps_lat, gps_lng=gps_lng, uploaded_by=member_id,
            )

        report = ServiceReport(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            report_id=self._next_report_id(scope),
            ticket_id=ticket_id,
            technician_member_id=member_id,
            report_data=report_data,
            # submitted, not approved: Phase 14 decides whether the work
            # was acceptable, and a technician marking their own visit
            # approved would make that step meaningless.
            status="submitted",
        )
        self._s.add(report)
        ticket.status = "completed"
        self._s.flush()
        return report

    # -------------------------------------------------------- read/write

    def get_report(self, scope: TenantScope, report_id: uuid.UUID) -> ServiceReport | None:
        return self._s.execute(
            select(ServiceReport).where(
                ServiceReport.id == report_id,
                ServiceReport.license_id == scope.license_id,
            )
        ).scalars().first()

    def get_report_for_ticket(
        self, scope: TenantScope, ticket_id: uuid.UUID,
    ) -> ServiceReport | None:
        return self._s.execute(
            select(ServiceReport).where(
                ServiceReport.license_id == scope.license_id,
                ServiceReport.ticket_id == ticket_id,
                ServiceReport.status != "rejected",
            )
        ).scalars().first()

    def list_reports(
        self, scope: TenantScope, *, status: str | None = None, limit: int = 100,
    ) -> list[ServiceReport]:
        query = select(ServiceReport).where(ServiceReport.license_id == scope.license_id)
        if status:
            query = query.where(ServiceReport.status == status)
        return list(
            self._s.execute(
                query.order_by(ServiceReport.created_at.desc()).limit(max(1, min(limit, 500)))
            ).scalars()
        )

    def attach_document(
        self, scope: TenantScope, report_id: uuid.UUID, *,
        document_id: uuid.UUID, pdf_path: str,
    ) -> ServiceReport:
        """Link a rendered PDF back to its report.

        Separate from creation because rendering can fail while the report
        itself is perfectly valid — and a visit whose paperwork exists but
        whose PDF did not render is a delivery problem, not a reason to
        lose the technician's work.
        """
        row = self.get_report(scope, report_id)
        if row is None:
            raise ReportNotFound("report not found in this tenant")
        row.generated_document_id = document_id
        row.pdf_path = pdf_path
        self._s.flush()
        return row

    def set_report_status(
        self, scope: TenantScope, report_id: uuid.UUID, *, status: str,
    ) -> ServiceReport:
        if status not in REPORT_STATUSES:
            raise ReportConflict(f"unknown report status: {status!r}")
        row = self.get_report(scope, report_id)
        if row is None:
            raise ReportNotFound("report not found in this tenant")
        if row.status == "approved" and status != "approved":
            # An approved report has been acted on — invoiced, closed,
            # reported to the customer. Un-approving it would rewrite a
            # decision other things already depend on.
            raise ReportConflict("an approved report cannot change status")
        row.status = status
        self._s.flush()
        return row
