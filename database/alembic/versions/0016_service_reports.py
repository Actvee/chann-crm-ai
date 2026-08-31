"""Phase 13 — check-in, evidence, and the service report.

Revision ID: 0016_service_reports
Revises: 0015_service_tickets

Two tables, and deliberately NOT a third for templates: the service report
uses the generic `document_templates` / `document_template_versions` from
Phase 10 with `document_type = 'service_report'` (13.3 says so
explicitly). A second versioning model would be the same problem solved
twice, and the two would drift.

`ticket_photos` carries GPS on the photo rather than on the ticket. A
check-in and a check-out happen in the same place but hours apart, and a
single pair of columns on the ticket could only ever record one of them —
which is the one that matters when a customer disputes whether anyone
turned up.

Coordinates are NUMERIC(10,7), not float. Seven decimal places is about a
centimetre, and float arithmetic on a location that may be evidence is a
precision loss nobody can later account for.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0016_service_reports"
down_revision = "0015_service_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column("report_id", sa.String(32), nullable=False),
        sa.Column(
            "ticket_id", UUID(as_uuid=True),
            sa.ForeignKey("service_tickets.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column(
            "technician_member_id", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"), nullable=True,
        ),
        # What the technician wrote, as data rather than columns. The fields
        # a service report needs differ by trade — an air-conditioning
        # report and a plumbing one share almost nothing — and a column per
        # field would mean a migration every time a tenant wanted one more.
        sa.Column("report_data", JSONB, nullable=False),
        sa.Column("pdf_path", sa.String(512), nullable=True),
        # Links to the generated_documents row, so a report's PDF has the
        # same audit trail (template version, snapshot, sha256) as a quote's.
        sa.Column(
            "generated_document_id", UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_service_reports_license_report", "service_reports", ["license_id", "report_id"],
    )
    # One submitted report per ticket. A second would make "the report for
    # this job" ambiguous at exactly the moment it is being approved.
    op.create_index(
        "uq_service_reports_ticket_open", "service_reports", ["ticket_id"],
        unique=True, postgresql_where=sa.text("status <> 'rejected'"),
    )

    op.create_table(
        "ticket_photos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column(
            "ticket_id", UUID(as_uuid=True),
            sa.ForeignKey("service_tickets.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("photo_url", sa.String(512), nullable=False),
        # checkin | checkout | evidence. The first two are the visit's own
        # record; evidence is anything the technician thought worth keeping.
        sa.Column("photo_type", sa.String(32), nullable=False, server_default="evidence"),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        # NUMERIC, not float: seven decimals is roughly a centimetre, and
        # float rounding on something that may be evidence is a loss nobody
        # can account for afterwards.
        sa.Column("gps_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("gps_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column(
            "uploaded_by", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_ticket_photos_ticket_type", "ticket_photos", ["ticket_id", "photo_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_photos_ticket_type", table_name="ticket_photos")
    op.drop_table("ticket_photos")
    op.drop_index("uq_service_reports_ticket_open", table_name="service_reports")
    op.drop_constraint(
        "uq_service_reports_license_report", "service_reports", type_="unique",
    )
    op.drop_table("service_reports")
