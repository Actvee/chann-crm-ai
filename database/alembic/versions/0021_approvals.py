"""Phase 14 — approval workflows and satisfaction surveys.

Revision ID: 0021_approvals
Revises: 0020_crm_essentials

Three tables from Master Spec §14.3, one correction: the spec writes
`satisfaction_surveys.license_id -> services.id`; there is no such table
and every tenant-owned row in this schema keys on `licenses.id`.

Design decisions the owner made on 3 Sep and this schema encodes:

* A default workflow is one step — the CS who owns the ticket approves.
  It is created lazily per license on first use (see the repository),
  never seeded here: a migration that inserts business rules for every
  tenant is a rule nobody can later find the origin of.
* "ปิดงาน" means the LAST approver passing. `approval_steps` therefore
  carries `step_order`, and the repository resolves the survey trigger
  from "no pending step remains", not from step 1.
* `approval_steps` has UNIQUE(entity_type, entity_id, step_order): a
  report cannot accidentally get two step-1 rows from a double submit.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_approvals"
down_revision = "0020_crm_essentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("rules_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("license_members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # One ACTIVE workflow per (license, entity type). Older ones stay as
    # history with is_active=false — "who changed the approval flow and
    # to what" is an audit question the rows themselves should answer.
    op.create_index(
        "ix_approval_workflows_active_one",
        "approval_workflows", ["license_id", "entity_type"],
        unique=True, postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "approval_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("approval_workflows.id", ondelete="SET NULL"), nullable=True),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("approver_type", sa.String(16), nullable=False),  # role | user
        sa.Column("approver_ref", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("acted_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("license_members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", "step_order",
                            name="uq_approval_steps_entity_order"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')", name="ck_approval_steps_status",
        ),
        sa.CheckConstraint(
            "approver_type IN ('role','user')", name="ck_approval_steps_approver_type",
        ),
    )
    op.create_index("ix_approval_steps_pending", "approval_steps",
                    ["license_id", "status", "approver_ref"])
    op.create_index("ix_approval_steps_entity", "approval_steps",
                    ["license_id", "entity_type", "entity_id"])

    op.create_table(
        "satisfaction_surveys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("service_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scale_config_json", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # One survey per ticket: a second approval round (after a reject
        # and resubmit) reuses the row rather than asking the customer
        # twice.
        sa.UniqueConstraint("ticket_id", name="uq_satisfaction_surveys_ticket"),
    )


def downgrade() -> None:
    op.drop_table("satisfaction_surveys")
    op.drop_index("ix_approval_steps_entity", table_name="approval_steps")
    op.drop_index("ix_approval_steps_pending", table_name="approval_steps")
    op.drop_table("approval_steps")
    op.drop_index("ix_approval_workflows_active_one", table_name="approval_workflows")
    op.drop_table("approval_workflows")
