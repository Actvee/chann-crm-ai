"""Phase 11 — assignment rules.

Revision ID: 0014_assignment_rules
Revises: 0013_notes_and_due_time

One active rule per (license, scope). The AI writes `rules_json` once, at
configuration time, from a policy someone typed; the runtime engine only
ever reads it. That split is the point of the phase — an assignment that
changed depending on what a model felt like today would be impossible to
explain to the person who did not get the job.

`is_active` rather than deleting: a rule that assigned work last month is
part of why those records look the way they do, and the audit trail
references it.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014_assignment_rules"
down_revision = "0013_notes_and_due_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignment_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        # "sales" or "technician". A plain string for the same reason
        # license_members.role is: the set is expected to grow, and an enum
        # would need a migration every time it does.
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("rules_json", JSONB, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "updated_by", UUID(as_uuid=True),
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

    # At most one ACTIVE rule per scope, enforced by the database rather
    # than by application code. Two active rules would make assignment
    # depend on row order, which is exactly the kind of silent
    # non-determinism this phase exists to prevent. Superseded rules stay
    # as rows with is_active=false, so the partial index still allows them.
    op.create_index(
        "uq_assignment_rules_active_scope",
        "assignment_rules",
        ["license_id", "scope"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_assignment_rules_active_scope", table_name="assignment_rules")
    op.drop_table("assignment_rules")
