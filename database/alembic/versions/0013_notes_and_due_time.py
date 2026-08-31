"""Notes as real rows, and follow-ups that can carry a time.

Revision ID: 0013_notes_and_due_time
Revises: 0012_deal_code_per_license

Two gaps that ACTION_PERMISSIONS already promised but nothing implemented.

NOTES
-----
`("create", "note"): "note.create"` has been in the permission table since
Phase 6, but there was no `notes` table at all. What existed was a single
`notes` TEXT column on customers, deals and follow-ups: one blob per
record, overwritten on every edit, with no author, no timestamp and no
history. "What did we agree with this customer in March" was unanswerable.

The per-record columns are deliberately left in place. They hold real data
today, and this migration does not try to guess which of them are notes
worth splitting into rows versus incidental scribbles — a backfill that
mangles a tenant's existing text is worse than two places to look for a
while. New notes go in the table; the old columns keep whatever they hold.

DUE TIME
--------
`follow_ups.due_date` is a DATE because the Master Spec says DATE, which is
right for "follow up on Friday" but cannot express "meet the customer at
14:00 on Friday". Rather than change the column's type — which would break
the date-only comparisons the repository is deliberately built around, and
force a timezone decision onto every existing row — an optional TIME is
added alongside it. NULL keeps today's exact meaning: a whole-day
reminder. A value makes it an appointment.

Time only, no zone: a Thai SMB's appointments are in its own local time,
and storing UTC would make "14:00" display differently depending on where
the reader happens to be, which is the opposite of what a person writing
"บ่ายสอง" means.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0013_notes_and_due_time"
down_revision = "0012_deal_code_per_license"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        # Polymorphic by (entity_type, entity_id), matching follow_ups. A note
        # can hang off a customer, a deal, a quote or anything added later
        # without a schema change per entity.
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # Who wrote it. chann_uid rather than a member id: the author is a
        # person, and their membership can be revoked without the note
        # losing its attribution.
        sa.Column("author_chann_uid", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # The only query that matters: every note on one record, newest first.
    op.create_index(
        "ix_notes_entity", "notes", ["license_id", "entity_type", "entity_id", "created_at"],
    )

    op.add_column("follow_ups", sa.Column("due_time", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("follow_ups", "due_time")
    op.drop_index("ix_notes_entity", table_name="notes")
    op.drop_table("notes")
