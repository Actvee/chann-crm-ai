"""Phase 12 — service tickets.

Revision ID: 0015_service_tickets
Revises: 0014_assignment_rules

The customer-facing half of the product: someone reports a fault, a CS
person turns it into a ticket, and a technician goes and fixes it.

Two shapes here are worth explaining because they look like omissions.

There is no separate `work_orders` table (12.1 says so explicitly). A
ticket IS the unit of work — splitting the report from the job would mean
keeping two rows in step for no benefit anyone in an SMB would ever see.

`assigned_target_type` + `assigned_to_ref` is a polymorphic pointer to
either a member or a team, with no foreign key. Two nullable FKs would let
a row point at both at once, which is not a state that means anything;
the pair means exactly one, and the repository is what enforces it.

`ticket_number` is per-tenant (T-YYYY-NNNN), matching customer_id, deal_id
and quote_id — see the Deal model's docstring for why global numbering was
abandoned across this project.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015_service_tickets"
down_revision = "0014_assignment_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_tickets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column("ticket_number", sa.String(32), nullable=False),
        # The customer as a person, not a CRM row: someone can report a
        # fault before anyone has created a customer record for them, and
        # refusing the report until the paperwork exists is backwards.
        sa.Column("customer_chann_uid", sa.String(32), nullable=True),
        sa.Column(
            "contact_id", UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True,
        ),
        # Denormalised on the ticket rather than read through contact_id.
        # The dispatch gate needs a name and a number for THIS visit, and a
        # customer record edited next month must not silently change what a
        # technician was told to expect.
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("customer_phone", sa.String(32), nullable=True),
        sa.Column(
            "product_id", UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("serial_number", sa.String(128), nullable=True),
        sa.Column("issue_description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("visibility", sa.String(32), nullable=False, server_default="public"),
        sa.Column("assigned_target_type", sa.String(32), nullable=True),
        sa.Column("assigned_to_ref", UUID(as_uuid=True), nullable=True),
        sa.Column("accept_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("service_address", sa.Text(), nullable=True),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.Column("scheduled_time", sa.Time(), nullable=True),
        sa.Column(
            "owner_member_id", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column(
            "created_by", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"), nullable=True,
        ),
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
        "uq_service_tickets_license_number", "service_tickets",
        ["license_id", "ticket_number"],
    )
    # The two queries a technician's day is made of: "what is open" and
    # "what is mine".
    op.create_index(
        "ix_service_tickets_status", "service_tickets", ["license_id", "status"],
    )
    op.create_index(
        "ix_service_tickets_assignee", "service_tickets",
        ["license_id", "assigned_target_type", "assigned_to_ref"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_tickets_assignee", table_name="service_tickets")
    op.drop_index("ix_service_tickets_status", table_name="service_tickets")
    op.drop_constraint(
        "uq_service_tickets_license_number", "service_tickets", type_="unique",
    )
    op.drop_table("service_tickets")
